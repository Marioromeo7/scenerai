"""Small contract tests for Vercel's build and workerless portfolio mode."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import vercel_build


def test_vercel_build_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        vercel_build.run_build()


def test_vercel_build_runs_migrations_before_idempotent_seed(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://build-test/db")
    with patch.object(vercel_build.subprocess, "run") as run:
        vercel_build.run_build()

    assert run.call_count == 2
    assert run.call_args_list[0].args[0][1:] == ["-m", "alembic", "upgrade", "head"]
    assert run.call_args_list[1].args[0][1:] == ["seed_demo.py"]
    assert all(call.kwargs["check"] for call in run.call_args_list)
    assert all(call.kwargs["cwd"] == Path(vercel_build.__file__).resolve().parent for call in run.call_args_list)


def test_vercel_routes_send_api_to_backend_and_rest_to_frontend():
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
    assert config["services"]["backend"]["entrypoint"] == "main:app"
    assert config["services"]["frontend"]["root"] == "frontend/app"
    assert config["rewrites"][0]["destination"]["service"] == "backend"
    assert config["rewrites"][-1]["destination"]["service"] == "frontend"
