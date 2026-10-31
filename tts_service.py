"""Speech providers, narration caching, and audio preparation."""
import array
import asyncio
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import wave
from pathlib import Path
import requests
from media_runtime import SESSION, log, log_progress, run

INDIC_VOICES = ('Divya', 'Rohit', 'Aman', 'Rani')
INDIC_LANGUAGES = {'hi', 'hin', 'hindi', 'hi-in'}
INDIC_HEALTH_TIMEOUT = 5
INDIC_SYNTH_TIMEOUT = 300
NARRATION_TRIM_VERSION = 1
NARRATION_EDGE_PADDING_SECONDS = 0.09
NARRATION_SILENCE_THRESHOLD_DBFS = -48.0
NARRATION_SILENCE_WINDOW_SECONDS = 0.01
EDGE_TTS_PROFILES = {
    'female': {
        'voices': {'hi': 'hi-IN-SwaraNeural', 'en': 'en-US-AriaNeural'},
        'pitch': '-3Hz',
        'rate': '+20%',
    },
    'male': {
        'voices': {'hi': 'hi-IN-MadhurNeural', 'en': 'en-US-GuyNeural'},
        'pitch': '-10Hz',
        'rate': '+18%',
    },
    'male-energetic': {
        'voices': {'hi': 'hi-IN-MadhurNeural', 'en': 'en-US-GuyNeural'},
        'pitch': '-2Hz',
        'rate': '+35%',
    },
}
EDGE_TTS_PROVIDERS = (
    'edge', 'edge-female', 'edge-femail', 'edge-male', 'edge-male-energetic',
)
def resolve_gemini_voice(name=None):
    raw = name or os.getenv('GEMINI_TTS_VOICE', 'Puck')
    if 'puck' in raw.lower():
        return 'Puck'
    return raw


GEMINI_TTS_VOICE = resolve_gemini_voice()
GEMINI_TTS_MAX_ATTEMPTS = 5
GEMINI_TTS_RETRY_BUFFER_SECONDS = 2.0
GEMINI_TTS_FALLBACK_RETRY_SECONDS = 20.0
TTS_CACHE_VERSION = 1

