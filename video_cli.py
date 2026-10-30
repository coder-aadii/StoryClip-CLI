#!/usr/bin/env python3
"""Standalone terminal-only photo-video generator; explicit music-rights approval required."""
import argparse
import asyncio
import array
import errno
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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from PIL import Image, ImageFilter, ImageStat

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
GEMINI_TTS_VOICE = 'Puck'
TTS_CACHE_VERSION = 1
RENDER_FINGERPRINT_VERSION = 2
SEGMENT_RENDER_VERSION = 1
PROJECT_FORMAT_VERSION = 2
AVATAR_PLAN_VERSION = 4
AVATAR_DIR = Path(__file__).resolve().parent / 'storage/Expression_Stickman'
AVATAR_MANIFEST_PATH = AVATAR_DIR / 'manifest.json'
OUTRO_CTA_IMAGE = Path(__file__).resolve().parent / 'storage/Outro_image/Like_share_comment_subscribe_combined.png'
AVATAR_DISPLAY_HEIGHT_RATIO = 0.36
AVATAR_BOOKEND_HEIGHT_RATIO = 0.52
AVATAR_SIDE_MARGIN = 32
AVATAR_BOTTOM_MARGIN = 18
AVATAR_TARGET_BLOCK_SECONDS = 10.0
AVATAR_MIN_POSITION_HOLD_SECONDS = 10.0
AVATAR_MOVE_SECONDS = 0.8
OUTRO_CTA_WIDTH_RATIO = 0.78
OUTRO_CTA_BOTTOM_MARGIN = 16
OUTRO_CTA_FADE_SECONDS = 0.45
OUTRO_PROFILES = ('default', 'facts-sun-le-bhai')
VIDEO_RESOLUTIONS = {
    '720p': (1280, 720),
    '1080p': (1920, 1080),
    '2160p': (3840, 2160),
}
# Keep UHD batches smaller while avoiding repeated full-length encode passes on 1-hour projects.
XFADE_MERGE_INPUTS = {'720p': 64, '1080p': 40, '2160p': 24}
ZOOM_OVERSCAN = 1.12
AVATAR_CUES = {
    'explanation', 'visual_focus', 'question', 'important_fact', 'warning',
    'reveal', 'surprise', 'celebration',
}
_AVATAR_MANIFEST_CACHE = None


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


def run(cmd, progress_label=None, progress_duration=None):
    if progress_label:
        return run_with_progress(cmd, progress_label, progress_duration)
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if p.returncode:
        status = f'exit code {p.returncode}'
        if p.returncode < 0:
            status = f'terminated by signal {-p.returncode}'
        details = p.stderr[-3000:].strip() or 'No diagnostic text was returned by the process.'
        raise RuntimeError(
            f"Command failed ({status}): {' '.join(str(x) for x in cmd[:8])}\n{details}"
        )
    return p.stdout.strip()


