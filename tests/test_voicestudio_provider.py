import hashlib
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import tts_service
import video_cli
import voicestudio_provider


def make_test_wav(channels=1, width=2, framerate=24000, frames=240):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(framerate)
        sample_bytes = b'\x00' * (width * channels)
        wav.writeframes(sample_bytes * frames)
    return output.getvalue()


def mock_response(*, content=b'', content_type='audio/wav', json_body=None, status_code=200):
    result = Mock()
    result.status_code = status_code
    result.content = content
    result.headers = {'Content-Type': content_type}
    result.json.return_value = json_body if json_body is not None else {}
    result.raise_for_status.return_value = None
    return result


class VoiceStudioProviderTests(unittest.TestCase):
    def test_cache_identity_stability_and_invalidation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_file = Path(temp_dir) / 'voice.wav'
            sample_file.write_bytes(make_test_wav(frames=100))

            id1 = voicestudio_provider.voicestudio_cache_identity(
                'Hello world', 'en', 'neutral', 'http://127.0.0.1:38123', sample_file
            )
            id2 = voicestudio_provider.voicestudio_cache_identity(
                'Hello world', 'en', 'neutral', 'http://127.0.0.1:38123', sample_file
            )
            self.assertEqual(id1, id2)
            self.assertEqual(id1['voice_sample_sha256'], hashlib.sha256(sample_file.read_bytes()).hexdigest())

            # Changing the voice sample content must invalidate cache identity
            sample_file.write_bytes(make_test_wav(frames=300))
            id3 = voicestudio_provider.voicestudio_cache_identity(
                'Hello world', 'en', 'neutral', 'http://127.0.0.1:38123', sample_file
            )
            self.assertNotEqual(id1['voice_sample_sha256'], id3['voice_sample_sha256'])

            # Changing speed must invalidate cache identity
            id4 = voicestudio_provider.voicestudio_cache_identity(
                'Hello world', 'en', 'neutral', 'http://127.0.0.1:38123', sample_file, speed=1.2
            )
            self.assertEqual(id4['speed'], 1.2)
            self.assertNotEqual(id3['speed'], id4['speed'])

    def test_text_sanitization_removes_punctuation_traps(self):
        raw = 'नमस्ते दोस्तों! क्या आप जानते हैं?। यह—एक परीक्षण है: ध्यान दें।'
        sanitized = voicestudio_provider.sanitize_text(raw, language='hi')
        self.assertNotIn('?।', sanitized)
        self.assertNotIn('—', sanitized)
        self.assertNotIn(':', sanitized)
        self.assertIn('क्या आप जानते हैं?', sanitized)
        self.assertIn(', ', sanitized)

    def test_hindi_number_expansion(self):
        self.assertEqual(voicestudio_provider.number_to_hindi(0), 'शून्य')
        self.assertEqual(voicestudio_provider.number_to_hindi(65), 'पैंसठ')
        self.assertEqual(voicestudio_provider.number_to_hindi(100), 'एक सौ')
        self.assertEqual(voicestudio_provider.number_to_hindi(2024), 'दो हज़ार चौबीस')
        sample_text = 'लगभग 65 मिलियन साल पहले, 100 टन के जीव थे।'
        sanitized = voicestudio_provider.sanitize_text(sample_text, language='hi')
        self.assertNotIn('65', sanitized)
        self.assertNotIn('100', sanitized)
        self.assertIn('पैंसठ', sanitized)
        self.assertIn('एक सौ', sanitized)

    def test_clause_splitting_for_long_sentences(self):
        from services.voicestudio.server import split_long_clause
        long_sentence = (
            'यह नज़ारा किसी हॉलीवुड फिल्म के सीन जैसा रोमांचक लग सकता है, लेकिन '
            'क्या आपने कभी गहराई से सोचा है कि असल ज़िंदगी में आज की धरती पर ऐसे '
            'विशालकाय जीव क्यों नहीं पाए जाते?'
        )
        chunks = split_long_clause(long_sentence, max_words=18)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c.split()), 20)

    def test_cache_identity_through_tts_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_file = Path(temp_dir) / 'ref.wav'
            sample_file.write_bytes(make_test_wav(frames=50))

            identity = tts_service.tts_cache_identity(
                'Test sentence', 'hi', 'voicestudio', 'neutral',
                voicestudio_url='http://127.0.0.1:38123', voice_sample=sample_file
            )
            self.assertEqual(identity['provider'], 'voicestudio')
            self.assertEqual(identity['settings']['base_url'], 'http://127.0.0.1:38123')
            self.assertEqual(identity['settings']['voice_sample_name'], 'ref.wav')

    def test_voicestudio_speak_success_atomic_write(self):
        health = mock_response(json_body={'status': 'ok', 'model': 'xtts-v2'})
        audio = make_test_wav(channels=1, width=2, framerate=24000, frames=240)
        synth = mock_response(content=audio, content_type='audio/wav')

        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / 'output.wav'
            sample_wav = Path(temp_dir) / 'sample.wav'
            sample_wav.write_bytes(audio)

            with patch.object(voicestudio_provider.SESSION, 'get', return_value=health) as get_mock, \
                 patch.object(voicestudio_provider.SESSION, 'post', return_value=synth) as post_mock:
                voicestudio_provider.voicestudio_speak(
                    'A great story begins here.', dest, 'en', mood='joy',
                    base_url='http://127.0.0.1:38123', voice_sample=sample_wav
                )

            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), audio)
            self.assertFalse(dest.with_name('output.raw.part.wav').exists())
            self.assertFalse(dest.with_name('output.norm.part.wav').exists())

            get_mock.assert_called_once_with('http://127.0.0.1:38123/health', timeout=5)
            post_mock.assert_called_once()
            called_url = post_mock.call_args[0][0]
            called_json = post_mock.call_args[1]['json']
            self.assertEqual(called_url, 'http://127.0.0.1:38123/api/synthesize')
            self.assertEqual(called_json['text'], 'A great story begins here.')
            self.assertEqual(called_json['language'], 'en')
            self.assertEqual(called_json['mood'], 'joy')
            self.assertIn('voice_sample', called_json)
            self.assertIn('voice_sample_base64', called_json)

    def test_voicestudio_resampling_on_different_samplerate(self):
        health = mock_response(json_body={'status': 'ok'})
        audio_44k = make_test_wav(channels=2, width=2, framerate=44100, frames=441)
        synth = mock_response(content=audio_44k, content_type='audio/wav')

        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / 'output_resampled.wav'
            with patch.object(voicestudio_provider.SESSION, 'get', return_value=health), \
                 patch.object(voicestudio_provider.SESSION, 'post', return_value=synth):
                voicestudio_provider.voicestudio_speak(
                    'Resampling test.', dest, 'en', base_url='http://127.0.0.1:38123'
                )

            self.assertTrue(dest.is_file())
            channels, width, rate = voicestudio_provider.get_wav_properties(dest)
            self.assertEqual(channels, 1)
            self.assertEqual(rate, 24000)

    def test_voicestudio_health_check_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / 'speech.wav'
            with patch.object(voicestudio_provider.SESSION, 'get', side_effect=requests.ConnectionError('Connection refused')):
                with self.assertRaisesRegex(RuntimeError, 'health check failed'):
                    voicestudio_provider.voicestudio_speak('Test.', dest, 'en', base_url='http://127.0.0.1:38123')
            self.assertFalse(dest.exists())

    def test_voicestudio_synthesize_timeout(self):
        health = mock_response(json_body={'status': 'ok'})
        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / 'speech.wav'
            with patch.object(voicestudio_provider.SESSION, 'get', return_value=health), \
                 patch.object(voicestudio_provider.SESSION, 'post', side_effect=requests.Timeout('Read timed out')):
                with self.assertRaisesRegex(RuntimeError, 'synthesis request timed out'):
                    voicestudio_provider.voicestudio_speak('Test.', dest, 'en', base_url='http://127.0.0.1:38123')
            self.assertFalse(dest.exists())

    def test_voicestudio_malformed_response_rejected(self):
        health = mock_response(json_body={'status': 'ok'})
        synth = mock_response(content=b'<html>Server Error</html>', content_type='text/html')

        with tempfile.TemporaryDirectory() as temp_dir:
            dest = Path(temp_dir) / 'speech.wav'
            with patch.object(voicestudio_provider.SESSION, 'get', return_value=health), \
                 patch.object(voicestudio_provider.SESSION, 'post', return_value=synth):
                with self.assertRaisesRegex(RuntimeError, 'unexpected Content-Type|not a valid RIFF/WAVE'):
                    voicestudio_provider.voicestudio_speak('Test.', dest, 'en', base_url='http://127.0.0.1:38123')
            self.assertFalse(dest.exists())


