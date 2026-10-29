import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_cli


class SceneSegmentTests(unittest.TestCase):
    def fixture(self, root, durations=(1.02, 1.02, 1.02)):
        scenes = []
        for index, duration in enumerate(durations):
            image = root / f'image-{index}.jpg'
            image.write_bytes(f'image {index}'.encode())
            scenes.append({'image': str(image), 'duration': duration,
                           'mood': 'neutral', 'text': f'Scene {index}'})
        pose = root / 'pose.png'
        pose.write_bytes(b'pose')
        cta = root / 'cta.png'
        cta.write_bytes(b'cta')
        block = {'start_scene': 0, 'end_scene': len(scenes) - 1,
                 'kind': 'content', 'position': 'left',
                 'from_position': 'center', 'pose': 'test'}
        plan = {'version': 4, 'asset_version': 1, 'blocks': [block]}
        manifest = {'version': 1, 'poses': {'test': {
            'path': pose, 'sha256': 'pose-digest', 'alpha_bbox': [0, 0, 10, 10],
        }}}
        return scenes, plan, manifest, cta

    def test_cumulative_frame_boundaries_cover_one_two_and_many_scenes(self):
        for durations, expected in (
            ((1.02,), [24]),
            ((1.02, 1.02), [24, 25]),
            ((1.02, 1.02, 1.02), [24, 25, 24]),
        ):
            scenes = [{'duration': duration} for duration in durations]
            self.assertEqual(video_cli.scene_frame_counts(scenes, 24), expected)
            self.assertEqual(sum(expected), round(sum(durations) * 24))
        with self.assertRaisesRegex(ValueError, 'shorter than one video frame'):
            video_cli.scene_frame_counts([{'duration': 0.001}], 24)

    def test_cache_reuses_unchanged_segments_and_invalidates_only_image_and_next_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenes, plan, manifest, cta = self.fixture(root)

            def fake_run(command):
                Path(command[-1]).write_bytes(b'encoded segment')
                return ''

            with patch.object(video_cli, 'run', side_effect=fake_run) as run, \
                    patch.object(video_cli, 'build_avatar_plan', return_value=plan), \
                    patch.object(video_cli, 'load_avatar_manifest', return_value=manifest), \
                    patch.object(video_cli, 'OUTRO_CTA_IMAGE', cta):
                video_cli.render_scene_segments(scenes, root, 24, 320, 180, 'neutral', True)
                self.assertEqual(run.call_count, 3)
                video_cli.render_scene_segments(scenes, root, 24, 320, 180, 'neutral', True)
                self.assertEqual(run.call_count, 3)
                Path(scenes[1]['image']).write_bytes(b'changed image')
                video_cli.render_scene_segments(scenes, root, 24, 320, 180, 'neutral', True)
                self.assertEqual(run.call_count, 5)
                self.assertEqual([Path(call.args[0][-1]).name for call in run.call_args_list[3:]],
                                 ['0001.part.mp4', '0002.part.mp4'])

    def test_failed_middle_scene_preserves_previous_segment_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenes, plan, manifest, cta = self.fixture(root)
            calls = []

            def flaky_run(command):
                calls.append(Path(command[-1]).name)
                if len(calls) == 2:
                    raise RuntimeError('render failed')
                Path(command[-1]).write_bytes(b'encoded segment')
                return ''

            with patch.object(video_cli, 'run', side_effect=flaky_run), \
                    patch.object(video_cli, 'build_avatar_plan', return_value=plan), \
                    patch.object(video_cli, 'load_avatar_manifest', return_value=manifest), \
                    patch.object(video_cli, 'OUTRO_CTA_IMAGE', cta):
                with self.assertRaisesRegex(RuntimeError, 'render failed'):
                    video_cli.render_scene_segments(scenes, root, 24, 320, 180, 'neutral', True)
                self.assertTrue((root / 'clips' / '0000.mp4').is_file())
                self.assertTrue((root / 'clips' / '0000.sha256').is_file())
                video_cli.render_scene_segments(scenes, root, 24, 320, 180, 'neutral', True)
                self.assertEqual(calls, ['0000.part.mp4', '0001.part.mp4',
                                         '0001.part.mp4', '0002.part.mp4'])

    def test_join_uses_stream_copy_and_validates_exact_frame_count(self):
        with tempfile.TemporaryDirectory(prefix="storyclip' join ") as directory:
            root = Path(directory)
            clips = root / 'clips'
            clips.mkdir()
            paths = [clips / '0000.mp4', clips / '0001.mp4']
            for path in paths:
                path.write_bytes(b'encoded segment')
            commands = []
            frame_count = ['49']

            def fake_run(command):
                commands.append(command)
                if command[0] == 'ffprobe':
                    return json.dumps({'streams': [{'codec_name': 'h264', 'width': 320,
                                                    'height': 180,
                                                    'nb_read_frames': frame_count[0]}]})
                Path(command[-1]).write_bytes(b'joined video')
                return ''

            with patch.object(video_cli, 'run', side_effect=fake_run):
                visual = video_cli.join_scene_segments(paths, ['a', 'b'], [24, 25],
                                                       root, 24, 320, 180)
                self.assertTrue(visual.is_file())
                self.assertIn(['-c:v', 'copy'], [commands[0][index:index + 2]
                                                 for index in range(len(commands[0]) - 1)])
                self.assertNotIn('libx264', commands[0])
                self.assertIn("'\\''", (clips / 'segments.ffconcat').read_text(encoding='utf-8'))
                video_cli.join_scene_segments(paths, ['a', 'b'], [24, 25], root, 24, 320, 180)
                self.assertEqual(len(commands), 2)
                frame_count[0] = '48'
                with self.assertRaisesRegex(RuntimeError, 'expected 49 H.264 frames'):
                    video_cli.join_scene_segments(paths, ['a', 'changed'], [24, 25],
                                                   root, 24, 320, 180)
                self.assertTrue(visual.is_file())
                self.assertTrue(all(path.is_file() for path in paths))


if __name__ == '__main__':
    unittest.main()
