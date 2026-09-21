import os
import subprocess
import sys
from types import SimpleNamespace

from app import auth


def test_production_disables_local_password_auth():
    environment = os.environ | {
        "FASTSHOP_ENV": "production",
        "FASTSHOP_ADMIN_EMAIL": "merchant@example.com",
        "FASTSHOP_ADMIN_PASSWORD": "",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app import auth; from app.config import settings; "
                "assert settings.admin_password == ''; "
                "assert auth.local_login_allowed() is False"
            ),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_explicit_production_hash_login_preserves_account_boundary(monkeypatch):
    password = "synthetic-admin-password-for-tests-only"
    encoded = auth.hash_admin_password(password)
    config = SimpleNamespace(is_production=True, allow_password_login=True,
        admin_email="admin@example.test", admin_password_hash=encoded, admin_password="ignored")
    monkeypatch.setattr(auth, "settings", config)
    assert auth.local_login_allowed()
    assert auth.valid_local_credentials(" ADMIN@example.test ", password)
    assert not auth.valid_local_credentials("someone@example.test", password)
    assert not auth.valid_local_credentials(config.admin_email, "incorrect-password-for-tests")
    assert not auth.valid_local_credentials(config.admin_email, "ignored")
    config.allow_password_login = False
    assert not auth.valid_local_credentials(config.admin_email, password)


def test_malformed_or_unbounded_hashes_fail_closed():
    for encoded in ("", "plain-password", "pbkdf2_sha256:999999999:a:b",
                    "pbkdf2_sha256:600000:" + "z" * 32 + ":" + "z" * 64):
        assert not auth.verify_admin_password("long-test-password-not-real", encoded)


def test_production_password_attempts_are_bounded(monkeypatch):
    monkeypatch.setattr(auth, "settings", SimpleNamespace(is_production=True))
    monkeypatch.setattr(auth, "_password_attempts", auth.deque())
    monkeypatch.setattr(auth.time, "monotonic", lambda: 100)
    assert all(auth.password_attempt_allowed() for _ in range(10))
    assert not auth.password_attempt_allowed()
    monkeypatch.setattr(auth.time, "monotonic", lambda: 161)
    assert auth.password_attempt_allowed()
