# StoryClip CLI — Terminal Photo-Video Generator

Standalone **Python CLI**, no frontend or StoryWeaver dependency.

**Flow:** idea → Gemini story → sentence-aware ~10-second scenes → Gemini Pexels photo queries → themed fallback photos → Edge/Gemini/local Indic Parler narration → scene-timed SRT → Jamendo candidate discovery/rights gate → FFmpeg landscape MP4 with still-photo crossfades.

## Requirements
- Python 3.12+
- FFmpeg and FFprobe (`sudo apt install ffmpeg`)
- API keys from Google AI Studio, Pexels and (optional) Jamendo.
- Internet access for LLM, images, speech and music. Actual usage depends on provider quotas and account restrictions.
- For `--tts indic-parler`, a running StoryWeaver Indic Parler service. Its verified API accepts Hindi only and returns a raw `audio/wav` body from `POST /synthesize`.

## Installation
```bash
cd storyclip_cli
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GEMINI_API_KEY, PEXELS_API_KEY and JAMENDO_CLIENT_ID
```

## Run
```bash
# Omit --output to use the story-name slug returned in Gemini's JSON response:
python video_cli.py --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --tts edge

# See planned scenes/queries first, without fetching media:
python video_cli.py --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --dry-run --output outputs/lighthouse

# Use a NEW output folder if you want a fresh script. Same folder resumes the saved plan.
python video_cli.py --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --tts edge --output outputs/lighthouse

# Script input / Hindi voice:
python video_cli.py --script my_hindi_script.txt --language hi --tts auto --output outputs/hindi_story

# Hindi storytelling with the local Indic Parler service and Rani voice:
python video_cli.py --script my_hindi_script.txt --language hi \
  --tts indic-parler --indic-url http://127.0.0.1:8765 --indic-voice Rani \
  --skip-music --output outputs/hindi_rani

# Force theme/music keywords:
python video_cli.py --script script.txt --language en --theme 'dark foggy forest' --music-mood 'horror,suspense' --output outputs/horror

# Disallow Jamendo network calls entirely:
python video_cli.py --script script.txt --language en --skip-music --output outputs/no_bgm

# Only if you have separately secured synchronization AND commercial rights:
python video_cli.py --script script.txt --language en --music-rights-confirmed --output outputs/licensed
```

> When `--output` is omitted, idea generation requests an `output_slug` in the existing Gemini JSON response and writes to `outputs/<story-name>`. Script input uses the script filename. The source fingerprint is saved in `project.json`; rerunning with the same idea/script, language, duration, theme, and music mood automatically finds and resumes that folder without regenerating the story. An explicit `--output` always overrides automatic naming.

## Files
`output/final.mp4`, `output/subtitles.srt`, `output/script.txt` (on fresh projects), `output/project.json`, `output/images/`, `output/voice/`, and intermediate clips. The SRT is **separate**, not burned into the video.

## Important behavior
- Scene boundaries respect sentence endings as much as feasible. One scene can be longer or shorter than 10 s. Scene **actual durations** come from synthesized voice audio.
- Subtitles use **one scene per cue**, aligned to the start/end of that scene's speech segment; they are not word-level alignment.
- Images are Pexels results with up to 8 theme-matched local fallback photos. Pexels attribution links/photographer names are in `project.json`. Respect Pexels API attribution requirements in any application interface.
- Jamendo *searches and lists* candidate instrumental tracks but by default **does not download or mix them**, even if `audiodownload_allowed=true`. Artist download permission is distinct from a license to synchronize or commercially use a track in a video. With `--music-rights-confirmed`, terminal requires an additional explicit acknowledgement of separately obtained rights; this is a manual gate, not automated legal validation. Music credits are recorded in `music/credits.json`. Without approved rights the video is narration-only.
- Edge TTS is an unofficial network-dependent service, not a guaranteed production API. Gemini TTS may have changing models/quotas; `--tts auto` attempts Gemini then falls back to Edge. Defaults to Edge for minimal API cost.
- `--tts indic-parler` is Hindi-only and strict: it never falls back to Gemini or Edge. Supported voices are `Divya`, `Rohit`, `Aman`, and `Rani`. If the local service fails, the command reports that error; successfully completed scene audio remains cached for a retry with the same command.
- Narration cache entries are keyed by the complete scene text, requested provider, voice, language, mood, endpoint/model settings, and output settings. Each entry also has matching JSON metadata and WAV validation, so changing a script scene or voice cannot reuse that scene's stale audio. Old filename-only cache files are ignored.
- `--style auto` grades every scene from its existing mood: fearful scenes use the darker horror treatment, joyful scenes use brighter color, and neutral scenes remain neutral. Explicit `--style horror`, `joy`, or `neutral` overrides all scenes. Styling is local FFmpeg work and makes no additional LLM or media-provider calls.
- By default, images alternate zoom-in and zoom-out regardless of their style. Use `--no-zoom` only when you want to disable this motion.
- Image collection, narration, and scene rendering print dependency-free progress bars with percentages and estimated remaining time. Cached work is included as immediately completed progress.
- Generates 1280×720 H.264, 24 fps (configurable FPS), with image crossfades. Video length and audio length can differ slightly due to frame rounding. Runtime and RAM scale with number of scenes; for very long projects, the xfade filter can be memory-heavy.
- No separate forced-alignment service. The output SRT is **scene-level accurate** based on audio clip durations, but not word-level accurate.
- Do not expose `.env` or provider API secrets. API search terms and script text go to third-party providers.

## Current limitations
- Rendering uses a single FFmpeg `xfade` graph; on 10+ minute videos with many photos it can consume substantial RAM. Split long projects into segments if needed.
- Several approved BGM files can be concatenated as a playlist; there is no automatic per-scene mood-switching or beat matching yet. Music ducking is a fixed low-volume mix, not dialogue-detection sidechain compression.
- No automated commercial-rights verification can be inferred from a Jamendo CC license alone.
- The client cannot detect a server-side model or weights change made in place behind the same Indic Parler URL. Use a new output folder (or change the endpoint) if that service implementation changes and fresh speech is required.
- No real cloud API integration test was run in this environment, because your keys are not available here. A local smoke test validates basic rendering and SRT functions.