def run_with_progress(cmd, label, duration):
    """Stream FFmpeg's machine-readable progress while retaining error details."""
    command = [str(x) for x in cmd]
    total_seconds = max(1, round(float(duration or 1)))
    started = time.monotonic()
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output_tail = ''
    progress_fields = {}
    last_reported = -1
    report_step = max(1, total_seconds // 20)
    for line in process.stdout:
        output_tail = (output_tail + line)[-3000:]
        if '=' not in line:
            continue
        key, value = line.rstrip().split('=', 1)
        progress_fields[key] = value
        if key != 'progress':
            continue
        timestamp = progress_fields.get('out_time_us') or progress_fields.get('out_time_ms')
        if timestamp and timestamp.isdigit():
            completed = min(total_seconds - 1, int(timestamp) // 1_000_000)
            if completed > last_reported and (
                last_reported < 0 or completed - last_reported >= report_step
            ):
                log_progress(label, completed, total_seconds, started)
                last_reported = completed
        progress_fields.clear()

    return_code = process.wait()
    if return_code:
        status = f'exit code {return_code}'
        if return_code < 0:
            status = f'terminated by signal {-return_code}'
        details = output_tail.strip() or 'No diagnostic text was returned by the process.'
        raise RuntimeError(
            f"Command failed ({status}): {' '.join(command[:8])}\n{details}"
        )
    log_progress(label, total_seconds, total_seconds, started)
    return ''


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


def reserved_intro_outro_seconds(outro_profile):
    return 20 if outro_profile == 'facts-sun-le-bhai' else 15


def narrative_from_idea(idea, language, minutes, outro_profile='default'):
    log('Generating narration script with Gemini')
    content_minutes=max(0.25, minutes-reserved_intro_outro_seconds(outro_profile)/60)
    words=int(content_minutes*(145 if language=='hi' else 155))
    prompt=(f'Create an engaging spoken explainer script for an educational photo-based video. '
            f'Language: {"Hindi in Devanagari" if language=="hi" else "English"}. Idea: {idea}. '
            f'Approximate MAIN CONTENT length: {content_minutes:.2f} minutes (~{words} words, prioritize natural pacing). '
            'A required intro and outro will be added separately, so do not write a greeting, channel call-to-action, or sign-off. '
            'Explain the topic clearly for a general audience with a compelling opening, coherent progression, concrete examples, and a concise takeaway. '
            'For science, history, or factual topics, distinguish established facts from uncertainty and do not invent claims. '
            'Satisfy the requested length without repetition. No time codes, camera directions, headings, or music directions. '
            'Return JSON object with keys "title" (short text), "output_slug" (2-8 lowercase ASCII words '
            'separated only by hyphens, suitable for a folder name), "narration" (continuous spoken text), '
            '"theme" (2-4 English words describing photo background), "music_moods" (array of 2 English keywords).')
    result=gemini_json(prompt)
    if not isinstance(result.get('narration'),str) or len(result['narration'])<30: raise ValueError('Gemini returned empty narration')
    return result


def source_identity(kind, text, language, minutes=None, theme=None, music_mood=None, outro_profile='default'):
    normalized = re.sub(r'\s+', ' ', text.strip())
    settings = {
        'kind': kind,
        'language': language,
        'minutes': float(minutes) if kind == 'idea' else None,
        'theme': theme or None,
        'music_mood': music_mood or None,
        'outro': outro_profile,
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


def intro_narration(title, language):
    display_title = re.sub(r'[_-]+', ' ', str(title or '')).strip() or ('आज का विषय' if language == 'hi' else "today's topic")
    if language == 'hi':
        return f'नमस्कार दोस्तों! आज के वीडियो में हम जानेंगे: {display_title}। चलिए शुरू करते हैं।'
    return f"Hello everyone! In today's video, we're exploring {display_title}. Let's begin."


def outro_narration(profile, language):
    if profile not in OUTRO_PROFILES:
        raise ValueError(f'Unsupported outro profile: {profile}')
    if profile == 'facts-sun-le-bhai':
        if language == 'hi':
            return ('ऐसे ही इंटरेस्टिंग वीडियो देखने के लिए हमारे चैनल फैक्ट सुन ले भाई को सब्सक्राइब करें। '
                    'और आप अगला वीडियो किस टॉपिक पर देखना चाहते हैं, कमेंट में जरूर बताएं! '
                    'हाँ भाई, इस वीडियो को लाइक करना मत भूलना। मैं मिलता हूँ अगले वीडियो में। '
                    'तब तक के लिए बाय-बाय, टाटा, सायोनारा!')
        return ('For more interesting videos like this, subscribe to our channel, Fact Sun Le Bhai. '
                "Tell us in the comments which topic you'd like to see in the next video! "
                "And yes, don't forget to like this video. I'll see you in the next one. "
                'Until then, bye-bye, tata, sayonara!')
    if language == 'hi':
        return ('वीडियो देखने के लिए धन्यवाद। अगर आपको यह वीडियो पसंद आया, तो इसे लाइक करें और ऐसे ही '
                'वीडियोस के लिए चैनल को सब्सक्राइब करें। फिर मिलेंगे अगले वीडियो में। तब तक के लिए बाय-बाय!')
    return ('Thanks for watching. If you enjoyed this video, please like it and subscribe for more. '
            'See you in the next video. Bye!')


def compose_narration(content, title, language, outro_profile):
    intro = intro_narration(title, language)
    outro = outro_narration(outro_profile, language)
    return intro, outro, '\n\n'.join((intro, content.strip(), outro))


def build_video_scenes(content, title, language, theme, outro_profile, tts_provider='edge'):
    intro, outro, full_narration = compose_narration(content, title, language, outro_profile)
    content_scenes = plan_scenes(content, language, theme, tts_provider)
    for scene in content_scenes:
        scene['kind'] = 'content'
    scenes = [
        {
            'text': intro,
            'query': f'{theme} educational introduction',
            'image': None,
            'mood': 'joy',
            'kind': 'intro',
            'avatar_cue': 'explanation',
        },
        *content_scenes,
        {
            'text': outro,
            'query': f'{theme} calm background',
            'image': None,
            'mood': 'joy',
            'kind': 'outro',
            'avatar_cue': 'celebration',
        },
    ]
    return scenes, intro, outro, full_narration


def load_avatar_manifest():
    global _AVATAR_MANIFEST_CACHE
    if _AVATAR_MANIFEST_CACHE is not None:
        return _AVATAR_MANIFEST_CACHE
    try:
        data = json.loads(AVATAR_MANIFEST_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as ex:
        raise RuntimeError(f'Avatar manifest is unavailable or invalid: {AVATAR_MANIFEST_PATH}') from ex
    poses = {}
    for pose in data.get('poses', []):
        name = pose.get('name')
        filename = pose.get('file')
        if not name or not filename or name in poses:
            raise RuntimeError('Avatar manifest contains a missing or duplicate pose name/file')
        path = (AVATAR_DIR / filename).resolve()
        if not path.is_relative_to(AVATAR_DIR.resolve()) or not path.is_file():
            raise RuntimeError(f'Avatar asset is missing or outside its asset directory: {filename}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != pose.get('sha256'):
            raise RuntimeError(f'Avatar asset checksum does not match manifest: {filename}')
        try:
            with Image.open(path) as image:
                alpha_bbox = image.getchannel('A').getbbox() if image.mode == 'RGBA' else None
                if image.mode != 'RGBA' or list(image.size) != pose.get('size') or list(alpha_bbox or ()) != pose.get('alpha_bbox'):
                    raise RuntimeError(f'Avatar asset RGBA geometry does not match manifest: {filename}')
        except OSError as ex:
            raise RuntimeError(f'Avatar asset cannot be decoded: {filename}') from ex
        poses[name] = {**pose, 'path': path}
    if not poses:
        raise RuntimeError('Avatar manifest contains no poses')
    _AVATAR_MANIFEST_CACHE = {'version': data.get('version'), 'poses': poses}
    return _AVATAR_MANIFEST_CACHE


def avatar_region_score(scenes, position):
    scores = []
    for scene in scenes:
        image_path = scene.get('image')
        if not image_path:
            continue
        path = Path(image_path)
        if not path.is_file():
            continue
        try:
            with Image.open(path) as image:
                gray = image.convert('L').resize((180, 102))
                left = {'left': 0, 'center': 60, 'right': 120}[position]
                region = gray.crop((left, 48, left + 60, 102))
                edge_mean = ImageStat.Stat(region.filter(ImageFilter.FIND_EDGES)).mean[0]
                contrast = ImageStat.Stat(region).stddev[0]
                scores.append(edge_mean + contrast * 0.35)
        except OSError:
            continue
    return sum(scores) / len(scores) if scores else None


def avatar_pose_for_block(scenes, position, previous_pose=None, block_index=0):
    priorities = (
        'warning', 'important_fact', 'reveal', 'question', 'surprise',
        'celebration', 'visual_focus', 'explanation',
    )
    cues = [scene.get('avatar_cue', 'explanation') for scene in scenes]
    cue = next((candidate for candidate in priorities if candidate in cues), 'explanation')
    pose = {
        'warning': 'serious_fact',
        'important_fact': 'serious_fact',
        'reveal': 'idea_reveal',
        'question': 'thinking',
        'surprise': 'shocked',
        'celebration': 'celebrating',
        'visual_focus': 'pointing_right' if position == 'left' else 'pointing_left' if position == 'right' else 'presenting',
        'explanation': ('explaining', 'presenting', 'pointing_up')[block_index % 3],
    }[cue]
    rare = {'idea_reveal', 'shocked', 'celebrating'}
    if pose in rare and previous_pose in rare:
        return 'explaining'
    return pose


def build_avatar_plan(scenes):
    manifest = load_avatar_manifest()
    blocks = []
    content_group = []
    content_seconds = 0.0
    grouped = []
    for index, scene in enumerate(scenes):
        if scene.get('kind') != 'content':
            if content_group:
                grouped.append(content_group)
                content_group = []
                content_seconds = 0.0
            grouped.append([(index, scene)])
            continue
        duration = float(scene.get('duration') or max(1.5, len(scene.get('text', '').split()) / 2.0))
        if content_group and content_seconds + duration > AVATAR_TARGET_BLOCK_SECONDS:
            grouped.append(content_group)
            content_group = []
            content_seconds = 0.0
        content_group.append((index, scene))
        content_seconds += duration
        if content_seconds >= AVATAR_TARGET_BLOCK_SECONDS:
            grouped.append(content_group)
            content_group = []
            content_seconds = 0.0
    if content_group:
        grouped.append(content_group)

    previous_position = None
    previous_pose = None
    position_held = 0.0
    content_block_index = 0
    for group in grouped:
        indices = [item[0] for item in group]
        block_scenes = [item[1] for item in group]
        kind = block_scenes[0].get('kind', 'content')
        duration = sum(float(scene.get('duration') or max(1.5, len(scene.get('text', '').split()) / 2.0)) for scene in block_scenes)
        if kind == 'intro':
            position, pose = 'center', 'waving'
        elif kind == 'outro':
            position, pose = 'center', 'waving'
        else:
            scores = {side: avatar_region_score(block_scenes, side) for side in ('left', 'right')}
            if previous_position == 'left':
                position = 'right'
            else:
                position = 'left'
            other = 'right' if position == 'left' else 'left'
            if position_held < AVATAR_MIN_POSITION_HOLD_SECONDS and previous_position in ('left', 'right'):
                position = previous_position
            elif scores[position] is not None and scores[other] is not None and scores[position] > scores[other] + 25:
                position = other
            pose = avatar_pose_for_block(block_scenes, position, previous_pose, content_block_index)
        if pose not in manifest['poses']:
            raise RuntimeError(f'Avatar pose {pose!r} is missing from manifest')
        state = {'pose': pose, 'position': position}
        for _, scene in group:
            scene['avatar'] = dict(state)
        blocks.append({
            'start_scene': indices[0],
            'end_scene': indices[-1],
            'kind': kind,
            'from_position': previous_position or 'center',
            **state,
        })
        if kind == 'content':
            if position == previous_position:
                position_held += duration
            else:
                previous_position = position
                position_held = duration
            previous_pose = pose
            content_block_index += 1
    return {
        'version': AVATAR_PLAN_VERSION,
        'asset_version': manifest['version'],
        'blocks': blocks,
    }


def expand_narration(project, seconds_missing):
    """Extend the main content; never time-stretch narration to meet requested minutes."""
    language = project['language']
    extra_words = max(70, int(seconds_missing * (2.3 if language == 'hi' else 2.55)))
    prompt = (f'Continue this explanatory narration naturally in {"Hindi (Devanagari)" if language == "hi" else "English"}. '
              f'Write approximately {extra_words} ADDITIONAL spoken words with useful related information, not repetition. '
              'Do not add a greeting, channel call-to-action, or sign-off; those are added separately. '
              'Return JSON with one key: "continuation". Narration so far:\n' + project.get('content_narration', project['narration'])[-7500:])
    result = gemini_json(prompt)
    addition = str(result.get('continuation', '')).strip()
    if len(addition) < 70:
        raise RuntimeError('Gemini returned an empty/short continuation')
    return addition


def sentences(text):
    # Hindi danda and sentence-final English punctuation; do not split on abbreviations perfectly.
    chunks=re.split(r'(?<=[.!?।！？])\s+|(?<=।)(?=[^\s])',re.sub(r'\s+',' ',text.strip()))
    return [s.strip() for s in chunks if s.strip()]


def narration_groups(narration, language, tts_provider='edge'):
    # Planning estimates only; measured speech still determines scene timing.
    # Keep sentences intact, even when a single sentence exceeds the target.
    paragraph_mode = tts_provider == 'gemini'
    target, maximum = (30, 45) if paragraph_mode else (7.5, 12.5)
    paragraphs = re.split(r'\n\s*\n', narration) if paragraph_mode else [narration]
    wps=1.75 if language=='hi' else 2.25
    groups=[]
    for paragraph in paragraphs:
        buf=[]; secs=0
        for s in sentences(paragraph):
            est=max(1.5,len(s.split())/wps)
            if buf and (secs+est>maximum or secs>=target):
                groups.append(' '.join(buf));buf=[];secs=0
            buf.append(s);secs+=est
        if buf:groups.append(' '.join(buf))
    return groups


def scene_chunking(tts_provider):
    return 'gemini-paragraph-v1' if tts_provider == 'gemini' else 'short-v1'


def plan_scenes(narration, language, theme, tts_provider='edge'):
    log('Planning scene imagery using Gemini')
    groups=narration_groups(narration, language, tts_provider)
    query_text='\n'.join(f'{i}: {g[:420]}' for i,g in enumerate(groups))
    prompt=(f'For every numbered narration segment, suggest one visually relevant Pexels PHOTO search query and one restrained avatar cue. '
            f'Narration language {language}; theme: {theme}. Queries MUST be ENGLISH, photographic, concrete, '
            'not abstract moods. 2-6 keywords. Keep visual descriptions faithful to what narrator says; '
            'do not hallucinate or claim that a visual is an exact depiction of real people/events. '
            f'Avatar cue must be one of: {", ".join(sorted(AVATAR_CUES))}. Use explanation for ordinary narration; '
            'only choose a special cue when the words clearly signal that beat. '
            'Return JSON with key "queries", array of objects {"id":integer,"query":string,"avatar_cue":string} covering all ids. '
            'Segments:\n'+query_text)
    # Chunk so long videos do not overload the model.
    mapping={}
    for offset in range(0,len(groups),35):
        subset=groups[offset:offset+35]
        local='\n'.join(f'{offset+i}: {g[:420]}' for i,g in enumerate(subset))
        data=gemini_json(prompt.split('Segments:\n')[0]+'Segments:\n'+local)
        for x in data.get('queries',[]):
            if isinstance(x,dict) and str(x.get('id','')).isdigit():
                cue = x.get('avatar_cue')
                mapping[int(x['id'])] = {
                    'query': str(x.get('query', '')),
                    'avatar_cue': cue if cue in AVATAR_CUES else 'explanation',
                }
    return [
        {
            'text': segment,
            'query': mapping.get(i, {}).get('query') or theme,
            'image': None,
            'mood': classify_mood(segment + ' ' + theme),
            'avatar_cue': mapping.get(i, {}).get('avatar_cue', 'explanation'),
        }
        for i, segment in enumerate(groups)
    ]


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
        current_image = Path(sc.get('image', '')) if sc.get('image') else None
        if current_image and current_image.is_file():
            log_progress('Images', i+1, len(scenes), progress_started)
            continue
        scene_key = hashlib.sha256((sc.get('kind', 'content') + '\0' + sc['text']).encode('utf-8')).hexdigest()[:20]
        dest=folder/f'scene_{scene_key}.jpg'
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
        config=types.GenerateContentConfig(response_modalities=['AUDIO'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_TTS_VOICE)))))
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


def compose_avatar_scene_clips(scenes, clip_paths, output_dir, fps, width, height, fade):
    """Add at most one avatar and the optional CTA to each scene clip."""
    manifest = load_avatar_manifest()
    plan = build_avatar_plan(scenes)
    blocks_by_scene = {}
    for block in plan['blocks']:
        for scene_index in range(block['start_scene'], block['end_scene'] + 1):
            blocks_by_scene[scene_index] = block

    avatar_clips = output_dir / 'clips' / 'avatar'
    avatar_clips.mkdir(parents=True, exist_ok=True)
    side_margin = round(AVATAR_SIDE_MARGIN * width / 1280)
    bottom_margin = round(AVATAR_BOTTOM_MARGIN * height / 720)
    cta_bottom_margin = round(OUTRO_CTA_BOTTOM_MARGIN * height / 720)
    cta_digest = hashlib.sha256(OUTRO_CTA_IMAGE.read_bytes()).hexdigest()

    def x_for(position):
        if position == 'left':
            return str(side_margin)
        if position == 'right':
            return f'main_w-overlay_w-{side_margin}'
        return '(main_w-overlay_w)/2'

    outputs = []
    for index, (scene, source_clip) in enumerate(zip(scenes, clip_paths)):
        block = blocks_by_scene[index]
        pose = manifest['poses'][block['pose']]
        is_bookend = block['kind'] in ('intro', 'outro')
        duration = float(scene['duration'])
        clip_duration = duration + (fade if index < len(scenes) - 1 else 0)
        frame_count = max(1, round(clip_duration * fps))
        avatar_height = round(height * (
            AVATAR_BOOKEND_HEIGHT_RATIO if is_bookend else AVATAR_DISPLAY_HEIGHT_RATIO
        ))
        x0, y0, x1, y1 = pose['alpha_bbox']
        start_of_block = index == block['start_scene']
        end_of_block = index == block['end_scene']
        fade_in = start_of_block
        fade_out = end_of_block
        fade_out_start = max(0.0, duration - AVATAR_MOVE_SECONDS)
        avatar_enable_end = duration if end_of_block else clip_duration
        dest = avatar_clips / f'{index:04}.mp4'
        stamp = dest.with_suffix('.sha256')
        source_stat = source_clip.stat()
        signature_data = {
            'plan_version': plan['version'],
            'asset_version': plan['asset_version'],
            'pose_sha256': pose['sha256'],
            'source': [str(source_clip), source_stat.st_size, source_stat.st_mtime_ns],
            'block': block,
            'duration': duration,
            'clip_duration': clip_duration,
            'fps': fps,
            'size': [width, height],
            'avatar_height': avatar_height,
            'margins': [side_margin, bottom_margin],
            'cta_sha256': cta_digest if block['kind'] == 'outro' else None,
            'cta_layout': [OUTRO_CTA_WIDTH_RATIO, cta_bottom_margin],
        }
        signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True).encode()).hexdigest()
        if dest.is_file() and stamp.is_file() and stamp.read_text(encoding='utf-8') == signature:
            outputs.append(dest)
            continue

        args = ['ffmpeg', '-y', '-loglevel', 'error', '-i', source_clip,
                '-loop', '1', '-framerate', str(fps), '-t', f'{clip_duration:.3f}', '-i', pose['path']]
        avatar_filters = [
            f'format=rgba,crop={x1-x0}:{y1-y0}:{x0}:{y0},scale=-1:{avatar_height},setsar=1'
        ]
        if fade_in:
            avatar_filters.append(f'fade=t=in:st=0:d={AVATAR_MOVE_SECONDS:.3f}:alpha=1')
        if fade_out:
            avatar_filters.append(
                f'fade=t=out:st={fade_out_start:.3f}:d={AVATAR_MOVE_SECONDS:.3f}:alpha=1'
            )
        filters = [f'[1:v]{",".join(avatar_filters)}[avatar]']
        x = x_for(block['position'])
        from_x = x_for(block.get('from_position', block['position']))
        if start_of_block and from_x != x:
            x = (
                f"'if(lt(t,{AVATAR_MOVE_SECONDS:.3f}),"
                f"({from_x})+(({x})-({from_x}))*t/{AVATAR_MOVE_SECONDS:.3f},{x})'"
            )
        y = '(main_h-overlay_h)/2' if is_bookend else f'main_h-overlay_h-{bottom_margin}'
        filters.append(
            f"[0:v][avatar]overlay=x={x}:y={y}:eof_action=pass:shortest=1:"
            f"enable='between(t,0,{avatar_enable_end:.3f})'[with_avatar]"
        )
        current_label = 'with_avatar'
        if block['kind'] == 'outro':
            cta_width = round(width * OUTRO_CTA_WIDTH_RATIO)
            with Image.open(OUTRO_CTA_IMAGE) as cta_image:
                source_width, source_height = cta_image.size
            cta_height = round(cta_width * source_height / source_width)
            cta_x = (width - cta_width) // 2
            cta_y = height - cta_bottom_margin - cta_height
            args.extend(['-loop', '1', '-framerate', str(fps), '-t', f'{clip_duration:.3f}', '-i', OUTRO_CTA_IMAGE])
            filters.append(
                f'[2:v]format=rgba,scale={cta_width}:{cta_height},'
                f'fade=t=in:st=0:d={OUTRO_CTA_FADE_SECONDS:.3f}:alpha=1[outro_cta]'
            )
            filters.append(
                f"[{current_label}][outro_cta]overlay=x={cta_x}:y={cta_y}:"
                f"eof_action=pass:shortest=1:enable='between(t,0,{duration:.3f})'[with_cta]"
            )
            current_label = 'with_cta'
        tmp = dest.with_name(dest.stem + '.part.mp4')
        run([
            *args, '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
            '-map', f'[{current_label}]', '-an', '-frames:v', str(frame_count), '-r', str(fps),
            '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23',
            '-pix_fmt', 'yuv420p', tmp,
        ])
        os.replace(tmp, dest)
        stamp.write_text(signature, encoding='utf-8')
        outputs.append(dest)
    return outputs, plan


