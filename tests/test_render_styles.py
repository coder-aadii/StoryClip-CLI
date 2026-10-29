import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_cli


class RenderStyleTests(unittest.TestCase):
    def render_commands(self, style, width=1280, height=720, zoom=True):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first_image = output / 'first.jpg'
            second_image = output / 'second.jpg'
            pose = output / 'pose.png'
            cta = output / 'cta.png'
            for path in (first_image, second_image, pose, cta):
                path.write_bytes(path.name.encode('utf-8'))
            scenes = [
                {'image': str(first_image), 'duration': 2.0, 'mood': 'horror', 'text': 'डर।'},
                {'image': str(second_image), 'duration': 2.0, 'mood': 'joy', 'text': 'खुशी।'},
            ]
            block = {'start_scene': 0, 'end_scene': 1, 'kind': 'content',
                     'position': 'left', 'from_position': 'center', 'pose': 'test'}
            plan = {'version': 4, 'asset_version': 1, 'blocks': [block]}
            manifest = {'version': 1, 'poses': {'test': {
                'path': pose, 'sha256': 'test', 'alpha_bbox': [0, 0, 10, 10],
            }}}

            def fake_run(command):
                Path(command[-1]).write_bytes(b'video')
                return ''

            with patch.object(video_cli, 'run', side_effect=fake_run) as run, \
                    patch.object(video_cli, 'build_avatar_plan', return_value=plan), \
                    patch.object(video_cli, 'load_avatar_manifest', return_value=manifest), \
                    patch.object(video_cli, 'OUTRO_CTA_IMAGE', cta):
                video_cli.render_scene_segments(scenes, output, 24, width, height, style, zoom)
            return [call.args[0] for call in run.call_args_list]

    def render_filters(self, style, **kwargs):
        return [command[command.index('-filter_complex') + 1]
                for command in self.render_commands(style, **kwargs)]

    def test_auto_applies_each_scene_mood_and_alternating_zoom(self):
        first, second = self.render_filters('auto')
        self.assertIn('scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720', first)
        self.assertIn('1.0+0.12*(((in-1+0)/58)', first)
        self.assertIn('brightness=-0.035:saturation=0.80,vignette=PI/4', first)
        self.assertNotIn('saturation=1.23', first)
        self.assertIn('1.12-0.12*(((in-1+0)/47)', second)
        self.assertIn('brightness=0.018:saturation=1.23:contrast=1.04', second)
        self.assertIn('1.0+0.12*(((in-1+48)/58)', second)
        self.assertIn('xfade=transition=fade:duration=0.458333333:offset=0', second)

    def test_explicit_style_overrides_every_scene_without_changing_motion(self):
        first, second = self.render_filters('neutral')
        for video_filter in (first, second):
            self.assertIn('perspective=eval=frame:sense=source:interpolation=cubic:', video_filter)
            self.assertNotIn('vignette=PI/4', video_filter)
            self.assertNotIn('saturation=1.23', video_filter)

    def test_zoom_samples_fractional_centered_positions_and_each_segment_is_encoded_once(self):
        first, second = self.render_filters('neutral')
        for video_filter in (first, second):
            self.assertIn('format=yuv444p,perspective=eval=frame:', video_filter)
            self.assertIn("x0='W/2-W/(2*", video_filter)
            self.assertIn(":x1='W/2+W/(2*", video_filter)
            self.assertIn('format=yuv420p', video_filter)
            self.assertNotIn('zoompan', video_filter)
        first_command, second_command = self.render_commands('neutral')
        self.assertEqual(first_command.count('-i'), 2)
        self.assertEqual(second_command.count('-i'), 4)
        self.assertEqual(first_command[first_command.index('-frames:v') + 1], '48')
        self.assertEqual(second_command[second_command.index('-frames:v') + 1], '48')
        self.assertNotIn('xfade=', first)

    def test_uhd_zoom_uses_output_resolution_and_no_zoom_skips_perspective(self):
        first, _ = self.render_filters('neutral', width=3840, height=2160)
        self.assertIn('scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160', first)
        self.assertIn('format=yuv444p,perspective=', first)
        first_static, _ = self.render_filters('neutral', zoom=False)
        self.assertNotIn('perspective', first_static)
        self.assertNotIn('yuv444p', first_static)


if __name__ == '__main__':
    unittest.main()
