"""FastAPI REST server for local VoiceStudio / XTTS-v2 speech synthesis."""

import base64
import io
import os
import re
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

DEFAULT_MODELS_DIR = Path('/home/rails/Work/AI-Models/voicestudio/checkpoints/XTTS-v2')
VOICES_DIR = Path('/home/rails/Work/AI-Models/voicestudio/voices')

app = FastAPI(title="VoiceStudio Local TTS Server", version="1.0.0")

_model = None
_config = None

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
    """Converts positive or negative integers into natural Hindi words."""
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


def _apply_xtts_compatibility_patches():
    """Bridges newer transformers library with XTTS-v2's custom GPT2InferenceModel."""
    try:
        from transformers import GenerationConfig
        from transformers.models.gpt2.modeling_gpt2 import GPT2LMHeadModel
        from TTS.tts.layers.xtts.gpt_inference import GPT2InferenceModel

        GPT2InferenceModel.prepare_inputs_for_generation = GPT2LMHeadModel.prepare_inputs_for_generation
        GPT2InferenceModel.can_generate = classmethod(lambda cls: True)
        GPT2InferenceModel._validate_model_class = lambda self: None
        GPT2InferenceModel._validate_model_kwargs = lambda self, model_kwargs: None

        orig_init = GPT2InferenceModel.__init__

        def patched_init(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            if getattr(self, "generation_config", None) is None:
                self.generation_config = GenerationConfig.from_model_config(self.config)

        GPT2InferenceModel.__init__ = patched_init

        orig_forward = GPT2InferenceModel.forward

        def patched_forward(self, input_ids=None, past_key_values=None, *args, **kwargs):
            if past_key_values is not None and isinstance(past_key_values, tuple) and not hasattr(past_key_values, "get_seq_length"):
                class TupleCacheWrapper(tuple):
                    def get_seq_length(self, layer_idx=0):
                        return self[layer_idx][0].shape[-2]

                past_key_values = TupleCacheWrapper(past_key_values)
            return orig_forward(self, input_ids=input_ids, past_key_values=past_key_values, *args, **kwargs)

        GPT2InferenceModel.forward = patched_forward

        # Patch XTTS tokenizer._expand_number to prevent num2words NotImplementedError on Hindi
        import TTS.tts.layers.xtts.tokenizer as xtts_tok
        orig_expand_number = getattr(xtts_tok, "_expand_number", None)

        def patched_expand_number(m, lang="en"):
            if lang in ("hi", "hin", "hindi"):
                try:
                    return number_to_hindi(int(m.group(0)))
                except Exception:
                    return m.group(0)
            if orig_expand_number is not None:
                try:
                    return orig_expand_number(m, lang=lang)
                except (NotImplementedError, KeyError):
                    return m.group(0)
            return m.group(0)

        xtts_tok._expand_number = patched_expand_number

    except Exception as ex:
        print(f"[VoiceStudio] Warning: could not apply transformers compatibility patches: {ex}")


def get_xtts_model():
    global _model, _config
    if _model is not None:
        return _model, _config

    _apply_xtts_compatibility_patches()

    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    checkpoint_dir = Path(os.getenv('VOICESTUDIO_CHECKPOINTS', str(DEFAULT_MODELS_DIR)))
    config_file = checkpoint_dir / 'config.json'

    if not config_file.is_file():
        raise RuntimeError(f'XTTS config file not found at {config_file}')

    print(f'[VoiceStudio] Loading XTTS-v2 model from {checkpoint_dir} ...')
    config = XttsConfig()
    config.load_json(str(config_file))
    model = Xtts.init_from_config(config)
    model.load_checkpoint(config, checkpoint_dir=str(checkpoint_dir), use_deepspeed=False)
    model.to("cpu")

    if hasattr(model, "gpt") and hasattr(model.gpt, "gpt_inference"):
        if getattr(model.gpt.gpt_inference, "generation_config", None) is None:
            from transformers import GenerationConfig

            model.gpt.gpt_inference.generation_config = GenerationConfig.from_model_config(
                model.gpt.gpt_inference.config
            )

    print('[VoiceStudio] XTTS-v2 model loaded successfully on CPU.')

    _model = model
    _config = config
    return _model, _config


def sanitize_text_for_xtts(text: str, language: str = "hi") -> str:
    """Cleans punctuation anomalies and expands numbers in Hindi to prevent num2words errors."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text.strip())
    # Expand numeric digits into natural Hindi words if language is Hindi
    if language in ("hi", "hin", "hindi"):
        s = replace_digits_with_hindi(s)
    # Replace em-dashes and en-dashes with commas
    s = re.sub(r"[\u2013\u2014—–]", ", ", s)
    # Replace colons and semicolons with commas
    s = re.sub(r"[:;]", ", ", s)
    # Fix stacked punctuation: ?। -> ?, !। -> !, .. -> .
    s = re.sub(r"\?[।\.]+", "?", s)
    s = re.sub(r"![।\.]+", "!", s)
    s = re.sub(r"[।\.]{2,}", "।", s)
    s = re.sub(r"\?{2,}", "?", s)
    s = re.sub(r"!{2,}", "!", s)
    # Ensure space after punctuation if followed by text
    s = re.sub(r"([,!।\?])(?=[^\s\d])", r"\1 ", s)
    # Collapse multiple spaces
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def split_long_clause(text: str, max_words: int = 20) -> list[str]:
    """Splits long sentences into natural sub-clauses to stay within XTTS-v2 attention buffer."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    pattern = r"([,।!\?]| और | लेकिन | कि | क्योंकि | परंतु | and | but | because | that )"
    tokens = re.split(pattern, text)
    chunks = []
    current = ""
    for tok in tokens:
        if not tok:
            continue
        test_chunk = current + tok
        if len(test_chunk.split()) > max_words and current.strip():
            chunks.append(current.strip())
            current = tok.lstrip()
        else:
            current = test_chunk
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]