def merge_xfade_batch(items, output, fade, fps, width, height, final_group=False, progress_label='Crossfade'):
    """Crossfade a bounded number of clips and cache the resulting disk-backed segment."""
    output.parent.mkdir(parents=True, exist_ok=True)
    stamp = output.with_suffix('.sha256')
    input_signatures = []
    for item in items:
        stat = item['path'].stat()
        input_signatures.append((str(item['path']), stat.st_size, stat.st_mtime_ns, item['duration']))
    signature = hashlib.sha256(json.dumps({
        'inputs': input_signatures,
        'fade': fade,
        'fps': fps,
        'size': [width, height],
        'final_group': final_group,
        'filter_version': 3,
        'encoder': ['libx264', 'veryfast', 20],
    }, sort_keys=True).encode()).hexdigest()
    if output.is_file() and stamp.is_file() and stamp.read_text(encoding='utf-8') == signature:
        log(f'{progress_label}: cached segment reused')
        return
    if len(items) == 1:
        shutil.copyfile(items[0]['path'], output)
        stamp.write_text(signature, encoding='utf-8')
        return

    args = ['ffmpeg', '-y', '-loglevel', 'error', '-progress', 'pipe:1', '-nostats']
    for item in items:
        args.extend(['-i', item['path']])
    filters = []
    for index in range(len(items)):
        filters.append(
            f'[{index}:v]setpts=PTS-STARTPTS,fps={fps},settb=1/{fps},format=yuv420p[v{index}]'
        )
    current = 'v0'
    elapsed = float(items[0]['duration'])
    for index in range(1, len(items)):
        next_label = f'x{index}'
        filters.append(
            f'[{current}][v{index}]xfade=transition=fade:duration={fade:.3f}:'
            f'offset={elapsed:.6f}[raw_{next_label}]'
        )
        # Some FFmpeg builds leave chained xfade outputs with an undefined
        # frame rate (1/0). Re-establish CFR and the xfade time base after
        # every transition before that output is used by the next xfade.
        filters.append(
            f'[raw_{next_label}]setpts=PTS-STARTPTS,fps={fps},settb=1/{fps},'
            f'format=yuv420p[{next_label}]'
        )
        current = next_label
        elapsed += float(items[index]['duration'])
    tmp = output.with_name(output.stem + '.part.mp4')
    run([
        *args, '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
        '-map', f'[{current}]', '-an', '-r', str(fps),
        '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '20',
        '-pix_fmt', 'yuv420p', tmp,
    ], progress_label=progress_label,
       progress_duration=sum(float(item['duration']) for item in items) + (0 if final_group else fade))
    os.replace(tmp, output)
    stamp.write_text(signature, encoding='utf-8')


