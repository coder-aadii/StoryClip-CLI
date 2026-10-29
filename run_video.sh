#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PYTHON="$ROOT_DIR/.venv/bin/python"
LOCAL_FFMPEG="$ROOT_DIR/.local-bin/ffmpeg"

if [[ ! -x "$PYTHON" ]]; then
  printf 'StoryClip virtual environment not found. Create it and install requirements first.\n' >&2
  exit 1
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  printf 'FFprobe was not found. Install FFmpeg and FFprobe, then run this command again.\n' >&2
  exit 1
fi

has_xfade() {
  "$1" -hide_banner -filters 2>/dev/null | grep -w 'xfade' >/dev/null
}

if [[ -x "$LOCAL_FFMPEG" ]] && has_xfade "$LOCAL_FFMPEG"; then
  export PATH="$ROOT_DIR/.local-bin:$PATH"
else
  SYSTEM_FFMPEG="$(command -v ffmpeg || true)"
  if [[ -z "$SYSTEM_FFMPEG" ]] || ! has_xfade "$SYSTEM_FFMPEG"; then
    cat >&2 <<EOF
No FFmpeg with the xfade filter was found.
Run this one-time setup from the repository folder:
  $PYTHON -m pip install imageio-ffmpeg
  mkdir -p .local-bin
  ln -sf "\$($PYTHON -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .local-bin/ffmpeg
Then run ./run_video.sh again.
EOF
    exit 1
  fi
fi

exec "$PYTHON" "$ROOT_DIR/video_cli.py" "$@"
