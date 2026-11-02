"""Tests for cloudflare_flux_provider.py."""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from cloudflare_flux_provider import (
    CLOUDFLARE_FLUX_MODEL,
    FLUX_DOODLE_STYLE_SUFFIX,
    MINIMUM_IMAGE_SIZE_BYTES,
    build_flux_prompt,
    compute_flux_cache_key,
    generate_flux_image,
    get_cloudflare_credentials,
    parse_flux_response,
    validate_image_bytes,
)

# Helper to generate dummy valid JPEG binary data of given size
def make_dummy_jpeg(size_bytes: int = 15000) -> bytes:
    from PIL import Image
    import numpy as np
    import io
    arr = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


class TestCloudflareFluxProvider(unittest.TestCase):
    def setUp(self):
        self.dummy_jpeg = make_dummy_jpeg(15000)
        self.dummy_b64 = base64.b64encode(self.dummy_jpeg).decode("utf-8")

    def test_get_cloudflare_credentials_explicit(self):
        acc, token = get_cloudflare_credentials("acc_123", "tok_456")
        self.assertEqual(acc, "acc_123")
        self.assertEqual(token, "tok_456")

    @patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "env_acc", "CLOUDFLARE_API_TOKEN": "env_tok"})
    def test_get_cloudflare_credentials_env(self):
        acc, token = get_cloudflare_credentials()
        self.assertEqual(acc, "env_acc")
        self.assertEqual(token, "env_tok")

    @patch.dict(os.environ, {}, clear=True)
    def test_get_cloudflare_credentials_missing_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_cloudflare_credentials()
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", str(ctx.exception))
        self.assertIn("CLOUDFLARE_API_TOKEN", str(ctx.exception))

    def test_build_flux_prompt_combines_action_and_style(self):
        action = "Stickman doctor with stethoscope smiling at an X-ray screen"
        prompt = build_flux_prompt(action)
        self.assertTrue(prompt.startswith(action))
        self.assertIn(FLUX_DOODLE_STYLE_SUFFIX, prompt)

    def test_build_flux_prompt_avoids_duplicate_style(self):
        already_styled = f"A stickman with cute dog, {FLUX_DOODLE_STYLE_SUFFIX}"
        prompt = build_flux_prompt(already_styled)
        self.assertEqual(prompt, already_styled)

    def test_build_flux_prompt_handles_empty(self):
        prompt = build_flux_prompt("")
        self.assertIn("stickman", prompt.lower())
        self.assertIn(FLUX_DOODLE_STYLE_SUFFIX, prompt)

    def test_build_flux_prompt_truncation(self):
        huge_query = "a" * 2500
        prompt = build_flux_prompt(huge_query)
        self.assertLessEqual(len(prompt), 2000)
        self.assertTrue(prompt.endswith("..."))

    def test_compute_flux_cache_key_deterministic(self):
        k1 = compute_flux_cache_key("test prompt", steps=4)
        k2 = compute_flux_cache_key("test prompt", steps=4)
        k3 = compute_flux_cache_key("different prompt", steps=4)
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertEqual(len(k1), 64)

    def test_validate_image_bytes_accepts_valid_jpeg(self):
        validate_image_bytes(self.dummy_jpeg)  # Should not raise

    def test_validate_image_bytes_rejects_small_payload(self):
        tiny_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        with self.assertRaises(RuntimeError) as ctx:
            validate_image_bytes(tiny_data)
        self.assertIn("unexpectedly small", str(ctx.exception))

    def test_validate_image_bytes_rejects_corrupted_header(self):
        bad_header = b"\x00\x00\x00\x00" + b"\xaa" * 15000
        with self.assertRaises(RuntimeError) as ctx:
            validate_image_bytes(bad_header)
        self.assertIn("unrecognized binary header", str(ctx.exception))

    def test_parse_flux_response_success(self):
        payload = {
            "success": True,
            "result": {"image": self.dummy_b64},
            "errors": [],
        }
        bytes_out = parse_flux_response(payload)
        self.assertEqual(bytes_out, self.dummy_jpeg)

    def test_parse_flux_response_api_error(self):
        payload = {
            "success": False,
            "errors": [{"code": 1001, "message": "Model not available"}],
        }
        with self.assertRaises(RuntimeError) as ctx:
            parse_flux_response(payload)
        self.assertIn("Model not available", str(ctx.exception))

    def test_parse_flux_response_missing_image(self):
        payload = {"success": True, "result": {}}
        with self.assertRaises(RuntimeError) as ctx:
            parse_flux_response(payload)
        self.assertIn("missing 'result.image'", str(ctx.exception))

    def test_generate_flux_image_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "scene_001.jpg"
            dest.write_bytes(self.dummy_jpeg)

            # mock session - should NOT be called because file already exists
            mock_session = MagicMock()
            result = generate_flux_image(
                scene_query="some query",
                dest_path=dest,
                account_id="acc",
                api_token="tok",
                session=mock_session,
            )
            self.assertEqual(result, dest)
            mock_session.post.assert_not_called()

    def test_generate_flux_image_success_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "images" / "scene_002.jpg"

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "success": True,
                "result": {"image": self.dummy_b64},
            }

            mock_session = MagicMock()
            mock_session.post.return_value = mock_response

            result = generate_flux_image(
                scene_query="Stickman waving hello",
                dest_path=dest,
                account_id="test_acc",
                api_token="test_tok",
                session=mock_session,
            )

            self.assertEqual(result, dest)
            self.assertTrue(dest.is_file())
            from PIL import Image
            with Image.open(dest) as saved_img:
                self.assertEqual(saved_img.size, (1280, 720))

            # Verify endpoint and headers
            mock_session.post.assert_called_once()
            call_args, call_kwargs = mock_session.post.call_args
            self.assertIn("test_acc/ai/run/@cf/black-forest-labs/flux-1-schnell", call_args[0])
            self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer test_tok")
            self.assertEqual(call_kwargs["json"]["steps"], 4)
            self.assertIn("Stickman waving hello", call_kwargs["json"]["prompt"])

    def test_generate_flux_image_auth_error_401(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "scene.jpg"

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response

            with self.assertRaises(RuntimeError) as ctx:
                generate_flux_image(
                    scene_query="Stickman running",
                    dest_path=dest,
                    account_id="acc",
                    api_token="bad_tok",
                    session=mock_session,
                )
            self.assertIn("Cloudflare authentication failed (HTTP 401)", str(ctx.exception))
            self.assertFalse(dest.exists())

    def test_generate_flux_image_rate_limit_429(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "scene.jpg"

            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response

            with self.assertRaises(RuntimeError) as ctx:
                generate_flux_image(
                    scene_query="Stickman jumping",
                    dest_path=dest,
                    account_id="acc",
                    api_token="tok",
                    session=mock_session,
                )
            self.assertIn("rate limit or daily free neuron quota exceeded (HTTP 429)", str(ctx.exception))
            self.assertFalse(dest.exists())

    def test_generate_flux_image_nsfw_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "scene.jpg"

            # First call returns 400 with NSFW error
            nsfw_response = MagicMock()
            nsfw_response.status_code = 400
            nsfw_response.text = "AiError: Input prompt contains NSFW content."
            nsfw_response.json.return_value = {
                "errors": [{"message": "Input prompt contains NSFW content."}],
                "success": False,
            }

            # Second call (retry) returns successful valid JPEG image
            img_bytes = make_dummy_jpeg()
            b64_img = base64.b64encode(img_bytes).decode("ascii")
            success_response = MagicMock()
            success_response.status_code = 200
            success_response.json.return_value = {
                "success": True,
                "result": {"image": b64_img},
            }

            mock_session = MagicMock()
            mock_session.post.side_effect = [nsfw_response, success_response]

            saved_path = generate_flux_image(
                scene_query="An innocent scene that false positive flagged",
                dest_path=dest,
                account_id="acc",
                api_token="tok",
                session=mock_session,
            )

            self.assertEqual(saved_path, dest)
            self.assertTrue(dest.is_file())
            self.assertEqual(mock_session.post.call_count, 2)
            retry_call_args = mock_session.post.call_args_list[1]
            self.assertIn("standing peacefully", retry_call_args[1]["json"]["prompt"])

    def test_expand_to_landscape_16_9(self):
        from PIL import Image
        import io
        from cloudflare_flux_provider import expand_to_landscape_16_9

        # Create a small 200x200 square test image
        square = Image.new("RGB", (200, 200), (250, 240, 230))
        buf = io.BytesIO()
        square.save(buf, format="JPEG")
        square_bytes = buf.getvalue()

        expanded_bytes = expand_to_landscape_16_9(square_bytes, target_resolution=(1280, 720))
        self.assertIsInstance(expanded_bytes, bytes)

        expanded_img = Image.open(io.BytesIO(expanded_bytes))
        self.assertEqual(expanded_img.size, (1280, 720))

    def test_create_blurred_image(self):
        from PIL import Image
        from cloudflare_flux_provider import create_blurred_image

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.jpg"
            dest = Path(tmpdir) / "blurred.jpg"

            # Create high-contrast source
            img = Image.new("RGB", (200, 200), (255, 255, 255))
            for x in range(50, 150):
                for y in range(50, 150):
                    img.putpixel((x, y), (0, 0, 0))
            img.save(src, format="JPEG")

            out_path = create_blurred_image(src, dest, radius=10)
            self.assertEqual(out_path, dest)
            self.assertTrue(dest.is_file())

            with Image.open(dest) as blurred_img:
                self.assertEqual(blurred_img.size, (200, 200))
                # The sharp black square should be blurred into gray at the border
                border_pixel = blurred_img.getpixel((50, 50))
                self.assertNotEqual(border_pixel, (0, 0, 0))
                self.assertNotEqual(border_pixel, (255, 255, 255))


class TestCloudflareFluxCLIAndPipeline(unittest.TestCase):
    def test_cli_parser_accepts_and_normalizes_image_provider(self):
        import video_cli

        p = video_cli.build_parser()
        for flag in ('cloudflare-flux', 'flux', 'cloudflare'):
            args = p.parse_args(['--idea', 'test idea', '--image-provider', flag])
            video_cli.validate_cli_args(p, args)
            self.assertEqual(args.image_provider, 'cloudflare-flux')

    def test_prerequisites_reject_missing_cloudflare_credentials(self):
        import video_cli

        p = video_cli.build_parser()
        args = p.parse_args(['--idea', 'test idea', '--image-provider', 'cloudflare-flux'])
        video_cli.validate_cli_args(p, args)

        with patch.dict(os.environ, {'GEMINI_API_KEY': 'gemini_test'}, clear=True):
            with patch('video_cli.check_tools'):
                with self.assertRaises(SystemExit):
                    video_cli.require_pipeline_prerequisites(p, args)

    def test_prerequisites_accept_cloudflare_without_pexels_key(self):
        import video_cli

        p = video_cli.build_parser()
        args = p.parse_args(['--idea', 'test idea', '--image-provider', 'cloudflare-flux'])
        video_cli.validate_cli_args(p, args)

        with patch.dict(
            os.environ,
            {
                'GEMINI_API_KEY': 'gemini_test',
                'CLOUDFLARE_ACCOUNT_ID': 'cf_acc',
                'CLOUDFLARE_API_TOKEN': 'cf_tok',
            },
            clear=True,
        ):
            with patch('video_cli.check_tools'):
                # Should not raise even though PEXELS_API_KEY is absent
                video_cli.require_pipeline_prerequisites(p, args)

    @patch('video_cli.gemini_json')
    def test_build_video_scenes_uses_stickman_queries_for_flux(self, mock_gemini):
        import video_cli

        mock_gemini.return_value = {
            'queries': [
                {'id': 0, 'query': 'Stickman scientist looking at computer in front of him', 'avatar_cue': 'explanation'}
            ]
        }
        scenes, intro, outro, full = video_cli.build_video_scenes(
            content='AI revolutionized computing.',
            title='AI Era',
            language='en',
            theme='artificial intelligence',
            outro_profile='default',
            image_provider='cloudflare-flux',
        )
        self.assertIn('conceptual', scenes[0]['query'].lower())
        self.assertIn('strictly no stick figures', scenes[0]['query'].lower())
        self.assertIn('outro', scenes[-1]['query'].lower())
        self.assertIn('stickman', scenes[1]['query'].lower())

    @patch('cloudflare_flux_provider.generate_flux_image')
    def test_gather_images_dispatches_to_cloudflare_flux(self, mock_generate):
        import video_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / 'images'
            scenes = [
                {'text': 'Intro beat', 'query': 'Intro backdrop', 'kind': 'intro'},
                {'text': 'Main beat', 'query': 'Stickman looking at robot in front of him', 'kind': 'content'},
            ]
            video_cli.gather_images(scenes, 'AI', folder, image_provider='cloudflare-flux')

            self.assertEqual(mock_generate.call_count, 2)
            self.assertEqual(scenes[0]['image_provider'], 'cloudflare-flux')
            self.assertEqual(scenes[1]['image_provider'], 'cloudflare-flux')

    @patch('cloudflare_flux_provider.generate_flux_image')
    def test_gather_images_blurs_last_image_for_outro(self, mock_generate):
        import video_cli
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / 'images'
            folder.mkdir(parents=True, exist_ok=True)
            content_img = folder / 'content.jpg'
            # Save a dummy 1280x720 image
            Image.new('RGB', (1280, 720), color='white').save(content_img, format='JPEG')

            scenes = [
                {'text': 'Main beat', 'query': 'Stickman looking at robot', 'kind': 'content', 'image': str(content_img)},
                {'text': 'Outro beat', 'query': 'Blurred outro backdrop', 'kind': 'outro'},
            ]
            video_cli.gather_images(scenes, 'AI', folder, image_provider='cloudflare-flux')

            # Outro should NOT call generate_flux_image because it blurs content_img!
            self.assertEqual(mock_generate.call_count, 0)
            self.assertEqual(scenes[1]['image_provider'], 'cloudflare-flux')
            self.assertTrue(Path(scenes[1]['image']).is_file())
            self.assertEqual(Path(scenes[1]['image']).name, 'outro_blurred.jpg')


if __name__ == "__main__":
    unittest.main()


