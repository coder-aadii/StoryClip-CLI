# StoryClip CLI Commands

Run commands from the repository folder.

## First-time setup

```bash
cd ~/Work/Github-Repos/storyclip_cli
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
nano .env
```

Add your `GEMINI_API_KEY` and `PEXELS_API_KEY` to `.env`. `./run_video.sh` uses the virtual environment automatically; you do not need to activate it for each run.

## Create a video from an idea

```bash
./run_video.sh \
  --idea "Why the sky looks blue" \
  --language hi \
  --minutes 2 \
  --tts edge-male-energetic \
  --outro facts-sun-le-bhai \
  --output outputs/blue-sky
```

Choose `--language hi` or `--language en`. `--minutes` sets an approximate target length. Use `--outro default` for the standard outro or `--outro facts-sun-le-bhai` for the branded outro.

The output defaults to 720p. Set `--resolution 1080p` or `--resolution 2160p` for UHD.

## Create a video from a script

```bash
./run_video.sh \
  --script my_script.txt \
  --language hi \
  --intro-text "तो क्या हाल है दोस्तों! आज के वीडियो में हम जानेंगे कि सोना इतना कीमती क्यों है। चलिए शुरू करते हैं।" \
  --tts edge-male \
  --output outputs/my-video
```

Use `--title` to supply a clean spoken title, or `--intro-text` for custom spoken intro narration. For Hindi scripts without `--title`, the intro automatically generates a natural topic title and avoids speaking raw code/ASCII filenames.

Use either `--idea` or `--script`.

## Minimalist whiteboard / stickman doodle videos (Cloudflare FLUX)

Generate YouTube whiteboard explainer stickman and doodle illustrations using Cloudflare Workers AI FLUX.1 [schnell] instead of stock photography:

```bash
./run_video.sh \
  --script my_script.txt \
  --language hi \
  --image-provider cloudflare-flux \
  --tts edge-male \
  --output outputs/doodle-explainer
```

- Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in `.env`.
- Free tier includes 10,000 Neurons/day (~35–40 images daily at $0).
- Zero local GPU stress (serverless cloud GPUs generate each scene in ~2 seconds).
- `flux` and `cloudflare` are accepted as convenient aliases for `--image-provider cloudflare-flux`.

Generate vertical YouTube Shorts (1080x1920, strictly under 1 minute, rapid 3–4s 1080p video B-roll cuts, soothing BGM, micro-outro):

```bash
./run_video.sh \
  --idea "3 mind blowing facts about the deep ocean" \
  --language hi \
  --format shorts \
  --tts edge-male \
  --output outputs/ocean-facts-shorts
```

- `--format shorts` (or `--format vertical`) automatically sets:
  - Resolution: `1080x1920` vertical 9:16 portrait.
  - Image provider: `pexels-video` (high-definition 1080p motion video clips from Pexels Video API; camera motion is preserved without synthetic zoom).
  - B-roll visual cuts: Rapid 3.2–4.0 second dynamic video cuts decoupled from sentence pauses.
  - Duration: Strictly under 60 seconds (target 48–54s).
  - Outro: `shorts` compact 4-second micro call-to-action with blurred backdrop.
  - BGM: Automatically selects theme-matched soothing/relaxing instrumental music from `storage/BGM/`.

Use `--tts gemini` (or `--tts puck`) for Puck narration (upbeat and lively male voice preset) in continuous chunks targeting 60–90 seconds. Images still switch at short visual scene boundaries (roughly 10 seconds), with local alignment setting their actual timing and SRT cues. Intro/outro remain separate. `--tts auto` and other providers retain per-scene synthesis. Choose a fresh `--output` directory for an older Gemini project; its saved scene layout will be rejected without modifying its media.

Before using Gemini narration, install the local alignment dependencies:

```bash
.venv/bin/python -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements-alignment.txt
```

The first alignment downloads a multilingual Whisper `small` model and runs on CPU. Set `STORYCLIP_ALIGN_MODEL=medium` for a larger model if needed (more memory, download, and processing time). Audio is processed locally. An alignment failure retains synthesized chunks; retrying reuses them. `--dry-run` does not load the alignment model, but still calls Gemini for scene planning. Add `--keep-intermediates` to retain both speech and alignment caches after a successful render.

Gemini TTS automatically cools down and retries up to four times after a `429 RESOURCE_EXHAUSTED` response. The retry delay reported by Google is honored with a small safety buffer. If the available quota remains exhausted, rerun the same command later; completed scene audio is reused.

After a verified successful render, the output keeps `final.mp4`, `subtitles.srt`, and `project.json`. Add `--keep-intermediates` before success to retain generated scripts, images, speech and music caches, and clips. Failed runs keep their work files. Extra files you put in the output directory are preserved.

Rendering caches one finished video segment per scene, including its avatar and incoming crossfade. A retry reuses valid segments, and the final video join copies their H.264 streams without another video encode. Scene boundaries are rounded on the cumulative narration timeline to avoid timing drift.

## Music

Music is enabled by default. Choose a mood by adding `--music-mood "nature,documentary"` to the video command. To disable music, add `--skip-music`.

## FFmpeg crossfades

Use `./run_video.sh` for every video command. It checks for `xfade`, selects `.local-bin/ffmpeg` when available, and launches the virtual environment automatically. If no FFmpeg with `xfade` is installed, run this one-time setup:

Crossfade batches print their segment count, progress, and ETA while FFmpeg encodes them.

```bash
.venv/bin/python -m pip install imageio-ffmpeg
mkdir -p .local-bin
ln -sf "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .local-bin/ffmpeg
```

## Resume a video

Rerun the same command with the same `--output` path and the same idea or script. An incomplete run reuses available work. A completed run with matching settings reports the existing video without provider calls. Use a new output directory if you change TTS, music, resolution, FPS, style, zoom, or other render settings. Passing `--keep-intermediates` after a cleaned run cannot restore deleted files.

## VoiceStudio (Local TTS & Voice Cloning)

Run VoiceStudio locally for unlimited offline speech synthesis and zero-shot voice cloning. Model checkpoints and caches reside in `/home/rails/Work/AI-Models`.

Start the service daemon:
```bash
./services/voicestudio/run_server.sh
```

Generate video with standard VoiceStudio narrator:
```bash
./run_video.sh \
  --idea "The mysteries of ancient deep ocean trenches" \
  --language en \
  --tts voicestudio \
  --output outputs/ocean-trenches
```

Zero-shot voice cloning using a reference WAV sample:
```bash
./run_video.sh \
  --script my_story.txt \
  --language hi \
  --tts voicestudio \
  --voice-sample "/home/rails/Work/AI-Models/voicestudio/voices/sample.wav" \
  --output outputs/cloned-story
```

The finished video is saved as `outputs/<name>/final.mp4`; subtitles are in `outputs/<name>/subtitles.srt`.
