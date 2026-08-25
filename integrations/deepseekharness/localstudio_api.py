from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

CONTROLLER_ROOT = os.environ.get("LOCALSTUDIO_CONTROLLER_URL", "http://127.0.0.1:8080").rstrip("/")


def _env_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    prefix = f"{key}="
    for line in lines:
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None
    return None


def controller_api_key() -> str:
    configured = os.environ.get("LOCALSTUDIO_CONTROLLER_API_KEY") or os.environ.get(
        "LOCAL_STUDIO_API_KEY"
    )
    if configured:
        return configured
    configured_env = os.environ.get("LOCALSTUDIO_CONTROLLER_ENV")
    candidates = [
        Path(configured_env).expanduser() if configured_env else None,
        Path.home() / "local-studio" / ".env",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        value = _env_value(candidate, "LOCAL_STUDIO_API_KEY")
        if value:
            return value
    raise RuntimeError("Local Studio controller API key not found")


def request_json(
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
    method: str | None = None,
) -> dict[str, Any] | list[Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request_method = method or ("GET" if payload is None else "POST")
    request = urllib.request.Request(
        CONTROLLER_ROOT + path,
        data=data,
        method=request_method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller_api_key()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode()) if raw else {}
