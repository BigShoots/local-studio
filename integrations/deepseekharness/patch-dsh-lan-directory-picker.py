#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


METHOD = '\n\t"host.pickDirectory",'


def default_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home
        / ".npm-global/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai",
        home / ".dsh/profiles/node_modules/@deepseek-ai",
    ]
    roots.extend((home / ".dsh/profiles").glob("*/node_modules/@deepseek-ai"))
    return roots


def targets(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        candidates = [
            root / "dsh-client-connection/lib/index.js",
            root / "dsh/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js",
        ]
        for candidate in candidates:
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


def patch(path: Path) -> bool:
    original = path.read_text()
    if "const PRIVILEGED_METHODS = new Set([" not in original:
        raise RuntimeError(f"privileged method set not found in {path}")
    updated = original.replace(METHOD, "", 1)
    if updated == original:
        return False
    path.write_text(updated)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*")
    args = parser.parse_args()
    roots = [Path(value) for value in args.roots] if args.roots else default_roots()
    found = targets(roots)
    if not found:
        raise RuntimeError("no DSH client-connection installation found")
    for target in found:
        changed = patch(target)
        print(
            f"dsh-lan-directory-picker: {'patched' if changed else 'already current'} {target}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
