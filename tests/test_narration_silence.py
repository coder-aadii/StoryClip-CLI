import array
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

import video_cli


def write_test_wav(path, segments, sample_rate=24000):
    samples = array.array('h')
    for seconds, amplitude in segments:
        count = round(seconds * sample_rate)
        samples.extend(
            round(amplitude * math.sin(2 * math.pi * 440 * i / sample_rate))
            for i in range(count)
        )
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def wav_seconds(path):
    with wave.open(str(path), 'rb') as source:
        return source.getnframes() / source.getframerate()


class NarrationSilenceTests(unittest.TestCase):
    def test_trims_only_clip_edges_and_preserves_interior_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw.wav'
            trimmed = Path(directory) / 'trimmed.wav'
            write_test_wav(source, [
                (0.5, 0), (1.0, 5000), (0.5, 0), (1.0, 5000), (0.7, 0),
            ])

            removed = video_cli.trim_wav_edge_silence(source, trimmed)

            self.assertAlmostEqual(removed, 1.02, delta=0.03)
            self.assertAlmostEqual(wav_seconds(trimmed), 2.68, delta=0.03)
            with wave.open(str(trimmed), 'rb') as audio:
                samples = array.array('h')
                samples.frombytes(audio.readframes(audio.getnframes()))
                sample_rate = audio.getframerate()
            # The 500 ms quiet pause between spoken sections is untouched.
            midpoint = round(1.34 * sample_rate)
            self.assertEqual(max(map(abs, samples[midpoint - 100:midpoint + 100])), 0)

    def test_silence_only_clip_is_not_collapsed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw.wav'
            trimmed = Path(directory) / 'trimmed.wav'
            write_test_wav(source, [(0.8, 0)])

            removed = video_cli.trim_wav_edge_silence(source, trimmed)

            self.assertEqual(removed, 0)
            self.assertAlmostEqual(wav_seconds(trimmed), 0.8, delta=0.001)

    def test_existing_tts_cache_is_trimmed_without_resynthesis(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'voice'
            folder.mkdir()
            scene = {'text': 'A test narration.', 'mood': 'neutral'}
            identity = video_cli.tts_cache_identity(scene['text'], 'en', 'edge-male', 'neutral')
            key = video_cli.cache_key(identity)
            raw = folder / f'0000_{key[:20]}.wav'
            metadata = raw.with_suffix('.json')
            write_test_wav(raw, [(0.5, 0), (1.0, 5000), (0.6, 0)])
            original_bytes = raw.read_bytes()
            metadata.write_text(json.dumps({'cache_key': key, 'identity': identity}), encoding='utf-8')

            with patch.object(video_cli, 'edge_speak', new_callable=AsyncMock) as speak, \
                    patch.object(video_cli, 'run') as run, \
                    patch.object(video_cli, 'audio_duration', side_effect=wav_seconds):
                narration = video_cli.narrate([scene], folder, 'en', 'edge-male')

            speak.assert_not_called()
            self.assertEqual(raw.read_bytes(), original_bytes)
            self.assertTrue(Path(scene['voice']).is_file())
            self.assertIn(f'trim_v{video_cli.NARRATION_TRIM_VERSION}', scene['voice'])
            self.assertLess(scene['duration'], wav_seconds(raw))
            manifest = folder / 'narration_concat.txt'
            self.assertIn(Path(scene['voice']).name, manifest.read_text(encoding='utf-8'))
            self.assertEqual(narration, folder / 'narration.wav')
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
