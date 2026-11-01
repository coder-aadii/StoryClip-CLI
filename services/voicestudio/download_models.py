#!/usr/bin/env python3
"""Pre-download VoiceStudio / XTTS-v2 model checkpoints into /home/rails/Work/AI-Models."""

import os
import sys
import urllib.request
from pathlib import Path

DEFAULT_MODELS_ROOT = Path('/home/rails/Work/AI-Models')
XTTS_REPO = 'coqui/XTTS-v2'
XTTS_FILES = [
    'config.json',
    'vocab.json',
    'speakers_xtts.pth',
    'dvae.pth',
    'mel_stats.pth',
    'model.pth',
]
BASE_URL = f'https://huggingface.co/{XTTS_REPO}/resolve/main'


def setup_model_directories(models_root: Path = DEFAULT_MODELS_ROOT) -> dict[str, Path]:
    voicestudio_dir = models_root / 'voicestudio'
    checkpoints_dir = voicestudio_dir / 'checkpoints' / 'XTTS-v2'
    voices_dir = voicestudio_dir / 'voices'
    hf_dir = models_root / 'huggingface'
    torch_dir = models_root / 'torch'

    for directory in (checkpoints_dir, voices_dir, hf_dir, torch_dir):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ['HF_HOME'] = str(hf_dir)
    os.environ['TORCH_HOME'] = str(torch_dir)
    os.environ['VOICESTUDIO_MODELS_DIR'] = str(voicestudio_dir)

    return {
        'root': models_root,
        'voicestudio': voicestudio_dir,
        'checkpoints': checkpoints_dir,
        'voices': voices_dir,
        'hf': hf_dir,
        'torch': torch_dir,
    }


def download_file(url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size > 0:
        print(f'  [Exists] {destination.name} ({destination.stat().st_size:,} bytes)')
        return

    tmp_file = destination.with_name(destination.name + '.part')
    print(f'  [Downloading] {destination.name} from {url} ...')

    def report_progress(block_num, block_size, total_size):
        if total_size > 0:
            downloaded = block_num * block_size
            percent = min(100.0, downloaded / total_size * 100.0)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            print(f'\r    {percent:5.1f}% [{mb_downloaded:.1f}/{mb_total:.1f} MB]', end='', flush=True)

    try:
        urllib.request.urlretrieve(url, tmp_file, reporthook=report_progress)
        print()
        os.replace(tmp_file, destination)
    finally:
        tmp_file.unlink(missing_ok=True)


def download_xtts_model(target_dir: Path) -> None:
    print(f'\nFetching XTTS-v2 model checkpoints to {target_dir}:')
    try:
        from huggingface_hub import snapshot_download
        print('  Using huggingface_hub snapshot_download...')
        snapshot_download(
            repo_id=XTTS_REPO,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )
        print('  Download complete.')
        return
    except ImportError:
        pass

    # Direct fallback via HTTP resolve URLs
    for filename in XTTS_FILES:
        url = f'{BASE_URL}/{filename}'
        dest = target_dir / filename
        download_file(url, dest)
    print('\nAll model files downloaded successfully.')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Pre-download VoiceStudio / XTTS-v2 checkpoints')
    parser.add_argument('target_dir', nargs='?', default=str(DEFAULT_MODELS_ROOT),
                        help='Target root models directory (default: /home/rails/Work/AI-Models)')
    parser.add_argument('--download', action='store_true',
                        help='Download XTTS-v2 model files into checkpoints directory')
    args = parser.parse_args()

    target_root = Path(args.target_dir)
    dirs = setup_model_directories(target_root)
    print(f'Model directories verified under: {dirs["root"]}')
    print(f'  Checkpoints: {dirs["checkpoints"]}')
    print(f'  Voices     : {dirs["voices"]}')
    print(f'  HF Cache   : {dirs["hf"]}')

    if args.download:
        download_xtts_model(dirs['checkpoints'])
    else:
        print('\nTo download the XTTS-v2 checkpoint files (~3.5 GB) into this directory, run:')
        print('  python services/voicestudio/download_models.py --download')
        print('\nOr start the container directly (it downloads automatically into this folder):')
        print('  ./services/voicestudio/run_server.sh')


if __name__ == '__main__':
    main()
