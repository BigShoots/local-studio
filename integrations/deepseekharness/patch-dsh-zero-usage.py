#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


ORIGINAL_USAGE_OF = 'const usageOf = (event) => event.type === "assistant/chunk" && event.data.chunk.type === "usage" ? event.data.chunk.usage : event.type === "assistant/message" ? event.data.usage : void 0;'
PATCHED_USAGE_OF = '''const usageIsZero = (usage) => usage.inputTokens === 0 && usage.outputTokens === 0 && (usage.cacheReadTokens ?? 0) === 0 && (usage.cacheWriteTokens ?? 0) === 0;
const usageOf = (event) => {
\tconst usage = event.type === "assistant/chunk" && event.data.chunk.type === "usage" ? event.data.chunk.usage : event.type === "assistant/message" ? event.data.usage : void 0;
\treturn usage === void 0 || usageIsZero(usage) ? void 0 : usage;
};'''
ORIGINAL_BUCKETS = '''\t\telse return state;
\t\tconst buckets = bucketsFrom(usage);'''
PATCHED_BUCKETS = '''\t\telse return state;
\t\tif (usageIsZero(usage)) return state;
\t\tconst buckets = bucketsFrom(usage);'''


def replace_once(text: str, original: str, patched: str, label: str) -> str:
    if patched in text:
        return text
    if text.count(original) != 1:
        raise RuntimeError(f"unable to locate {label}")
    return text.replace(original, patched, 1)


def patch(path: Path) -> bool:
    original = path.read_text()
    updated = replace_once(original, ORIGINAL_USAGE_OF, PATCHED_USAGE_OF, "usage extractor")
    updated = replace_once(updated, ORIGINAL_BUCKETS, PATCHED_BUCKETS, "usage projection")
    updated = replace_once(
        updated,
        'key: "tokenUsage",\n\tstateVersion: 1,',
        'key: "tokenUsage",\n\tstateVersion: 2,',
        "token usage state version",
    )
    updated = replace_once(
        updated,
        'key: "contextPressure",\n\tstateVersion: 4,',
        'key: "contextPressure",\n\tstateVersion: 5,',
        "context pressure state version",
    )
    if updated == original:
        return False
    path.write_text(updated)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        nargs="?",
        default=str(
            Path.home()
            / ".npm-global/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai"
        ),
    )
    args = parser.parse_args()
    target = Path(args.root) / "dsh-token-meter/lib/index.js"
    changed = patch(target)
    print(f"dsh-zero-usage: {'patched' if changed else 'already current'} {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
