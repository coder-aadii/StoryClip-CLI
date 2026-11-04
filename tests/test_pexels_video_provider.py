"""Unit tests for Pexels 1080p Portrait Video Provider."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pexels_video_provider import (
    download_video_file,
    extract_broll_clip,
    get_pexels_api_key,
    pexels_video_search,
    select_best_portrait_file,
)


class TestPexelsVideoProvider(unittest.TestCase):
    def test_get_pexels_api_key_explicit(self):
        self.assertEqual(get_pexels_api_key("custom_key"), "custom_key")

    def test_get_pexels_api_key_env(self):
        with patch.dict(os.environ, {"PEXELS_API_KEY": "env_key"}, clear=True):
            self.assertEqual(get_pexels_api_key(), "env_key")

    def test_get_pexels_api_key_missing_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                get_pexels_api_key(None)
            self.assertIn("Missing PEXELS_API_KEY", str(ctx.exception))

    def test_select_best_portrait_file_prefers_1080p(self):
        files = [
            {"width": 1920, "height": 1080, "file_type": "video/mp4", "link": "http://example.com/landscape.mp4"},
            {"width": 720, "height": 1280, "file_type": "video/mp4", "link": "http://example.com/hd.mp4"},
            {"width": 1080, "height": 1920, "file_type": "video/mp4", "link": "http://example.com/fhd.mp4"},
            {"width": 360, "height": 640, "file_type": "video/mp4", "link": "http://example.com/sd.mp4"},
        ]
        best = select_best_portrait_file(files)
        self.assertIsNotNone(best)
        self.assertEqual(best["width"], 1080)
        self.assertEqual(best["height"], 1920)
        self.assertEqual(best["link"], "http://example.com/fhd.mp4")

    def test_select_best_portrait_file_empty(self):
        self.assertIsNone(select_best_portrait_file([]))
        self.assertIsNone(select_best_portrait_file([{"invalid": "data"}]))

    def test_pexels_video_search_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "videos": [
                {
                    "id": 9999,
                    "duration": 15,
                    "url": "https://pexels.com/video/9999",
                    "user": {"name": "Test Filmmaker"},
                    "video_files": [
                        {
                            "width": 1080,
                            "height": 1920,
                            "quality": "hd",
                            "file_type": "video/mp4",
                            "link": "https://videos.pexels.com/9999-1080p.mp4",
                        }
                    ],
                }
            ]
        }
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        results = pexels_video_search("technology", count=5, api_key="test_tok", session=mock_session)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 9999)
        self.assertEqual(results[0]["width"], 1080)
        self.assertEqual(results[0]["height"], 1920)
        self.assertEqual(results[0]["photographer"], "Test Filmmaker")
        self.assertEqual(results[0]["url"], "https://videos.pexels.com/9999-1080p.mp4")

    def test_pexels_video_search_401_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        with self.assertRaises(RuntimeError) as ctx:
            pexels_video_search("tech", api_key="bad_tok", session=mock_session)
        self.assertIn("Pexels authentication failed", str(ctx.exception))

    def test_pexels_video_search_429_rate_limit(self):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        with self.assertRaises(RuntimeError) as ctx:
            pexels_video_search("tech", api_key="tok", session=mock_session)
        self.assertIn("rate limit exceeded", str(ctx.exception))

    def test_download_video_file_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "test_video.mp4"

            # Create mock response streaming 60 KB
            dummy_bytes = b"0" * (60 * 1024)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.iter_content.return_value = [dummy_bytes[:30000], dummy_bytes[30000:]]
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = None

            mock_session = MagicMock()
            mock_session.get.return_value = mock_response

            saved_path = download_video_file("http://example.com/video.mp4", dest, session=mock_session)
            self.assertEqual(saved_path, dest)
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.stat().st_size, len(dummy_bytes))

            # Second call should be a cache hit (session.get not called again)
            mock_session.get.reset_mock()
            hit_path = download_video_file("http://example.com/video.mp4", dest, session=mock_session)
            self.assertEqual(hit_path, dest)
            mock_session.get.assert_not_called()

    @patch("subprocess.run")
    def test_extract_broll_clip_invokes_ffmpeg_without_zoom(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.mp4"
            src.write_bytes(b"dummy_video_data")
            dest = Path(tmpdir) / "broll_01.mp4"

            # Fake successful write on destination path when ffmpeg runs
            def fake_run(cmd, *args, **kwargs):
                part = dest.with_suffix(dest.suffix + ".part.mp4")
                part.write_bytes(b"rendered_mp4_bytes")
                return MagicMock(returncode=0)

            mock_subprocess.side_effect = fake_run

            out_clip = extract_broll_clip(src, dest, duration=3.5, width=1080, height=1920)
            self.assertEqual(out_clip, dest)
            self.assertTrue(dest.is_file())

            # Check FFmpeg arguments
            mock_subprocess.assert_called_once()
            call_cmd = mock_subprocess.call_args[0][0]
            cmd_str = " ".join(call_cmd)
            self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", cmd_str)
            self.assertNotIn("zoompan", cmd_str)  # No synthetic zoom!
            self.assertIn("-ss 0.000", cmd_str)
            self.assertIn("-t 3.500", cmd_str)


if __name__ == "__main__":
    unittest.main()
