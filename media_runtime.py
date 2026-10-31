"""Shared subprocess and terminal progress helpers."""
import subprocess
import time
import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "storyclip-cli/0.1"})

def log(s):
    print(f'[storyclip] {s}', flush=True)


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours:d}h {minutes:02d}m {seconds:02d}s'
    if minutes:
        return f'{minutes:d}m {seconds:02d}s'
    return f'{seconds:d}s'


def progress_message(label, completed, total, started, now=None):
    now = time.monotonic() if now is None else now
    total = max(1, total)
    completed = min(max(0, completed), total)
    fraction = completed / total
    width = 20
    filled = round(width * fraction)
    bar = '#' * filled + '-' * (width - filled)
    elapsed = max(0, now - started)
    if completed >= total:
        timing = f'elapsed {format_duration(elapsed)}'
    elif completed:
        eta = elapsed / completed * (total - completed)
        timing = f'ETA {format_duration(eta)}'
    else:
        timing = 'ETA calculating'
    return f'{label} [{bar}] {fraction * 100:5.1f}% ({completed}/{total}) {timing}'


def log_progress(label, completed, total, started):
    log(progress_message(label, completed, total, started))


def run(cmd, progress_label=None, progress_duration=None):
    if progress_label:
        return run_with_progress(cmd, progress_label, progress_duration)
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if p.returncode:
        status = f'exit code {p.returncode}'
        if p.returncode < 0:
            status = f'terminated by signal {-p.returncode}'
        details = p.stderr[-3000:].strip() or 'No diagnostic text was returned by the process.'
        raise RuntimeError(
            f"Command failed ({status}): {' '.join(str(x) for x in cmd[:8])}\n{details}"
        )
    return p.stdout.strip()


def run_with_progress(cmd, label, duration):
    """Stream FFmpeg's machine-readable progress while retaining error details."""
    command = [str(x) for x in cmd]
    total_seconds = max(1, round(float(duration or 1)))
    started = time.monotonic()
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output_tail = ''
    progress_fields = {}
    last_reported = -1
    report_step = max(1, total_seconds // 20)
    for line in process.stdout:
        output_tail = (output_tail + line)[-3000:]
        if '=' not in line:
            continue
        key, value = line.rstrip().split('=', 1)
        progress_fields[key] = value
        if key != 'progress':
            continue
        timestamp = progress_fields.get('out_time_us') or progress_fields.get('out_time_ms')
        if timestamp and timestamp.isdigit():
            completed = min(total_seconds - 1, int(timestamp) // 1_000_000)
            if completed > last_reported and (
                last_reported < 0 or completed - last_reported >= report_step
            ):
                log_progress(label, completed, total_seconds, started)
                last_reported = completed
        progress_fields.clear()

    return_code = process.wait()
    if return_code:
        status = f'exit code {return_code}'
        if return_code < 0:
            status = f'terminated by signal {-return_code}'
        details = output_tail.strip() or 'No diagnostic text was returned by the process.'
        raise RuntimeError(
            f"Command failed ({status}): {' '.join(command[:8])}\n{details}"
        )
    log_progress(label, total_seconds, total_seconds, started)
    return ''