def apply_audio_tempo(pcm_bytes: bytes, sample_rate: int, tempo: float) -> bytes:
    """Applies pitch-preserving time stretching via FFmpeg atempo without vocoder pause distortion."""
    if abs(tempo - 1.0) < 0.02:
        return pcm_bytes
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as in_f:
        in_path = in_f.name
        in_f.write(pcm_bytes)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
        out_path = out_f.name

    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", in_path,
            "-filter:a", f"atempo={tempo:.2f}",
            "-ar", str(sample_rate),
            "-ac", "1",
            "-f", "wav",
            out_path,
        ]
        subprocess.run(cmd, check=True)
        return Path(out_path).read_bytes()
    except Exception as ex:
        print(f"[VoiceStudio] Warning: FFmpeg atempo tempo adjustment failed: {ex}. Using original audio.")
        return pcm_bytes
    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                os.unlink(p)


class SynthRequest(BaseModel):
    text: str
    language: str = "en"
    mood: Optional[str] = "neutral"
    speed: Optional[float] = None
    voice_sample: Optional[str] = None
    voice_sample_base64: Optional[str] = None
    output_format: Optional[str] = "wav"
    sample_rate: Optional[int] = 24000


@app.get("/health")
@app.get("/api/status")
def health():
    return {
        "status": "ok",
        "service": "VoiceStudio",
        "model": "XTTS-v2",
        "ready": True,
    }


@app.post("/api/synthesize")
@app.post("/synthesize")
def synthesize(req: SynthRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Narration text cannot be empty")

    model, config = get_xtts_model()

    # Determine reference speaker audio
    ref_audio_path = None
    temp_sample_file = None

    try:
        if req.voice_sample_base64:
            audio_bytes = base64.b64decode(req.voice_sample_base64)
            temp_sample_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_sample_file.write(audio_bytes)
            temp_sample_file.flush()
            temp_sample_file.close()
            ref_audio_path = temp_sample_file.name
        elif req.voice_sample and Path(req.voice_sample).is_file():
            ref_audio_path = str(Path(req.voice_sample).resolve())
        else:
            # Fallback to default voice sample (prefer channel voice if available)
            channel_sample = VOICES_DIR / "my_channel_voice.wav"
            if channel_sample.is_file():
                ref_audio_path = str(channel_sample)
            else:
                default_samples = sorted(VOICES_DIR.glob("*.wav"))
                if default_samples:
                    ref_audio_path = str(default_samples[0])
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="No voice sample provided and no default reference audio found in voices directory",
                    )

        # Compute speaker latents
        gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
            audio_path=[ref_audio_path]
        )

        # Synthesize audio
        # Mapping language: 'hi' or 'en'
        lang = "hi" if req.language in ("hi", "hin", "hindi") else "en"

        # 1. Clean text anomalies, expand numbers, and split long sentences into natural sub-clauses
        sanitized_text = sanitize_text_for_xtts(req.text, language=lang)
        sub_chunks = split_long_clause(sanitized_text, max_words=20)

        # 2. Stable sampling parameters to eliminate word shouting / over-expression
        temperature = float(os.getenv("VOICESTUDIO_TEMPERATURE", "0.50"))
        top_p = float(os.getenv("VOICESTUDIO_TOP_P", "0.85"))
        top_k = int(os.getenv("VOICESTUDIO_TOP_K", "50"))
        repetition_penalty = float(os.getenv("VOICESTUDIO_REP_PENALTY", "6.0"))

        collected_waves = []
        for idx, chunk in enumerate(sub_chunks):
            if not chunk.strip():
                continue
            chunk_out = model.inference(
                text=chunk,
                language=lang,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                speed=1.0,  # Native 1.0x to avoid vocoder pause-stretching gaps
            )
            chunk_wav = np.array(chunk_out["wav"])
            chunk_wav = (chunk_wav * 32767).clip(-32768, 32767).astype(np.int16)
            collected_waves.append(chunk_wav)
            if idx < len(sub_chunks) - 1:
                # Add natural 60ms micro-pause between sub-clauses
                collected_waves.append(np.zeros(int(24000 * 0.06), dtype=np.int16))

        if not collected_waves:
            raise HTTPException(status_code=400, detail="Synthesis produced no audio")

        full_wav = np.concatenate(collected_waves)

        out_buffer = io.BytesIO()
        with wave.open(out_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(full_wav.tobytes())

        wav_bytes = out_buffer.getvalue()

        # 3. Apply desired tempo / speed post-processing via FFmpeg atempo (artifact-free)
        speed = float(req.speed) if req.speed is not None else float(os.getenv("VOICESTUDIO_SPEED", "1.15"))
        if abs(speed - 1.0) >= 0.02:
            wav_bytes = apply_audio_tempo(wav_bytes, 24000, speed)

        return Response(content=wav_bytes, media_type="audio/wav")

    finally:
        if temp_sample_file and os.path.exists(temp_sample_file.name):
            os.unlink(temp_sample_file.name)
