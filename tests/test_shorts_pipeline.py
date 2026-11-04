"""Unit tests for YouTube Shorts vertical video pipeline."""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import video_cli


class ShortsPipelineTests(unittest.TestCase):

    def test_shorts_cli_flag_defaults(self):
        """--format shorts automatically configures resolution, provider, outro, duration, and BGM mood."""
        p = video_cli.build_parser()
        args = p.parse_args([
            '--idea', 'A quick tech fact',
            '--format', 'shorts',
            '--output', 'outputs/test-shorts',
        ])
        validated = video_cli.validate_cli_args(p, args)

        self.assertEqual(validated.format, 'shorts')
        self.assertEqual(validated.resolution, 'shorts')
        self.assertEqual(validated.image_provider, 'pexels-video')
        self.assertEqual(validated.outro, 'shorts')
        self.assertAlmostEqual(validated.minutes, 0.85)
        self.assertEqual(validated.music_mood, 'soothing')

    def test_shorts_resolution_canvas(self):
        """Shorts resolution evaluates to 1080x1920 portrait canvas."""
        self.assertIn('shorts', video_cli.VIDEO_RESOLUTIONS)
        self.assertEqual(video_cli.VIDEO_RESOLUTIONS['shorts'], (1080, 1920))
        self.assertIn('shorts', video_cli.XFADE_MERGE_INPUTS)

    def test_shorts_micro_outro_profile(self):
        """Micro-outro profile 'shorts' is supported and returns punchy scripts."""
        self.assertIn('shorts', video_cli.OUTRO_PROFILES)

        hi_text = video_cli.outro_narration('shorts', 'hi')
        self.assertIn('सब्सक्राइब', hi_text)
        self.assertTrue(len(hi_text.split()) < 20)

        en_text = video_cli.outro_narration('shorts', 'en')
        self.assertIn('subscribe', en_text.lower())
        self.assertTrue(len(en_text.split()) < 20)

    def test_shorts_broll_narration_grouping(self):
        """narration_groups with is_shorts=True produces balanced ~5.0s B-roll visual slots."""
        # A long sentence with 24 words (approx 11-12 seconds of speech)
        long_sentence = (
            "Artificial intelligence is transforming every modern industry from healthcare and medicine "
            "to software robotics transportation and space exploration at an unprecedented exponential velocity."
        )
        groups = video_cli.narration_groups(long_sentence, 'en', 'edge', is_shorts=True)
        # 24 words at ~5.0s per slot produces 2 balanced ~5.5s slots of 12 words each
        self.assertTrue(len(groups) >= 2)
        for g in groups:
            word_count = len(g.split())
            self.assertTrue(8 <= word_count <= 14)

    def test_soothing_bgm_matcher(self):
        """Themes with soothing/relaxing/calm select soothing BGM tracks."""
        track = video_cli.select_builtin_bgm({'music_moods': ['soothing']})
        self.assertTrue(len(track) > 0)
        track_path = Path(track[0]['path'])
        self.assertTrue(track_path.exists())
        self.assertIn('theme', track_path.stem)
        self.assertTrue('soothing' in track_path.stem or 'relaxing' in track_path.stem)

    def test_video_broll_disables_zoom(self):
        """append_scene_visual disables synthetic zoompan on real video files."""
        inputs = []
        filters = []
        scene = {
            'text': 'A bustling street',
            'image': '/tmp/test_clip.mp4',
            'is_video': True,
            'duration': 3.5,
        }
        manifest = {'poses': {'explaining': {'path': '/tmp/test.png'}}}
        label = video_cli.append_scene_visual(
            inputs, filters, scene,
            scene_index=0, block={'position': 'center', 'pose': 'explaining'},
            manifest=manifest, prefix='current', scene_frames=87,
            frame_offset=0, input_frames=87, fade_frames=12,
            fps=25, width=1080, height=1920, style='neutral',
            zoom=True, has_next=True,
        )
        filter_str = ';'.join(filters)
        # Should not contain zoom factor calculations or zoompan
        self.assertNotIn('1.0+0.05*', filter_str)
        self.assertNotIn('1.0+0.08*', filter_str)

    def test_shorts_avatar_alternates_position_and_changes_poses(self):
        """In Shorts, avatar holds position for 6-8s before moving sides, while dynamically updating expressions."""
        # 4s-5s scenes: avatar holds on 'left' for scene 1 & 2 (total 8.0s), then moves to 'right' for scene 3 & 4 (total 8.0s)
        scenes = [
            {'kind': 'intro', 'duration': 3.5, 'text': 'Intro greeting', 'avatar_cue': 'explanation', 'is_video': True},
            {'kind': 'content', 'duration': 4.0, 'text': 'Did you know the ocean has gold?', 'avatar_cue': 'question', 'is_video': True},
            {'kind': 'content', 'duration': 4.0, 'text': 'Scientists discovered a secret trench.', 'avatar_cue': 'reveal', 'is_video': True},
            {'kind': 'content', 'duration': 4.0, 'text': 'The pressure is extremely dangerous.', 'avatar_cue': 'warning', 'is_video': True},
            {'kind': 'content', 'duration': 4.0, 'text': 'Subscribe to our channel now!', 'avatar_cue': 'celebration', 'is_video': True},
            {'kind': 'outro', 'duration': 4.0, 'text': 'Outro farewell', 'avatar_cue': 'celebration', 'is_video': True},
        ]
        plan = video_cli.build_avatar_plan(scenes)
        blocks = plan['blocks']
        self.assertEqual(len(blocks), len(scenes))
        self.assertEqual(blocks[0]['position'], 'center')
        # Scene 1 & 2 hold on left for 8.0s
        self.assertEqual(blocks[1]['position'], 'left')
        self.assertEqual(blocks[1]['from_position'], 'center')
        self.assertEqual(blocks[2]['position'], 'left')
        self.assertEqual(blocks[2]['from_position'], 'left')
        # Scene 3 & 4 hold on right for 8.0s
        self.assertEqual(blocks[3]['position'], 'right')
        self.assertEqual(blocks[3]['from_position'], 'left')  # slides from left to right
        self.assertEqual(blocks[4]['position'], 'right')
        self.assertEqual(blocks[4]['from_position'], 'right')
        # Outro returns to center
        self.assertEqual(blocks[5]['position'], 'center')
        self.assertEqual(blocks[5]['from_position'], 'right')

        # Check expressions dynamically change per scene cue even while holding position
        self.assertIn(blocks[1]['pose'], ['thinking', 'confused'])
        self.assertIn(blocks[2]['pose'], ['idea_reveal', 'pointing_up', 'smug'])
        self.assertIn(blocks[3]['pose'], ['serious_fact', 'nervous_sweat', 'dramatic_pause'])
        self.assertIn(blocks[4]['pose'], ['celebrating', 'laughing'])
        self.assertNotEqual(blocks[1]['pose'], blocks[2]['pose'])


if __name__ == '__main__':
    unittest.main()
