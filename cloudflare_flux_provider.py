"""Cloudflare Workers AI (FLUX.1 Schnell) image provider adapter.

Provides fast, cloud-accelerated text-to-image generation for minimalist
YouTube whiteboard explainer stickman and doodle illustrations using
Black Forest Labs FLUX.1 [schnell] hosted on Cloudflare Workers AI.

No local GPU or heavy machine learning models required.
Free tier includes 10,000 Neurons per day on Cloudflare (~35-40 images/day).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from media_runtime import SESSION, log

load_dotenv()

# Model & endpoint defaults
CLOUDFLARE_FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4/accounts"
CLOUDFLARE_DEFAULT_STEPS = 4
CLOUDFLARE_DEFAULT_TIMEOUT = 45
MINIMUM_IMAGE_SIZE_BYTES = 10240  # 10 KB sanity threshold for valid JPEGs

# Standardized whiteboard doodle art direction style signature
FLUX_DOODLE_STYLE_SUFFIX = (
    "minimalist black ink doodle illustration, YouTube whiteboard animation style, "
    "clean outlines, warm beige paper background, single red accent color, "
    "flat 2D vector, strictly no text, no words, no letters, no labels, no banners, no signs, no gradients, no photorealism"
)


def create_blurred_image(source_path: Path | str, dest_path: Path | str, radius: int = 25) -> Path:
    """Create a blurred version of source_path image using Gaussian blur for outro scene backgrounds."""
    from PIL import Image, ImageFilter
    src = Path(source_path)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        rgb_img = img.convert("RGB")
        blurred = rgb_img.filter(ImageFilter.GaussianBlur(radius=radius))
        part = dest.with_suffix(dest.suffix + ".part")
        blurred.save(part, format="JPEG", quality=92)
        os.replace(part, dest)
    return dest


def get_cloudflare_credentials(
    account_id: str | None = None,
    api_token: str | None = None,
) -> tuple[str, str]:
    """Retrieve and validate Cloudflare account ID and API token from params or env."""
    acc_id = (account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = (api_token or os.getenv("CLOUDFLARE_API_TOKEN") or "").strip()

    missing = []
    if not acc_id:
        missing.append("CLOUDFLARE_ACCOUNT_ID")
    if not token:
        missing.append("CLOUDFLARE_API_TOKEN")

    if missing:
        raise ValueError(
            f"Missing required Cloudflare credential(s): {', '.join(missing)}. "
            "Set these in your .env file or pass them explicitly."
        )
    return acc_id, token


def build_flux_prompt(scene_query: str) -> str:
    """Compose the concise scene action query with the locked minimalist whiteboard style."""
    clean_query = (scene_query or "").strip().rstrip(",.")
    if not clean_query:
        clean_query = "A stickman figure standing curiously with a simple prop in front of him"

    # If the query is already fully styled, preserve it as-is
    if FLUX_DOODLE_STYLE_SUFFIX in clean_query:
        return clean_query

    # Avoid requesting banners, posters, signs, or text that cause Flux to draw misspelled gibberish
    clean_query = re.sub(
        r'\b(?:banner|poster|signboard|billboard|sign|signs|text|words|letters|label|labels|writing|title)\b',
        'symbol',
        clean_query,
        flags=re.IGNORECASE,
    )

    prompt = f"{clean_query}, {FLUX_DOODLE_STYLE_SUFFIX}"

    # Enforce safe character cap (Cloudflare max is 2048 chars)
    if len(prompt) > 2000:
        prompt = prompt[:1997] + "..."
    return prompt


def compute_flux_cache_key(
    prompt: str,
    model: str = CLOUDFLARE_FLUX_MODEL,
    steps: int = CLOUDFLARE_DEFAULT_STEPS,
) -> str:
    """Generate deterministic SHA-256 cache identity for an image generation request."""
    identity = f"{model}:{steps}:{prompt.strip()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def validate_image_bytes(data: bytes) -> None:
    """Validate that raw bytes constitute a non-empty, valid JPEG (or PNG) image."""
    if not isinstance(data, bytes):
        raise RuntimeError("Cloudflare image payload is not valid binary bytes")
    if len(data) < MINIMUM_IMAGE_SIZE_BYTES:
        raise RuntimeError(
            f"Cloudflare generated image unexpectedly small ({len(data)} bytes < {MINIMUM_IMAGE_SIZE_BYTES} bytes)"
        )

    # Check for standard JPEG or PNG magic header bytes
    is_jpeg = data.startswith(b"\xff\xd8\xff")
    is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    if not (is_jpeg or is_png):
        raise RuntimeError("Cloudflare returned image data with unrecognized binary header (expected JPEG or PNG)")


def parse_flux_response(response_data: dict[str, Any] | bytes) -> bytes:
    """Parse Cloudflare REST response and extract binary image bytes."""
    # In some Workers AI bindings, raw bytes may be returned directly
    if isinstance(response_data, bytes):
        validate_image_bytes(response_data)
        return response_data

    if not isinstance(response_data, dict):
        raise RuntimeError(f"Unexpected response type from Cloudflare API: {type(response_data).__name__}")

    # Check Cloudflare success status
    if not response_data.get("success", False):
        errors = response_data.get("errors", [])
        if errors and isinstance(errors, list):
            msg = "; ".join(e.get("message", str(e)) for e in errors if isinstance(e, dict))
        else:
            msg = str(errors or "Unknown API error")
        raise RuntimeError(f"Cloudflare Workers AI generation failed: {msg}")

    result = response_data.get("result")
    if not isinstance(result, dict) or "image" not in result:
        raise RuntimeError("Cloudflare Workers AI response missing 'result.image' payload")

    b64_image = result["image"]
    if not isinstance(b64_image, str) or not b64_image.strip():
        raise RuntimeError("Cloudflare Workers AI returned empty 'image' field in result")

    try:
        raw_bytes = base64.b64decode(b64_image)
    except Exception as ex:
        raise RuntimeError(f"Failed to decode Base64 image payload from Cloudflare: {ex}") from ex

    validate_image_bytes(raw_bytes)
    return raw_bytes


def expand_to_landscape_16_9(
    image_bytes: bytes,
    target_resolution: tuple[int, int] | None = (1280, 720),
) -> bytes:
    """Seamlessly expand a square/non-16:9 doodle image to 16:9 landscape.

    Samples background color from corners and borders, and replicates the edge
    columns outwards to preserve any continuous ground/floor lines seamlessly.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        current_ratio = w / h
        # If already approximately 16:9 (ratio 1.777 ± 0.05)
        if abs(current_ratio - (16 / 9)) < 0.05:
            if target_resolution and (w, h) != target_resolution:
                resized = img.resize(target_resolution, Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=95)
                return buf.getvalue()
            return image_bytes

        # Calculate 16:9 dimensions based on height
        target_w = int(round(h * 16 / 9))
        pad_left = (target_w - w) // 2
        pad_right = target_w - w - pad_left

        # Sample background color from top corners and borders
        arr = np.array(img)
        sample = np.concatenate([
            arr[:30, :50].reshape(-1, 3),
            arr[:30, -50:].reshape(-1, 3),
            arr[:, :5].reshape(-1, 3),
            arr[:, -5:].reshape(-1, 3),
        ], axis=0)
        bg_color = tuple(np.median(sample, axis=0).astype(int))

        # Build expanded canvas
        canvas = Image.new("RGB", (target_w, h), bg_color)
        left_col = img.crop((0, 0, 1, h)).resize((pad_left, h), Image.Resampling.NEAREST)
        right_col = img.crop((w - 1, 0, w, h)).resize((pad_right, h), Image.Resampling.NEAREST)

        canvas.paste(left_col, (0, 0))
        canvas.paste(img, (pad_left, 0))
        canvas.paste(right_col, (pad_left + w, 0))

        if target_resolution:
            canvas = canvas.resize(target_resolution, Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        canvas.save(out_buf, format="JPEG", quality=95)
        return out_buf.getvalue()
    except Exception as ex:
        log(f"Warning: Failed to expand image to 16:9 landscape ({ex}); using original")
        return image_bytes


def generate_flux_image(
    scene_query: str,
    dest_path: Path | str,
    account_id: str | None = None,
    api_token: str | None = None,
    steps: int = CLOUDFLARE_DEFAULT_STEPS,
    timeout: int = CLOUDFLARE_DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> Path:
    """Generate a minimalist scene doodle via Cloudflare FLUX.1 [schnell] with atomic caching.

    Args:
        scene_query: Concise literal stickman action description.
        dest_path: Destination file path (e.g. outputs/<proj>/images/scene_001.jpg).
        account_id: Cloudflare Account ID (defaults to CLOUDFLARE_ACCOUNT_ID env).
        api_token: Cloudflare API Token (defaults to CLOUDFLARE_API_TOKEN env).
        steps: Number of diffusion steps (default 4 for Schnell).
        timeout: Request timeout in seconds.
        session: Optional shared requests.Session.

    Returns:
        Path to the verified, atomically saved image file.
    """
    target = Path(dest_path)
    full_prompt = build_flux_prompt(scene_query)

    # 1. Cache Hit Detection: If destination already exists and is a valid image, skip request
    if target.is_file() and target.stat().st_size >= MINIMUM_IMAGE_SIZE_BYTES:
        try:
            sample_header = target.read_bytes()[:16]
            if sample_header.startswith(b"\xff\xd8\xff") or sample_header.startswith(b"\x89PNG\r\n\x1a\n"):
                return target
        except Exception:
            pass  # Corrupt or unreadable file; proceed to regenerate

    # 2. Validate Credentials & Build Endpoint
    acc_id, token = get_cloudflare_credentials(account_id, api_token)
    endpoint_url = f"{CLOUDFLARE_API_BASE}/{acc_id}/ai/run/{CLOUDFLARE_FLUX_MODEL}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "storyclip-cli/1.0",
    }
    payload = {
        "prompt": full_prompt,
        "steps": max(1, min(8, int(steps))),
    }

    http = session or SESSION

    # 3. HTTP Request Execution
    try:
        resp = http.post(endpoint_url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout as ex:
        raise RuntimeError(f"Cloudflare Workers AI request timed out after {timeout}s") from ex
    except requests.exceptions.RequestException as ex:
        raise RuntimeError(f"Cloudflare Workers AI network error: {ex}") from ex

    # 4. Handle Status Codes with Actionable Errors
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Cloudflare authentication failed (HTTP {resp.status_code}). "
            "Please verify that CLOUDFLARE_API_TOKEN has 'Workers AI: Read' permissions."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "Cloudflare Workers AI rate limit or daily free neuron quota exceeded (HTTP 429). "
            "Try again later or check your Cloudflare dashboard."
        )
    if resp.status_code != 200:
        error_detail = resp.text[:400]
        try:
            err_json = resp.json()
            if isinstance(err_json, dict) and "errors" in err_json:
                error_detail = "; ".join(e.get("message", str(e)) for e in err_json["errors"])
        except Exception:
            pass

        if "nsfw" in error_detail.lower():
            log(f"Warning: Cloudflare flagged prompt for NSFW ({error_detail}). Retrying with safe neutral stickman doodle...")
            safe_fallback_query = "A stick figure character standing peacefully in front of a simple symbol on a clean floor"
            safe_payload = {
                "prompt": build_flux_prompt(safe_fallback_query),
                "steps": payload["steps"],
            }
            try:
                resp = http.post(endpoint_url, headers=headers, json=safe_payload, timeout=timeout)
            except Exception as retry_ex:
                raise RuntimeError(f"Cloudflare Workers AI retry after NSFW error failed: {retry_ex}") from retry_ex

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Cloudflare Workers AI error after NSFW recovery retry (HTTP {resp.status_code}): {resp.text[:300]}"
                )
        else:
            raise RuntimeError(f"Cloudflare Workers AI error (HTTP {resp.status_code}): {error_detail}")

    # 5. Extract and Validate Binary Image
    try:
        data = resp.json()
    except Exception as ex:
        raise RuntimeError(f"Cloudflare Workers AI returned non-JSON response: {resp.text[:200]}") from ex

    image_bytes = parse_flux_response(data)
    image_bytes = expand_to_landscape_16_9(image_bytes)

    # 6. Atomic File Write via .part
    target.parent.mkdir(parents=True, exist_ok=True)
    part_path = target.with_suffix(target.suffix + ".part")
    try:
        part_path.write_bytes(image_bytes)
        os.replace(part_path, target)
    finally:
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass

    return target
