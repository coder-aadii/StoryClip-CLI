# StoryClip CLI — Terminal Photo-Video Generator

Standalone **Python CLI**, no frontend or StoryWeaver dependency.

**Flow:** idea → Gemini script → mandatory narrated intro and outro → sentence-aware ~10-second scenes → Gemini Pexels photo queries → themed fallback photos → Edge/Gemini/local Indic Parler narration → expression-stickman avatar → scene-timed SRT → bundled theme-matched BGM (or optional rights-cleared Jamendo track) → FFmpeg landscape MP4 with still-photo crossfades.

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
# Edit .env and set GEMINI_API_KEY and PEXELS_API_KEY
# JAMENDO_CLIENT_ID is optional; bundled BGM works without it.
```

## Run
Use `./run_video.sh` to launch the CLI. It selects the local FFmpeg build with `xfade` when available and uses the project's virtual environment.

```bash
# Omit --output to use the story-name slug returned in Gemini's JSON response:
./run_video.sh --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --tts edge

# Select the branded Fact Sun Le Bhai outro:
./run_video.sh --idea 'Why the sky looks blue' --language hi --minutes 2 \
  --outro facts-sun-le-bhai --tts edge

# See planned scenes/queries first, without fetching media:
./run_video.sh --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --dry-run --output outputs/lighthouse

# Use a NEW output folder if you want a fresh script. Same folder resumes the saved plan.
./run_video.sh --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --tts edge --output outputs/lighthouse

# Keep scripts, downloaded images, narration caches, music, and render clips after success:
./run_video.sh --idea 'A suspenseful mystery in an abandoned lighthouse' --language en --minutes 2 --tts edge --keep-intermediates --output outputs/lighthouse-kept

# Script input / Hindi voice:
./run_video.sh --script my_hindi_script.txt --language hi --tts auto --output outputs/hindi_story

# Hindi storytelling with the local Indic Parler service and Rani voice:
./run_video.sh --script my_hindi_script.txt --language hi \
  --tts indic-parler --indic-url http://127.0.0.1:8765 --indic-voice Rani \
  --skip-music --output outputs/hindi_rani

# Force theme/music keywords:
./run_video.sh --script script.txt --language en --theme 'dark foggy forest' --music-mood 'horror,suspense' --output outputs/horror

# Disable all BGM:
./run_video.sh --script script.txt --language en --skip-music --output outputs/no_bgm

