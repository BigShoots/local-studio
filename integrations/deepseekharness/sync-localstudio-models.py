#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.error
from pathlib import Path

from localstudio_api import request_json

DSH = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
SETTINGS = DSH / "settings.yaml"
PROVIDER = "localstudio"


def pretty_name(value: str) -> str:
    name = value.replace("@", " ").replace("-", " ").replace("_", " ")
    return f"{re.sub(r'\s+', ' ', name).strip()} (Local Studio)"


def fetch_models() -> list[dict[str, object]] | None:
    try:
        payload = request_json("/recipes", timeout=10)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, ValueError):
        return None
    if not isinstance(payload, list):
        return []
    models: list[dict[str, object]] = []
    seen: set[str] = set()
    for recipe in payload:
        if not isinstance(recipe, dict):
            continue
        recipe_id = recipe.get("id")
        served_name = recipe.get("served_model_name")
        model_id = served_name if isinstance(served_name, str) and served_name else recipe_id
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        seen.add(model_id)
        recipe_name = recipe.get("name")
        model: dict[str, object] = {
            "id": model_id,
            "name": f"{recipe_name} (Local Studio)"
            if isinstance(recipe_name, str) and recipe_name.strip()
            else pretty_name(model_id),
        }
        context = recipe.get("max_model_len")
        if isinstance(context, int) and context > 0:
            model["contextWindow"] = context
        if recipe.get("vision") is True:
            model["input"] = ["text", "image"]
        else:
            model["input"] = ["text"]
        models.append(model)
    return models


def existing_models(text: str) -> dict[str, dict[str, object]]:
    block = re.search(
        rf"    {PROVIDER}:\n(?:      .*\n)*?      models:\n((?:        .*\n)+)", text
    )
    if not block:
        return {}
    found: dict[str, dict[str, object]] = {}
    current: dict[str, object] | None = None
    for line in block.group(1).splitlines():
        id_match = re.match(r"        - id:\s*(.+)\s*$", line)
        if id_match:
            current = {"id": id_match.group(1).strip()}
            found[str(current["id"])] = current
            continue
        if current is None:
            continue
        field = re.match(r"          (name|contextWindow|maxTokens|input):\s*(.+)\s*$", line)
        if not field:
            continue
        key, raw = field.group(1), field.group(2).strip()
        if key == "name":
            current[key] = raw
        elif key == "input":
            current[key] = [item.strip() for item in raw.strip("[]").split(",") if item.strip()]
        else:
            try:
                current[key] = int(raw)
            except ValueError:
                pass
    return found


def merged_models(
    discovered: list[dict[str, object]], previous: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for model in discovered:
        old = previous.get(str(model["id"]), {})
        row = {"id": model["id"], "name": old.get("name") or model.get("name")}
        for key in ("contextWindow", "maxTokens", "input"):
            if key in model:
                row[key] = model[key]
            elif key in old:
                row[key] = old[key]
        merged.append(row)
    return merged


def render_models(models: list[dict[str, object]]) -> str:
    if not models:
        return "      models: []\n"
    lines = ["      models:"]
    for model in models:
        lines.append(f"        - id: {model['id']}")
        if model.get("name"):
            lines.append(f"          name: {model['name']}")
        if model.get("contextWindow"):
            lines.append(f"          contextWindow: {model['contextWindow']}")
        if model.get("maxTokens"):
            lines.append(f"          maxTokens: {model['maxTokens']}")
        inputs = model.get("input")
        if isinstance(inputs, list) and inputs:
            lines.append(f"          input: [ {', '.join(str(value) for value in inputs)} ]")
    return "\n".join(lines) + "\n"


def write_if_changed(models: list[dict[str, object]]) -> bool:
    current = SETTINGS.read_text()
    models = merged_models(models, existing_models(current))
    block = render_models(models)
    pattern = re.compile(
        rf"(    {PROVIDER}:\n(?:      .*\n)*?)      models:(?: \[\])?\n(?:        .*\n)*", re.M
    )
    if not pattern.search(current):
        raise RuntimeError(f"{PROVIDER} provider missing from {SETTINGS}")
    updated = pattern.sub(r"\1" + block, current, count=1)
    if updated == current:
        return False
    temporary = SETTINGS.with_suffix(".yaml.tmp")
    temporary.write_text(updated)
    temporary.replace(SETTINGS)
    os.chmod(SETTINGS, 0o600)
    return True


def sync_once() -> bool:
    models = fetch_models()
    if models is None:
        print("localstudio-sync: controller not reachable", file=sys.stderr)
        return False
    if write_if_changed(models):
        print(f"localstudio-sync: updated {len(models)} model(s)", flush=True)
        return True
    print(f"localstudio-sync: already current ({len(models)} model(s))")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    sync_once()
    if not args.watch:
        return 0
    signature: tuple[tuple[object, ...], ...] | None = None
    while True:
        time.sleep(max(1.0, args.interval))
        models = fetch_models()
        if models is None:
            continue
        current_signature = tuple(
            (model.get("id"), model.get("contextWindow"), tuple(model.get("input", [])))
            for model in models
        )
        if current_signature == signature:
            continue
        signature = current_signature
        write_if_changed(models)
        print(f"localstudio-sync: catalog now {len(models)} model(s)", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
