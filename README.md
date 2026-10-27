# StoryClip CLI — Terminal Photo-Video Generator

Standalone **Python CLI**, no frontend or StoryWeaver dependency.

**Flow:** idea → Gemini story → sentence-aware ~10-second scenes → Gemini Pexels photo queries → themed fallback photos → Edge/Gemini narration → scene-timed SRT → Jamendo candidate discovery/rights gate → FFmpeg landscape MP4 with still-photo crossfades.

## Requirements
- Python 3.12+
- FFmpeg and FFprobe (`sudo apt install ffmpeg`)
- API keys from Google AI Studio, Pexels and (optional) Jamendo.
- Internet access for LLM, images, speech and music. Actual usage depends on provider quotas and account restrictions.

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
# See planned scenes/queries first, without fetching media:
python video_cli.py --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --dry-run --output outputs/lighthouse

# Use a NEW output folder if you want a fresh script. Same folder resumes the saved plan.
python video_cli.py --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --tts edge --output outputs/lighthouse

# Script input / Hindi voice:
python video_cli.py --script my_hindi_script.txt --language hi --tts auto --output outputs/hindi_story

# Force theme/music keywords:
python video_cli.py --script script.txt --language en --theme 'dark foggy forest' --music-mood 'horror,suspense' --output outputs/horror

# Disallow Jamendo network calls entirely:
python video_cli.py --script script.txt --language en --skip-music --output outputs/no_bgm

# Only if you have separately secured synchronization AND commercial rights:
python video_cli.py --script script.txt --language en --music-rights-confirmed --output outputs/licensed
```

> `--dry-run` saves script/scene plan to `project.json`. Running again with the same `--output` continues the saved plan, ignoring changes to idea/script. To regenerate, select a new output folder.

## Files
`output/final.mp4`, `output/subtitles.srt`, `output/script.txt` (on fresh projects), `output/project.json`, `output/images/`, `output/voice/`, and intermediate clips. The SRT is **separate**, not burned into the video.

## Important behavior
- Scene boundaries respect sentence endings as much as feasible. One scene can be longer or shorter than 10 s. Scene **actual durations** come from synthesized voice audio.
- Subtitles use **one scene per cue**, aligned to the start/end of that scene's speech segment; they are not word-level alignment.
- Images are Pexels results with up to 8 theme-matched local fallback photos. Pexels attribution links/photographer names are in `project.json`. Respect Pexels API attribution requirements in any application interface.
- Jamendo *searches and lists* candidate instrumental tracks but by default **does not download or mix them**, even if `audiodownload_allowed=true`. Artist download permission is distinct from a license to synchronize or commercially use a track in a video. With `--music-rights-confirmed`, terminal requires an additional explicit acknowledgement of separately obtained rights; this is a manual gate, not automated legal validation. Music credits are recorded in `music/credits.json`. Without approved rights the video is narration-only.
- Edge TTS is an unofficial network-dependent service, not a guaranteed production API. Gemini TTS may have changing models/quotas; `--tts auto` attempts Gemini then falls back to Edge. Defaults to Edge for minimal API cost.
- Generates 1280×720 H.264, 24 fps (configurable FPS), with image crossfades. Video length and audio length can differ slightly due to frame rounding. Runtime and RAM scale with number of scenes; for very long projects, the xfade filter can be memory-heavy.
- No separate forced-alignment service. The output SRT is **scene-level accurate** based on audio clip durations, but not word-level accurate.
- Do not expose `.env` or provider API secrets. API search terms and script text go to third-party providers.

## Current limitations
- Rendering uses a single FFmpeg `xfade` graph; on 10+ minute videos with many photos it can consume substantial RAM. Split long projects into segments if needed.
- Several approved BGM files can be concatenated as a playlist; there is no automatic per-scene mood-switching or beat matching yet. Music ducking is a fixed low-volume mix, not dialogue-detection sidechain compression.
- No automated commercial-rights verification can be inferred from a Jamendo CC license alone.
- No real cloud API integration test was run in this environment, because your keys are not available here. A local smoke test validates basic rendering and SRT functions.