def merge_scene_clips(clip_paths, durations, output_dir, fps, width, height, fade, resolution):
    """Merge scenes in a bounded fan-in tree so filter memory does not grow with scene count."""
    items = [
        {'path': path, 'duration': float(duration)}
        for path, duration in zip(clip_paths, durations)
    ]
    if len(items) == 1:
        shutil.copyfile(items[0]['path'], output_dir / 'visual_only.mp4')
        return output_dir / 'visual_only.mp4'

    fan_in = XFADE_MERGE_INPUTS[resolution]
    merge_dir = output_dir / 'clips' / 'xfade'
    level = 0
    while len(items) > fan_in:
        merged = []
        group_starts = list(range(0, len(items), fan_in))
        log(f'Crossfade pass {level + 1}: {len(items)} segments in groups of up to {fan_in}')
        for group_index, start in enumerate(group_starts):
            group = items[start:start + fan_in]
            group_duration = sum(item['duration'] for item in group)
            is_final_group = start + len(group) == len(items)
            progress_label = (
                f'Crossfade pass {level + 1} group {group_index + 1}/{len(group_starts)} '
                f'({len(group)} segments)'
            )
            log(f'{progress_label}: starting, about {format_duration(group_duration)} of video')
            path = merge_dir / f'pass-{level:02d}-{group_index:04d}.mp4'
            if len(group) > 1:
                merge_xfade_batch(
                    group, path, fade, fps, width, height, is_final_group,
                    progress_label=progress_label,
                )
            merged.append({'path': path if len(group) > 1 else group[0]['path'], 'duration': group_duration})
        items = merged
        level += 1

    log(f'Final crossfade pass: {len(items)} segments')
    output = output_dir / 'visual_only.mp4'
    merge_xfade_batch(
        items, output, fade, fps, width, height, final_group=True,
        progress_label=f'Final crossfade ({len(items)} segments)',
    )
    return output


