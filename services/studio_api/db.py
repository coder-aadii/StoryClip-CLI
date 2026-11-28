"""Database and persistence management for StoryClip Studio using SQLite in WAL mode.

Manages:
- `users`: Operator accounts with Argon2id password hashes
- `render_jobs`: Persistent video rendering jobs with OS PID and command metadata
- `ws_tickets`: Single-use 30-second WebSocket connection tickets
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_DIR = PROJECT_ROOT / "storage"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "studio.db"


def get_db() -> sqlite3.Connection:
    """Connect to SQLite database with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Configure WAL mode for concurrent reads/writes and busy timeout
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db():
    """Initialize SQLite database tables and indexes."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS render_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            project_slug TEXT NOT NULL,
            status TEXT NOT NULL, -- 'pending', 'running', 'completed', 'failed', 'interrupted'
            progress REAL DEFAULT 0.0,
            stage TEXT,
            pid INTEGER,
            command_args TEXT NOT NULL,
            logs TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ws_tickets (
            ticket TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_render_jobs_status ON render_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_render_jobs_user ON render_jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_ws_tickets_exp ON ws_tickets(expires_at);
        """)
        conn.commit()


def reconcile_interrupted_jobs() -> int:
    """Detect and mark orphaned jobs whose OS processes died during server downtime."""
    count = 0
    with get_db() as conn:
        rows = conn.execute("SELECT id, pid, status FROM render_jobs WHERE status = 'running'").fetchall()
        for row in rows:
            job_id = row["id"]
            pid = row["pid"]
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    # Verify command line of the running PID matches python / video_cli
                    cmdline_path = Path(f"/proc/{pid}/cmdline")
                    if cmdline_path.exists():
                        cmdline = cmdline_path.read_text(errors="ignore")
                        if "video_cli" in cmdline or "python" in cmdline:
                            alive = True
                    else:
                        alive = True
                except (OSError, ProcessLookupError):
                    alive = False

            if not alive:
                conn.execute("""
                    UPDATE render_jobs
                    SET status = 'interrupted',
                        stage = 'Interrupted by server reboot or crash',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (job_id,))
                count += 1
        conn.commit()
    return count
