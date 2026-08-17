#!/usr/bin/env python3
"""Rewrap implicitly-concatenated string blocks to fit the line-length limit.

Ruff's E501 fires on CLI ``help=`` blocks written as adjacent string literals,
one per line, where a line runs past the limit. Splitting a single offending
line leaves a stub literal behind, so this rewraps the whole block: it joins
the adjacent literals, refills them to the limit, and writes them back with the
word separator kept at the end of each literal. The concatenated string the
compiler sees is unchanged.

Usage:
    python scripts/rewrap_long_help.py [--limit N] PATH [PATH ...]

Exits 1 if an over-long line is not part of a block this can rewrap, so the
caller sees what still needs a human.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# One whole line holding a single double-quoted literal, optionally introduced
# by a keyword (``help=``) and optionally followed by a comma.
_LITERAL_LINE = re.compile(
    r'^(?P<indent>[ ]*)(?P<lead>[A-Za-z_][A-Za-z_0-9]*=)?'
    r'"(?P<body>(?:[^"\\]|\\.)*)"(?P<trail>,?)[ ]*$'
)


def _fill(body: str, indent: str, lead: str, limit: int) -> list[str]:
    """Split one joined body into literal lines that each fit the limit."""
    words = body.split(" ")
    lines: list[str] = []
    current = ""
    first = True
    for word in words:
        budget = limit - len(indent) - 2 - (len(lead) if first else 0)
        candidate = word if not current else f"{current} {word}"
        # A trailing space is written on every literal but the last, so keep
        # room for it while deciding.
        if current and len(candidate) + 1 > budget:
            lines.append(current)
            current = word
            first = False
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _rewrap_block(block: list[re.Match[str]], limit: int) -> list[str] | None:
    """Rewrap one run of adjacent literal lines, or None when unchanged."""
    indent = block[0].group("indent")
    lead = block[0].group("lead") or ""
    trail = block[-1].group("trail")
    if any(m.group("indent") != indent for m in block[1:]):
        return None
    if any(m.group("lead") for m in block[1:]):
        return None

    body = "".join(m.group("body") for m in block)
    parts = _fill(body, indent, lead, limit)
    out: list[str] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        text = part if last else part + " "
        prefix = lead if i == 0 else ""
        out.append(f'{indent}{prefix}"{text}"{trail if last else ""}')
    return out


def _rewrap_file(path: Path, limit: int) -> tuple[int, list[int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    rewrapped = 0
    unfixable: list[int] = []

    i = 0
    while i < len(lines):
        match = _LITERAL_LINE.match(lines[i])
        if match is None:
            if len(lines[i]) > limit:
                unfixable.append(i + 1)
            out.append(lines[i])
            i += 1
            continue

        block = [match]
        j = i + 1
        while not block[-1].group("trail") and j < len(lines):
            nxt = _LITERAL_LINE.match(lines[j])
            if nxt is None or nxt.group("lead"):
                break
            block.append(nxt)
            j += 1

        if any(len(lines[i + k]) > limit for k in range(len(block))):
            replacement = _rewrap_block(block, limit)
            if replacement is None:
                unfixable.extend(
                    i + k + 1 for k in range(len(block)) if len(lines[i + k]) > limit
                )
                out.extend(lines[i:j])
            else:
                out.extend(replacement)
                rewrapped += 1
        else:
            out.extend(lines[i:j])
        i = j

    if rewrapped:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return rewrapped, unfixable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=88)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    failed = False
    for path in args.paths:
        rewrapped, unfixable = _rewrap_file(path, args.limit)
        print(f"{path}: rewrapped {rewrapped} block(s)")
        for lineno in unfixable:
            print(f"  {path}:{lineno}: not a rewrappable string block")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
