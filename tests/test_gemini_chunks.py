import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_cli


class GeminiChunkTests(unittest.TestCase):
    def test_larger_chunks_preserve_sentences_and_other_provider_behavior(self):
        for language, ending in [('en', '.'), ('hi', '।')]:
            with self.subTest(language=language):
                sentence = ' '.join(['word'] * 20) + ending
                narration = ' '.join([sentence] * 12)
                short = video_cli.narration_groups(narration, language)
                longer = video_cli.narration_groups(narration, language, 'gemini')
                self.assertLess(len(longer), len(short))
                self.assertEqual(' '.join(longer), narration)
                self.assertTrue(all(chunk.endswith(ending) for chunk in longer))
                wps = 1.75 if language == 'hi' else 2.25
                self.assertTrue(all(len(chunk.split()) / wps <= 45 for chunk in longer))
                self.assertEqual(video_cli.narration_groups(narration, language, 'auto'), short)

    def test_paragraph_breaks_and_long_sentences_remain_intact(self):
        long_sentence = ' '.join(['word'] * 150) + '.'
        text = f'First paragraph.\n\nSecond paragraph.\n\n{long_sentence}'
        self.assertEqual(
            video_cli.narration_groups(text, 'en', 'gemini'),
            ['First paragraph.', 'Second paragraph.', long_sentence],
        )
        self.assertEqual(video_cli.narration_groups('', 'en', 'gemini'), [])

    def test_cli_saves_and_resumes_paragraph_plan_and_rejects_legacy_layout(self):
        narration = ' '.join(['This is a sentence with enough words for the scene planner.'] * 16)
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'input.txt'
            script.write_text(narration, encoding='utf-8')
            output = Path(directory) / 'project'
            argv = ['video_cli.py', '--script', str(script), '--language', 'en',
                    '--tts', 'gemini', '--dry-run', '--output', str(output)]
            with patch.object(sys, 'argv', argv), \
                    patch.object(video_cli, 'require_pipeline_prerequisites'), \
                    patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}), \
                    patch.object(video_cli, 'gemini_json', return_value={'queries': []}) as api:
                video_cli.main()
                manifest = output / 'project.json'
                project = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertEqual(project['scene_chunking'], 'gemini-paragraph-v1')
                self.assertEqual(project['scenes'][0]['kind'], 'intro')
                self.assertEqual(project['scenes'][-1]['kind'], 'outro')
                self.assertEqual(
                    [scene['text'] for scene in project['scenes'][1:-1]],
                    video_cli.narration_groups(narration, 'en', 'gemini'),
                )
                api.reset_mock()
                video_cli.main()
                api.assert_not_called()
                del project['scene_chunking']
                manifest.write_text(json.dumps(project), encoding='utf-8')
                before = manifest.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'different narration chunk size'):
                    video_cli.main()
                self.assertEqual(manifest.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
