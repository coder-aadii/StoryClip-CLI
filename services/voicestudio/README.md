# VoiceStudio Local Service Setup

This directory contains launchers and utilities for running the local VoiceStudio TTS and voice cloning service for StoryClip CLI.

## Storage Layout

All models, checkpoints, and HuggingFace/Torch caches are stored in:
`/home/rails/Work/AI-Models/`

```text
/home/rails/Work/AI-Models/
├── voicestudio/
│   ├── checkpoints/      # Neural voice model checkpoints
│   └── voices/           # Curated reference audio clips (.wav)
├── huggingface/          # HF hub cache (HF_HOME)
└── torch/                # Torch hub cache (TORCH_HOME)
```

## Running the Service

### Option A: Via Docker (Recommended)
```bash
./services/voicestudio/run_server.sh
```

### Option B: Native Host Environment
Ensure your environment has the required dependencies and start the service:
```bash
python -m voicestudio.server --port 8000
```

## Using with StoryClip CLI

Use `--tts voicestudio` with optional `--voicestudio-url` and `--voice-sample`:

```bash
# Standard VoiceStudio narration
.venv/bin/python video_cli.py \
  --idea "A serene journey across ancient Kyoto" \
  --language en --minutes 1 \
  --tts voicestudio \
  --skip-music

# Zero-shot voice cloning with reference sample
.venv/bin/python video_cli.py \
  --script my_story.txt \
  --language hi \
  --tts voicestudio \
  --voice-sample "/home/rails/Work/AI-Models/voicestudio/voices/narrator.wav" \
  --skip-music
```
