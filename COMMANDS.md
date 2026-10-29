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

Add your `GEMINI_API_KEY` and `PEXELS_API_KEY` to `.env`. Then activate the environment:

```bash
source .venv/bin/activate
```

## Create a video from an idea

```bash
python video_cli.py \
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
python video_cli.py \
  --script my_script.txt \
  --language hi \
  --tts edge \
  --output outputs/my-video
```

Use either `--idea` or `--script`.

## Music

Music is enabled by default. Choose a mood by adding `--music-mood "nature,documentary"` to the video command. To disable music, add `--skip-music`.

## FFmpeg crossfades

Check that your FFmpeg supports `xfade`:

```bash
ffmpeg -hide_banner -filters 2>/dev/null | grep -w xfade
```

If no filter is listed, install the bundled FFmpeg and use it in this terminal:

```bash
.venv/bin/python -m pip install imageio-ffmpeg
mkdir -p .local-bin
ln -sf "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .local-bin/ffmpeg
export PATH="$PWD/.local-bin:$PATH"
```

## Resume a video

Rerun the same command with the same `--output` path and the same idea or script. Completed work is reused when it matches the saved project.

The finished video is saved as `outputs/<name>/final.mp4`; subtitles are in `outputs/<name>/subtitles.srt`.
