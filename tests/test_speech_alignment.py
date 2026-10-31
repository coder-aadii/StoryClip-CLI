import tempfile
import unittest
import wave
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import speech_alignment as alignment
import tts_service as tts


class AlignmentTests(unittest.TestCase):
    def test_uneven_speech_sets_real_boundaries_not_equal_slices(self):
        scenes = [{'text': 'नमस्ते दुनिया।'}, {'text': 'यह कहानी है।'}]
        words = [dict(word=w, start=a, end=b) for w, a, b in [
            ('नमस्ते', 0.1, 1), ('दुनिया।', 1.1, 2),
            ('यह', 6, 7), ('कहानी', 7, 8), ('है।', 8, 9)]]
        self.assertEqual(alignment.scene_boundaries(words, scenes, 10), [0, 6, 10])

    def test_invalid_or_incomplete_alignment_is_rejected(self):
        scenes = [{'text': 'Hello.'}, {'text': 'World.'}]
        good = [dict(word='Hello.', start=0, end=1), dict(word='World.', start=2, end=3)]
        for words in [good[:1], [], [dict(word='Hello.', start=0, end=0), good[1]],
                      [good[0], dict(word='World.', start=float('nan'), end=3)]]:
            with self.subTest(words=words), self.assertRaises(ValueError):
                alignment.scene_boundaries(words, scenes, 4)

    def test_alignment_cache_reuse_and_audio_invalidation(self):
        words = [SimpleNamespace(word='Hello.', start=0.1, end=1),
                 SimpleNamespace(word='World.', start=2, end=3)]
        model = Mock()
        model.align.return_value = SimpleNamespace(segments=[SimpleNamespace(words=words)])
        backend = Mock()
        backend.load_model.return_value = model
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(alignment, 'require_alignment', return_value=backend), \
                patch.object(alignment, '_MODELS', {}), \
                patch.dict(sys.modules, {'torch': SimpleNamespace(
                    get_num_threads=lambda: 4, set_num_threads=lambda count: None)}), \
                patch('importlib.metadata.version', return_value='test'), \
                patch.dict('os.environ', {'STORYCLIP_ALIGN_MODEL': 'small'}):
            audio = Path(directory) / 'voice.wav'
            def write_audio(sample):
                with wave.open(str(audio), 'wb') as wav:
                    wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(sample * (4 * 24000))
            write_audio(b'\x01\x00')
            scenes = [{'text': 'Hello.'}, {'text': 'World.'}]
            self.assertEqual(alignment.align_scenes(audio, scenes, 'en'), [0, 2, 4])
            self.assertEqual(alignment.align_scenes(audio, scenes, 'en'), [0, 2, 4])
            model.align.assert_called_once()
            write_audio(b'\x02\x00')
            alignment.align_scenes(audio, scenes, 'en')
            self.assertEqual(model.align.call_count, 2)


class BatchedNarrationTests(unittest.TestCase):
    def test_bookends_stay_separate_and_content_is_batched(self):
        scenes = [{'text': 'Hello.', 'kind': 'intro'}] + [
            {'text': ' '.join(['word'] * 20), 'kind': 'content'} for _ in range(12)
        ] + [{'text': 'Bye.', 'kind': 'outro'}]
        groups = tts.speech_chunks(scenes, 'hi')
        self.assertEqual(groups, [[0], list(range(1, 7)), list(range(7, 13)), [13]])

    def test_multiple_visuals_share_continuous_audio_and_alignment_retry_reuses_tts(self):
        scenes = [{'text': 'Hello.', 'kind': 'content'}, {'text': 'World.', 'kind': 'content'}]
        def speak(text, dest, language, mood):
            with wave.open(str(dest), 'wb') as wav:
                wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\x10\x10' * (4 * 24000))
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tts, 'gemini_speak', side_effect=speak) as synth, \
                patch.object(tts, 'run') as run, \
                patch.object(alignment, 'require_alignment'), \
                patch.object(alignment, 'align_scenes', side_effect=[
                    RuntimeError('alignment unavailable'), [0, 1, 4]]):
            folder = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'alignment unavailable'):
                tts.narrate(scenes, folder, 'en', 'gemini')
            tts.narrate(scenes, folder, 'en', 'gemini')
            synth.assert_called_once()
            self.assertEqual(synth.call_args.args[0], 'Hello. World.')
            self.assertEqual(scenes[0]['voice'], scenes[1]['voice'])
            self.assertEqual([s['duration'] for s in scenes], [1, 3])
            self.assertEqual(scenes[1]['voice_start'], 1)
            self.assertEqual(len((folder / 'narration_concat.txt').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