def audio_duration(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def edge_tts_settings(language, provider='edge'):
    if provider not in EDGE_TTS_PROVIDERS:
        raise ValueError(f'Unsupported Edge TTS provider: {provider}')
    profile_name = {
        'edge-male': 'male',
        'edge-male-energetic': 'male-energetic',
    }.get(provider, 'female')
    try:
        profile = EDGE_TTS_PROFILES[profile_name]
        voice = profile['voices'][language]
    except KeyError as ex:
        raise ValueError(f'Unsupported Edge TTS language: {language}') from ex
    return {'voice': voice, 'pitch': profile['pitch'], 'rate': profile['rate']}


async def edge_speak(text, dest, language, mood='neutral', provider='edge'):
    # Edge offers voice/rate/pitch control but not reliable acting instructions.
    import edge_tts
    settings = edge_tts_settings(language, provider)
    await edge_tts.Communicate(text, **settings).save(str(dest))


def gemini_rate_limit_delay(error, fallback_seconds):
    """Return a bounded 429 retry delay, or None for a non-rate-limit error."""
    message = str(error)
    code = getattr(error, 'code', None)
    if code != 429 and not re.search(r'\b429\b|RESOURCE_EXHAUSTED', message, re.IGNORECASE):
        return None
    matches = re.findall(
        r'(?:retryDelay["\']?\s*:\s*["\']?|retry\s+in\s+)(\d+(?:\.\d+)?)s',
        message,
        re.IGNORECASE,
    )
    server_delay = max((float(value) for value in matches), default=fallback_seconds)
    return min(300.0, max(1.0, server_delay + GEMINI_TTS_RETRY_BUFFER_SECONDS))


def gemini_speak(text, dest, language, mood='neutral'):
    # Gemini generateContent single-speaker API. SDK response may be WAV or raw PCM.
    from google import genai
    from google.genai import types
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    delivery = {'horror': 'Narrate with restrained dread, uneasy pauses and rising tension. Sound like a human telling a frightening story, not an announcer.', 'joy': 'Narrate warmly, smiling, animated and full of genuine delight.', 'neutral': 'Narrate naturally with authentic storytelling emotion and varied intonation.'}.get(mood,'Narrate naturally.')
    request = {
        'model': os.getenv('GEMINI_TTS_MODEL','gemini-2.5-flash-preview-tts'),
        'contents': f'{delivery} Speak the following words verbatim in {"Hindi" if language=="hi" else "English"}; do not read the directions aloud. Script: {text}',
        'config': types.GenerateContentConfig(response_modalities=['AUDIO'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_TTS_VOICE)))),
    }
    for attempt in range(1, GEMINI_TTS_MAX_ATTEMPTS + 1):
        try:
            response=client.models.generate_content(**request)
            break
        except Exception as ex:
            fallback = GEMINI_TTS_FALLBACK_RETRY_SECONDS * attempt
            delay = gemini_rate_limit_delay(ex, fallback)
            if delay is None or attempt == GEMINI_TTS_MAX_ATTEMPTS:
                raise
            log(
                f'Gemini TTS rate limited (429); cooling down {delay:.1f}s before '
                f'retry {attempt + 1}/{GEMINI_TTS_MAX_ATTEMPTS}'
            )
            time.sleep(delay)
    parts=response.candidates[0].content.parts
    blob=next((p.inline_data for p in parts if getattr(p,'inline_data',None)),None)
    if not blob or not blob.data:raise RuntimeError('Gemini returned no audio')
    data=blob.data
    if data[:4]==b'RIFF':dest.write_bytes(data)
    else:
        with wave.open(str(dest),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(data)


def validate_wav_bytes(data):
    """Validate the raw WAV format returned by the local Indic service."""
    if not isinstance(data, bytes) or len(data) < 44 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise RuntimeError('Indic Parler returned malformed audio: response is not a RIFF/WAVE file')
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            if wav.getnchannels() < 1 or wav.getsampwidth() < 1 or wav.getframerate() < 1 or wav.getnframes() < 1:
                raise RuntimeError('Indic Parler returned malformed audio: WAV has no playable frames')
    except (EOFError, wave.Error) as ex:
        raise RuntimeError(f'Indic Parler returned malformed audio: invalid WAV data ({ex})') from ex


def validate_wav_file(path):
    try:
        with wave.open(str(path), 'rb') as wav:
            return wav.getnchannels() >= 1 and wav.getsampwidth() >= 1 and wav.getframerate() >= 1 and wav.getnframes() >= 1
    except (EOFError, OSError, wave.Error):
        return False


def response_error_detail(response):
    try:
        body = response.json()
        if isinstance(body, dict) and body.get('detail'):
            return str(body['detail'])
    except (ValueError, requests.RequestException):
        pass
    text = getattr(response, 'text', '').strip()
    return text[:500] if text else getattr(response, 'reason', 'unknown server error')


def indic_parler_speak(text, dest, language, voice, base_url):
    """Synthesize Hindi speech from the service's raw audio/wav response."""
    narration = text.strip()
    if not narration:
        raise ValueError('Indic Parler narration text cannot be empty')
    if len(narration) > 5000:
        raise ValueError('Indic Parler narration text exceeds the service limit of 5000 characters')
    if language.lower() not in INDIC_LANGUAGES:
        raise ValueError('Indic Parler supports Hindi only (use --language hi)')
    if voice not in INDIC_VOICES:
        raise ValueError(f'Invalid Indic Parler voice {voice!r}; choose one of: {", ".join(INDIC_VOICES)}')

    health_url = f"{base_url.rstrip('/')}/health"
    try:
        r = SESSION.get(health_url, timeout=INDIC_HEALTH_TIMEOUT)
        r.raise_for_status()
        health = r.json()
        if not isinstance(health, dict) or health.get('status') != 'ok':
            raise RuntimeError(f'Indic Parler health check returned an invalid response: {health!r}')
    except requests.Timeout as ex:
        raise RuntimeError(f'Indic Parler health check timed out after {INDIC_HEALTH_TIMEOUT}s: {ex}') from ex
    except requests.RequestException as ex:
        raise RuntimeError(f'Indic Parler health check failed: {ex}') from ex
    except ValueError as ex:
        raise RuntimeError(f'Indic Parler health check returned invalid JSON: {ex}') from ex

    payload = {'text': narration, 'voice': voice, 'language': 'hi-IN'}
    synth_url = f"{base_url.rstrip('/')}/synthesize"
    try:
        r = SESSION.post(synth_url, json=payload, timeout=INDIC_SYNTH_TIMEOUT)
        r.raise_for_status()
    except requests.Timeout as ex:
        raise RuntimeError(f'Indic Parler synthesis request timed out after {INDIC_SYNTH_TIMEOUT}s: {ex}') from ex
    except requests.HTTPError as ex:
        response = ex.response if ex.response is not None else r
        raise RuntimeError(f'Indic Parler synthesis failed (HTTP {response.status_code}): {response_error_detail(response)}') from ex
    except requests.RequestException as ex:
        raise RuntimeError(f'Indic Parler synthesis request failed: {ex}') from ex

    content_type = r.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    if content_type != 'audio/wav':
        raise RuntimeError(f'Indic Parler returned unexpected Content-Type {content_type or "<missing>"!r}; expected audio/wav')
    validate_wav_bytes(r.content)

    dest = Path(dest)
    tmp = dest.with_name(dest.name + '.part')
    try:
        tmp.write_bytes(r.content)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def tts_cache_identity(text, language, provider, mood, indic_url=None, indic_voice=None):
    identity = {
        'version': TTS_CACHE_VERSION,
        'text': text,
        'language': language,
        'provider': provider,
        'mood': mood,
        'output': {'format': 'wav', 'sample_rate': 24000, 'channels': 1},
    }
    edge = edge_tts_settings(language)
    gemini = {
        'model': os.getenv('GEMINI_TTS_MODEL', 'gemini-2.5-flash-preview-tts'),
        'voice': GEMINI_TTS_VOICE,
        'delivery_prompt_version': 1,
    }
    if provider == 'indic-parler':
        identity['settings'] = {'base_url': indic_url.rstrip('/'), 'voice': indic_voice, 'language': 'hi-IN'}
    elif provider in EDGE_TTS_PROVIDERS:
        identity['settings'] = edge_tts_settings(language, provider)
    elif provider == 'gemini':
        identity['settings'] = gemini
    elif provider == 'auto':
        identity['settings'] = {'fallback_order': ['gemini', 'edge'], 'gemini': gemini, 'edge': edge}
    else:
        raise ValueError(f'Unsupported TTS provider: {provider}')
    return identity


def cache_key(identity):
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(serialized).hexdigest()


def tts_cache_hit(wav, metadata, identity, key):
    if not wav.is_file() or not metadata.is_file() or not validate_wav_file(wav):
        return False
    try:
        saved = json.loads(metadata.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return saved.get('cache_key') == key and saved.get('identity') == identity


def trim_wav_edge_silence(source, destination):
    """Trim sustained silence at clip edges while preserving pauses inside speech."""
    source = Path(source)
    destination = Path(destination)
    tmp = destination.with_name(destination.name + '.part')
    try:
        with wave.open(str(source), 'rb') as wav_in:
            channels = wav_in.getnchannels()
            sample_width = wav_in.getsampwidth()
            sample_rate = wav_in.getframerate()
            frame_count = wav_in.getnframes()
            params = wav_in.getparams()
            if wav_in.getcomptype() != 'NONE' or sample_width not in (1, 2, 3, 4):
                raise RuntimeError(f'Unsupported narration WAV encoding in {source}')
            pcm = wav_in.readframes(frame_count)

        window_frames = max(1, round(sample_rate * NARRATION_SILENCE_WINDOW_SECONDS))
        frame_width = channels * sample_width
        max_amplitude = (1 << (sample_width * 8 - 1)) - 1
        threshold = max_amplitude * (10 ** (NARRATION_SILENCE_THRESHOLD_DBFS / 20))
        first_active = None
        last_active = None
        for block_start in range(0, frame_count, window_frames):
            block_end = min(frame_count, block_start + window_frames)
            byte_start = block_start * frame_width
            byte_end = block_end * frame_width
            block = pcm[byte_start:byte_end]
            if sample_width == 1:
                peak = max((abs(value - 128) for value in block), default=0)
            elif sample_width in (2, 4):
                samples = array.array('h' if sample_width == 2 else 'i')
                samples.frombytes(block)
                if sys.byteorder != 'little':
                    samples.byteswap()
                peak = max((abs(value) for value in samples), default=0)
            else:
                peak = 0
                for offset in range(0, len(block), 3):
                    value = int.from_bytes(block[offset:offset + 3], 'little', signed=True)
                    peak = max(peak, abs(value))
            if peak > threshold:
                if first_active is None:
                    first_active = block_start
                last_active = block_end

        padding = round(sample_rate * NARRATION_EDGE_PADDING_SECONDS)
        if first_active is None:
            start_frame, end_frame = 0, frame_count
        else:
            start_frame = max(0, first_active - padding)
            end_frame = min(frame_count, last_active + padding)
        trimmed_pcm = pcm[start_frame * frame_width:end_frame * frame_width]

        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tmp), 'wb') as wav_out:
            wav_out.setnchannels(params.nchannels)
            wav_out.setsampwidth(params.sampwidth)
            wav_out.setframerate(params.framerate)
            wav_out.setcomptype(params.comptype, params.compname)
            wav_out.writeframes(trimmed_pcm)
        if not validate_wav_file(tmp):
            raise RuntimeError(f'Unable to prepare trimmed narration WAV: {destination}')
        os.replace(tmp, destination)
        return (frame_count - (end_frame - start_frame)) / sample_rate
    finally:
        tmp.unlink(missing_ok=True)


