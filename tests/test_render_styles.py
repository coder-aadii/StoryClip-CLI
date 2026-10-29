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
            first_image.touch()
            second_image.touch()
            scenes = [
                {'image': str(first_image), 'duration': 2.0, 'mood': 'horror', 'text': 'डर।'},
                {'image': str(second_image), 'duration': 2.0, 'mood': 'joy', 'text': 'खुशी।'},
            ]
            with patch.object(video_cli, 'run', return_value='') as run, \
                    patch.object(video_cli, 'compose_avatar_scene_clips', return_value=([], {'blocks': []})), \
                    patch.object(video_cli, 'merge_scene_clips', return_value=output / 'visual_only.mp4'):
                video_cli.render(scenes, output / 'narration.wav', None, output,
                                 width=width, height=height, style=style, zoom=zoom)

            return [call.args[0] for call in run.call_args_list[:2]]

    def render_filters(self, style, **kwargs):
        return [command[command.index('-vf') + 1]
                for command in self.render_commands(style, **kwargs)]

    def test_auto_applies_each_scene_mood_and_keeps_smooth_alternating_zoom(self):
        first, second = self.render_filters('auto')

        self.assertIn('scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720', first)
        self.assertIn("x0='W/2-W/(2*(1.0+0.12*(((in-1)/58)*((in-1)/58)*(3-2*((in-1)/58)))))'", first)
        self.assertIn('brightness=-0.035:saturation=0.80,vignette=PI/4', first)
        self.assertNotIn('saturation=1.23', first)

        self.assertIn('scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720', second)
        self.assertIn("x0='W/2-W/(2*(1.12-0.12*(((in-1)/47)*((in-1)/47)*(3-2*((in-1)/47)))))'", second)
        self.assertIn('brightness=0.018:saturation=1.23:contrast=1.04', second)
        self.assertNotIn('vignette=PI/4', second)

    def test_explicit_style_overrides_every_scene_without_changing_motion(self):
        first, second = self.render_filters('neutral')

        self.assertIn('perspective=eval=frame:sense=source:interpolation=cubic:', first)
        self.assertIn('perspective=eval=frame:sense=source:interpolation=cubic:', second)
        for video_filter in (first, second):
            self.assertNotIn('vignette=PI/4', video_filter)
            self.assertNotIn('saturation=1.23', video_filter)

    def test_zoom_samples_fractional_centered_positions_at_output_resolution(self):
        first, second = self.render_filters('neutral')

        for video_filter in (first, second):
            self.assertIn('format=yuv444p,perspective=eval=frame:', video_filter)
            self.assertIn("x0='W/2-W/(2*", video_filter)
            self.assertIn(":x1='W/2+W/(2*", video_filter)
            self.assertIn(":y2='H/2+H/(2*", video_filter)
            self.assertIn(":x3='W/2+W/(2*", video_filter)
            self.assertIn('format=yuv420p', video_filter)
            self.assertNotIn('zoompan', video_filter)
        first_command, second_command = self.render_commands('neutral')
        self.assertEqual(first_command[first_command.index('-frames:v') + 1], '59')
        self.assertEqual(second_command[second_command.index('-frames:v') + 1], '48')

    def test_uhd_zoom_uses_output_resolution_and_no_zoom_skips_perspective(self):
        first, _ = self.render_filters('neutral', width=3840, height=2160)
        self.assertIn('scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160', first)
        self.assertIn('format=yuv444p,perspective=', first)
        first_static, _ = self.render_filters('neutral', zoom=False)
        self.assertNotIn('perspective', first_static)
        self.assertNotIn('yuv444p', first_static)


if __name__ == '__main__':
    unittest.main()
