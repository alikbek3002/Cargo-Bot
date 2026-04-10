from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from cargo_bots.tasks.celery_app import celery_app


if __name__ == "__main__":
    celery_app.worker_main(["worker", "-l", os.getenv("CELERY_LOG_LEVEL", "info")])
