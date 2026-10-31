import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import video_cli
import tts_service


class RateLimitError(Exception):
    code = 429


class GeminiTtsRetryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
        environment.start()
        self.addCleanup(environment.stop)

    def response_with_pcm(self):
        blob = SimpleNamespace(data=b'\x01\x00' * 240)
        part = SimpleNamespace(inline_data=blob)
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))]
        )

    def test_rate_limit_honors_retry_delay_then_writes_audio(self):
        error = RateLimitError(
            "429 RESOURCE_EXHAUSTED: Please retry in 8.491882765s; "
            "'retryDelay': '8s'"
        )
        generate = Mock(side_effect=[error, self.response_with_pcm()])
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with tempfile.TemporaryDirectory() as directory, \
                patch('google.genai.Client', return_value=client), \
                patch.object(tts_service.time, 'sleep') as sleep:
            destination = Path(directory) / 'speech.wav'
            tts_service.gemini_speak('नमस्ते।', destination, 'hi')

            sleep.assert_called_once_with(10.491882765)
            self.assertEqual(generate.call_count, 2)
            with wave.open(str(destination), 'rb') as audio:
                self.assertEqual(audio.getframerate(), 24000)
                self.assertGreater(audio.getnframes(), 0)

    def test_non_rate_limit_error_is_not_retried(self):
        generate = Mock(side_effect=RuntimeError('500 provider failure'))
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with tempfile.TemporaryDirectory() as directory, \
                patch('google.genai.Client', return_value=client), \
                patch.object(tts_service.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '500 provider failure'):
                tts_service.gemini_speak(
                    'Test.', Path(directory) / 'speech.wav', 'en'
                )

        sleep.assert_not_called()
        self.assertEqual(generate.call_count, 1)

    def test_rate_limit_retries_are_bounded(self):
        generate = Mock(side_effect=RateLimitError('429 RESOURCE_EXHAUSTED'))
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with tempfile.TemporaryDirectory() as directory, \
                patch('google.genai.Client', return_value=client), \
                patch.object(tts_service.time, 'sleep') as sleep, \
                patch.object(tts_service, 'GEMINI_TTS_MAX_ATTEMPTS', 3):
            with self.assertRaises(RateLimitError):
                tts_service.gemini_speak(
                    'Test.', Path(directory) / 'speech.wav', 'en'
                )

        self.assertEqual(generate.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [22.0, 42.0])


if __name__ == '__main__':
    unittest.main()
