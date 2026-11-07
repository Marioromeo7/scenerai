"""Opt-in, low-cost live authentication check for the configured Groq key.

Run with RUN_GROQ_KEY_CHECK=1. The test only lists model metadata; it does not
generate text or print the credential.
"""
import os

import pytest
from groq import Groq

from config import settings


def test_groq_api_key_authenticates_and_lists_models():
    if os.getenv("RUN_GROQ_KEY_CHECK") != "1":
        pytest.skip("Set RUN_GROQ_KEY_CHECK=1 to make the live Groq key check")
    if not settings.groq_api_key.strip():
        pytest.fail("GROQ_API_KEY is missing from the environment or .env file")

    client = Groq(api_key=settings.groq_api_key, timeout=15.0, max_retries=0)
    try:
        response = client.models.list()
    except Exception as exc:
        pytest.fail(f"Groq key check failed ({type(exc).__name__}); credential was not displayed")

    assert response.data is not None
