"""Shared by the codex/ standalone scripts (api_transaction_security_test.py,
engine_stress_test.py) to populate os.environ from the project's real .env
before importing config/database/main -- those scripts run directly from the
repo root, outside docker-compose, so nothing else has loaded .env for them
yet. Was previously copy-pasted into each script individually, and each copy
pointed at backend.env/postgres.env -- generated/legacy files per
.claude/CLAUDE.md's "Do Not Touch" list, not the real config source, so a
stale copy of either could silently override the actual .env values.
"""
import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    raw_bytes = path.read_bytes()
    text = (raw_bytes.decode("utf-16", errors="ignore") if b"\x00" in raw_bytes
            else raw_bytes.decode("utf-8", errors="replace"))
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if "\x00" not in key and "\x00" not in value:
            os.environ.setdefault(key, value)
