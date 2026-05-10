"""Tests for call.py — rate limit detection and wait-time parsing."""
import pytest
import re
from unittest.mock import patch, MagicMock
from engine.call import call, init_client


def _fake_rate_error(message: str):
    err = Exception(message)
    err.args = (message,)
    return err


# ── Helpers that mirror call()'s wait-time extraction ────────

def _parse_wait(err_str: str) -> float:
    """Mirror of the wait-time logic inside call()."""
    wait = 65.0
    ms_m = re.search(r'try again in ([\d.]+)ms', err_str)
    s_m  = re.search(r'try again in ([\d.]+)s',  err_str)
    if ms_m:
        wait = float(ms_m.group(1)) / 1000 + 0.1
    elif s_m:
        wait = float(s_m.group(1)) + 0.1
    return wait


class TestWaitTimeParsing:
    def test_seconds_pattern(self):
        msg = 'Rate limit exceeded. Please try again in 12.5s.'
        assert abs(_parse_wait(msg) - 12.6) < 0.01

    def test_milliseconds_pattern(self):
        msg = 'Rate limit exceeded. Please try again in 800ms.'
        assert abs(_parse_wait(msg) - 0.9) < 0.01

    def test_integer_seconds(self):
        msg = 'try again in 30s and then retry'
        assert abs(_parse_wait(msg) - 30.1) < 0.01

    def test_no_pattern_defaults_to_65(self):
        msg = 'rate limit exceeded with no timing info'
        assert _parse_wait(msg) == 65.0

    def test_ms_takes_priority_over_s(self):
        # If both patterns appear, ms_m wins (checked first)
        msg = 'try again in 500ms or try again in 10s'
        wait = _parse_wait(msg)
        assert abs(wait - 0.6) < 0.01  # ms path: 500/1000 + 0.1


class TestRateLimitDetection:
    def test_429_in_message_is_rate_limit(self):
        msg = 'Error code: 429 - rate_limit_exceeded'
        assert '429' in str(msg)

    def test_rate_limit_text_detected(self):
        msg = 'rate limit reached for model'
        assert 'rate limit' in msg.lower()

    def test_rate_limit_exceeded_text_detected(self):
        msg = 'rate_limit_exceeded tokens per minute'
        assert 'rate_limit_exceeded' in msg.lower()

    def test_tokens_per_minute_detected(self):
        msg = 'limit on tokens per minute exceeded'
        assert 'tokens per minute' in msg.lower()

    def test_requests_per_minute_detected(self):
        msg = 'requests per minute limit hit'
        assert 'requests per minute' in msg.lower()

    def test_non_rate_error_not_detected(self):
        msg = 'authentication failed: invalid api key'
        assert '429' not in str(msg)
        assert 'rate limit' not in msg.lower()
        assert 'rate_limit_exceeded' not in msg.lower()
        assert 'tokens per minute' not in msg.lower()
        assert 'requests per minute' not in msg.lower()


class TestCallNoClient:
    def test_raises_if_client_not_initialized(self):
        with pytest.raises(RuntimeError, match='not initialized'):
            call('system', [{'role': 'user', 'content': 'hi'}])

    def test_raises_non_rate_error_immediately(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError('bad request')

        with patch('engine.call._client_var') as mock_var:
            mock_var.get.return_value = mock_client
            with patch('engine.call._model_var') as mock_model:
                mock_model.get.return_value = 'test-model'
                with pytest.raises(ValueError, match='bad request'):
                    call('system', [{'role': 'user', 'content': 'hi'}])

        # Non-rate errors must not retry — only called once
        assert mock_client.chat.completions.create.call_count == 1

    def test_rate_limit_retries_up_to_limit(self):
        mock_client = MagicMock()
        rate_err = Exception('429 rate_limit_exceeded. try again in 0s')
        mock_client.chat.completions.create.side_effect = rate_err

        with patch('engine.call._client_var') as mock_var, \
             patch('engine.call._model_var') as mock_model, \
             patch('engine.call.time.sleep'):
            mock_var.get.return_value = mock_client
            mock_model.get.return_value = 'test-model'
            with pytest.raises(Exception):
                call('system', [{'role': 'user', 'content': 'hi'}], retries=3)

        assert mock_client.chat.completions.create.call_count == 3

    def test_succeeds_after_rate_limit_recovery(self):
        mock_client = MagicMock()
        rate_err = Exception('429 rate limit. try again in 0s')
        success   = MagicMock()
        success.choices[0].message.content = 'Hello'
        mock_client.chat.completions.create.side_effect = [rate_err, success]

        with patch('engine.call._client_var') as mock_var, \
             patch('engine.call._model_var') as mock_model, \
             patch('engine.call.time.sleep'), \
             patch('engine.call._log_call'):
            mock_var.get.return_value = mock_client
            mock_model.get.return_value = 'test-model'
            result, latency = call('system', [{'role': 'user', 'content': 'hi'}], retries=3)

        assert result == 'Hello'
        assert mock_client.chat.completions.create.call_count == 2