# Use a local track only if you have suitable video-sync rights:
./run_video.sh --script script.txt --language en --bgm-file storage/horror_theme_1.mp3 --output outputs/horror
```

> Every video includes a narrated intro and outro with the avatar. `--outro default` is the generic like/subscribe sign-off; `--outro facts-sun-le-bhai` uses the branded channel call-to-action. The selected profile is part of the project identity. When `--output` is omitted, idea generation requests an `output_slug` in the existing Gemini JSON response and writes to `outputs/<story-name>`. Script input uses the script filename. Rerunning with the same source options and outro profile automatically finds and resumes that folder.

## Files
After a successful render, a normal output contains only `final.mp4`, `subtitles.srt`, and `project.json`. The SRT is **separate**, not burned into the video. The manifest retains narration, scene timing, avatar plan, image attribution, music selection, render settings fingerprint, and final video details; local paths to removed images and speech are cleared. `--keep-intermediates` retains `script.txt`, `video_script.txt`, `avatar_plan.json`, downloaded images, speech and music caches, and render clips. Unknown user-added files are preserved, so a customized output directory may contain more than the three standard files.

## Important behavior
- Scene boundaries respect sentence endings as much as feasible. One scene can be longer or shorter than 10 s. Scene **actual durations** come from synthesized voice audio.
- Intro and outro are mandatory narrated scenes. Idea-generated main content reserves time for both bookends inside the requested `--minutes`; supplied scripts remain the main content and have the bookends added around them.
- The avatar appears throughout: larger and centered in both dimensions during the intro/outro, then starts on the left and alternates sides during content. Pose blocks follow roughly 10-second narration scenes, with a smooth slide between sides and image-space scoring to avoid especially busy areas. The avatar is composited into each scene clip before bounded crossfade assembly.
- The outro overlays the supplied combined Like, Comment, Share, and Subscribe image in front of the avatar's lower legs. The image is scaled proportionally and appears only during the outro.
- Subtitles use **one scene per cue**, aligned to the start/end of that scene's speech segment; they are not word-level alignment.
- Images are Pexels results with up to 8 theme-matched local fallback photos. Pexels attribution links/photographer names are in `project.json`. Respect Pexels API attribution requirements in any application interface.
- Bundled science, horror, mystery, nature, and technology tracks in `storage/` are selected by project theme and mixed at a low default linear volume of `0.08` (about −22 dB), with a short crossfade when looping and fades at the beginning/end. Set `--bgm-volume` from `0` to `1` to adjust it, or use `--skip-music` to disable all BGM. Track sources and license notes are in [`storage/ATTRIBUTION.md`](storage/ATTRIBUTION.md).
- Jamendo *searches and lists* candidate instrumental tracks but does not download or mix them unless `--music-rights-confirmed` is supplied. Artist download permission is distinct from a license to synchronize or commercially use a track in a video. The terminal also requires an explicit acknowledgement of separately obtained rights; this is a manual gate, not automated legal validation. If no Jamendo track is approved, the bundled theme track is used instead.
- Edge TTS is an unofficial network-dependent service, not a guaranteed production API. `--tts edge-male` uses Madhur for Hindi (Guy for English) at pitch `-10Hz` and rate `+18%`; `--tts edge-male-energetic` uses the same voices at pitch `-2Hz` and rate `+35%`. `--tts edge-female` uses Swara for Hindi (Aria for English) at pitch `-3Hz` and rate `+20%`; the requested `edge-femail` spelling and the existing `edge` value are aliases for this female profile. `--tts auto` attempts Gemini then falls back to the female Edge profile. Defaults to Edge for minimal API cost.
- `--tts indic-parler` is Hindi-only and strict: it never falls back to Gemini or Edge. Supported voices are `Divya`, `Rohit`, `Aman`, and `Rani`. If the local service fails, the command reports that error; successfully completed scene audio remains cached for a retry with the same command.
- Narration cache entries are keyed by the complete scene text, requested provider, voice, language, mood, endpoint/model settings, and output settings. Each entry also has matching JSON metadata and WAV validation, so changing a script scene or voice cannot reuse that scene's stale audio. Old filename-only cache files are ignored.
- All TTS providers pass through the same narration preparation step. It trims only sustained near-silence at each clip edge, retaining about 90 ms at the start and end while preserving pauses within the clip. Versioned trimmed WAVs are derived from the original TTS cache, so rerunning an existing project recalculates narration timing, SRT, and visual transitions without synthesizing speech again.
- `--style auto` grades every scene from its existing mood: fearful scenes use the darker horror treatment, joyful scenes use brighter color, and neutral scenes remain neutral. Explicit `--style horror`, `joy`, or `neutral` overrides all scenes. Styling is local FFmpeg work and makes no additional LLM or media-provider calls.
- By default, images alternate centered zoom-in and zoom-out motion across each full scene regardless of their style. The zoom uses fractional-pixel sampling to keep the image centered without the crop-position shake. Use `--no-zoom` only when you want to disable this motion.
- Image collection, narration, and scene rendering print dependency-free progress bars with percentages and estimated remaining time. Cached work is reported as reused.
- Failed or interrupted runs retain work files for resuming. After the final video and subtitles are validated, default cleanup removes only recognized generated files; `--keep-intermediates` must be chosen before successful cleanup, including when rerunning an older project. A completed project rerun with the same source and settings returns its existing files without provider calls. Changed TTS, music, or render settings require a new output directory; adding `--keep-intermediates` after cleanup cannot restore deleted caches.
- Generates 1280×720 H.264 by default, 24 fps (configurable FPS), with optional 1080p and 2160p UHD output via `--resolution`. Scene zoom renders at the selected output resolution with fractional-pixel sampling. Each scene segment is encoded with its avatar and incoming crossfade, then compatible segments are joined without re-encoding video. A render job processes at most two scene visuals at a time, so peak filter memory does not grow with scene count. Long UHD renders still require substantial disk space and CPU time.
- No separate forced-alignment service. The output SRT is **scene-level accurate** based on audio clip durations, but not word-level accurate.
- Do not expose `.env` or provider API secrets. API search terms and script text go to third-party providers.

## Current limitations
- One theme-matched BGM track plays across the full video. There is no automatic per-scene mood-switching or beat matching yet. Music ducking is a fixed low-volume mix, not dialogue-detection sidechain compression.
- No automated commercial-rights verification can be inferred from a Jamendo CC license alone.
- The client cannot detect a server-side model or weights change made in place behind the same Indic Parler URL. Use a new output folder (or change the endpoint) if that service implementation changes and fresh speech is required.
- No real cloud API integration test was run in this environment, because your keys are not available here. A local smoke test validates basic rendering and SRT functions.
