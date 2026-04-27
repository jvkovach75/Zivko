from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_STATE_PATH = Path(__file__).resolve().parent.parent / "last_run_inputs.json"


def shared_run_state_path() -> Path:
    return RUN_STATE_PATH


def read_shared_run_state() -> dict[str, Any] | None:
    if not RUN_STATE_PATH.exists():
        return None
    try:
        return json.loads(RUN_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_shared_run_state(
    *,
    source: str,
    command: str,
    status: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any] | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "command": command,
        "status": status,
        "inputs": inputs,
        "outputs": outputs or {},
        "error": error,
        "metadata": metadata or {},
    }
    RUN_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return RUN_STATE_PATH