def scene_frame_counts(scenes, fps):
    """Round cumulative audio boundaries once so scene rounding cannot drift."""
    if fps <= 0:
        raise ValueError('FPS must be positive')
    counts = []
    elapsed = 0.0
    previous = 0
    for index, scene in enumerate(scenes):
        duration = float(scene['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f'Scene {index + 1} has no valid narration duration')
        elapsed += duration
        boundary = round(elapsed * fps)
        if boundary <= previous:
            raise ValueError(f'Scene {index + 1} is shorter than one video frame at {fps} FPS')
        counts.append(boundary - previous)
        previous = boundary
    return counts


def append_scene_visual(inputs, filters, scene, scene_index, block, manifest, prefix,
                        input_frames, frame_offset, scene_frames, fade_frames,
                        fps, width, height, style, zoom, has_next):
    """Add one scene's image, avatar, and optional outro graphic to a filter graph."""
    image_input = len(inputs)
    # Overlay EOF handling can drop its last frame, so provide one source frame of padding.
    inputs.append((scene['image'], input_frames + 1))
    avatar = manifest['poses'][block['pose']]
    avatar_input = len(inputs)
    inputs.append((avatar['path'], input_frames + 1))
    duration = float(scene['duration'])
    scene_style = style
    if style == 'auto':
        scene_style = scene.get('mood')
        if scene_style not in ('horror', 'joy', 'neutral'):
            scene_style = classify_mood(scene.get('text', ''))

    base = (f'trim=end_frame={input_frames + 1},settb=1/{fps},'
            f'setpts=PTS-STARTPTS+{frame_offset},'
            f'scale={width}:{height}:force_original_aspect_ratio=increase,'
            f'crop={width}:{height}')
    if zoom:
        total_frames = scene_frames + (fade_frames if has_next else 0)
        progress = f'((in-1+{frame_offset})/{total_frames-1})' if total_frames > 1 else '0'
        eased = f'({progress}*{progress}*(3-2*{progress}))'
        delta = ZOOM_OVERSCAN - 1
        factor = (f'1.0+{delta:.2f}*{eased}' if scene_index % 2 == 0
                  else f'{ZOOM_OVERSCAN:.2f}-{delta:.2f}*{eased}')
        left = f'W/2-W/(2*({factor}))'
        right = f'W/2+W/(2*({factor}))'
        top = f'H/2-H/(2*({factor}))'
        bottom = f'H/2+H/(2*({factor}))'
        base += (f',format=yuv444p,perspective=eval=frame:sense=source:interpolation=cubic:'
                 f"x0='{left}':y0='{top}':x1='{right}':y1='{top}':"
                 f"x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}'")
    if scene_style == 'horror':
        base += ',eq=brightness=-0.035:saturation=0.80,vignette=PI/4'
    elif scene_style == 'joy':
        base += ',eq=brightness=0.018:saturation=1.23:contrast=1.04'
    filters.append(f'[{image_input}:v]{base},setsar=1,format=yuv420p[{prefix}_image]')

    is_bookend = block['kind'] in ('intro', 'outro')
    avatar_height = round(height * (AVATAR_BOOKEND_HEIGHT_RATIO if is_bookend
                                    else AVATAR_DISPLAY_HEIGHT_RATIO))
    x0, y0, x1, y1 = avatar['alpha_bbox']
    avatar_filter = (f'trim=end_frame={input_frames + 1},settb=1/{fps},'
                     f'setpts=PTS-STARTPTS+{frame_offset},format=rgba,'
                     f'crop={x1-x0}:{y1-y0}:{x0}:{y0},scale=-1:{avatar_height},setsar=1')
    if scene_index == block['start_scene']:
        avatar_filter += f',fade=t=in:st=0:d={AVATAR_MOVE_SECONDS:.3f}:alpha=1'
    if scene_index == block['end_scene']:
        avatar_filter += (f',fade=t=out:st={max(0.0, duration-AVATAR_MOVE_SECONDS):.3f}:'
                          f'd={AVATAR_MOVE_SECONDS:.3f}:alpha=1')
    filters.append(f'[{avatar_input}:v]{avatar_filter}[{prefix}_avatar]')

    side_margin = round(AVATAR_SIDE_MARGIN * width / 1280)
    bottom_margin = round(AVATAR_BOTTOM_MARGIN * height / 720)

    def x_for(position):
        if position == 'left':
            return str(side_margin)
        if position == 'right':
            return 'main_w-overlay_w-' + str(side_margin)
        return '(main_w-overlay_w)/2'

    x = x_for(block['position'])
    from_x = x_for(block.get('from_position', block['position']))
    if scene_index == block['start_scene'] and from_x != x:
        x = (f"'if(lt(t,{AVATAR_MOVE_SECONDS:.3f}),({from_x})+"
             f"(({x})-({from_x}))*t/{AVATAR_MOVE_SECONDS:.3f},{x})'")
    y = '(main_h-overlay_h)/2' if is_bookend else f'main_h-overlay_h-{bottom_margin}'
    avatar_end = duration if scene_index == block['end_scene'] else duration + (fade_frames / fps if has_next else 0)
    filters.append(
        f'[{prefix}_image][{prefix}_avatar]overlay=x={x}:y={y}:eof_action=pass:'
        f"shortest=1:enable='between(t,0,{avatar_end:.6f})'[{prefix}_with_avatar]"
    )
    current = f'{prefix}_with_avatar'
    if block['kind'] == 'outro':
        cta_input = len(inputs)
        inputs.append((OUTRO_CTA_IMAGE, input_frames + 1))
        cta_width = round(width * OUTRO_CTA_WIDTH_RATIO)
        with Image.open(OUTRO_CTA_IMAGE) as cta_image:
            source_width, source_height = cta_image.size
        cta_height = round(cta_width * source_height / source_width)
        cta_x = (width - cta_width) // 2
        cta_y = height - round(OUTRO_CTA_BOTTOM_MARGIN * height / 720) - cta_height
        filters.append(
            f'[{cta_input}:v]trim=end_frame={input_frames + 1},settb=1/{fps},'
            f'setpts=PTS-STARTPTS+{frame_offset},format=rgba,scale={cta_width}:{cta_height},'
            f'fade=t=in:st=0:d={OUTRO_CTA_FADE_SECONDS:.3f}:alpha=1[{prefix}_cta]'
        )
        filters.append(
            f'[{current}][{prefix}_cta]overlay=x={cta_x}:y={cta_y}:eof_action=pass:'
            f"shortest=1:enable='between(t,0,{duration:.6f})'[{prefix}_with_cta]"
        )
        current = f'{prefix}_with_cta'
    filters.append(f'[{current}]setpts=PTS-STARTPTS,fps={fps},settb=1/{fps},format=yuv420p[{prefix}_ready]')
    return f'{prefix}_ready'


def render_scene_segments(scenes, output_dir, fps, width, height, style, zoom):
    """Encode each finished scene once, including its incoming transition."""
    counts = scene_frame_counts(scenes, fps)
    if not counts:
        raise ValueError('Cannot render a project without scenes')
    fade_frames = min(round(0.45 * fps), min(counts) // 4) if len(counts) > 1 else 0
    plan = build_avatar_plan(scenes)
    manifest = load_avatar_manifest()
    blocks = {}
    for block in plan['blocks']:
        for scene_index in range(block['start_scene'], block['end_scene'] + 1):
            blocks[scene_index] = block
    images = [hashlib.sha256(Path(scene['image']).read_bytes()).hexdigest() for scene in scenes]
    cta_digest = (hashlib.sha256(OUTRO_CTA_IMAGE.read_bytes()).hexdigest()
                  if any(block['kind'] == 'outro' for block in plan['blocks']) else None)
    clips = output_dir / 'clips'
    if clips.is_symlink():
        raise RuntimeError('Refusing to render into a symlinked clips directory')
    clips.mkdir(exist_ok=True)
    outputs = []
    signatures = []
    started = time.monotonic()
    for index, scene in enumerate(scenes):
        previous = index - 1 if fade_frames and index else None
        signature = cache_key({
            'version': SEGMENT_RENDER_VERSION,
            'scene': index,
            'image': images[index],
            'previous_image': images[previous] if previous is not None else None,
            'duration': float(scene['duration']),
            'previous_duration': float(scenes[previous]['duration']) if previous is not None else None,
            'frames': counts[index],
            'previous_frames': counts[previous] if previous is not None else None,
            'fade_frames': fade_frames,
            'fps': fps,
            'size': [width, height],
            'style': style,
            'scene_mood': scene.get('mood'),
            'previous_mood': scenes[previous].get('mood') if previous is not None else None,
            'scene_text': scene.get('text') if style == 'auto' else None,
            'previous_text': scenes[previous].get('text') if previous is not None and style == 'auto' else None,
            'zoom': zoom,
            'zoom_overscan': ZOOM_OVERSCAN,
            'avatar_layout': [AVATAR_DISPLAY_HEIGHT_RATIO, AVATAR_BOOKEND_HEIGHT_RATIO,
                              AVATAR_SIDE_MARGIN, AVATAR_BOTTOM_MARGIN, AVATAR_MOVE_SECONDS],
            'cta_layout': [OUTRO_CTA_WIDTH_RATIO, OUTRO_CTA_BOTTOM_MARGIN,
                           OUTRO_CTA_FADE_SECONDS],
            'block': blocks[index],
            'previous_block': blocks[previous] if previous is not None else None,
            'pose': manifest['poses'][blocks[index]['pose']]['sha256'],
            'previous_pose': manifest['poses'][blocks[previous]['pose']]['sha256'] if previous is not None else None,
            'avatar_plan_version': plan['version'],
            'avatar_asset_version': plan['asset_version'],
            'outro_cta': cta_digest if blocks[index]['kind'] == 'outro' else None,
            'encoder': ['libx264', 'veryfast', 23, 'yuv420p'],
        })
        dest = clips / f'{index:04}.mp4'
        stamp = dest.with_suffix('.sha256')
        if dest.is_symlink() or stamp.is_symlink():
            raise RuntimeError(f'Refusing symlinked scene cache path for scene {index + 1}')
        try:
            cached = json.loads(stamp.read_text(encoding='utf-8'))
            reusable = (dest.is_file() and dest.stat().st_size > 0 and
                        cached == {'signature': signature, 'size': dest.stat().st_size})
        except (OSError, ValueError):
            reusable = False
        if reusable:
            outputs.append(dest)
            signatures.append(signature)
            log_progress('Rendering', index + 1, len(scenes), started)
            continue
        inputs = []
        filters = []
        if previous is not None:
            previous_label = append_scene_visual(
                inputs, filters, scenes[previous], previous, blocks[previous], manifest,
                'previous', fade_frames, counts[previous], counts[previous], fade_frames,
                fps, width, height, style, zoom, previous < len(scenes) - 1,
            )
        current_label = append_scene_visual(
            inputs, filters, scene, index, blocks[index], manifest, 'current',
            counts[index], 0, counts[index], fade_frames,
            fps, width, height, style, zoom, index < len(scenes) - 1,
        )
        if previous is not None:
            filters.append(
                f'[{previous_label}][{current_label}]xfade=transition=fade:'
                f'duration={fade_frames/fps:.9f}:offset=0,'
                f'trim=end_frame={counts[index]},settb=1/{fps},setpts=N[out]'
            )
        else:
            filters.append(f'[{current_label}]trim=end_frame={counts[index]}[out]')
        command = ['ffmpeg', '-y', '-loglevel', 'error']
        for path, frames in inputs:
            command.extend(['-loop', '1', '-framerate', str(fps),
                            '-t', f'{frames/fps:.9f}', '-i', str(path)])
        temporary = dest.with_name(dest.stem + '.part.mp4')
        if temporary.is_symlink():
            raise RuntimeError(f'Refusing symlinked temporary scene path for scene {index + 1}')
        command.extend([
            '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
            '-map', '[out]', '-an', '-frames:v', str(counts[index]), '-r', str(fps),
            '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23',
            '-pix_fmt', 'yuv420p', '-video_track_timescale', str(fps * 1000), temporary,
        ])
        log(f'Rendering finished scene {index + 1}/{len(scenes)}')
        run(command)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f'Scene {index + 1} render produced no video; prior segments were retained')
        os.replace(temporary, dest)
        write_json_atomic(stamp, {'signature': signature, 'size': dest.stat().st_size})
        outputs.append(dest)
        signatures.append(signature)
        log_progress('Rendering', index + 1, len(scenes), started)
    return outputs, signatures, counts, fade_frames, plan


def join_scene_segments(paths, signatures, counts, output_dir, fps, width, height):
    """Join compatible H.264 scene segments without decoding their video."""
    visual = output_dir / 'visual_only.mp4'
    stamp = output_dir / 'visual_only.sha256'
    clips = output_dir / 'clips'
    if clips.is_symlink() or visual.is_symlink() or stamp.is_symlink():
        raise RuntimeError('Refusing symlinked joined-video cache paths')
    signature = cache_key({'version': SEGMENT_RENDER_VERSION, 'segments': signatures,
                           'frames': counts, 'fps': fps, 'size': [width, height]})
    try:
        cached = json.loads(stamp.read_text(encoding='utf-8'))
        if (visual.is_file() and visual.stat().st_size > 0 and
                cached == {'signature': signature, 'size': visual.stat().st_size}):
            log('Final video join: cached output reused')
            return visual
    except (OSError, ValueError):
        pass
    manifest = clips / 'segments.ffconcat'
    if manifest.is_symlink():
        raise RuntimeError('Refusing a symlinked scene concat manifest')
    entries = ['ffconcat version 1.0']
    for path in paths:
        escaped = str(path.resolve()).replace("'", "'\\''")
        entries.append(f"file '{escaped}'")
    manifest.write_text('\n'.join(entries) + '\n', encoding='utf-8')
    temporary = output_dir / 'visual_only.part.mp4'
    if temporary.is_symlink():
        raise RuntimeError('Refusing symlinked joined-video cache paths')
    run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
         '-i', manifest, '-map', '0:v:0', '-an', '-c:v', 'copy', temporary])
    if not temporary.is_file() or temporary.stat().st_size == 0:
        raise RuntimeError('Scene join produced no video; segments were retained for retry')
    try:
        probe = json.loads(run([
            'ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height,nb_read_frames',
            '-of', 'json', temporary,
        ]))
        stream = next(iter(probe.get('streams', [])), {})
        actual_frames = int(stream.get('nb_read_frames', -1))
    except (ValueError, TypeError, AttributeError) as ex:
        raise RuntimeError('FFprobe could not validate the joined video; segments were retained') from ex
    if (stream.get('codec_name') != 'h264' or
            (stream.get('width'), stream.get('height')) != (width, height) or
            actual_frames != sum(counts)):
        raise RuntimeError(
            f'Scene join failed frame or format validation: expected {sum(counts)} H.264 '
            f'frames at {width}x{height}, got {actual_frames} frames with '
            f'{stream.get("codec_name")} at {stream.get("width")}x{stream.get("height")}; '
            'segments were retained for retry'
        )
    os.replace(temporary, visual)
    write_json_atomic(stamp, {'signature': signature, 'size': visual.stat().st_size})
    return visual


