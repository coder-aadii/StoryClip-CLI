```bash
cd ~/Work/Github-Repos/storyclip_cli
source .venv/bin/activate
export PATH="$PWD/.local-bin:$PATH"

python video_cli.py \
  --idea "एक डरावनी हवेली की कहानी।" \
  --language hi \
  --minutes 5 \
  --tts indic-parler \
  --indic-voice Rani \
  --style auto \
```

# StoryClip CLI — Command Cheat Sheet

A quick reference for the **actual options in `video_cli.py`**. Run all commands from the project's root folder on Ubuntu.

## 0. Start here (each new terminal)

```bash
cd ~/Work/Github-Repos/storyclip_cli
source .venv/bin/activate
python video_cli.py --help
```

> If your prompt already shows `~/Work/Github-Repos/storyclip_cli`, **do not** `cd storyclip_cli` again. If your prompt already shows `(.venv)`, activation is unnecessary.

## 1. First-time installation

```bash
cd ~/Work/Github-Repos/storyclip_cli
python3.12 -m venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt --python .venv/bin/python --default-index https://pypi.org/simple
cp .env.example .env
nano .env
```

Set these values in `.env` (do not publish this file):

```dotenv
GEMINI_API_KEY=your_key
PEXELS_API_KEY=your_key
JAMENDO_CLIENT_ID=your_client_id
# Optional model overrides:
GEMINI_TEXT_MODEL=gemini-2.5-flash
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
# Optional local Indic Parler defaults:
INDIC_PARLER_URL=http://127.0.0.1:8765
INDIC_PARLER_VOICE=Rani
```

Useful checks:

```bash
python -m pip check
python -c "import requests, dotenv, edge_tts; print('Dependencies OK')"
ffmpeg -version | head -n 2
ffprobe -version | head -n 1
```

## 2. FFmpeg crossfade setup (your Ubuntu 20.04 FFmpeg 4.2 lacks `xfade`)

Install a separately bundled FFmpeg (keeping `/usr/bin/ffmpeg` unchanged):

```bash
uv pip install imageio-ffmpeg --python .venv/bin/python
mkdir -p .local-bin
ln -sf "$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .local-bin/ffmpeg
```

Check the bundled binary has `xfade`:

```bash
"$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" -hide_banner -filters 2>/dev/null | grep xfade
```

**Before generating videos in each new terminal**, use:

```bash
export PATH="$PWD/.local-bin:$PATH"
which ffmpeg
ffmpeg -hide_banner -filters 2>/dev/null | grep xfade
```

If the filter check is empty, **do not run the full video render yet**: that build does not support the required transition filter. `ffprobe` can remain the system-provided executable.

## 3. Generate videos from an IDEA

You can omit `--output`. Gemini already returns structured JSON for a new story; the CLI now asks that response for a short ASCII `output_slug` and creates `outputs/<story-name>` automatically:

```bash
python video_cli.py \
  --idea "A mysterious lighthouse on a stormy night" \
  --language en --minutes 2 --tts edge --skip-music
```

The chosen path is printed as `Automatic output directory: ...` and saved in `project.json`. Repeating the same source options finds that manifest by fingerprint and resumes it without another story-generation call. Pass `--output` whenever you want to override the generated folder name.

**Hindi, 1-minute test, free Edge TTS, no Jamendo requests:**

```bash
python video_cli.py \
  --idea "एक रहस्यमयी जंगल में छिपा हुआ प्राचीन मंदिर" \
  --language hi --minutes 1 --tts edge --skip-music \
  --output outputs/hindi_test
```

**Hindi, 5-minute target, Gemini narration with Edge fallback:**

```bash
python video_cli.py \
  --idea "एक रहस्यमयी जंगल में छिपा हुआ प्राचीन मंदिर" \
  --language hi --minutes 5 --tts auto \
  --output outputs/temple_story
```

**English, 2-minute story:**

```bash
python video_cli.py \
  --idea "A mysterious lighthouse on a stormy night" \
  --language en --minutes 2 --tts edge \
  --output outputs/lighthouse
```

`--minutes` is an approximate *script-generation target*, not a guaranteed final duration. Actual length follows the narration.

## 4. Generate from an EXISTING SCRIPT

Create/edit a UTF-8 script:

```bash
nano my_script.txt
```

Hindi:

```bash
python video_cli.py --script my_script.txt --language hi --tts edge --output outputs/my_hindi_video
```

