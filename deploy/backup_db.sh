#!/usr/bin/env bash
# ==============================================================================
# StoryClip Studio — Automated SQLite Database Backup Script
# Uses SQLite online backup API to ensure hot backups during active renders
# ==============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${REPO_DIR}/storage/studio.db"
BACKUP_DIR="${REPO_DIR}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DEST="${BACKUP_DIR}/studio_${TIMESTAMP}.db"

mkdir -p "${BACKUP_DIR}"

if [ -f "${DB_PATH}" ]; then
    sqlite3 "${DB_PATH}" ".backup '${DEST}'"
    echo "[StoryClip Backup] Database backed up to ${DEST}"

    # Retain backups for 14 days, prune older
    find "${BACKUP_DIR}" -name "studio_*.db" -type f -mtime +14 -delete
else
    echo "[StoryClip Backup] No database found at ${DB_PATH}"
fi