def render(scenes, narration, music, output_dir, fps=24, width=1280, height=720, style='auto', zoom=True, bgm_volume=DEFAULT_BGM_VOLUME, final_output=None):
    """Encode finished scene segments, join by stream copy, then mux audio."""
    files, signatures, counts, _, avatar_plan = render_scene_segments(
        scenes, output_dir, fps, width, height, style, zoom,
    )
    save_json(output_dir / 'avatar_plan.json', avatar_plan)
    visual = join_scene_segments(files, signatures, counts, output_dir, fps, width, height)
    out = final_output if final_output is not None else output_dir / 'final.mp4'
    total = sum(float(scene['duration']) for scene in scenes)
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


def requested_render_settings(args):
    return {
        'tts': args.tts,
        'indic_voice': args.indic_voice if args.tts == 'indic-parler' else None,
        'fps': args.fps,
        'resolution': args.resolution,
        'style': args.style,
        'zoom': not args.no_zoom,
        'bgm_volume': args.bgm_volume,
        'bgm_file': str(args.bgm_file.resolve()) if args.bgm_file else None,
        'skip_music': args.skip_music,
        'music_rights_confirmed': args.music_rights_confirmed,
        'duration_tolerance': args.duration_tolerance,
    }


def render_fingerprint(args):
    settings = {
        **requested_render_settings(args),
        'version': RENDER_FINGERPRINT_VERSION,
        'tts_identity': tts_cache_identity('', args.language, args.tts, 'neutral', args.indic_url, args.indic_voice),
        'gemini_text_model': os.getenv('GEMINI_TEXT_MODEL', 'gemini-2.5-flash'),
        'avatar_plan_version': AVATAR_PLAN_VERSION,
        'narration_trim_version': NARRATION_TRIM_VERSION,
    }
    return cache_key(settings)


def validate_final_video(path, expected_duration, width, height):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f'Final mux produced no video at {path}; work files were retained for retry')
    try:
        probe = json.loads(run([
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration:stream=codec_type,width,height', '-of', 'json', path,
        ]))
        duration = float(probe['format']['duration'])
        streams = probe['streams']
        video = next((stream for stream in streams if stream.get('codec_type') == 'video'), None)
        audio = next((stream for stream in streams if stream.get('codec_type') == 'audio'), None)
    except (AttributeError, KeyError, TypeError, ValueError) as ex:
        raise RuntimeError('FFprobe could not validate the final video; work files were retained for retry') from ex
    if not video or not audio or (video.get('width'), video.get('height')) != (width, height):
        raise RuntimeError('Final video is missing the expected video/audio streams or dimensions; work files were retained')
    if not math.isfinite(duration) or duration <= 0 or abs(duration - expected_duration) > max(0.5, expected_duration * 0.001):
        raise RuntimeError('Final video duration does not match narration; work files were retained for retry')
    return duration