English:

```bash
python video_cli.py --script my_script.txt --language en --tts auto --output outputs/my_english_video
```

Hindi with the local Indic Parler service and the Rani storytelling voice:

```bash
curl --fail --show-error http://127.0.0.1:8765/health

# Optional service-only smoke test (no Gemini, Pexels, or video render):
curl --fail --show-error \
  --header 'Content-Type: application/json' \
  --data '{"text":"नमस्ते। यह रानी की एक छोटी जाँच है।","voice":"Rani","language":"hi-IN"}' \
  http://127.0.0.1:8765/synthesize \
  --output /tmp/indic-rani-smoke.wav
ffprobe -v error \
  -show_entries stream=codec_name,sample_rate,channels:format=duration \
  -of default=noprint_wrappers=1 /tmp/indic-rani-smoke.wav

python video_cli.py \
  --script my_hindi_script.txt --language hi \
  --tts indic-parler \
  --indic-url http://127.0.0.1:8765 \
  --indic-voice Rani \
  --skip-music \
  --output outputs/hindi_rani
```

The verified service accepts Hindi only. Its voices are `Divya`, `Rohit`, `Aman`, and `Rani`; `/synthesize` returns raw `audio/wav`, not JSON or base64. Explicit `--tts indic-parler` never falls back to Gemini or Edge. On failure, rerun the same command after fixing the service: completed scene WAVs are retained and configuration-validated.

Use **either** `--idea` **or** `--script`, never both.

## 5. Preview the plan without downloading media

```bash
python video_cli.py \
  --idea "An abandoned castle with a dark secret" \
  --language en --minutes 3 --dry-run \
  --output outputs/castle_plan
```

Inspect planned scenes:

```bash
python -m json.tool outputs/castle_plan/project.json | less
```

**Important:** `--dry-run` still calls Gemini to generate/plan the story if no project exists; it skips photo downloads, narration, music, and rendering.

## 6. Control Pexels and music search keywords

Override the general photo theme:

```bash
python video_cli.py \
  --idea "A haunted forest" --language en --minutes 2 \
  --theme "dark misty forest" \
  --output outputs/forest_theme
```

Search Jamendo with specific moods:

```bash
python video_cli.py \
  --idea "A scary abandoned mansion" --language en --minutes 2 \
  --music-mood "horror,suspense,cinematic" \
  --output outputs/mansion
```

Skip Jamendo completely:

```bash
python video_cli.py \
  --idea "A joyful summer festival" --language en --minutes 2 \
  --skip-music --output outputs/festival
```

**Music rights:** Jamendo results are *candidates only* by default. The CLI does **not** download/mix them unless you supply `--music-rights-confirmed` and explicitly acknowledge that you independently obtained the necessary synchronization/commercial rights. Do not use that flag solely because a track appears on Jamendo or is downloadable.

## 7. Pick TTS provider, language, and frame rate

| Option | Values | Meaning |
|---|---|---|
| `--language` | `hi`, `en` | Hindi or English |
| `--tts` | `edge`, `gemini`, `auto`, `indic-parler` | Edge, Gemini, Gemini-with-Edge-fallback, or strict local Hindi Indic Parler |
| `--indic-url` | HTTP(S) base URL | Indic Parler endpoint (default `http://127.0.0.1:8765`) |
| `--indic-voice` | `Divya`, `Rohit`, `Aman`, `Rani` | Indic Parler voice (default `Divya`) |
| `--minutes` | positive number up to 60 | Target duration for *idea generation* |
| `--output` | directory path | Optional override; otherwise uses `outputs/<story-name>` |
| `--fps` | integer | Output frames per second (default 24) |
| `--style` | `auto`, `horror`, `joy`, `neutral` | `auto` grades each scene from its mood; explicit values override the whole video |
| `--no-zoom` | flag | Disable the default alternating zoom-in/zoom-out motion |

Example (30 fps):

```bash
python video_cli.py --idea "A journey through space" --language en --minutes 2 --tts edge --fps 30 --skip-music --output outputs/space30
```

Output resolution is currently fixed to **1280×720 landscape**. There is no `--resolution` or `--render-only` flag in this version.

With `--style auto`, styling is scene-specific and uses moods already stored in the scene plan; it does not make extra Gemini calls. Image motion remains independent: scenes alternate zoom-in and zoom-out unless `--no-zoom` is supplied.

