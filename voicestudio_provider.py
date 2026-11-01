"""VoiceStudio local speech synthesis provider and client adapter.

Provides zero-shot voice cloning and unlimited offline narration by communicating
with a local VoiceStudio REST API service (default: http://127.0.0.1:38123).
Model weights and checkpoints are managed separately in /home/rails/Work/AI-Models.
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import time
import unicodedata
import wave
from pathlib import Path
from urllib.parse import urlparse

import requests
from media_runtime import SESSION, log, run

VOICESTUDIO_DEFAULT_URL = os.getenv('VOICESTUDIO_URL', 'http://127.0.0.1:38123')
VOICESTUDIO_DEFAULT_SPEED = float(os.getenv('VOICESTUDIO_SPEED', '1.15'))
VOICESTUDIO_HEALTH_TIMEOUT = 5
VOICESTUDIO_SYNTH_TIMEOUT = 300
VOICESTUDIO_MAX_TEXT_LENGTH = 5000
TARGET_SAMPLE_RATE = 24000
TARGET_CHANNELS = 1


def validate_wav_bytes(data: bytes) -> None:
    """Validate that raw bytes constitute a valid RIFF/WAVE file with playable frames."""
    if not isinstance(data, bytes) or len(data) < 44 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise RuntimeError('VoiceStudio returned malformed audio: response is not a valid RIFF/WAVE file')
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            if wav.getnchannels() < 1 or wav.getsampwidth() < 1 or wav.getframerate() < 1 or wav.getnframes() < 1:
                raise RuntimeError('VoiceStudio returned malformed audio: WAV has no playable frames')
    except (EOFError, wave.Error) as ex:
        raise RuntimeError(f'VoiceStudio returned malformed audio: invalid WAV data ({ex})') from ex


def validate_wav_file(path: Path) -> bool:
    """Check that a file on disk is an openable WAV with at least one frame."""
    try:
        with wave.open(str(path), 'rb') as wav:
            return (wav.getnchannels() >= 1 and
                    wav.getsampwidth() >= 1 and
                    wav.getframerate() >= 1 and
                    wav.getnframes() >= 1)
    except (EOFError, OSError, wave.Error):
        return False


def get_wav_properties(path: Path) -> tuple[int, int, int]:
    """Return (channels, sample_width, framerate) for a WAV file."""
    with wave.open(str(path), 'rb') as wav:
        return wav.getnchannels(), wav.getsampwidth(), wav.getframerate()


def compute_sample_sha256(voice_sample: Path | str | None) -> str | None:
    """Compute SHA-256 digest of a reference voice sample file if provided."""
    if not voice_sample:
        return None
    sample_path = Path(voice_sample)
    if not sample_path.is_file():
        raise FileNotFoundError(f'VoiceStudio voice sample file not found: {sample_path}')
    return hashlib.sha256(sample_path.read_bytes()).hexdigest()


HINDI_0_TO_99 = {
    0: 'शून्य', 1: 'एक', 2: 'दो', 3: 'तीन', 4: 'चार', 5: 'पांच', 6: 'छह', 7: 'सात', 8: 'आठ', 9: 'नौ', 10: 'दस',
    11: 'ग्यारह', 12: 'बारह', 13: 'तेरह', 14: 'चौदह', 15: 'पंद्रह', 16: 'सोलह', 17: 'सत्रह', 18: 'अठारह', 19: 'उन्नीस',
    20: 'बीस', 21: 'इक्कीस', 22: 'बाईस', 23: 'तेईस', 24: 'चौबीस', 25: 'पच्चीस', 26: 'छब्बीस', 27: 'सत्ताईस', 28: 'अट्ठाईस', 29: 'उनतीस',
    30: 'तीस', 31: 'इकतीस', 32: 'बत्तीस', 33: 'तैंतीस', 34: 'चौंतीस', 35: 'पैंतीस', 36: 'छत्तीस', 37: 'सैंतीस', 38: 'अड़तीस', 39: 'उनतालीस',
    40: 'चालीस', 41: 'इकतालीस', 42: 'बयालीस', 43: 'तैंतालीस', 44: 'चवालीस', 45: 'पैंतालीस', 46: 'छियालीस', 47: 'सैंतालीस', 48: 'अड़तालीस', 49: 'उनचास',
    50: 'पचास', 51: 'इक्यावन', 52: 'बावन', 53: 'तिरपन', 54: 'चौवन', 55: 'पचपन', 56: 'छप्पन', 57: 'सत्तावन', 58: 'अट्ठावन', 59: 'उनसठ',
    60: 'साठ', 61: 'इकसठ', 62: 'बासठ', 63: 'तिरसठ', 64: 'चौंसठ', 65: 'पैंसठ', 66: 'छियासठ', 67: 'सरसठ', 68: 'अड़सठ', 69: 'उनहत्तर',
    70: 'सत्तर', 71: 'इकहत्तर', 72: 'बहत्तर', 73: 'तिहत्तर', 74: 'चौहत्तर', 75: 'पचहत्तर', 76: 'छिहत्तर', 77: 'सतहत्तर', 78: 'अठहत्तर', 79: 'उन्यासी',
    80: 'अस्सी', 81: 'इक्यासी', 82: 'बयासी', 83: 'तिरासी', 84: 'चौरासी', 85: 'पचासी', 86: 'छियासी', 87: 'सत्तासी', 88: 'अट्ठासी', 89: 'नवासी',
    90: 'नब्बे', 91: 'इक्यानवे', 92: 'बानवे', 93: 'तिरानवे', 94: 'चौरानवे', 95: 'पंचानवे', 96: 'छियानवे', 97: 'सत्तानवे', 98: 'अट्ठानवे', 99: 'निन्यानवे'
}


def number_to_hindi(n: int) -> str:
    """Converts integers into natural Hindi words."""
    if n < 0:
        return 'माइनस ' + number_to_hindi(-n)
    if n < 100:
        return HINDI_0_TO_99.get(n, str(n))
    if n < 1000:
        hundreds = n // 100
        rem = n % 100
        return (number_to_hindi(hundreds) + ' सौ ' + (number_to_hindi(rem) if rem else '')).strip()
    if n < 100000:
        thousands = n // 1000
        rem = n % 1000
        return (number_to_hindi(thousands) + ' हज़ार ' + (number_to_hindi(rem) if rem else '')).strip()
    if n < 10000000:
        lakhs = n // 100000
        rem = n % 100000
        return (number_to_hindi(lakhs) + ' लाख ' + (number_to_hindi(rem) if rem else '')).strip()
    crores = n // 10000000
    rem = n % 10000000
    return (number_to_hindi(crores) + ' करोड़ ' + (number_to_hindi(rem) if rem else '')).strip()


def replace_digits_with_hindi(text: str) -> str:
    """Replaces numeric digit strings with their Hindi word equivalents."""
    def _repl(m):
        try:
            return number_to_hindi(int(m.group(0)))
        except (ValueError, TypeError):
            return m.group(0)
    return re.sub(r'\b\d+\b', _repl, text)


def sanitize_text(text: str, language: str = 'hi') -> str:
    """Cleans punctuation anomalies and expands numbers in Hindi to prevent num2words errors."""
    if not text:
        return ""
    import unicodedata
    s = unicodedata.normalize("NFC", text.strip())
    if language in ('hi', 'hin', 'hindi'):
        s = replace_digits_with_hindi(s)
    s = re.sub(r"[\u2013\u2014—–]", ", ", s)
    s = re.sub(r"[:;]", ", ", s)
    s = re.sub(r"\?[।\.]+", "?", s)
    s = re.sub(r"![।\.]+", "!", s)
    s = re.sub(r"[।\.]{2,}", "।", s)
    s = re.sub(r"\?{2,}", "?", s)
    s = re.sub(r"!{2,}", "!", s)
    s = re.sub(r"([,!।\?])(?=[^\s\d])", r"\1 ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def voicestudio_cache_identity(text: str, language: str, mood: str = 'neutral',
                              base_url: str | None = None,
                              voice_sample: Path | str | None = None,
                              speed: float | None = None) -> dict:
    """Construct deterministic cache identity dictionary for VoiceStudio synthesis.

    Includes the SHA-256 hash of the voice sample so updating the audio file
    properly invalidates previously cached audio segments.
    """
    resolved_url = (base_url or VOICESTUDIO_DEFAULT_URL).rstrip('/')
    sample_hash = compute_sample_sha256(voice_sample)
    eff_speed = round(float(speed), 2) if speed is not None else round(VOICESTUDIO_DEFAULT_SPEED, 2)
    clean_text = sanitize_text(text, language=language)
    return {
        'base_url': resolved_url,
        'language': language,
        'mood': mood,
        'speed': eff_speed,
        'voice_sample_sha256': sample_hash,
        'voice_sample_name': Path(voice_sample).name if voice_sample else None,
        'output': {'format': 'wav', 'sample_rate': TARGET_SAMPLE_RATE, 'channels': TARGET_CHANNELS},
    }


def response_error_detail(response: requests.Response) -> str:
    """Extract informative error text from a service response."""
    try:
        body = response.json()
        if isinstance(body, dict):
            for key in ('detail', 'error', 'message'):
                if body.get(key):
                    return str(body[key])
    except (ValueError, requests.RequestException):
        pass
    text = getattr(response, 'text', '').strip()
    return text[:500] if text else getattr(response, 'reason', 'unknown server error')


def check_voicestudio_health(base_url: str | None = None, timeout: int = VOICESTUDIO_HEALTH_TIMEOUT) -> dict:
    """Probe the local VoiceStudio service for health and readiness."""
    url = (base_url or VOICESTUDIO_DEFAULT_URL).rstrip('/')
    health_url = f'{url}/health'
    try:
        r = SESSION.get(health_url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f'VoiceStudio health check returned invalid payload: {data!r}')
        if data.get('status') not in ('ok', 'ready', 'healthy') and not data.get('model'):
            # Allow permissive ok check if status is absent but model info is returned
            if 'error' in data:
                raise RuntimeError(f'VoiceStudio health check reported error: {data["error"]}')
        return data
    except requests.Timeout as ex:
        raise RuntimeError(f'VoiceStudio health check timed out after {timeout}s: {ex}') from ex
    except requests.RequestException as ex:
        # Also attempt fallback endpoint /api/status if /health gives 404
        if getattr(getattr(ex, 'response', None), 'status_code', None) == 404:
            try:
                fallback_url = f'{url}/api/status'
                r_fallback = SESSION.get(fallback_url, timeout=timeout)
                r_fallback.raise_for_status()
                return r_fallback.json()
            except Exception:
                pass
        raise RuntimeError(f'VoiceStudio health check failed at {health_url}: {ex}') from ex
    except ValueError as ex:
        raise RuntimeError(f'VoiceStudio health check returned invalid JSON: {ex}') from ex


def normalize_to_target_wav(source: Path, destination: Path) -> None:
    """Ensure audio conforms to 24000 Hz, 1-channel, 16-bit PCM WAV.

    If the source already matches the target specification, copies it directly.
    Otherwise, uses ffmpeg to resample cleanly.
    """
    channels, width, rate = get_wav_properties(source)
    if channels == TARGET_CHANNELS and rate == TARGET_SAMPLE_RATE and width == 2:
        shutil.copyfile(source, destination)
        return

    run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-i', str(source),
        '-ar', str(TARGET_SAMPLE_RATE),
        '-ac', str(TARGET_CHANNELS),
        '-sample_fmt', 's16',
        str(destination),
    ])


def voicestudio_speak(text: str, dest: Path, language: str, mood: str = 'neutral',
                      base_url: str | None = None,
                      voice_sample: Path | str | None = None,
                      speed: float | None = None,
                      timeout: int = VOICESTUDIO_SYNTH_TIMEOUT) -> None:
    """Synthesize speech using the local VoiceStudio REST API.

    Writes to an atomic temporary file, validates WAV integrity, normalizes
    to 24kHz mono PCM WAV, and atomically renames to the destination path.
    """
    narration = sanitize_text(text.strip(), language=language)
    if not narration:
        raise ValueError('VoiceStudio narration text cannot be empty')
    if len(narration) > VOICESTUDIO_MAX_TEXT_LENGTH:
        raise ValueError(f'VoiceStudio narration text exceeds limit of {VOICESTUDIO_MAX_TEXT_LENGTH} characters')

    url = (base_url or VOICESTUDIO_DEFAULT_URL).rstrip('/')
    check_voicestudio_health(url)

    eff_speed = round(float(speed), 2) if speed is not None else round(VOICESTUDIO_DEFAULT_SPEED, 2)

    payload = {
        'text': narration,
        'language': language,
        'mood': mood,
        'speed': eff_speed,
        'output_format': 'wav',
        'sample_rate': TARGET_SAMPLE_RATE,
    }

    if voice_sample:
        sample_path = Path(voice_sample).resolve()
        if not sample_path.is_file():
            raise FileNotFoundError(f'VoiceStudio voice sample file not found: {sample_path}')
        payload['voice_sample'] = str(sample_path)
        try:
            sample_bytes = sample_path.read_bytes()
            payload['voice_sample_base64'] = base64.b64encode(sample_bytes).decode('ascii')
        except OSError as ex:
            log(f'Warning: Could not read voice sample for base64 encoding: {ex}')

    synth_url = f'{url}/api/synthesize'
    try:
        r = SESSION.post(synth_url, json=payload, timeout=timeout)
        if r.status_code == 404:
            # Fallback to alternative common route /synthesize
            r = SESSION.post(f'{url}/synthesize', json=payload, timeout=timeout)
        r.raise_for_status()
    except requests.Timeout as ex:
        raise RuntimeError(f'VoiceStudio synthesis request timed out after {timeout}s: {ex}') from ex
    except requests.HTTPError as ex:
        response = ex.response if ex.response is not None else r
        raise RuntimeError(f'VoiceStudio synthesis failed (HTTP {response.status_code}): {response_error_detail(response)}') from ex
    except requests.RequestException as ex:
        raise RuntimeError(f'VoiceStudio synthesis request failed: {ex}') from ex

    content_type = r.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    if content_type and content_type not in ('audio/wav', 'audio/x-wav', 'application/octet-stream'):
        raise RuntimeError(f'VoiceStudio returned unexpected Content-Type {content_type!r}; expected audio/wav')

    validate_wav_bytes(r.content)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_raw = dest.with_name(f'{dest.stem}.raw.part.wav')
    tmp_normalized = dest.with_name(f'{dest.stem}.norm.part.wav')

    try:
        tmp_raw.write_bytes(r.content)
        normalize_to_target_wav(tmp_raw, tmp_normalized)
        if not validate_wav_file(tmp_normalized):
            raise RuntimeError(f'VoiceStudio produced an invalid or unreadable WAV file: {dest.name}')
        os.replace(tmp_normalized, dest)
    finally:
        tmp_raw.unlink(missing_ok=True)
        tmp_normalized.unlink(missing_ok=True)
