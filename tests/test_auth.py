import os
import subprocess
import sys


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
