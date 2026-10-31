"""Local forced alignment of known narration to short visual scene boundaries."""
import hashlib
import importlib.metadata
import math
import os
import unicodedata

from media_runtime import log

ALIGNMENT_VERSION = 1
_MODELS = {}


def require_alignment():
    try:
        import stable_whisper
    except ImportError as ex:
        raise RuntimeError(
            'Gemini scene alignment requires stable-ts. Install with '
            '.venv/bin/python -m pip install -r requirements-alignment.txt'
        ) from ex
    return stable_whisper


def alignment_settings():
    return {'version': ALIGNMENT_VERSION, 'model': os.getenv('STORYCLIP_ALIGN_MODEL', 'small'),
            'device': 'cpu'}


def normalized(text):
    # Preserve Devanagari vowel marks; ignore whitespace and punctuation only.
    return ''.join(c.lower() for c in unicodedata.normalize('NFC', text)
                   if unicodedata.category(c)[0] in ('L', 'M', 'N'))


def scene_boundaries(words, scenes, duration):
    """Validate complete alignment before deriving any scene timings."""
    if not words or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Empty audio or word alignment')
    expected = [normalized(scene['text']) for scene in scenes]
    if not all(expected):
        raise ValueError('Scene has no alignable text')
    actual, starts, offset, previous_end = [], {}, 0, 0.0
    for word in words:
        token = normalized(word['word'])
        start, end = float(word['start']), float(word['end'])
        if (not math.isfinite(start) or not math.isfinite(end) or start < previous_end - 0.02
                or start < 0 or end <= start or end > duration + 0.1):
            raise ValueError('Invalid or overlapping word timestamps')
        previous_end = end
        if token:
            starts[offset] = start
            offset += len(token)
            actual.append(token)
    if ''.join(actual) != ''.join(expected):
        raise ValueError('Alignment did not cover the complete script')
    boundaries, offset = [0.0], 0
    for text in expected[:-1]:
        offset += len(text)
        if offset not in starts:
            raise ValueError('Scene boundary is missing from aligned words')
        boundaries.append(starts[offset])
    boundaries.append(duration)
    if any(b <= a for a, b in zip(boundaries, boundaries[1:])):
        raise ValueError('Alignment produced an empty scene')
    return boundaries


def align_scenes(audio, scenes, language):
    from tts_service import audio_duration, cache_key, write_json_atomic
    import json

    duration = audio_duration(audio)
    if len(scenes) == 1:
        return [0.0, duration]
    settings = alignment_settings()
    identity = {**settings, 'language': language, 'texts': [s['text'] for s in scenes],
                'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest(),
                'stable_ts': importlib.metadata.version('stable-ts')}
    key = cache_key(identity)
    cache = audio.parent / f'alignment_{key}.json'
    try:
        saved = json.loads(cache.read_text(encoding='utf-8'))
        if saved.get('identity') == identity:
            return scene_boundaries(saved['words'], scenes, duration)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    log(f'Aligning {len(scenes)} visual scenes to Gemini audio locally')
    try:
        backend = require_alignment()
        model_name = settings['model']
        if model_name not in _MODELS:
            import torch
            # Avoid CPU oversubscription on machines exposing many logical cores.
            torch.set_num_threads(min(4, torch.get_num_threads()))
            log(f'Loading local alignment model {model_name}; first use may download model weights')
            _MODELS[model_name] = backend.load_model(model_name, device='cpu')
        result = _MODELS[model_name].align(
            str(audio), '\n'.join(s['text'] for s in scenes), language=language,
            verbose=None, regroup=False, original_split=True, token_step=0,
        )
        if result is None:
            raise ValueError('Aligner returned no timestamps')
        words = [{'word': word.word, 'start': word.start, 'end': word.end}
                 for segment in result.segments for word in segment.words]
        boundaries = scene_boundaries(words, scenes, duration)
        write_json_atomic(cache, {'identity': identity, 'words': words})
        return boundaries
    except Exception as ex:
        raise RuntimeError(
            'Local Gemini speech alignment failed; synthesized audio is cached. '
            f'Check the alignment dependencies/model, then retry. Cause: {ex}'
        ) from ex
