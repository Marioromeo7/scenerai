"""
Transaction-backed API security probe for Scenarai.

Creates temporary users/personas/scenarios through the FastAPI app, lets route
handlers call commit(), then rolls everything back at the outer connection
transaction. No test data should persist when the script exits normally.

Usage from repo root:
  python codex/api_transaction_security_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if not BACKEND.exists() and (ROOT / "main.py").exists():
    BACKEND = ROOT
sys.path.insert(0, str(BACKEND))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-16", errors="ignore") if b"\x00" in raw_bytes else raw_bytes.decode("utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if "\x00" not in key and "\x00" not in value:
            os.environ.setdefault(key, value)


load_env_file(ROOT / "backend.env")
load_env_file(ROOT / "postgres.env")

import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

import main as app_module  # noqa: E402
from database import engine  # noqa: E402


def assert_status(name: str, actual: int, allowed: set[int], details: list[dict[str, Any]]) -> None:
    passed = actual in allowed
    details.append({"name": name, "passed": passed, "status": actual, "expected": sorted(allowed)})
    marker = "PASS" if passed else "FAIL"
    print(f"{marker}: {name}: status={actual}, expected={sorted(allowed)}")


def print_body(label: str, response: httpx.Response) -> None:
    try:
        body = response.json()
    except Exception:
        body = response.text[:500]
    print(f"{label} body: {body}")


async def run() -> int:
    app = app_module.app
    details: list[dict[str, Any]] = []

    async with engine.connect() as conn:
        outer = await conn.begin()
        TestSession = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async def override_get_db():
            async with TestSession() as session:
                yield session

        app.dependency_overrides[app_module.get_db] = override_get_db

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                user_a = {
                    "email": "codex-a@scenarai-test.com",
                    "password": "correct horse battery staple",
                }
                user_b = {
                    "email": "codex-b@scenarai-test.com",
                    "password": "correct horse battery staple",
                }
                ra = await client.post("/auth/register", json=user_a)
                rb = await client.post("/auth/register", json=user_b)
                assert_status("register user A", ra.status_code, {201}, details)
                assert_status("register user B", rb.status_code, {201}, details)
                if ra.status_code != 201 or rb.status_code != 201:
                    print_body("register user A", ra)
                    print_body("register user B", rb)
                    return 1

                token_a = ra.json()["access_token"]
                token_b = rb.json()["access_token"]
                ha = {"Authorization": f"Bearer {token_a}"}
                hb = {"Authorization": f"Bearer {token_b}"}

                persona = await client.post(
                    "/personas",
                    headers=ha,
                    json={"name": "Alex", "pronouns": "they/them", "brief": "test persona"},
                )
                assert_status("owner creates persona", persona.status_code, {201}, details)

                scenario_payload = {
                    "char_name": "Iris Vale",
                    "char_pronouns": "she/her",
                    "char_title": "Archivist of a sealed room",
                    "char_personality": "Controlled, indirect, refuses direct confession.",
                    "greeting": "Iris stands outside the sealed archive. The key is in Alex's pocket.",
                }
                scenario = await client.post("/scenarios", headers=ha, json=scenario_payload)
                assert_status("owner creates draft scenario", scenario.status_code, {201}, details)
                if scenario.status_code != 201:
                    return 1
                sid = scenario.json()["id"]

                owner_get = await client.get(f"/scenarios/{sid}", headers=ha)
                assert_status("owner can read own draft", owner_get.status_code, {200}, details)

                stranger_get = await client.get(f"/scenarios/{sid}", headers=hb)
                assert_status("stranger cannot read unpublished draft", stranger_get.status_code, {403, 404}, details)

                stranger_update = await client.put(
                    f"/scenarios/{sid}",
                    headers=hb,
                    json={"char_name": "Changed"},
                )
                assert_status("stranger cannot update draft", stranger_update.status_code, {403, 404}, details)

                stranger_delete = await client.delete(f"/scenarios/{sid}", headers=hb)
                assert_status("stranger cannot delete draft", stranger_delete.status_code, {403, 404}, details)

                stranger_save = await client.post(f"/scenarios/{sid}/save", headers=hb)
                assert_status("stranger cannot save unpublished draft", stranger_save.status_code, {403, 404}, details)
        finally:
            app.dependency_overrides.pop(app_module.get_db, None)
            await outer.rollback()

    failures = [d for d in details if not d["passed"]]
    print("\nRolled back outer transaction.")
    if failures:
        print(f"FAIL: {len(failures)} security expectation(s) failed.")
        return 1
    print("PASS: security expectations held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
