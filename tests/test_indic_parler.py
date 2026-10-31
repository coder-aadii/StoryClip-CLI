import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import video_cli
import tts_service


def wav_bytes(frames=240):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b'\x00\x00' * frames)
    return output.getvalue()


def response(*, content=b'', content_type='application/json', json_body=None):
    result = Mock()
    result.content = content
    result.headers = {'Content-Type': content_type}
    result.json.return_value = json_body
    result.raise_for_status.return_value = None
    return result


class IndicParlerClientTests(unittest.TestCase):
    def test_success_writes_exact_wav_response_atomically(self):
        health = response(json_body={'status': 'ok', 'model_loaded': True})
        audio = wav_bytes()
        synthesis = response(content=audio, content_type='audio/wav; charset=binary')

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'speech.wav'
            with patch.object(tts_service.SESSION, 'get', return_value=health) as get, \
                    patch.object(tts_service.SESSION, 'post', return_value=synthesis) as post:
                tts_service.indic_parler_speak(
                    'नमस्ते दुनिया।', destination, 'hi', 'Divya', 'http://127.0.0.1:8765/'
                )

            self.assertEqual(destination.read_bytes(), audio)
            self.assertFalse(destination.with_name('speech.wav.part').exists())
            get.assert_called_once_with('http://127.0.0.1:8765/health', timeout=5)
            post.assert_called_once_with(
                'http://127.0.0.1:8765/synthesize',
                json={'text': 'नमस्ते दुनिया।', 'voice': 'Divya', 'language': 'hi-IN'},
                timeout=300,
            )

    def test_unavailable_server_reports_health_error(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tts_service.SESSION, 'get', side_effect=requests.ConnectionError('connection refused')):
            destination = Path(directory) / 'speech.wav'
            with self.assertRaisesRegex(RuntimeError, 'health check failed.*connection refused'):
                tts_service.indic_parler_speak(
                    'नमस्ते।', destination, 'hi', 'Divya', 'http://127.0.0.1:8765'
                )
            self.assertFalse(destination.exists())

    def test_synthesis_timeout_reports_actual_phase(self):
        health = response(json_body={'status': 'ok'})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tts_service.SESSION, 'get', return_value=health), \
                patch.object(tts_service.SESSION, 'post', side_effect=requests.Timeout('read timed out')):
            destination = Path(directory) / 'speech.wav'
            with self.assertRaisesRegex(RuntimeError, 'synthesis request timed out after 300s.*read timed out'):
                tts_service.indic_parler_speak(
                    'नमस्ते।', destination, 'hi', 'Rohit', 'http://127.0.0.1:8765'
                )
            self.assertFalse(destination.exists())

    def test_http_error_reports_service_detail(self):
        health = response(json_body={'status': 'ok'})
        rejected = response(json_body={'detail': 'Speech generation failed: model unavailable'})
        rejected.status_code = 500
        rejected.raise_for_status.side_effect = requests.HTTPError(
            '500 Server Error', response=rejected
        )
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tts_service.SESSION, 'get', return_value=health), \
                patch.object(tts_service.SESSION, 'post', return_value=rejected):
            destination = Path(directory) / 'speech.wav'
            with self.assertRaisesRegex(
                    RuntimeError, 'HTTP 500.*Speech generation failed: model unavailable'):
                tts_service.indic_parler_speak(
                    'नमस्ते।', destination, 'hi', 'Rani', 'http://127.0.0.1:8765'
                )
            self.assertFalse(destination.exists())

    def test_malformed_synthesis_response_is_not_cached(self):
        health = response(json_body={'status': 'ok'})
        malformed = response(content=b'not a wave file', content_type='audio/wav')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tts_service.SESSION, 'get', return_value=health), \
                patch.object(tts_service.SESSION, 'post', return_value=malformed):
            destination = Path(directory) / 'speech.wav'
            with self.assertRaisesRegex(RuntimeError, 'malformed audio.*RIFF/WAVE'):
                tts_service.indic_parler_speak(
                    'नमस्ते।', destination, 'hi', 'Aman', 'http://127.0.0.1:8765'
                )
            self.assertFalse(destination.exists())

    def test_invalid_voice_and_unsupported_language_fail_before_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tts_service.SESSION, 'get') as get:
            destination = Path(directory) / 'speech.wav'
            with self.assertRaisesRegex(ValueError, 'Invalid Indic Parler voice'):
                tts_service.indic_parler_speak(
                    'नमस्ते।', destination, 'hi', 'Unknown', 'http://127.0.0.1:8765'
                )
            with self.assertRaisesRegex(ValueError, 'supports Hindi only'):
                tts_service.indic_parler_speak(
                    'Hello.', destination, 'en', 'Divya', 'http://127.0.0.1:8765'
                )
            get.assert_not_called()