def write_json_atomic(path, obj):
    tmp = path.with_name(path.name + '.part')
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def narrate_units(scenes, folder, language, provider, indic_url=None, indic_voice=None):
    folder.mkdir(parents=True,exist_ok=True)
    durations=[]
    progress_started = time.monotonic()
    for i,sc in enumerate(scenes):
        mood = sc.get('mood', 'neutral')
        identity = tts_cache_identity(sc['text'], language, provider, mood, indic_url, indic_voice)
        key = cache_key(identity)
        wav = folder / f'{i:04}_{key[:20]}.wav'
        metadata = wav.with_suffix('.json')
        if not tts_cache_hit(wav, metadata, identity, key):
            log(f'TTS scene {i+1}/{len(scenes)} using {provider}')
            tmp_wav = wav.with_name(wav.stem + '.part.wav')
            tmp_mp3 = wav.with_name(wav.stem + '.part.mp3')
            resolved_provider = provider
            try:
                if provider == 'gemini':
                    gemini_speak(sc['text'], tmp_wav, language, mood)
                elif provider == 'auto':
                    try:
                        gemini_speak(sc['text'], tmp_wav, language, mood)
                        resolved_provider = 'gemini'
                    except Exception as ex:
                        tmp_wav.unlink(missing_ok=True)
                        resolved_provider = 'edge'
                        log(f'Gemini TTS unavailable, falling back to Edge: {ex}')
                        asyncio.run(edge_speak(sc['text'], tmp_mp3, language, mood))
                        run(['ffmpeg','-y','-loglevel','error','-i',tmp_mp3,'-ar','24000','-ac','1',tmp_wav])
                elif provider in EDGE_TTS_PROVIDERS:
                    asyncio.run(edge_speak(sc['text'], tmp_mp3, language, mood, provider))
                    run(['ffmpeg','-y','-loglevel','error','-i',tmp_mp3,'-ar','24000','-ac','1',tmp_wav])
                elif provider == 'indic-parler':
                    # Explicit Indic selection is strict: failures propagate; no other provider is called.
                    indic_parler_speak(sc['text'], tmp_wav, language, indic_voice, indic_url)
                else:
                    raise ValueError(f'Unsupported TTS provider: {provider}')

                if not validate_wav_file(tmp_wav):
                    raise RuntimeError(f'{resolved_provider} TTS produced an invalid or empty WAV file')
                os.replace(tmp_wav, wav)
                write_json_atomic(metadata, {
                    'cache_key': key,
                    'identity': identity,
                    'resolved_provider': resolved_provider,
                })
            finally:
                tmp_wav.unlink(missing_ok=True)
                tmp_mp3.unlink(missing_ok=True)
        trimmed_wav = wav.with_name(f'trim_v{NARRATION_TRIM_VERSION}_{wav.stem}.wav')
        if not validate_wav_file(trimmed_wav):
            removed = trim_wav_edge_silence(wav, trimmed_wav)
            if removed >= 0.05:
                log(f'Trimmed {removed:.2f}s of edge silence from narration scene {i+1}')
        durations.append(audio_duration(trimmed_wav))
        sc['voice']=str(trimmed_wav)
        sc['duration']=durations[-1]
        log_progress('Narration', i+1, len(scenes), progress_started)
    # concat PCM WAV files via ffmpeg concat demuxer
    manifest=folder/'narration_concat.txt'
    manifest.write_text(''.join("file '"+Path(sc['voice']).name+"'\n" for sc in scenes),encoding='utf-8')
    output=folder/'narration.wav'
    run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',manifest,'-ar','24000','-ac','1',output])
    return output