During long operations, the CLI prints per-stage progress and ETA, for example:

```text
[storyclip] Images    [########------------]  40.0% (8/20) ETA 24s
[storyclip] Narration [############--------]  60.0% (12/20) ETA 1m 18s
[storyclip] Rendering [################----]  80.0% (16/20) ETA 35s
```

## 8. Resume after an error (without regenerating the saved plan)

Run the same command with the **same `--output` directory**:

```bash
python video_cli.py \
  --idea "एक रहस्यमयी जंगल में छिपा हुआ प्राचीन मंदिर" \
  --language hi --minutes 5 --tts edge --skip-music \
  --output outputs/first_test
```

Existing `project.json` is reused; cached photo/audio/clip assets are reused when present. Some processing steps still run, so **this is a resume, not a dedicated render-only mode**. Fix FFmpeg `xfade` first. **Do not delete** `outputs/first_test` if you want to preserve assets.

For projects initially created without `--output`, repeating the same source options also locates and resumes the matching automatically named directory. This lookup is local and does not call Gemini again.

Narration caching is configuration-aware. Text, language, provider, voice, mood, endpoint/model settings, and audio settings must all match, and the cached WAV must validate. Changing the Rani/Divya choice creates a new cache entry. If the input script changes, the CLI requires a new output folder instead of silently reusing the old project or audio.

For an entirely new script/plan, choose a **new** output folder:

```bash
python video_cli.py --idea "A different story" --language en --minutes 2 --output outputs/different_story
```

Changing `--script` text or `--language` while reusing a folder with `project.json` is rejected so stale media cannot be reused; choose a new output folder. Changing `--idea`, `--theme`, or `--music-mood` does not replace an already saved plan.

## 9. Find, inspect, and play output files

```bash
ls -lh outputs/first_test/
cat outputs/first_test/script.txt
less outputs/first_test/subtitles.srt
python -m json.tool outputs/first_test/project.json | less
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 outputs/first_test/final.mp4
vlc outputs/first_test/final.mp4
```

Typical folder:

```text
outputs/first_test/
  final.mp4         # video with narration, if rendering succeeded
  subtitles.srt     # separate scene-level subtitle file
  script.txt        # generated or supplied narration
  project.json      # saved plan/assets/scene durations
  images/           # downloaded images
  voice/            # generated narration
  clips/            # intermediate scene clips
  music/            # only if applicable
```

The `.srt` is **separate**, not burned into the video.

## 10. Troubleshooting

**`No such filter: xfade`** — confirm you are using the new bundled FFmpeg and that `ffmpeg -filters` displays `xfade`:

```bash
export PATH="$PWD/.local-bin:$PATH"
which ffmpeg
ffmpeg -hide_banner -filters 2>/dev/null | grep xfade
```

**Dependency install timeout** — retry with `uv`:

```bash
uv pip install -r requirements.txt --python .venv/bin/python --default-index https://pypi.org/simple
```

Alternative pip retry:

```bash
python -m pip install -r requirements.txt --timeout 180 --retries 10 --progress-bar off
```

**`cd: storyclip_cli: No such file or directory`** — you're probably already in that folder. Check:

```bash
pwd
ls video_cli.py
```

**Can't find `final.mp4`** — generation may have stopped before render; inspect the last error and check if `project.json` and scene clips exist. Retry the same `--output` after fixing the error.

**Video shorter than requested** — `--minutes` guides LLM script length; TTS duration determines actual video duration.

**No BGM** — this is expected unless Jamendo access works **and** you independently have appropriate music rights and confirm them. `--skip-music` intentionally disables music.

**Indic Parler connection error** — confirm the local service first:

```bash
curl --fail --show-error http://127.0.0.1:8765/health
```

Do not switch to `auto` merely to hide the error: `auto` means Gemini with Edge fallback, while explicit `indic-parler` intentionally fails with the real local-service error and keeps completed audio for resuming.

## 11. Full CLI help

```bash
python video_cli.py --help
```

### Quick everyday command

```bash
cd ~/Work/Github-Repos/storyclip_cli
source .venv/bin/activate
export PATH="$PWD/.local-bin:$PATH"
python video_cli.py --idea "YOUR IDEA HERE" --language hi --minutes 2 --tts edge --skip-music --output outputs/new_project
```

Omit `--output outputs/new_project` when you want automatic story-based naming. Keep an explicit unique output folder when you need a particular path.
