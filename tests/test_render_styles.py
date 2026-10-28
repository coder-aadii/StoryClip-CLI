import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_cli


class RenderStyleTests(unittest.TestCase):
    def render_filters(self, style):
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
            with patch.object(video_cli, 'run', return_value='') as run:
                video_cli.render(scenes, output / 'narration.wav', None, output, style=style)

            filters = []
            for call in run.call_args_list[:2]:
                command = call.args[0]
                filters.append(command[command.index('-vf') + 1])
            return filters

    def test_auto_applies_each_scene_mood_and_keeps_smooth_alternating_zoom(self):
        first, second = self.render_filters('auto')

        self.assertIn('scale=5120:2880:force_original_aspect_ratio=increase,crop=5120:2880', first)
        self.assertIn("zoompan=z='1.0+0.12*((on/58)*(on/58)*(3-2*(on/58)))'", first)
        self.assertIn('brightness=-0.035:saturation=0.80,vignette=PI/4', first)
        self.assertNotIn('saturation=1.23', first)

        self.assertIn('scale=5120:2880:force_original_aspect_ratio=increase,crop=5120:2880', second)
        self.assertIn("zoompan=z='1.12-0.12*((on/47)*(on/47)*(3-2*(on/47)))'", second)
        self.assertIn('brightness=0.018:saturation=1.23:contrast=1.04', second)
        self.assertNotIn('vignette=PI/4', second)

    def test_explicit_style_overrides_every_scene_without_changing_motion(self):
        first, second = self.render_filters('neutral')

        self.assertIn("zoompan=z='1.0+0.12*((on/58)*(on/58)*(3-2*(on/58)))'", first)
        self.assertIn("zoompan=z='1.12-0.12*((on/47)*(on/47)*(3-2*(on/47)))'", second)
        for video_filter in (first, second):
            self.assertNotIn('vignette=PI/4', video_filter)
            self.assertNotIn('saturation=1.23', video_filter)

    def test_zoom_uses_the_existing_frame_count_and_center_alignment(self):
        first, second = self.render_filters('neutral')

        self.assertIn("x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=59:s=1280x720:fps=24", first)
        self.assertIn("x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=48:s=1280x720:fps=24", second)


if __name__ == '__main__':
    unittest.main()