MANAGED_ARTIFACTS = {
    '.': re.compile(r'(?:script\.txt|video_script\.txt|avatar_plan\.json|visual_only\.(?:mp4|sha256|part\.mp4)|final\.part\.mp4|project\.json\.part)'),
    'images': re.compile(r'(?:fallback_[0-9]+|scene_[0-9a-f]{20})\.jpg'),
    'voice': re.compile(r'(?:narration\.wav|narration_concat\.txt|[0-9]{4}_[0-9a-f]{20}\.(?:wav|json|part\.wav|part\.mp3|json\.part)|trim_v[0-9]+_[0-9]{4}_[0-9a-f]{20}\.wav(?:\.part)?)'),
    'music': re.compile(r'(?:credits\.json|jamendo_[0-9]+\.mp3)'),
    'clips': re.compile(r'(?:[0-9]{4}\.(?:mp4|sha256|part\.mp4)|segments\.ffconcat)'),
    'clips/avatar': re.compile(r'[0-9]{4}\.(?:mp4|sha256|part\.mp4)'),
    'clips/xfade': re.compile(r'pass-[0-9]{2}-[0-9]{4}\.(?:mp4|sha256|part\.mp4)'),
}


def cleanup_project_artifacts(output_dir, source, protected_paths=()):
    root = output_dir.resolve()
    unsafe = {
        Path('/'), Path('/tmp').resolve(), Path.cwd().resolve(),
        (Path(__file__).resolve().parent / 'outputs').resolve(),
        *Path.cwd().resolve().parents,
    }
    if root in unsafe:
        raise RuntimeError(f'Refusing cleanup of broad output directory: {root}')
    manifest = root / 'project.json'
    if manifest.is_symlink() or not manifest.is_file():
        raise RuntimeError('Refusing cleanup without a regular project manifest')
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    if saved.get('status') != 'cleaning' or saved.get('source') != source:
        raise RuntimeError('Refusing cleanup because the project manifest is not in the expected cleaning state')
    protected = {Path(path).resolve() for path in protected_paths if path is not None}
    unknown = []
    for relative in ('clips/xfade', 'clips/avatar', 'clips', 'images', 'voice', 'music', '.'):
        folder = root / relative
        if folder.is_symlink():
            unknown.append(relative)
            continue
        if not folder.exists():
            continue
        if not folder.is_dir() or not folder.resolve().is_relative_to(root):
            unknown.append(relative)
            continue
        for child in folder.iterdir():
            child_relative = str(child.relative_to(root))
            if relative == '.' and child.name in ('project.json', 'final.mp4', 'subtitles.srt'):
                continue
            if child.is_symlink():
                unknown.append(child_relative)
            elif child.is_dir():
                if (relative == '.' and child.name in ('images', 'voice', 'music', 'clips')) or (
                    relative == 'clips' and child.name in ('avatar', 'xfade')
                ):
                    continue
                unknown.append(child_relative)
            elif (MANAGED_ARTIFACTS[relative].fullmatch(child.name)
                  and child.resolve().is_relative_to(root) and child.resolve() not in protected):
                child.unlink()
            else:
                unknown.append(child_relative)
        if relative != '.':
            try:
                folder.rmdir()
            except OSError as ex:
                if ex.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    raise
    return unknown


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
    p.add_argument('--keep-intermediates',action='store_true',help='Retain generated scripts, media caches, and clips after a successful render')
    p.add_argument('--theme',help='English Pexels theme search override')
    p.add_argument('--music-mood',help='Comma-separated Jamendo search moods')
    p.add_argument('--music-rights-confirmed',action='store_true',help='Use Jamendo candidates only after separately clearing sync/commercial rights and confirming interactively')
    p.add_argument('--skip-music',action='store_true')
    p.add_argument('--dry-run',action='store_true',help='Stop after script, scenes and search queries are planned')
    p.add_argument('--fps',type=int,default=24)
    p.add_argument('--resolution', choices=VIDEO_RESOLUTIONS, default='720p',
                   help='Output size: 720p, 1080p, or 2160p UHD')
    p.add_argument('--style',choices=['auto','horror','joy','neutral'],default='auto',help='Visual grading: auto follows each scene mood; another value overrides every scene')
    p.add_argument('--no-zoom',action='store_true')
    p.add_argument('--bgm-file',type=Path,help='Local music file for which you have suitable video-sync rights')
    p.add_argument('--bgm-volume',type=float,default=DEFAULT_BGM_VOLUME,help='BGM linear volume from 0 to 1 (default: 0.08)')
    p.add_argument('--duration-tolerance',type=float,default=0.07,help='Fractional tolerance for idea-generated videos')
    p.add_argument('--outro', choices=OUTRO_PROFILES, default='default',
                   help='Mandatory outro profile (default: generic; facts-sun-le-bhai adds the branded Hindi/English call-to-action)')
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


def require_pipeline_prerequisites(p, args):
    check_tools()
    if not os.getenv('GEMINI_API_KEY'):
        p.error('Missing GEMINI_API_KEY in .env')
    if not args.dry_run and not os.getenv('PEXELS_API_KEY'):
        p.error('Missing PEXELS_API_KEY in .env')