class VoiceStudioCLITests(unittest.TestCase):
    def test_cli_parsing_voicestudio_options(self):
        p = video_cli.build_parser()
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_file = Path(temp_dir) / 'voice.wav'
            sample_file.write_bytes(make_test_wav())

            args = p.parse_args([
                '--idea', 'A test story',
                '--tts', 'voicestudio',
                '--voicestudio-url', 'http://127.0.0.1:38123',
                '--voice-sample', str(sample_file),
                '--voicestudio-speed', '1.15',
            ])
            video_cli.validate_cli_args(p, args)
            self.assertEqual(args.tts, 'voicestudio')
            self.assertEqual(args.voicestudio_url, 'http://127.0.0.1:38123')
            self.assertEqual(args.voice_sample, sample_file)
            self.assertEqual(args.voicestudio_speed, 1.15)

    def test_cli_rejects_invalid_voicestudio_url(self):
        p = video_cli.build_parser()
        args = p.parse_args([
            '--idea', 'A test story',
            '--tts', 'voicestudio',
            '--voicestudio-url', 'invalid://url with spaces',
        ])
        with self.assertRaises(SystemExit):
            video_cli.validate_cli_args(p, args)

    def test_cli_rejects_missing_voice_sample_file(self):
        p = video_cli.build_parser()
        args = p.parse_args([
            '--idea', 'A test story',
            '--tts', 'voicestudio',
            '--voice-sample', '/non/existent/path/to/voice.wav',
        ])
        with self.assertRaises(SystemExit):
            video_cli.validate_cli_args(p, args)


