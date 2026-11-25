"""The assistant's model endpoint is configurable; older XAI_* settings keep working."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def settings_with(**env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("MODEL_", "XAI_"))}
    code = "from app.config import settings; print(settings.xai_base_url); print(settings.xai_api_key)"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                            env=clean | {"DB_URL": "", "FASTSHOP_DATA_DIR": str(ROOT / "data")} | env,
                            timeout=60, check=True)
    return result.stdout.split()


def test_model_settings_take_precedence_over_the_older_xai_names():
    assert settings_with(XAI_API_KEY="xai-key") == ["https://api.x.ai/v1", "xai-key"]
    assert settings_with(XAI_API_KEY="xai-key", MODEL_API_KEY="model-key",
                         MODEL_BASE_URL="https://models.example.test/v1/") == [
        "https://models.example.test/v1", "model-key"]
