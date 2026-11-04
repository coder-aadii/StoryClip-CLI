"""Pexels 1080p Portrait Video provider for vertical YouTube Shorts.

Fetches real motion HD/FHD portrait video clips from Pexels Video API,
trims them to dynamic 3-4 second B-roll slots without synthetic zoom,
and provides seamless 1080x1920 vertical video tracks.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from media_runtime import SESSION, log

load_dotenv()

PEXELS_VIDEOS_API_URL = "https://api.pexels.com/videos/search"
DEFAULT_TIMEOUT = 30
MINIMUM_VIDEO_SIZE_BYTES = 50 * 1024  # 50 KB sanity threshold


def get_pexels_api_key(key: str | None = None) -> str:
    """Retrieve and validate Pexels API key from argument or environment."""
    api_key = (key or os.getenv("PEXELS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("Missing PEXELS_API_KEY in environment or arguments")
    return api_key


def select_best_portrait_file(
    video_files: list[dict[str, Any]],
    target_width: int = 1080,
    target_height: int = 1920,
) -> dict[str, Any] | None:
    """Select the best portrait MP4 file closest to 1080x1920 resolution.

    Prefers exact 1080x1920, then higher resolution portrait (1440x2560, 2160x3840),
    then 720x1280. Rejects landscape or corrupt entries.
    """
    if not video_files:
        return None

    mp4_candidates = [
        f for f in video_files
        if isinstance(f, dict)
        and f.get("link")
        and f.get("width")
        and f.get("height")
        and (f.get("file_type") == "video/mp4" or ".mp4" in str(f.get("link")).lower())
    ]
    if not mp4_candidates:
        return None

    # Filter for portrait or vertical orientation (height >= width)
    portrait_candidates = [f for f in mp4_candidates if f["height"] >= f["width"]]
    pool = portrait_candidates if portrait_candidates else mp4_candidates

    def score_candidate(f: dict[str, Any]) -> tuple[int, int, int]:
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        # Exact 1080x1920 match gets highest score
        is_exact = 1 if (w == target_width and h == target_height) else 0
        # Penalize if smaller than 720x1280
        is_hd = 1 if (h >= 1280 or w >= 720) else 0
        # Prefer closest resolution to target 1080x1920 (prefers 1080p and 720p over 4K UHD)
        distance_penalty = -(abs(w - target_width) + abs(h - target_height))
        return (is_exact, is_hd, distance_penalty)

    pool.sort(key=score_candidate, reverse=True)
    return pool[0]


def pexels_video_search(
    query: str,
    count: int = 10,
    orientation: str = "portrait",
    api_key: str | None = None,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Search Pexels Videos API for portrait video candidates.

    Returns a list of standardized video candidate dicts with direct download URLs.
    """
    token = get_pexels_api_key(api_key)
    http = session or SESSION
    headers = {
        "Authorization": token,
        "User-Agent": "storyclip-cli/1.0",
    }
    params = {
        "query": query.strip(),
        "orientation": orientation,
        "per_page": max(1, min(20, int(count))),
    }

    try:
        resp = http.get(PEXELS_VIDEOS_API_URL, headers=headers, params=params, timeout=timeout)
    except requests.exceptions.Timeout as ex:
        raise RuntimeError(f"Pexels Video API search timed out after {timeout}s") from ex
    except requests.exceptions.RequestException as ex:
        raise RuntimeError(f"Pexels Video API network error: {ex}") from ex

    if resp.status_code in (401, 403):
        raise RuntimeError(f"Pexels authentication failed (HTTP {resp.status_code}). Verify PEXELS_API_KEY.")
    if resp.status_code == 429:
        raise RuntimeError("Pexels Video API rate limit exceeded (HTTP 429). Please retry later.")
    if resp.status_code != 200:
        raise RuntimeError(f"Pexels Video API search failed (HTTP {resp.status_code}): {resp.text[:200]}")

    try:
        data = resp.json()
    except Exception as ex:
        raise RuntimeError(f"Pexels Video API returned invalid JSON: {ex}") from ex

    videos = data.get("videos", [])
    results = []
    for v in videos:
        if not isinstance(v, dict):
            continue
        best_file = select_best_portrait_file(v.get("video_files", []))
        if not best_file or not best_file.get("link"):
            continue

        user = v.get("user") or {}
        results.append({
            "id": v.get("id"),
            "url": best_file["link"],
            "width": best_file.get("width"),
            "height": best_file.get("height"),
            "quality": best_file.get("quality"),
            "duration": float(v.get("duration") or 0.0),
            "photographer": user.get("name") if isinstance(user, dict) else "Pexels Creator",
            "pexels_url": v.get("url"),
        })

    return results


def download_video_file(
    url: str,
    dest_path: Path | str,
    max_bytes: int = 250_000_000,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> Path:
    """Download video stream with atomic write and size validation."""
    target = Path(dest_path)
    if target.is_file() and target.stat().st_size >= MINIMUM_VIDEO_SIZE_BYTES:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    part_path = target.with_suffix(target.suffix + ".part")
    http = session or SESSION

    try:
        with http.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            written = 0
            with open(part_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError(f"Video file exceeded maximum download size ({max_bytes} bytes)")
                    f.write(chunk)

            if written < MINIMUM_VIDEO_SIZE_BYTES:
                raise RuntimeError(f"Downloaded video file unexpectedly small ({written} bytes)")

            os.replace(part_path, target)
    finally:
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass

    return target


def extract_broll_clip(
    source_video: Path | str,
    dest_clip: Path | str,
    duration: float = 3.5,
    start_offset: float = 0.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Trim and conform a video clip to exact vertical dimensions without synthetic zoom.

    Applies scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920.
    """
    src = Path(source_video)
    dest = Path(dest_clip)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not src.is_file():
        raise FileNotFoundError(f"Source video file does not exist: {src}")

    part_dest = dest.with_suffix(dest.suffix + ".part.mp4")
    filter_expr = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps={fps}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_offset:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(src),
        "-vf", filter_expr,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        str(part_dest),
    ]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace")[-400:]
            raise RuntimeError(f"FFmpeg B-roll extraction failed (code {proc.returncode}): {err}")

        os.replace(part_dest, dest)
    finally:
        if part_dest.exists():
            try:
                part_dest.unlink()
            except OSError:
                pass

    return dest
