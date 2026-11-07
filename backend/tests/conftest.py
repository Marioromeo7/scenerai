import sys
import os
from pathlib import Path

import pytest

# Put backend/ on the path so tests can import engine.*, config, etc.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def prevent_accidental_webhook_delivery(monkeypatch):
    """Unit tests must not send real alerts unless explicitly opted in."""
    if os.getenv("RUN_WEBHOOK_CHECK") != "1":
        from config import settings
        monkeypatch.setattr(settings, "alert_webhook_url", "")
