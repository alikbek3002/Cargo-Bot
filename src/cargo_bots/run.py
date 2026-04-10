from __future__ import annotations

import os
from typing import Final


APP_TARGETS: Final[dict[str, str]] = {
    "combined_web": "cargo_bots.main:app",
    "admin_web": "cargo_bots.admin_app:app",
    "client_web": "cargo_bots.client_app:app",
}


def normalize_app_role(value: str | None) -> str:
    if value is None or not value.strip():
        raise RuntimeError(
            "APP_ROLE is not set. Use one of: admin_web, client_web, combined_web, worker. "
            "On Railway you can also deploy from /admin-web, /client-web, or /worker."
        )
    role = value.strip().lower()
    if role not in {*APP_TARGETS, "worker"}:
        raise RuntimeError(
            "Unsupported APP_ROLE. Use one of: admin_web, client_web, combined_web, worker."
        )
    return role


def main() -> None:
    role = normalize_app_role(os.getenv("APP_ROLE"))
    if role == "worker":
        _run_worker()
        return
    _run_web(APP_TARGETS[role])


def _run_web(app_target: str) -> None:
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app_target, host="0.0.0.0", port=port)


def _run_worker() -> None:
    from cargo_bots.tasks.celery_app import celery_app

    celery_app.worker_main(["worker", "-l", os.getenv("CELERY_LOG_LEVEL", "info")])


if __name__ == "__main__":
    main()
