#!/usr/bin/env python3
"""Standalone terminal-only photo-video generator; explicit music-rights approval required."""
import argparse
import asyncio
import array
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import wave
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'storyclip-cli/0.1'})

INDIC_VOICES = ('Divya', 'Rohit', 'Aman', 'Rani')
INDIC_LANGUAGES = {'hi', 'hin', 'hindi', 'hi-in'}
INDIC_HEALTH_TIMEOUT = 5
INDIC_SYNTH_TIMEOUT = 300
DEFAULT_BGM_VOLUME = 0.09
BGM_LOOP_CROSSFADE_SECONDS = 1.0
NARRATION_TRIM_VERSION = 1
NARRATION_EDGE_PADDING_SECONDS = 0.09
NARRATION_SILENCE_THRESHOLD_DBFS = -48.0
NARRATION_SILENCE_WINDOW_SECONDS = 0.01
BUILTIN_BGM_FILES = {
    'science': 'science_explanation_theme_1.mp3',
    'horror': 'horror_theme_1.mp3',
    'mystery': 'mystery_theme_1.mp3',
    'nature': 'nature_documentary_theme_1.mp3',
    'technology': 'technology_theme_1.mp3',
}
BUILTIN_BGM_DIR = Path(__file__).resolve().parent / 'storage/BGM'
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
TTS_CACHE_VERSION = 1


def log(s):
    print(f'[storyclip] {s}', flush=True)


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours:d}h {minutes:02d}m {seconds:02d}s'
    if minutes:
        return f'{minutes:d}m {seconds:02d}s'
    return f'{seconds:d}s'


def progress_message(label, completed, total, started, now=None):
    now = time.monotonic() if now is None else now
    total = max(1, total)
    completed = min(max(0, completed), total)
    fraction = completed / total
    width = 20
    filled = round(width * fraction)
    bar = '#' * filled + '-' * (width - filled)
    elapsed = max(0, now - started)
    if completed >= total:
        timing = f'elapsed {format_duration(elapsed)}'
    elif completed:
        eta = elapsed / completed * (total - completed)
        timing = f'ETA {format_duration(eta)}'
    else:
        timing = 'ETA calculating'
    return f'{label} [{bar}] {fraction * 100:5.1f}% ({completed}/{total}) {timing}'


def log_progress(label, completed, total, started):
    log(progress_message(label, completed, total, started))


def run(cmd):
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"Command failed: {' '.join(str(x) for x in cmd[:8])}\n{p.stderr[-3000:]}")
    return p.stdout.strip()


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def check_tools():
    for name in ('ffmpeg', 'ffprobe'):
        if not shutil.which(name):
            raise RuntimeError(f'{name} not found. Install with: sudo apt install ffmpeg')


