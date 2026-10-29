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
  --tts edge \
  --output outputs/my-video
```

Use either `--idea` or `--script`.

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

The finished video is saved as `outputs/<name>/final.mp4`; subtitles are in `outputs/<name>/subtitles.srt`.