def main():
    p = build_parser()
    args=p.parse_args()
    validate_cli_args(p, args)

    if args.minutes<=0 or args.minutes>60: p.error('--minutes must be between 0 and 60')
    if not 0 <= args.bgm_volume <= 1: p.error('--bgm-volume must be between 0 and 1')

    current_script = args.script.read_text(encoding='utf-8').strip() if args.script else None
    identity = source_identity(
        'idea' if args.idea else 'script',
        args.idea if args.idea else current_script,
        args.language,
        args.minutes,
        args.theme,
        args.music_mood,
        args.outro,
    )
    requested_fingerprint = render_fingerprint(args)
    prepared_project = None
    if args.output is None:
        output_base = Path('outputs')
        matching_output = find_matching_output(output_base, identity)
        if matching_output:
            args.output = matching_output
            log(f'Automatically resuming matching project: {args.output}')
        else:
            if args.idea:
                require_pipeline_prerequisites(p, args)
                prepared_project = narrative_from_idea(args.idea,args.language,args.minutes,args.outro)
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
        if project.get('format_version') != PROJECT_FORMAT_VERSION:
            raise RuntimeError(
                'This saved project predates mandatory avatar intro/outro scenes; use a new --output directory '
                'to avoid reusing media planned for a different scene layout'
            )
        if project.get('outro_profile') != args.outro:
            raise RuntimeError(
                f'Saved project outro is {project.get("outro_profile")!r}, but --outro is {args.outro!r}; '
                'use a new --output directory for a different intro/outro plan'
            )
        if project.get('source') != identity:
            raise RuntimeError(
                'The requested source or generation settings differ from the saved project; use a new --output '
                'directory so narration, scenes, and cached media remain matched'
            )
        if project.get('language') != args.language:
            raise RuntimeError(
                f'Saved project language is {project.get("language")!r}, but --language is {args.language!r}; '
                'use a new --output directory for a different language'
            )
        if project.get('scene_chunking', 'short-v1') != scene_chunking(args.tts):
            raise RuntimeError(
                'Saved project uses a different narration chunk size; use a new --output directory '
                'for Gemini paragraph narration or short-scene narration'
            )
        if args.script:
            if current_script != project.get('content_narration', project.get('narration', '')).strip():
                raise RuntimeError(
                    'The script differs from the narration saved in project.json; use a new --output '
                    'directory so the scene plan and media cannot be reused for stale text'
                )
        if project.get('intermediates') not in (None, 'keep', 'delete_on_success'):
            raise RuntimeError('Saved project has an unknown intermediate-retention policy')
        if project.get('status') in ('cleaning', 'complete'):
            if project['status'] == 'cleaning' and project.get('intermediates') != 'delete_on_success':
                raise RuntimeError('Saved project has inconsistent cleanup state; no files were removed')
            if project.get('render_fingerprint') != requested_fingerprint:
                raise RuntimeError('Completed project settings differ from this command; use a new --output directory')
            if args.keep_intermediates and project.get('intermediates') != 'keep':
                raise RuntimeError('Intermediates were already removed; --keep-intermediates cannot restore them. Use a new --output directory')
            movie = args.output / 'final.mp4'
            subtitle = args.output / 'subtitles.srt'
            if not movie.is_file() or movie.stat().st_size == 0 or not subtitle.is_file() or subtitle.stat().st_size == 0:
                raise RuntimeError('Completed project is missing final.mp4 or subtitles.srt; use a new --output directory')
            if project['status'] == 'cleaning':
                unknown = cleanup_project_artifacts(args.output, identity, (args.script, args.bgm_file))
                project['status'] = 'complete'
                project['cleanup'] = {'status': 'done', 'additional_files': len(unknown)}
                write_json_atomic(meta, project)
                if unknown:
                    log(f'Cleanup preserved {len(unknown)} additional files or directories')
            log(f'Project already complete. VIDEO: {movie}\nSRT: {subtitle}\nMANIFEST: {meta}')
            return
        if project.get('status') not in (None, 'in_progress'):
            raise RuntimeError('Saved project has an unknown lifecycle status; use a new --output directory')
        require_pipeline_prerequisites(p, args)
        project['status'] = 'in_progress'
        project['intermediates'] = 'keep' if args.keep_intermediates else project.get('intermediates', 'delete_on_success')
        write_json_atomic(meta, project)
        log('Resuming saved project manifest')
    else:
        require_pipeline_prerequisites(p, args)
        if prepared_project is not None:
            project = prepared_project
        elif args.idea:
            project=narrative_from_idea(args.idea,args.language,args.minutes,args.outro)
        else:
            project={'title':args.script.stem,'narration':current_script,'theme':args.theme or 'cinematic nature landscape','music_moods':['cinematic']}
        project['source'] = identity
        project['format_version'] = PROJECT_FORMAT_VERSION
        project['status'] = 'in_progress'
        project['intermediates'] = 'keep' if args.keep_intermediates else 'delete_on_success'
        project['outro_profile'] = args.outro
        project['scene_chunking'] = scene_chunking(args.tts)
        project['output_slug'] = args.output.name
        project['language']=args.language
        project['theme']=args.theme or project.get('theme') or 'cinematic landscape'
        project['music_moods']=[x.strip() for x in args.music_mood.split(',')] if args.music_mood else project.get('music_moods',['cinematic'])
        project['content_narration'] = project['narration'].strip()
        scenes, intro, outro, video_narration = build_video_scenes(
            project['content_narration'], project.get('title'), args.language, project['theme'], args.outro, args.tts
        )
        project['intro_narration'] = intro
        project['outro_narration'] = outro
        project['video_narration'] = video_narration
        project['scenes'] = scenes
        project['avatar_plan'] = build_avatar_plan(project['scenes'])
        write_json_atomic(meta,project)
        (args.output/'script.txt').write_text(project['content_narration'],encoding='utf-8')
        (args.output/'video_script.txt').write_text(project['video_narration'],encoding='utf-8')
    if args.dry_run:
        log(f'Dry run: {len(project["scenes"])} planned scenes with mandatory intro/outro and avatar states in {meta}');return
    scenes=project['scenes']
    gather_images(scenes,project['theme'],args.output/'images');write_json_atomic(meta,project)
    voice=narrate(scenes,args.output/'voice',project['language'],args.tts,args.indic_url,args.indic_voice)
    length=make_srt(scenes,args.output/'subtitles.srt')
    project['avatar_plan'] = build_avatar_plan(scenes)
    write_json_atomic(meta,project)
    content_length = sum(scene['duration'] for scene in scenes if scene.get('kind') == 'content')
    bookend_length = sum(scene['duration'] for scene in scenes if scene.get('kind') != 'content')
    content_target = max(0, 60 * args.minutes - bookend_length)
    if args.idea and project.get('content_narration') and content_length < content_target*(1-args.duration_tolerance):
        # At most two continuations, avoiding endless retries and preserving old audio.
        for attempt in range(2):
            deficit=content_target-content_length
            log(f'Script shorter than target by {deficit:.1f}s; requesting meaningful continuation {attempt+1}/2')
            extra=expand_narration(project,deficit)
            added=plan_scenes(extra,project['language'],project['theme'],args.tts)
            for scene in added:
                scene['kind'] = 'content'
            scenes[-1:-1] = added
            project['content_narration'] += '\n' + extra
            project['narration'] = project['content_narration']
            project['video_narration'] = '\n\n'.join((
                project['intro_narration'], project['content_narration'], project['outro_narration'],
            ))
            project['avatar_plan'] = build_avatar_plan(scenes)
            write_json_atomic(meta,project)
            (args.output/'script.txt').write_text(project['content_narration'],encoding='utf-8')
            (args.output/'video_script.txt').write_text(project['video_narration'],encoding='utf-8')
            gather_images(scenes,project['theme'],args.output/'images')
            voice=narrate(scenes,args.output/'voice',project['language'],args.tts,args.indic_url,args.indic_voice)
            length=make_srt(scenes,args.output/'subtitles.srt')
            content_length = sum(scene['duration'] for scene in scenes if scene.get('kind') == 'content')
            project['avatar_plan'] = build_avatar_plan(scenes)
            write_json_atomic(meta,project)
            if content_length >= content_target*(1-args.duration_tolerance): break
        if abs(content_length-content_target)>content_target*args.duration_tolerance:
            log(f'WARNING: Main content is {content_length:.1f}s versus {content_target:.1f}s target; no artificial slowdown applied.')
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
    width, height = VIDEO_RESOLUTIONS[args.resolution]
    project['background_music'] = [
        {
            'source': 'jamendo' if track.get('id') else ('local' if args.bgm_file else 'built-in'),
            'name': track.get('name') or Path(track['path']).name,
            'artist': track.get('artist'),
            'license': track.get('license'),
        }
        for track in (music or [])
    ]
    write_json_atomic(meta, project)
    temporary_movie = args.output / 'final.part.mp4'
    if temporary_movie.is_symlink():
        raise RuntimeError('Refusing to mux into a symlinked final.part.mp4 in the output directory')
    movie=render(scenes,voice,music,args.output,args.fps,width=width,height=height,
                 style=args.style,zoom=not args.no_zoom,bgm_volume=args.bgm_volume,
                 final_output=temporary_movie)
    subtitle = args.output / 'subtitles.srt'
    if not subtitle.is_file() or subtitle.stat().st_size == 0:
        raise RuntimeError('Subtitles are missing or empty; work files were retained for retry')
    actual_duration = validate_final_video(movie, length, width, height)
    final_movie = args.output / 'final.mp4'
    os.replace(movie, final_movie)
    project['render_fingerprint'] = requested_fingerprint
    project['requested_settings'] = requested_render_settings(args)
    project['final_artifact'] = {
        'name': final_movie.name,
        'duration': actual_duration,
        'width': width,
        'height': height,
        'completed_at': datetime.now(timezone.utc).isoformat(),
    }
    if project['intermediates'] == 'keep':
        project['status'] = 'complete'
        project['cleanup'] = {'status': 'kept'}
    else:
        for scene in scenes:
            scene.pop('image', None)
            scene.pop('voice', None)
        project['status'] = 'cleaning'
        project['cleanup'] = {'status': 'pending'}
    write_json_atomic(meta, project)
    if project['status'] == 'cleaning':
        unknown = cleanup_project_artifacts(args.output, identity, (args.script, args.bgm_file))
        project['status'] = 'complete'
        project['cleanup'] = {'status': 'done', 'additional_files': len(unknown)}
        write_json_atomic(meta, project)
        if unknown:
            log(f'Cleanup preserved {len(unknown)} additional files or directories')
    log(f'VIDEO: {final_movie}\nSRT: {subtitle}\nMANIFEST: {meta}')


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,Exception) as ex:
        log(f'ERROR: {ex}');sys.exit(1)
