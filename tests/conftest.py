import os
from pathlib import Path

import pydantic_ai.models
import pytest

if os.getenv("DIG_RUN_REAL_MODEL_TESTS") != "1":
    pydantic_ai.models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
