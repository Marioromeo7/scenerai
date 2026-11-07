"""Opt-in live delivery check for the configured incoming webhook.

Run with RUN_WEBHOOK_CHECK=1 after adding ALERT_WEBHOOK_URL to the local .env.
This sends one small test message to that private webhook.
"""
import os

import pytest

import alerting
from config import settings


@pytest.mark.asyncio
async def test_configured_webhook_accepts_a_test_alert():
    if os.getenv("RUN_WEBHOOK_CHECK") != "1":
        pytest.skip("Set RUN_WEBHOOK_CHECK=1 to send a test message to the configured webhook")
    if not settings.alert_webhook_url.strip():
        pytest.fail("ALERT_WEBHOOK_URL is missing from the environment or .env file")

    delivered = await alerting.send_alert(
        "webhook_test", "Scenarai webhook connection test succeeded."
    )

    assert delivered is True, "Webhook rejected or could not receive the test message"