class VoiceStudioNarratePipelineTests(unittest.TestCase):
    def test_narrate_uses_voicestudio_and_caches(self):
        health = mock_response(json_body={'status': 'ok'})
        audio = make_test_wav(frames=240)
        synth = mock_response(content=audio, content_type='audio/wav')

        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / 'voice'
            scenes = [{'text': 'Scene one narrative.', 'mood': 'neutral'},
                      {'text': 'Scene two narrative.', 'mood': 'neutral'}]

            with patch.object(voicestudio_provider.SESSION, 'get', return_value=health), \
                 patch.object(voicestudio_provider.SESSION, 'post', return_value=synth) as post_mock:
                output = tts_service.narrate(
                    scenes, folder, 'en', 'voicestudio', voicestudio_url='http://127.0.0.1:38123'
                )

            self.assertTrue(output.is_file())
            self.assertEqual(post_mock.call_count, 2)
            self.assertIn('duration', scenes[0])
            self.assertIn('duration', scenes[1])

            # Rerunning with identical scenes must hit cache and make 0 new synthesis calls
            with patch.object(voicestudio_provider.SESSION, 'get', return_value=health), \
                 patch.object(voicestudio_provider.SESSION, 'post', return_value=synth) as post_reused:
                reused_output = tts_service.narrate(
                    scenes, folder, 'en', 'voicestudio', voicestudio_url='http://127.0.0.1:38123'
                )
            self.assertEqual(post_reused.call_count, 0)


if __name__ == '__main__':
    unittest.main()
