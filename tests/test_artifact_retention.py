import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import video_cli


class ArtifactRetentionTests(unittest.TestCase):
    @staticmethod
    def fake_images(scenes, theme, folder):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'fallback_11.jpg').write_bytes(b'photo')
        image = folder / f'scene_{"a" * 20}.jpg'
        image.write_bytes(b'photo')
        for scene in scenes:
            scene.update(image=str(image), credit='Photographer', pexels_url='https://www.pexels.com/photo/11')
        return scenes

    @staticmethod
    def fake_narration(scenes, folder, language, provider, indic_url, indic_voice):
        folder.mkdir(parents=True, exist_ok=True)
        cache = folder / f'0000_{"b" * 20}.wav'
        cache.write_bytes(b'cached speech')
        cache.with_suffix('.json').write_text('{}', encoding='utf-8')
        (folder / 'narration_concat.txt').write_text('concat', encoding='utf-8')
        narration = folder / 'narration.wav'
        narration.write_bytes(b'joined speech')
        for scene in scenes:
            scene['voice'] = str(cache)
            scene['duration'] = 1.0
        return narration

    @staticmethod
    def fake_render(scenes, narration, music, output_dir, *args, **kwargs):
        (output_dir / 'visual_only.mp4').write_bytes(b'visual')
        (output_dir / 'visual_only.sha256').write_text('signature', encoding='utf-8')
        (output_dir / 'visual_only.part.mp4').write_bytes(b'stale join')
        (output_dir / 'avatar_plan.json').write_text('{}', encoding='utf-8')
        clips = output_dir / 'clips'
        clips.mkdir(exist_ok=True)
        (clips / '0000.mp4').write_bytes(b'clip')
        (clips / '0000.sha256').write_text('signature', encoding='utf-8')
        (clips / 'segments.ffconcat').write_text('ffconcat version 1.0\n', encoding='utf-8')
        avatar = clips / 'avatar'
        avatar.mkdir(exist_ok=True)
        (avatar / '0000.mp4').write_bytes(b'avatar')
        xfade = clips / 'xfade'
        xfade.mkdir(exist_ok=True)
        (xfade / 'pass-00-0000.mp4').write_bytes(b'crossfade')
        destination = kwargs['final_output']
        destination.write_bytes(b'complete mux')
        return destination

    @contextmanager
    def pipeline(self, *, keys=True, render=None, validate=None):
        credentials = {
            'GEMINI_API_KEY': 'test-key' if keys else '',
            'PEXELS_API_KEY': 'test-key' if keys else '',
            'GEMINI_TTS_MODEL': 'test-model',
            'GEMINI_TEXT_MODEL': 'test-text-model',
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, credentials))
            tools = SimpleNamespace(
                check=stack.enter_context(patch.object(video_cli, 'check_tools')),
                planner=stack.enter_context(patch.object(
                    video_cli, 'plan_scenes', return_value=[{
                        'text': 'The river runs through the village.',
                        'query': 'river village', 'mood': 'neutral', 'image': None,
                    }],
                )),
                images=stack.enter_context(patch.object(
                    video_cli, 'gather_images', side_effect=self.fake_images,
                )),
                narration=stack.enter_context(patch.object(
                    video_cli, 'narrate', side_effect=self.fake_narration,
                )),
                avatar=stack.enter_context(patch.object(
                    video_cli, 'build_avatar_plan', return_value={'version': 1, 'blocks': []},
                )),
                renderer=stack.enter_context(patch.object(
                    video_cli, 'render', side_effect=render or self.fake_render,
                )),
                validator=stack.enter_context(patch.object(
                    video_cli, 'validate_final_video', side_effect=validate, return_value=3.0,
                )),
            )
            yield tools

    @staticmethod
    def run_cli(script, output, *options):
        argv = ['video_cli.py', '--script', str(script), '--language', 'en',
                '--output', str(output), '--skip-music', *options]
        with patch.object(sys, 'argv', argv):
            video_cli.main()

    def test_default_success_retains_three_files_and_truthful_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('The river runs through the village.', encoding='utf-8')
            output = base / 'custom-video'
            with self.pipeline() as tools:
                self.run_cli(script, output)
                self.assertEqual(tools.renderer.call_count, 1)
                self.assertEqual(tools.validator.call_count, 1)
            self.assertEqual({path.name for path in output.iterdir()},
                             {'final.mp4', 'subtitles.srt', 'project.json'})
            project = json.loads((output / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual(project['status'], 'complete')
            self.assertEqual(project['intermediates'], 'delete_on_success')
            self.assertEqual(project['cleanup']['status'], 'done')
            self.assertEqual(project['final_artifact']['duration'], 3.0)
            self.assertEqual((project['final_artifact']['width'], project['final_artifact']['height']),
                             (1280, 720))
            self.assertTrue(project['final_artifact']['completed_at'])
            self.assertEqual(len(project['render_fingerprint']), 64)
            self.assertEqual(project['requested_settings']['resolution'], '720p')
            self.assertEqual(project['requested_settings']['tts'], 'edge')
            self.assertEqual(project['content_narration'], script.read_text(encoding='utf-8'))
            self.assertEqual(project['scenes'][1]['credit'], 'Photographer')
            self.assertEqual(project['scenes'][1]['pexels_url'], 'https://www.pexels.com/photo/11')
            self.assertTrue(all('image' not in scene and 'voice' not in scene
                                for scene in project['scenes']))
            self.assertTrue(script.is_file())

    def test_keep_option_and_completed_noop_even_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('A small story.', encoding='utf-8')
            output = base / 'video'
            with self.pipeline():
                self.run_cli(script, output, '--keep-intermediates')
            self.assertTrue((output / 'images').is_dir())
            self.assertTrue((output / 'voice' / 'narration.wav').is_file())
            self.assertTrue((output / 'clips' / 'avatar' / '0000.mp4').is_file())
            project = json.loads((output / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual((project['status'], project['intermediates'], project['cleanup']['status']),
                             ('complete', 'keep', 'kept'))
            self.assertIn('voice', project['scenes'][0])
            with self.pipeline(keys=False) as tools:
                self.run_cli(script, output)
                tools.check.assert_not_called()
                tools.images.assert_not_called()
                tools.narration.assert_not_called()
                tools.renderer.assert_not_called()

    def test_cleaned_noop_rejects_changed_settings_and_late_keep(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('A small story.', encoding='utf-8')
            output = base / 'video'
            with self.pipeline():
                self.run_cli(script, output)
            with self.pipeline(keys=False) as tools:
                self.run_cli(script, output)
                tools.images.assert_not_called()
                tools.narration.assert_not_called()
                tools.renderer.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, 'settings differ'):
                    self.run_cli(script, output, '--fps', '25')
                with patch.dict(os.environ, {'GEMINI_TEXT_MODEL': 'changed-model'}):
                    with self.assertRaisesRegex(RuntimeError, 'settings differ'):
                        self.run_cli(script, output)
                with self.assertRaisesRegex(RuntimeError, 'cannot restore'):
                    self.run_cli(script, output, '--keep-intermediates')

    def test_failed_render_and_failed_validation_keep_recoverable_work(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('A small story.', encoding='utf-8')
            output = base / 'video'
            with self.pipeline(render=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('mux failed'))):
                with self.assertRaisesRegex(RuntimeError, 'mux failed'):
                    self.run_cli(script, output)
            project = json.loads((output / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual(project['status'], 'in_progress')
            self.assertTrue((output / 'voice' / 'narration.wav').is_file())
            with self.pipeline(validate=RuntimeError('bad probe')):
                with self.assertRaisesRegex(RuntimeError, 'bad probe'):
                    self.run_cli(script, output)
            self.assertTrue((output / 'final.part.mp4').is_file())
            self.assertFalse((output / 'final.mp4').exists())
            self.assertTrue((output / 'images').is_dir())
            with self.pipeline():
                self.run_cli(script, output)
            self.assertTrue((output / 'final.mp4').is_file())

    def test_incomplete_project_keeps_saved_retention_policy_on_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('A small story.', encoding='utf-8')
            output = base / 'video'
            with self.pipeline(render=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('mux failed'))):
                with self.assertRaisesRegex(RuntimeError, 'mux failed'):
                    self.run_cli(script, output, '--keep-intermediates')
            with self.pipeline():
                self.run_cli(script, output)
            project = json.loads((output / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual(project['intermediates'], 'keep')
            self.assertTrue((output / 'voice' / 'narration.wav').is_file())

    def test_completion_record_failure_does_not_cleanup_and_symlinked_mux_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('A small story.', encoding='utf-8')
            output = base / 'video'
            real_writer = video_cli.write_json_atomic

            def fail_completion(path, project):
                if project.get('status') == 'cleaning':
                    raise OSError('manifest write failed')
                real_writer(path, project)

            with self.pipeline(), patch.object(video_cli, 'write_json_atomic',
                                               side_effect=fail_completion):
                with self.assertRaisesRegex(OSError, 'manifest write failed'):
                    self.run_cli(script, output)
            self.assertEqual(json.loads((output / 'project.json').read_text(encoding='utf-8'))['status'],
                             'in_progress')
            self.assertTrue((output / 'voice' / 'narration.wav').is_file())
            self.assertTrue((output / 'final.mp4').is_file())

            external = base / 'external.mp4'
            external.write_bytes(b'untouched')
            (output / 'final.part.mp4').symlink_to(external)
            with self.pipeline() as tools:
                with self.assertRaisesRegex(RuntimeError, 'symlinked final.part.mp4'):
                    self.run_cli(script, output)
                tools.renderer.assert_not_called()
            self.assertEqual(external.read_bytes(), b'untouched')

    def test_interrupted_cleanup_resumes_without_pipeline_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'input.txt'
            script.write_text('A small story.', encoding='utf-8')
            output = base / 'video'

            def interrupted_cleanup(folder, source, protected_paths):
                (folder / 'script.txt').unlink()
                raise OSError('cleanup interrupted')

            with self.pipeline(), patch.object(video_cli, 'cleanup_project_artifacts',
                                               side_effect=interrupted_cleanup):
                with self.assertRaisesRegex(OSError, 'cleanup interrupted'):
                    self.run_cli(script, output)
            project = json.loads((output / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual(project['status'], 'cleaning')
            self.assertTrue((output / 'final.mp4').is_file())
            self.assertTrue((output / 'voice' / 'narration.wav').is_file())
            with self.pipeline(keys=False) as tools:
                self.run_cli(script, output)
                tools.check.assert_not_called()
                tools.images.assert_not_called()
                tools.narration.assert_not_called()
                tools.renderer.assert_not_called()
            self.assertEqual({path.name for path in output.iterdir()},
                             {'final.mp4', 'subtitles.srt', 'project.json'})
            self.assertEqual(json.loads((output / 'project.json').read_text(encoding='utf-8'))['status'],
                             'complete')

    def test_cleanup_confines_paths_and_preserves_unknown_and_supplied_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / 'custom-output'
            output.mkdir()
            source = {'kind': 'script', 'sha256': 'test'}
            (output / 'project.json').write_text(
                json.dumps({'status': 'cleaning', 'source': source}), encoding='utf-8',
            )
            (output / 'final.mp4').write_bytes(b'final')
            (output / 'subtitles.srt').write_text('subtitle', encoding='utf-8')
            (output / 'script.txt').write_text('user supplied script', encoding='utf-8')
            (output / 'video_script.txt').write_text('generated', encoding='utf-8')
            (output / 'notes.txt').write_text('user notes', encoding='utf-8')
            external = base / 'external.jpg'
            external.write_bytes(b'external image')
            images = output / 'images'
            images.mkdir()
            (images / f'scene_{"a" * 20}.jpg').symlink_to(external)
            (images / 'fallback_11.jpg').write_bytes(b'generated')
            (images / 'my-photo.jpg').write_bytes(b'user image')
            external_clips = base / 'external-clips'
            external_clips.mkdir()
            (external_clips / '0000.mp4').write_bytes(b'external clip')
            (output / 'clips').symlink_to(external_clips, target_is_directory=True)
            music = output / 'music'
            music.mkdir()
            supplied_bgm = music / 'jamendo_123.mp3'
            supplied_bgm.write_bytes(b'user supplied music')

            for _ in range(2):
                unknown = video_cli.cleanup_project_artifacts(output, source,
                    (output / 'script.txt', supplied_bgm))
                self.assertIn('notes.txt', unknown)
            self.assertTrue((output / 'script.txt').is_file())
            self.assertTrue(supplied_bgm.is_file())
            self.assertFalse((output / 'video_script.txt').exists())
            self.assertFalse((images / 'fallback_11.jpg').exists())
            self.assertTrue((images / 'my-photo.jpg').is_file())
            self.assertTrue((images / f'scene_{"a" * 20}.jpg').is_symlink())
            self.assertEqual(external.read_bytes(), b'external image')
            self.assertEqual((external_clips / '0000.mp4').read_bytes(), b'external clip')
            with self.assertRaisesRegex(RuntimeError, 'Refusing cleanup'):
                video_cli.cleanup_project_artifacts(base, source)
            output_root = base / 'outputs'
            output_root.mkdir()
            with patch.object(video_cli, '__file__', str(base / 'video_cli.py')):
                with self.assertRaisesRegex(RuntimeError, 'broad output directory'):
                    video_cli.cleanup_project_artifacts(output_root, source)

    def test_ffprobe_validation_checks_streams_dimensions_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'final.part.mp4'
            video.write_bytes(b'video')
            probe = {'format': {'duration': '3.02'}, 'streams': [
                {'codec_type': 'video', 'width': 1280, 'height': 720},
                {'codec_type': 'audio'},
            ]}
            with patch.object(video_cli, 'run', return_value=json.dumps(probe)):
                self.assertEqual(video_cli.validate_final_video(video, 3, 1280, 720), 3.02)
            probe['streams'].pop()
            with patch.object(video_cli, 'run', return_value=json.dumps(probe)):
                with self.assertRaisesRegex(RuntimeError, 'video/audio streams'):
                    video_cli.validate_final_video(video, 3, 1280, 720)
            probe['streams'].append({'codec_type': 'audio'})
            probe['format']['duration'] = '12.0'
            with patch.object(video_cli, 'run', return_value=json.dumps(probe)):
                with self.assertRaisesRegex(RuntimeError, 'does not match narration'):
                    video_cli.validate_final_video(video, 3, 1280, 720)


if __name__ == '__main__':
    unittest.main()