def audio_duration(path):
    return float(json.loads(run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',path]))['format']['duration'])


def gemini_json(prompt):
    from google import genai
    from google.genai import types
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    model=os.getenv('GEMINI_TEXT_MODEL','gemini-2.5-flash')
    last=None
    for attempt in range(3):
        try:
            response=client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(response_mime_type='application/json',temperature=0.6))
            return json.loads(response.text)
        except Exception as e:
            last=e
            if attempt<2: time.sleep(2**attempt)
    raise RuntimeError(f'Gemini text generation failed: {last}')


def narrative_from_idea(idea, language, minutes):
    log('Generating narration script with Gemini')
    words=int(minutes*(145 if language=='hi' else 155))
    prompt=(f'Create an original, engaging spoken narration for a photo-based documentary/story video. '
            f'Language: {"Hindi in Devanagari" if language=="hi" else "English"}. Idea: {idea}. '
            f'Approximate spoken length: {minutes} minutes (~{words} words, prioritize natural pacing). '
            'Write a complete plot with compelling sensory detail; satisfy the requested length without repetition. No time codes, no camera directions, no headings, no background-music directions in the narration. '
            'Do not invent factual claims presented as established truths; when fictional, make it clearly fiction. '
            'Return JSON object with keys "title" (short text), "output_slug" (2-8 lowercase ASCII words '
            'separated only by hyphens, suitable for a folder name), "narration" (continuous spoken text), '
            '"theme" (2-4 English words describing photo background), "music_moods" (array of 2 English keywords).')
    result=gemini_json(prompt)
    if not isinstance(result.get('narration'),str) or len(result['narration'])<30: raise ValueError('Gemini returned empty narration')
    return result


def source_identity(kind, text, language, minutes=None, theme=None, music_mood=None):
    normalized = re.sub(r'\s+', ' ', text.strip())
    settings = {
        'kind': kind,
        'language': language,
        'minutes': float(minutes) if kind == 'idea' else None,
        'theme': theme or None,
        'music_mood': music_mood or None,
    }
    serialized = json.dumps(
        {'text': normalized, **settings}, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')
    return {**settings, 'sha256': hashlib.sha256(serialized).hexdigest()}


def safe_output_slug(suggested, fallback_seed):
    normalized = unicodedata.normalize('NFKD', str(suggested or ''))
    ascii_name = normalized.encode('ascii', 'ignore').decode('ascii').lower()
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_name).strip('-')[:64].rstrip('-')
    if not slug:
        digest = hashlib.sha256(fallback_seed.encode('utf-8')).hexdigest()[:10]
        slug = f'story-{digest}'
    return slug


def find_matching_output(base, identity):
    if not base.is_dir():
        return None
    for manifest in sorted(base.glob('*/project.json')):
        try:
            project = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if project.get('source') == identity:
            return manifest.parent
    return None


def available_output_path(base, slug):
    candidate = base / slug
    if not candidate.exists():
        return candidate
    for number in range(2, 10_000):
        candidate = base / f'{slug}-{number}'
        if not candidate.exists():
            return candidate
    raise RuntimeError(f'Could not allocate an output directory for slug {slug!r}')


def classify_mood(text):
    value = text.lower()
    if any(word in value for word in ('horror', 'haunt', 'ghost', 'creep', 'fear', 'scary', 'भूत', 'डर', 'भयानक', 'रहस्यमयी', 'चुड़ैल')):
        return 'horror'
    if any(word in value for word in ('joy', 'happy', 'party', 'festival', 'celebrat', 'खुशी', 'उत्सव', 'जश्न')):
        return 'joy'
    return 'neutral'


def expand_narration(project, seconds_missing):
    """Append a story continuation; never time-stretch narration to meet requested minutes."""
    language = project['language']
    extra_words = max(70, int(seconds_missing * (2.3 if language == 'hi' else 2.55)))
    prompt = (f'Continue this original narrative naturally in {"Hindi (Devanagari)" if language == "hi" else "English"}. '
              f'Write approximately {extra_words} ADDITIONAL spoken words, adding meaningful action or resolution, not repetition. '
              'Do not repeat the ending; this continuation will be appended after the existing text. '
              'Return JSON with one key: "continuation". Story so far:\n' + project['narration'][-7500:])
    result = gemini_json(prompt)
    addition = str(result.get('continuation', '')).strip()
    if len(addition) < 70:
        raise RuntimeError('Gemini returned an empty/short continuation')
    return addition


def sentences(text):
    # Hindi danda and sentence-final English punctuation; do not split on abbreviations perfectly.
    chunks=re.split(r'(?<=[.!?।！？])\s+|(?<=।)(?=[^\s])',re.sub(r'\s+',' ',text.strip()))
    return [s.strip() for s in chunks if s.strip()]


def plan_scenes(narration, language, theme):
    log('Planning scene imagery using Gemini')
    units=sentences(narration)
    # group short sentences into approximate 10s segments; long sentences remain intact.
    wps=1.75 if language=='hi' else 2.25
    groups=[]; buf=[]; secs=0
    for s in units:
        est=max(1.5,len(s.split())/wps)
        if buf and (secs+est>12.5 or secs>=7.5):
            groups.append(' '.join(buf));buf=[];secs=0
        buf.append(s);secs+=est
    if buf:groups.append(' '.join(buf))
    query_text='\n'.join(f'{i}: {g[:420]}' for i,g in enumerate(groups))
    prompt=(f'For every numbered narration segment, suggest one visually relevant Pexels PHOTO search query. '
            f'Narration language {language}; theme: {theme}. Queries MUST be ENGLISH, photographic, concrete, '
            'not abstract moods. 2-6 keywords. Keep visual descriptions faithful to what narrator says; '
            'do not hallucinate or claim that a visual is an exact depiction of real people/events. '
            'Return JSON with key "queries", array of objects {"id":integer,"query":string} covering all ids. '
            'Segments:\n'+query_text)
    # Chunk so long videos do not overload the model.
    mapping={}
    for offset in range(0,len(groups),35):
        subset=groups[offset:offset+35]
        local='\n'.join(f'{offset+i}: {g[:420]}' for i,g in enumerate(subset))
        data=gemini_json(prompt.split('Segments:\n')[0]+'Segments:\n'+local)
        for x in data.get('queries',[]):
            if isinstance(x,dict) and str(x.get('id','')).isdigit(): mapping[int(x['id'])]=str(x.get('query',''))
    return [{'text':s,'query':mapping.get(i,theme),'image':None,'mood':classify_mood(s+' '+theme)} for i,s in enumerate(groups)]


def pexels_search(query, count=8):
    r=SESSION.get('https://api.pexels.com/v1/search',headers={'Authorization':os.environ['PEXELS_API_KEY']},params={'query':query,'per_page':count,'orientation':'landscape'},timeout=25)
    r.raise_for_status()
    return r.json().get('photos',[])


def fetch_file(url, path, max_bytes=25_000_000):
    if not url.startswith('https://'): raise ValueError('HTTPS download URL required')
    # Do not follow arbitrary user-supplied URLs; only provider-returned image/audio URLs.
    with SESSION.get(url,stream=True,timeout=45) as r:
        r.raise_for_status(); size=0
        with path.open('wb') as f:
            for chunk in r.iter_content(128*1024):
                size+=len(chunk)
                if size>max_bytes: raise RuntimeError('Download exceeded size limit')
                f.write(chunk)
    if size<512: raise RuntimeError('Downloaded file unexpectedly small')


def gather_images(scenes, theme, folder):
    folder.mkdir(exist_ok=True,parents=True)
    fallbacks=[]
    log('Downloading themed fallback photos')
    for p in pexels_search(theme,12):
        url=p.get('src',{}).get('large2x') or p.get('src',{}).get('large')
        if url and len(fallbacks)<8:
            try:
                dest=folder/f'fallback_{p["id"]}.jpg'
                if not dest.exists():fetch_file(url,dest)
                fallbacks.append({'path':str(dest),'credit':p.get('photographer'),'pexels_url':p.get('url')})
            except Exception as ex: log(f'Fallback photo failed: {ex}')
    if not fallbacks: raise RuntimeError('Pexels provided no downloadable theme fallback photos')
    used=set()
    progress_started = time.monotonic()
    for i,sc in enumerate(scenes):
        dest=folder/f'scene_{i:04}.jpg'
        if dest.exists():
            sc['image']=str(dest)
            log_progress('Images', i+1, len(scenes), progress_started)
            continue
        photo=None
        try:
            matches=pexels_search(sc['query'],8)
            for p in matches:
                if p['id'] not in used:
                    url=p.get('src',{}).get('large2x') or p.get('src',{}).get('large')
                    if url:
                        try: fetch_file(url,dest); photo=p; used.add(p['id']);break
                        except Exception:pass
        except Exception as ex: log(f'Pexels search failed for scene {i}: {ex}')
        if photo:
            sc['image']=str(dest);sc['credit']=photo.get('photographer');sc['pexels_url']=photo.get('url')
        else:
            fb=fallbacks[i%len(fallbacks)]
            shutil.copyfile(fb['path'],dest)
            sc['image']=str(dest);sc['credit']=fb['credit'];sc['pexels_url']=fb['pexels_url'];sc['fallback']=True
        log_progress('Images', i+1, len(scenes), progress_started)
    return scenes


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


def gemini_speak(text, dest, language, mood='neutral'):
    # Gemini generateContent single-speaker API. SDK response may be WAV or raw PCM.
    from google import genai
    from google.genai import types
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    delivery = {'horror': 'Narrate with restrained dread, uneasy pauses and rising tension. Sound like a human telling a frightening story, not an announcer.', 'joy': 'Narrate warmly, smiling, animated and full of genuine delight.', 'neutral': 'Narrate naturally with authentic storytelling emotion and varied intonation.'}.get(mood,'Narrate naturally.')
    response=client.models.generate_content(
        model=os.getenv('GEMINI_TTS_MODEL','gemini-2.5-flash-preview-tts'),
        contents=f'{delivery} Speak the following words verbatim in {"Hindi" if language=="hi" else "English"}; do not read the directions aloud. Script: {text}',
        config=types.GenerateContentConfig(response_modalities=['AUDIO'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Kore')))))
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
        'voice': 'Kore',
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


def narrate(scenes, folder, language, provider, indic_url=None, indic_voice=None):
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


def make_srt(scenes, output):
    def stamp(s):
        ms=int(round(s*1000));hr,ms=divmod(ms,3600000);mn,ms=divmod(ms,60000);sec,ms=divmod(ms,1000)
        return f'{hr:02}:{mn:02}:{sec:02},{ms:03}'
    curr=0;records=[]
    for i,sc in enumerate(scenes,1):
        end=curr+sc['duration']
        records.append(f'{i}\n{stamp(curr)} --> {stamp(end)}\n{sc["text"]}\n')
        sc['start']=curr;sc['end']=end
        curr=end
    output.write_text('\n'.join(records)+'\n',encoding='utf-8')
    return curr


def jamendo_candidates(mood,limit=12):
    r=SESSION.get('https://api.jamendo.com/v3.0/tracks/',params={
        'client_id':os.environ['JAMENDO_CLIENT_ID'],'format':'json','limit':limit,
        'search':mood,'vocalinstrumental':'instrumental','include':'licenses',
    },timeout=25)
    r.raise_for_status();data=r.json()
    if data.get('headers',{}).get('status')!='success':
        raise RuntimeError(data.get('headers',{}).get('error_message','Jamendo search failed'))
    return data.get('results',[])


def select_builtin_bgm(project):
    """Select one licensed local music bed based on the whole-project metadata."""
    context = ' '.join([
        str(project.get('title', '')),
        str(project.get('theme', '')),
        ' '.join(str(mood) for mood in project.get('music_moods', [])),
    ]).lower()
    theme_keywords = (
        ('horror', ('horror', 'haunted', 'ghost', 'witch', 'scary', 'fear', 'spooky', 'भूत', 'डर', 'चुड़ैल', 'हवेली', 'भयानक')),
        ('science', ('science', 'scientific', 'space', 'black hole', 'astronomy', 'universe', 'cosmos', 'physics', 'biology', 'विज्ञान', 'वैज्ञानिक', 'अंतरिक्ष', 'ब्लैक होल', 'ब्रह्मांड', 'भौतिक')),
        ('technology', ('technology', 'tech', 'digital', 'robot', 'computer', 'engineering', 'तकनीक', 'टेक्नोलॉजी', 'रोबोट', 'कंप्यूटर')),
        ('nature', ('nature', 'wildlife', 'animal', 'forest', 'ocean', 'sea', 'landscape', 'प्रकृति', 'वन्यजीव', 'जानवर', 'जंगल', 'समुद्र', 'पृथ्वी')),
        ('mystery', ('mystery', 'mysterious', 'suspense', 'detective', 'investigation', 'रहस्य', 'रहस्यमय', 'सस्पेंस', 'जासूस')),
    )
    genre = next((name for name, keywords in theme_keywords if any(word in context for word in keywords)), 'science')
    path = BUILTIN_BGM_DIR / BUILTIN_BGM_FILES[genre]
    if not path.is_file():
        log(f'Built-in {genre} BGM is missing at {path}; rendering narration-only')
        return None
    log(f'Using built-in {genre} BGM: {path.name}')
    return [{'path': str(path)}]


def select_bgm(moods, folder, duration, approved):
    """Choose one or more tracks but require explicit proof of additional rights.
    A Creative Commons NC/ND track must NEVER be silently used in monetized video.
    """
    folder.mkdir(exist_ok=True,parents=True)
    tracks=[]; seen=set()
    for mood in moods[:3]:
        for t in jamendo_candidates(mood):
            tid=t.get('id')
            if tid not in seen and t.get('audiodownload_allowed') and t.get('audiodownload'):
                tracks.append(t);seen.add(tid)
    if not tracks:
        log('No downloadable Jamendo tracks returned; video will have narration only.')
        return None,[]
    log('Jamendo candidates (licenses are NOT automatically cleared for synchronization/commercial use):')
    for i,t in enumerate(tracks[:10]):
        log(f'{i+1}. {t.get("name")} by {t.get("artist_name")} | {t.get("license_ccurl")} | {t.get("duration")} s')
    if not approved:
        log('Skipping Jamendo BGM. To import a track, provide --music-rights-confirmed only after independently securing rights.')
        return None, tracks
    if not sys.stdin.isatty():
        raise RuntimeError('Music-rights confirmation requires an interactive terminal.')
    print('You confirm you possess rights to synchronize and, if needed, commercially publish selected tracks.')
    if input('Type I HAVE THE RIGHTS to proceed: ').strip()!='I HAVE THE RIGHTS':
        log('Music not approved; using narration only.');return None,tracks
    chosen=[];total=0
    for t in tracks:
        if total>=duration:break
        url=t.get('audiodownload')
        dest=folder/f'jamendo_{t["id"]}.mp3'
        try:
            if not dest.exists():fetch_file(url,dest,40_000_000)
            length=audio_duration(dest)
            if length<8:continue
            chosen.append({'path':str(dest),'id':t['id'],'name':t.get('name'),'artist':t.get('artist_name'), 'license':t.get('license_ccurl')})
            total+=length
        except Exception as e: log(f'Music track download failed: {e}')
    save_json(folder/'credits.json',chosen)
    return chosen,tracks


def render(scenes, narration, music, output_dir, fps=24, width=1280, height=720, style='auto', zoom=True, bgm_volume=DEFAULT_BGM_VOLUME):
    """Render a lightweight Ken Burns photo movie with per-scene grading and fades."""
    clips = output_dir / 'clips'
    clips.mkdir(exist_ok=True)
    dur = [float(x['duration']) for x in scenes]
    fade = min(0.45, min(dur)/4) if len(dur) > 1 else 0
    progress_started = time.monotonic()
    for i, sc in enumerate(scenes):
        scene_style = style
        if style == 'auto':
            scene_style = sc.get('mood')
            if scene_style not in ('horror', 'joy', 'neutral'):
                scene_style = classify_mood(sc.get('text', ''))
        dest = clips / f'{i:04}.mp4'
        seconds = dur[i] + (fade if i < len(scenes)-1 else 0)
        frames = max(1, round(seconds*fps))
        # Oversampling keeps zoompan's integer crop coordinates from wobbling at output
        # resolution. Ease across the full scene so motion never hits a zoom limit early.
        if zoom:
            progress = f'(on/{frames-1})' if frames > 1 else '0'
            eased = f'({progress}*{progress}*(3-2*{progress}))'
            factor = f'1.0+0.12*{eased}' if i % 2 == 0 else f'1.12-0.12*{eased}'
            motion_width = width * 4
            motion_height = height * 4
            vf = (f'scale={motion_width}:{motion_height}:force_original_aspect_ratio=increase,'
                  f'crop={motion_width}:{motion_height},'
                  f'zoompan=z=\'{factor}\':x=\'iw/2-iw/zoom/2\':y=\'ih/2-ih/zoom/2\':'
                  f'd={frames}:s={width}x{height}:fps={fps}')
        else:
            vf = f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}'
        if scene_style == 'horror':
            vf += ',eq=brightness=-0.035:saturation=0.80,vignette=PI/4'
        elif scene_style == 'joy':
            vf += ',eq=brightness=0.018:saturation=1.23:contrast=1.04'
        vf += ',setsar=1,format=yuv420p'
        # Fingerprint inputs: rerenders on effects changes but preserves other usable assets.
        signature = hashlib.sha256(json.dumps([str(sc['image']),os.path.getmtime(sc['image']),seconds,fps,width,height,vf],sort_keys=True).encode()).hexdigest()
        stamp = dest.with_suffix('.sha256')
        if dest.exists() and stamp.exists() and stamp.read_text() == signature:
            log_progress('Rendering', i+1, len(scenes), progress_started)
            continue
        log(f'Rendering motion image {i+1}/{len(scenes)}')
        run(['ffmpeg','-y','-loglevel','error','-loop','1','-framerate',str(fps),'-i',sc['image'],
             '-vf',vf,'-frames:v',str(frames),'-c:v','libx264','-preset','veryfast','-crf','23',
             '-pix_fmt','yuv420p',dest])
        stamp.write_text(signature)
        log_progress('Rendering', i+1, len(scenes), progress_started)
    visual = output_dir / 'visual_only.mp4'
    files = [clips / f'{i:04}.mp4' for i in range(len(scenes))]
    if len(files) == 1:
        shutil.copyfile(files[0],visual)
    else:
        log('Combining rendered scenes with crossfades')
        inputs = []
        for f in files: inputs.extend(['-i',f])
        parts = []
        label = '0:v'
        elapsed = dur[0]
        for i in range(1,len(files)):
            nxt = f'v{i}'
            parts.append(f'[{label}][{i}:v]xfade=transition=fade:duration={fade:.3f}:offset={elapsed:.3f}[{nxt}]')
            label = nxt
            elapsed += dur[i]
        run(['ffmpeg','-y','-loglevel','error',*inputs,'-filter_complex',';'.join(parts),
             '-map',f'[{label}]','-c:v','libx264','-preset','veryfast','-crf','23',
             '-pix_fmt','yuv420p',visual])
    out = output_dir / 'final.mp4'
    total = sum(dur)
    log('Muxing narration and optional music into final video')
    if music:
        first = music[0]['path']
        track_duration = audio_duration(first)
        loop_fade = min(BGM_LOOP_CROSSFADE_SECONDS, track_duration / 4)
        copies = max(1, math.ceil((total - loop_fade) / (track_duration - loop_fade)))
        inputs = ['-i', visual, '-i', narration]
        inputs.extend(['-i', first] * copies)
        filters = []
        current = '2:a'
        for copy_index in range(1, copies):
            next_label = f'bgm_loop_{copy_index}'
            filters.append(
                f'[{current}][{copy_index + 2}:a]acrossfade=d={loop_fade:.3f}:c1=tri:c2=tri[{next_label}]'
            )
            current = next_label
        fade_in = min(1.5, total / 4)
        fade_out = min(2.0, total / 4)
        fade_out_start = max(0, total - fade_out)
        filters.append(
            f'[{current}]atrim=duration={total:.3f},asetpts=PTS-STARTPTS,volume={bgm_volume},'
            f'afade=t=in:st=0:d={fade_in:.3f},'
            f'afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}[bg]'
        )
        filters.append('[1:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]')
        args = ['ffmpeg','-y','-loglevel','error',*inputs,'-filter_complex',';'.join(filters),
                '-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k',
                '-t',f'{total:.3f}','-movflags','+faststart',out]
    else:
        args = ['ffmpeg','-y','-loglevel','error','-i',visual,'-i',narration,
                '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k',
                '-t',f'{total:.3f}','-movflags','+faststart',out]
    run(args)
    return out


def build_parser():
    p=argparse.ArgumentParser(description='Idea/script -> Pexels photo video, TTS, background music, SRT')
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--idea',help='One-line idea')
    source.add_argument('--script',type=Path,help='Existing UTF-8 script file')
    p.add_argument('--language',choices=['hi','en'],default='hi')
    p.add_argument('--minutes',type=float,default=2,help='Approximate length for idea-to-script')
    p.add_argument('--tts',choices=[*EDGE_TTS_PROVIDERS,'gemini','auto','indic-parler'],default='edge',
                   help='Narrator: Edge female/male/energetic profile, Gemini, Gemini-to-Edge fallback, or Indic Parler')
    p.add_argument('--indic-url',help='Base URL for Indic Parler TTS service',default=os.getenv('INDIC_PARLER_URL','http://127.0.0.1:8765'))
    p.add_argument('--indic-voice',choices=INDIC_VOICES,help='Voice for Indic Parler TTS',default=os.getenv('INDIC_PARLER_VOICE','Divya'))
    p.add_argument('--output',type=Path,help='Output directory (default: outputs/<story-name>)')
    p.add_argument('--theme',help='English Pexels theme search override')
    p.add_argument('--music-mood',help='Comma-separated Jamendo search moods')
    p.add_argument('--music-rights-confirmed',action='store_true',help='Use Jamendo candidates only after separately clearing sync/commercial rights and confirming interactively')
    p.add_argument('--skip-music',action='store_true')
    p.add_argument('--dry-run',action='store_true',help='Stop after script, scenes and search queries are planned')
    p.add_argument('--fps',type=int,default=24)
    p.add_argument('--style',choices=['auto','horror','joy','neutral'],default='auto',help='Visual grading: auto follows each scene mood; another value overrides every scene')
    p.add_argument('--no-zoom',action='store_true')
    p.add_argument('--bgm-file',type=Path,help='Local music file for which you have suitable video-sync rights')
    p.add_argument('--bgm-volume',type=float,default=DEFAULT_BGM_VOLUME,help='BGM linear volume from 0 to 1 (default: 0.08)')
    p.add_argument('--duration-tolerance',type=float,default=0.07,help='Fractional tolerance for idea-generated videos')
    return p


def validate_cli_args(p, args):
    if args.tts == 'indic-parler':
        if args.indic_voice not in INDIC_VOICES:
            p.error(f'--indic-voice must be one of: {", ".join(INDIC_VOICES)}')
        parsed = urlparse(args.indic_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.params or parsed.query or parsed.fragment:
            p.error('--indic-url must be an http(s) base URL without query parameters or fragments')
        if args.language != 'hi':
            p.error('--tts indic-parler supports only --language hi')


def main():
    p = build_parser()
    args=p.parse_args()
    validate_cli_args(p, args)

    if args.minutes<=0 or args.minutes>60: p.error('--minutes must be between 0 and 60')
    check_tools()
    if not 0 <= args.bgm_volume <= 1: p.error('--bgm-volume must be between 0 and 1')
    for key in ('GEMINI_API_KEY',):
        if not os.getenv(key):p.error(f'Missing {key} in .env')
    if not args.dry_run and not os.getenv('PEXELS_API_KEY'):p.error('Missing PEXELS_API_KEY in .env')

    current_script = args.script.read_text(encoding='utf-8').strip() if args.script else None
    identity = source_identity(
        'idea' if args.idea else 'script',
        args.idea if args.idea else current_script,
        args.language,
        args.minutes,
        args.theme,
        args.music_mood,
    )
    prepared_project = None
    if args.output is None:
        output_base = Path('outputs')
        matching_output = find_matching_output(output_base, identity)
        if matching_output:
            args.output = matching_output
            log(f'Automatically resuming matching project: {args.output}')
        else:
            if args.idea:
                prepared_project = narrative_from_idea(args.idea,args.language,args.minutes)
                suggested_slug = prepared_project.get('output_slug') or prepared_project.get('title')
                fallback_seed = args.idea
            else:
                prepared_project = {
                    'title': args.script.stem,
                    'narration': current_script,
                    'theme': args.theme or 'cinematic nature landscape',
                    'music_moods': ['cinematic'],
                }
                suggested_slug = args.script.stem
                fallback_seed = current_script
            slug = safe_output_slug(suggested_slug, fallback_seed)
            args.output = available_output_path(output_base, slug)
            log(f'Automatic output directory: {args.output}')

    args.output.mkdir(parents=True,exist_ok=True)
    meta=args.output/'project.json'
    if meta.exists():
        project=json.loads(meta.read_text(encoding='utf-8'))
        if project.get('language') != args.language:
            raise RuntimeError(
                f'Saved project language is {project.get("language")!r}, but --language is {args.language!r}; '
                'use a new --output directory for a different language'
            )
        if args.script:
            if current_script != project.get('narration', '').strip():
                raise RuntimeError(
                    'The script differs from the narration saved in project.json; use a new --output '
                    'directory so the scene plan and media cannot be reused for stale text'
                )
        log('Resuming saved project manifest')
    else:
        if prepared_project is not None:
            project = prepared_project
        elif args.idea:
            project=narrative_from_idea(args.idea,args.language,args.minutes)
        else:
            project={'title':args.script.stem,'narration':current_script,'theme':args.theme or 'cinematic nature landscape','music_moods':['cinematic']}
        project['source'] = identity
        project['output_slug'] = args.output.name
        project['language']=args.language
        project['theme']=args.theme or project.get('theme') or 'cinematic landscape'
        project['music_moods']=[x.strip() for x in args.music_mood.split(',')] if args.music_mood else project.get('music_moods',['cinematic'])
        project['scenes']=plan_scenes(project['narration'],args.language,project['theme'])
        save_json(meta,project)
        (args.output/'script.txt').write_text(project['narration'],encoding='utf-8')
    if args.dry_run:
        log(f'Dry run: {len(project["scenes"])} planned scenes in {meta}');return
    scenes=project['scenes']
    gather_images(scenes,project['theme'],args.output/'images');save_json(meta,project)
    voice=narrate(scenes,args.output/'voice',project['language'],args.tts,args.indic_url,args.indic_voice)
    length=make_srt(scenes,args.output/'subtitles.srt');save_json(meta,project)
    if args.idea and project.get('narration') and length < 60*args.minutes*(1-args.duration_tolerance):
        # At most two continuations, avoiding endless retries and preserving old audio.
        for attempt in range(2):
            deficit=60*args.minutes-length
            log(f'Script shorter than target by {deficit:.1f}s; requesting meaningful continuation {attempt+1}/2')
            old=len(scenes)
            extra=expand_narration(project,deficit)
            added=plan_scenes(extra,project['language'],project['theme'])
            scenes.extend(added)
            project['narration'] += '\n' + extra
            # Scene files are indexed; existing assets/narration remain reusable.
            save_json(meta,project)
            (args.output/'script.txt').write_text(project['narration'],encoding='utf-8')
            gather_images(scenes,project['theme'],args.output/'images')
            voice=narrate(scenes,args.output/'voice',project['language'],args.tts,args.indic_url,args.indic_voice)
            length=make_srt(scenes,args.output/'subtitles.srt')
            save_json(meta,project)
            if length >= 60*args.minutes*(1-args.duration_tolerance): break
        if abs(length-60*args.minutes)>60*args.minutes*args.duration_tolerance:
            log(f'WARNING: Actual {length:.1f}s is outside requested duration tolerance; no artificial slowdown applied.')
    log(f'Narration duration: {length:.1f}s; scenes: {len(scenes)}')
    music=None
    if args.skip_music:
        log('Background music disabled by --skip-music')
    elif args.bgm_file:
        if not args.bgm_file.is_file(): p.error('--bgm-file does not exist')
        music=[{'path':str(args.bgm_file.resolve())}]
    else:
        if args.music_rights_confirmed and os.getenv('JAMENDO_CLIENT_ID'):
            try:music,candidates=select_bgm(project['music_moods'],args.output/'music',length,args.music_rights_confirmed)
            except Exception as ex:log(f'Jamendo unavailable: {ex}; using built-in BGM')
        elif args.music_rights_confirmed:
            log('No JAMENDO_CLIENT_ID; using built-in BGM')
        if not music:
            music=select_builtin_bgm(project)
    movie=render(scenes,voice,music,args.output,args.fps,style=args.style,zoom=not args.no_zoom,bgm_volume=args.bgm_volume)
    log(f'VIDEO: {movie}\nSRT: {args.output / "subtitles.srt"}\nMANIFEST: {meta}')


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,Exception) as ex:
        log(f'ERROR: {ex}');sys.exit(1)
