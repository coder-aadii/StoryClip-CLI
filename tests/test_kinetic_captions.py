import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import video_cli


class KineticCaptionsTests(unittest.TestCase):
    def test_format_ass_timestamp(self):
        self.assertEqual(video_cli.format_ass_timestamp(0), "0:00:00.00")
        self.assertEqual(video_cli.format_ass_timestamp(1.25), "0:00:01.25")
        self.assertEqual(video_cli.format_ass_timestamp(65.5), "0:01:05.50")
        self.assertEqual(video_cli.format_ass_timestamp(3661.12), "1:01:01.12")

    def test_generate_kinetic_ass_skips_bookends_and_animates_content(self):
        scenes = [
            {'kind': 'intro', 'start': 0.0, 'duration': 3.5, 'text': 'Intro greeting.'},
            {'kind': 'content', 'start': 3.5, 'duration': 4.0, 'text': 'Supermarkets use *secret* psychology.'},
            {'kind': 'content', 'start': 7.5, 'duration': 3.0, 'text': 'Carts make you buy 40% more items.'},
            {'kind': 'outro', 'start': 10.5, 'duration': 3.0, 'text': 'Subscribe for more facts!'},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = Path(tmpdir) / 'captions.ass'
            video_cli.generate_kinetic_ass(scenes, ass_path, width=1080, height=1920)

            self.assertTrue(ass_path.is_file())
            content = ass_path.read_text(encoding='utf-8')

            # Intro and Outro must be completely skipped
            self.assertNotIn("Intro greeting", content)
            self.assertNotIn("INTRO GREETING", content)
            self.assertNotIn("Subscribe for more", content)
            self.assertNotIn("SUBSCRIBE FOR MORE", content)

            # Content words must be present one at a time in uppercase
            self.assertIn("SUPERMARKETS", content)
            self.assertIn("PSYCHOLOGY", content)

            # Check kinetic pop animation tags (1.18x -> 1.0x over 100ms)
            self.assertIn(r"\an5\pos(540,1113)\fscx118\fscy118\t(0,100,\fscx100\fscy100)", content)

            # Check gold highlighting for starred word and percentage
            self.assertIn(r"{\c&H3AD2FF&}SECRET{\c&HFFFFFF&}", content)
            self.assertIn(r"{\c&H3AD2FF&}40%{\c&HFFFFFF&}", content)

    def test_generate_kinetic_ass_font_selection(self):
        scenes = [
            {'kind': 'content', 'start': 0.0, 'duration': 2.0, 'text': 'Word test.'}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_en = Path(tmpdir) / 'captions_en.ass'
            video_cli.generate_kinetic_ass(scenes, ass_en, language='en')
            self.assertIn("Style: Kinetic,Fredoka,", ass_en.read_text(encoding='utf-8'))

            ass_hi = Path(tmpdir) / 'captions_hi.ass'
            video_cli.generate_kinetic_ass(scenes, ass_hi, language='hi')
            self.assertIn("Style: Kinetic,Poppins,", ass_hi.read_text(encoding='utf-8'))

    def test_cli_captions_defaults(self):
        parser = video_cli.build_parser()

        # Shorts format defaults to captions=True
        args_shorts = parser.parse_args(['--idea', 'Test', '--format', 'shorts'])
        video_cli.validate_cli_args(parser, args_shorts)
        self.assertTrue(args_shorts.captions)

        # Landscape format defaults to captions=False
        args_landscape = parser.parse_args(['--idea', 'Test', '--format', 'landscape'])
        video_cli.validate_cli_args(parser, args_landscape)
        self.assertFalse(args_landscape.captions)

        # Explicit --no-captions on shorts overrides default
        args_no_caps = parser.parse_args(['--idea', 'Test', '--format', 'shorts', '--no-captions'])
        video_cli.validate_cli_args(parser, args_no_caps)
        self.assertFalse(args_no_caps.captions)

        # Explicit --captions on landscape enables it
        args_explicit = parser.parse_args(['--idea', 'Test', '--format', 'landscape', '--captions'])
        video_cli.validate_cli_args(parser, args_explicit)
        self.assertTrue(args_explicit.captions)

    @patch('video_cli.run')
    @patch('video_cli.join_scene_segments')
    @patch('video_cli.render_scene_segments')
    def test_render_with_captions_invokes_subtitles_filter(self, mock_segments, mock_join, mock_run):
        scenes = [
            {'kind': 'content', 'start': 0.0, 'duration': 3.0, 'text': 'Short kinetic clip.'}
        ]
        mock_segments.return_value = (['c1.mp4'], ['sig1'], [72], None, {'blocks': []})
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            visual_path = out_dir / 'visual_only.mp4'
            visual_path.touch()
            mock_join.return_value = visual_path

            voice_path = out_dir / 'voice.mp3'
            voice_path.touch()

            video_cli.render(
                scenes, voice_path, None, out_dir, fps=24, width=1080, height=1920,
                burn_captions=True,
            )

            # Check that ffmpeg was invoked with ass filter and complex shaping
            self.assertTrue(mock_run.called)
            ffmpeg_cmd = mock_run.call_args[0][0]
            self.assertIn('-vf', ffmpeg_cmd)
            vf_index = ffmpeg_cmd.index('-vf')
            self.assertIn('ass=', ffmpeg_cmd[vf_index + 1])
            self.assertIn('shaping=complex', ffmpeg_cmd[vf_index + 1])
            self.assertIn('-c:v', ffmpeg_cmd)
            cv_index = ffmpeg_cmd.index('-c:v')
            self.assertEqual(ffmpeg_cmd[cv_index + 1], 'libx264')


if __name__ == '__main__':
    unittest.main()
