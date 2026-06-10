"""Test bootstrap: provide required env vars before companion.* imports."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_USER_ID", "12345")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
