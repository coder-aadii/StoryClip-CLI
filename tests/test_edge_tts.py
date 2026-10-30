import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import video_cli


class EdgeTtsTests(unittest.TestCase):
    def test_hindi_female_uses_requested_voice_pitch_and_rate(self):
        speaker = Mock()
        speaker.save = AsyncMock()

        with tempfile.TemporaryDirectory() as directory, \
                patch('edge_tts.Communicate', return_value=speaker) as communicate:
            destination = Path(directory) / 'speech.mp3'
            asyncio.run(video_cli.edge_speak('नमस्ते।', destination, 'hi', 'horror'))

        communicate.assert_called_once_with(
            'नमस्ते।', voice='hi-IN-SwaraNeural', pitch='-3Hz', rate='+20%'
        )
        speaker.save.assert_awaited_once_with(str(destination))

    def test_hindi_male_uses_madhur_deep_fast_profile(self):
        speaker = Mock()
        speaker.save = AsyncMock()

        with tempfile.TemporaryDirectory() as directory, \
                patch('edge_tts.Communicate', return_value=speaker) as communicate:
            destination = Path(directory) / 'speech.mp3'
            asyncio.run(video_cli.edge_speak(
                'नमस्ते।', destination, 'hi', provider='edge-male'
            ))

        communicate.assert_called_once_with(
            'नमस्ते।', voice='hi-IN-MadhurNeural', pitch='-10Hz', rate='+18%'
        )
        speaker.save.assert_awaited_once_with(str(destination))

    def test_english_keeps_english_voice_with_requested_pitch_and_rate(self):
        self.assertEqual(
            video_cli.edge_tts_settings('en'),
            {'voice': 'en-US-AriaNeural', 'pitch': '-3Hz', 'rate': '+20%'},
        )

    def test_cli_accepts_male_and_both_female_spellings(self):
        parser = video_cli.build_parser()

        for provider in ('edge-male', 'edge-female', 'edge-femail'):
            with self.subTest(provider=provider):
                args = parser.parse_args(['--script', 'story.txt', '--tts', provider])
                self.assertEqual(args.tts, provider)
        self.assertEqual(
            video_cli.edge_tts_settings('hi', 'edge-femail'),
            {'voice': 'hi-IN-SwaraNeural', 'pitch': '-3Hz', 'rate': '+20%'},
        )

    def test_cache_identity_contains_all_edge_synthesis_settings(self):
        edge = video_cli.tts_cache_identity('एक कहानी।', 'hi', 'edge', 'neutral')
        auto = video_cli.tts_cache_identity('एक कहानी।', 'hi', 'auto', 'neutral')
        female = {'voice': 'hi-IN-SwaraNeural', 'pitch': '-3Hz', 'rate': '+20%'}
        male = video_cli.tts_cache_identity('एक कहानी।', 'hi', 'edge-male', 'neutral')

        self.assertEqual(edge['settings'], female)
        self.assertEqual(auto['settings']['edge'], female)
        self.assertEqual(
            male['settings'],
            {'voice': 'hi-IN-MadhurNeural', 'pitch': '-10Hz', 'rate': '+18%'},
        )
        self.assertNotEqual(video_cli.cache_key(edge), video_cli.cache_key(male))

    def test_gemini_uses_puck_voice_in_cache_identity(self):
        gemini = video_cli.tts_cache_identity('A story.', 'en', 'gemini', 'neutral')
        auto = video_cli.tts_cache_identity('A story.', 'en', 'auto', 'neutral')

        self.assertEqual(video_cli.GEMINI_TTS_VOICE, 'Puck')
        self.assertEqual(gemini['settings']['voice'], 'Puck')
        self.assertEqual(auto['settings']['gemini']['voice'], 'Puck')


if __name__ == '__main__':
    unittest.main()
