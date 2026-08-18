"""Tests for config.py's _warn_on_weak_defaults -- extracted from what
would otherwise be untestable import-time side effects (settings = Settings()
runs once at module load). Found live via code review that jwt_secret,
the postgres password embedded in database_url, and redis_url all silently
fall back to well-known weak defaults with no signal at all if .env is
ever missing them in a real deployment -- confirmed the current .env-backed
deployment isn't affected, but the silent-failure mode itself is real."""
import logging
from unittest.mock import MagicMock

from config import _warn_on_weak_defaults


def _settings(jwt_secret='real-secret-value', database_url='postgresql+asyncpg://u:realpass@host/db',
              redis_url='redis://:realpass@host:6379/0'):
    return MagicMock(jwt_secret=jwt_secret, database_url=database_url, redis_url=redis_url)


class TestWarnOnWeakDefaults:
    def test_no_warning_when_everything_is_configured(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_on_weak_defaults(_settings())
        assert 'SECURITY' not in caplog.text

    def test_warns_when_jwt_secret_is_the_dev_default(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_on_weak_defaults(_settings(jwt_secret='dev_secret'))
        assert 'JWT_SECRET' in caplog.text

    def test_warns_when_postgres_password_is_changeme(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_on_weak_defaults(_settings(database_url='postgresql+asyncpg://scenarai:changeme@localhost:5432/scenarai'))
        assert 'POSTGRES' in caplog.text

    def test_warns_when_redis_url_has_no_password(self, caplog):
        with caplog.at_level(logging.WARNING):
            _warn_on_weak_defaults(_settings(redis_url='redis://localhost:6379/0'))
        assert 'REDIS_URL' in caplog.text

    def test_password_only_containing_changeme_as_a_substring_does_not_false_positive(self, caplog):
        """changemeanwhile123 legitimately contains "changeme" as a
        substring -- must not trip the check. The check matches the exact
        default credential shape (":changeme@"), not a bare substring of
        the whole password."""
        with caplog.at_level(logging.WARNING):
            _warn_on_weak_defaults(_settings(
                database_url='postgresql+asyncpg://u:changemeanwhile123@host/db'
            ))
        assert 'POSTGRES' not in caplog.text
