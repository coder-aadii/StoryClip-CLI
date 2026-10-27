import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_cli


class ProgressTests(unittest.TestCase):
    def test_progress_message_includes_bar_percentage_and_eta(self):
        message = video_cli.progress_message('Rendering', 2, 4, started=100, now=130)

        self.assertIn('[##########----------]', message)
        self.assertIn('50.0% (2/4)', message)
        self.assertIn('ETA 30s', message)

    def test_completed_progress_uses_elapsed_time(self):
        message = video_cli.progress_message('Narration', 4, 4, started=100, now=165)

        self.assertIn('[####################]', message)
        self.assertIn('100.0% (4/4)', message)
        self.assertIn('elapsed 1m 05s', message)


class AutomaticOutputTests(unittest.TestCase):
    def test_narrative_prompt_requests_structured_output_slug(self):
        result = {
            'title': 'Lost Child at the Fair',
            'output_slug': 'lost-child-at-the-fair',
            'narration': 'A sufficiently long narration for validating the structured response.',
            'theme': 'colorful village fair',
            'music_moods': ['hopeful', 'warm'],
        }
        with patch.object(video_cli, 'gemini_json', return_value=result) as gemini:
            self.assertEqual(video_cli.narrative_from_idea('A child at a fair', 'en', 2), result)

        self.assertIn('"output_slug"', gemini.call_args.args[0])
        self.assertIn('lowercase ASCII', gemini.call_args.args[0])

    def test_slug_is_sanitized_and_has_non_ascii_fallback(self):
        self.assertEqual(
            video_cli.safe_output_slug(' Lost Child: At The Fair! ', 'seed'),
            'lost-child-at-the-fair',
        )
        self.assertRegex(video_cli.safe_output_slug('मेले की कहानी', 'seed'), r'^story-[a-f0-9]{10}$')

    def test_omitted_output_uses_story_slug_and_resumes_without_llm(self):
        generated = {
            'title': 'Lost Child at the Fair',
            'output_slug': 'lost-child-at-the-fair',
            'narration': 'यह परीक्षण के लिए पर्याप्त लंबी और स्वाभाविक हिंदी कहानी है।',
            'theme': 'colorful village fair',
            'music_moods': ['hopeful', 'warm'],
        }
        scenes = [{'text': generated['narration'], 'query': 'village fair', 'mood': 'neutral'}]
        argv = [
            'video_cli.py', '--idea', 'मेले में खोया बच्चा', '--language', 'hi',
            '--minutes', '5', '--dry-run',
        ]

        previous_directory = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                with patch.object(sys, 'argv', argv), \
                        patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}), \
                        patch.object(video_cli, 'check_tools'), \
                        patch.object(video_cli, 'narrative_from_idea', return_value=generated) as narrative, \
                        patch.object(video_cli, 'plan_scenes', return_value=scenes) as planner:
                    video_cli.main()

                output = Path('outputs/lost-child-at-the-fair')
                manifest = output / 'project.json'
                self.assertTrue(manifest.is_file())
                project = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertEqual(project['output_slug'], 'lost-child-at-the-fair')
                self.assertEqual(project['source']['kind'], 'idea')
                self.assertEqual(len(project['source']['sha256']), 64)
                narrative.assert_called_once()
                planner.assert_called_once()

                with patch.object(sys, 'argv', argv), \
                        patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}), \
                        patch.object(video_cli, 'check_tools'), \
                        patch.object(video_cli, 'narrative_from_idea') as narrative, \
                        patch.object(video_cli, 'plan_scenes') as planner:
                    video_cli.main()

                narrative.assert_not_called()
                planner.assert_not_called()
        finally:
            os.chdir(previous_directory)


if __name__ == '__main__':
    unittest.main()