class IndicParlerCacheTests(unittest.TestCase):
    @staticmethod
    def synthesize(text, destination, language, voice, base_url):
        destination.write_bytes(wav_bytes())

    def run_narrate(self, scenes, folder, voice='Divya', side_effect=None):
        speaker = side_effect or self.synthesize
        with patch.object(tts_service, 'indic_parler_speak', side_effect=speaker) as speak, \
                patch.object(tts_service, 'audio_duration', return_value=0.01), \
                patch.object(tts_service, 'run', return_value=''):
            tts_service.narrate(
                scenes, folder, 'hi', 'indic-parler', 'http://127.0.0.1:8765', voice
            )
        return speak

    def test_cache_identity_covers_provider_language_and_synthesis_settings(self):
        base = tts_service.tts_cache_identity(
            'एक कहानी।', 'hi', 'indic-parler', 'neutral',
            'http://127.0.0.1:8765', 'Rani',
        )
        variants = [
            tts_service.tts_cache_identity(
                'दूसरी कहानी।', 'hi', 'indic-parler', 'neutral',
                'http://127.0.0.1:8765', 'Rani',
            ),
            tts_service.tts_cache_identity(
                'एक कहानी।', 'hi', 'indic-parler', 'neutral',
                'http://127.0.0.1:8765', 'Divya',
            ),
            tts_service.tts_cache_identity(
                'एक कहानी।', 'hi', 'indic-parler', 'neutral',
                'http://127.0.0.1:9999', 'Rani',
            ),
            tts_service.tts_cache_identity(
                'एक कहानी।', 'en', 'indic-parler', 'neutral',
                'http://127.0.0.1:8765', 'Rani',
            ),
            tts_service.tts_cache_identity('एक कहानी।', 'hi', 'edge', 'neutral'),
            tts_service.tts_cache_identity(
                'एक कहानी।', 'hi', 'indic-parler', 'joy',
                'http://127.0.0.1:8765', 'Rani',
            ),
        ]
        keys = {tts_service.cache_key(base), *(tts_service.cache_key(item) for item in variants)}
        self.assertEqual(len(keys), 7)

    def test_cache_reuse_and_text_and_voice_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = self.run_narrate([{'text': 'पहला वाक्य।'}], folder)
            self.assertEqual(first.call_count, 1)
            first_wav = next(folder.glob('0000_*.wav'))

            reused = self.run_narrate([{'text': 'पहला वाक्य।'}], folder)
            self.assertEqual(reused.call_count, 0)

            changed_text = self.run_narrate([{'text': 'बदला हुआ वाक्य।'}], folder)
            self.assertEqual(changed_text.call_count, 1)
            text_wav = sorted(folder.glob('0000_*.wav'))[-1]
            self.assertNotEqual(first_wav.name, text_wav.name)

            changed_voice = self.run_narrate([{'text': 'पहला वाक्य।'}], folder, voice='Rani')
            self.assertEqual(changed_voice.call_count, 1)
            self.assertEqual(len(list(folder.glob('0000_*.json'))), 3)

    def test_failure_never_falls_back_and_completed_scene_resumes(self):
        attempts = []

        def fail_second(text, destination, language, voice, base_url):
            attempts.append(text)
            if text == 'दूसरा।':
                raise RuntimeError('actual backend failure')
            destination.write_bytes(wav_bytes())

        scenes = [{'text': 'पहला।'}, {'text': 'दूसरा।'}]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(tts_service, 'indic_parler_speak', side_effect=fail_second), \
                    patch.object(tts_service, 'edge_speak') as edge, \
                    patch.object(tts_service, 'gemini_speak') as gemini, \
                    patch.object(tts_service, 'audio_duration', return_value=0.01), \
                    patch.object(tts_service, 'run', return_value=''):
                with self.assertRaisesRegex(RuntimeError, 'actual backend failure'):
                    tts_service.narrate(
                        scenes, folder, 'hi', 'indic-parler', 'http://127.0.0.1:8765', 'Divya'
                    )
                edge.assert_not_called()
                gemini.assert_not_called()

            self.assertEqual(attempts, ['पहला।', 'दूसरा।'])
            self.assertEqual(len(list(folder.glob('0000_*.wav'))), 1)
            self.assertEqual(len(list(folder.glob('0000_*.json'))), 1)

            resumed = self.run_narrate(scenes, folder)
            self.assertEqual(resumed.call_count, 1)
            self.assertEqual(resumed.call_args.args[0], 'दूसरा।')


class IndicParlerCliTests(unittest.TestCase):
    def test_all_indic_cli_options_are_accepted(self):
        parser = video_cli.build_parser()
        args = parser.parse_args([
            '--script', 'hindi.txt', '--language', 'hi', '--tts', 'indic-parler',
            '--indic-url', 'http://127.0.0.1:8765', '--indic-voice', 'Rohit',
        ])
        video_cli.validate_cli_args(parser, args)
        self.assertEqual(args.tts, 'indic-parler')
        self.assertEqual(args.indic_url, 'http://127.0.0.1:8765')
        self.assertEqual(args.indic_voice, 'Rohit')

    def test_invalid_voice_url_and_english_are_rejected(self):
        parser = video_cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([
                '--script', 'hindi.txt', '--tts', 'indic-parler', '--indic-voice', 'Unknown'
            ])

        args = parser.parse_args([
            '--script', 'hindi.txt', '--tts', 'indic-parler', '--indic-url', 'localhost:8765'
        ])
        with self.assertRaises(SystemExit):
            video_cli.validate_cli_args(parser, args)

        with patch.dict('os.environ', {'INDIC_PARLER_VOICE': 'Unknown'}):
            args = video_cli.build_parser().parse_args([
                '--script', 'hindi.txt', '--tts', 'indic-parler'
            ])
        with self.assertRaises(SystemExit):
            video_cli.validate_cli_args(parser, args)

        args = parser.parse_args([
            '--script', 'english.txt', '--language', 'en', '--tts', 'indic-parler'
        ])
        with self.assertRaises(SystemExit):
            video_cli.validate_cli_args(parser, args)


if __name__ == '__main__':
    unittest.main()