def speech_chunks(scenes, language):
    """Batch short visual scenes without crossing intro/outro boundaries."""
    wps = 1.75 if language == 'hi' else 2.25
    groups, current, words = [], [], 0
    for index, scene in enumerate(scenes):
        count = len(scene['text'].split())
        bookend = scene.get('kind', 'content') != 'content'
        if current and (bookend or words >= 60 * wps or words + count > 90 * wps):
            groups.append(current)
            current, words = [], 0
        if bookend:
            groups.append([index])
        else:
            current.append(index)
            words += count
    if current:
        groups.append(current)
    return groups


def narrate(scenes, folder, language, provider, indic_url=None, indic_voice=None):
    if provider != 'gemini':
        return narrate_units(scenes, folder, language, provider, indic_url, indic_voice)

    from speech_alignment import align_scenes, require_alignment
    require_alignment()
    groups = speech_chunks(scenes, language)
    chunks = []
    for indices in groups:
        moods = {scenes[i].get('mood', 'neutral') for i in indices}
        chunks.append({
            'text': ' '.join(scenes[i]['text'] for i in indices),
            'mood': 'horror' if 'horror' in moods else ('joy' if 'joy' in moods else 'neutral'),
        })
    log(f'Gemini narration: {len(chunks)} speech chunks for {len(scenes)} visual scenes')
    output = narrate_units(chunks, folder, language, provider, indic_url, indic_voice)
    for chunk, indices in zip(chunks, groups):
        members = [scenes[i] for i in indices]
        boundaries = align_scenes(Path(chunk['voice']), members, language)
        for index, scene in enumerate(members):
            scene['voice'] = chunk['voice']
            scene['voice_start'] = boundaries[index]
            scene['voice_end'] = boundaries[index + 1]
            scene['duration'] = boundaries[index + 1] - boundaries[index]
    return output


