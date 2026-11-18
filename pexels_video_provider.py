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
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from media_runtime import SESSION, log

load_dotenv()

PEXELS_VIDEOS_API_URL = "https://api.pexels.com/videos/search"
DEFAULT_TIMEOUT = 30
MAX_PEXELS_RETRIES = 3
MINIMUM_VIDEO_SIZE_BYTES = 50 * 1024  # 50 KB sanity threshold


def get_pexels_api_key(key: str | None = None) -> str:
    """Retrieve and validate Pexels API key from argument or environment."""
    api_key = (key or os.getenv("PEXELS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("Missing PEXELS_API_KEY in environment or arguments")
    return api_key


def select_best_video_file(
    video_files: list[dict[str, Any]],
    target_width: int = 1280,
    target_height: int = 720,
    orientation: str = "landscape",
) -> dict[str, Any] | None:
    """Select the best MP4 file closest to target resolution and orientation.

    Prefers exact resolution match, matching orientation (landscape vs portrait),
    then HD resolution (at least 720p).
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

    is_portrait_target = (orientation == "portrait" or target_height > target_width)
    if is_portrait_target:
        matching = [f for f in mp4_candidates if f["height"] >= f["width"]]
    else:
        matching = [f for f in mp4_candidates if f["width"] >= f["height"]]

    pool = matching if matching else mp4_candidates

    def score_candidate(f: dict[str, Any]) -> tuple[int, int, int]:
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        # Exact resolution match gets highest score
        is_exact = 1 if (w == target_width and h == target_height) else 0
        # Check if HD (at least 720 in smaller dimension or matches target)
        min_dim = min(w, h)
        is_hd = 1 if (min_dim >= 720 or (w >= target_width and h >= target_height)) else 0
        # Distance penalty from target resolution
        distance_penalty = -(abs(w - target_width) + abs(h - target_height))
        return (is_exact, is_hd, distance_penalty)

    pool.sort(key=score_candidate, reverse=True)
    return pool[0]


def select_best_portrait_file(
    video_files: list[dict[str, Any]],
    target_width: int = 1080,
    target_height: int = 1920,
) -> dict[str, Any] | None:
    """Select the best portrait MP4 file closest to 1080x1920 resolution.

    Prefers exact 1080x1920, then higher resolution portrait (1440x2560, 2160x3840),
    then 720x1280. Rejects landscape or corrupt entries.
    """
    return select_best_video_file(
        video_files,
        target_width=target_width,
        target_height=target_height,
        orientation="portrait",
    )


def pexels_video_search(
    query: str,
    count: int = 10,
    orientation: str = "portrait",
    target_width: int | None = None,
    target_height: int | None = None,
    api_key: str | None = None,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Search Pexels Videos API for video candidates matching query and orientation.

    Returns a list of standardized video candidate dicts with direct download URLs.
    """
    token = get_pexels_api_key(api_key)
    http = session or SESSION
    headers = {
        "Authorization": token,
        "User-Agent": "storyclip-cli/1.0",
    }
    params: dict[str, Any] = {
        "query": query.strip(),
        "per_page": max(1, min(20, int(count))),
    }
    if orientation:
        params["orientation"] = orientation

    # Determine resolution targets if not provided
    if target_width is None or target_height is None:
        if orientation == "portrait":
            resolved_w, resolved_h = 1080, 1920
        else:
            resolved_w, resolved_h = 1280, 720
    else:
        resolved_w, resolved_h = target_width, target_height

    resp = None
    for attempt in range(1, MAX_PEXELS_RETRIES + 1):
        try:
            resp = http.get(PEXELS_VIDEOS_API_URL, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.Timeout as ex:
            if attempt < MAX_PEXELS_RETRIES:
                delay = 2.0 * attempt
                log(f"Pexels Video API search timed out ({timeout}s); retrying in {delay:.1f}s (attempt {attempt + 1}/{MAX_PEXELS_RETRIES})...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Pexels Video API search timed out after {timeout}s (tried {MAX_PEXELS_RETRIES} times)") from ex
        except requests.exceptions.RequestException as ex:
            if attempt < MAX_PEXELS_RETRIES:
                delay = 2.0 * attempt
                log(f"Pexels Video API network error: {ex}; retrying in {delay:.1f}s (attempt {attempt + 1}/{MAX_PEXELS_RETRIES})...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Pexels Video API network error: {ex}") from ex

        if resp.status_code in (401, 403):
            raise RuntimeError(f"Pexels authentication failed (HTTP {resp.status_code}). Verify PEXELS_API_KEY.")
        if resp.status_code == 429:
            if attempt < MAX_PEXELS_RETRIES:
                delay = 4.0 * attempt
                log(f"Pexels Video API rate limited (429); cooling down {delay:.1f}s (attempt {attempt + 1}/{MAX_PEXELS_RETRIES})...")
                time.sleep(delay)
                continue
            raise RuntimeError("Pexels Video API rate limit exceeded (HTTP 429). Please retry later.")
        if resp.status_code >= 500:
            if attempt < MAX_PEXELS_RETRIES:
                delay = 2.0 * attempt
                log(f"Pexels Video API server error (HTTP {resp.status_code}); retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
        if resp.status_code != 200:
            raise RuntimeError(f"Pexels Video API search failed (HTTP {resp.status_code}): {resp.text[:200]}")
        break

    try:
        data = resp.json()
    except Exception as ex:
        raise RuntimeError(f"Pexels Video API returned invalid JSON: {ex}") from ex

    videos = data.get("videos", [])
    results = []
    for v in videos:
        if not isinstance(v, dict):
            continue
        best_file = select_best_video_file(
            v.get("video_files", []),
            target_width=resolved_w,
            target_height=resolved_h,
            orientation=orientation,
        )
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

    for attempt in range(1, MAX_PEXELS_RETRIES + 1):
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
                return target
        except Exception as ex:
            if part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass
            if attempt < MAX_PEXELS_RETRIES:
                delay = 2.0 * attempt
                log(f"Video download attempt {attempt} failed ({ex}); retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            raise
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
