#!/usr/bin/env python3
"""Remap commit hashes in JSONL changelogs after a git history rewrite.

After a history rewrite (filter-repo, rebase), released JSONL changelog files
still embed pre-rewrite commit SHAs. Once the old refs are removed, those SHAs
become unresolvable and changelog validation breaks permanently.

This script builds an old->new SHA mapping by zipping the two histories
(valid when the rewrite preserved commit order, count, author dates, and
subjects -- verified before mapping), then rewrites every hash in the JSONL
files. Released JSONL files are chmod 444; the script temporarily makes them
writable and restores the mode.

Usage:
    scripts/remap_changelog_hashes.py --old-ref origin/main --new-ref HEAD \
        --changes-dir .rlsbl-monorepo/releasables/orxtra/changes
"""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
from pathlib import Path


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def build_mapping(old_ref: str, new_ref: str) -> dict[str, str]:
    merge_base = git_lines("merge-base", new_ref, old_ref)[0]
    old_shas = git_lines("rev-list", "--reverse", f"{merge_base}..{old_ref}")
    new_shas = git_lines("rev-list", "--reverse", f"{merge_base}..{new_ref}")[: len(old_shas)]
    if len(old_shas) != len(new_shas):
        sys.exit(f"history length mismatch: {len(old_shas)} old vs {len(new_shas)} new")

    old_meta = git_lines("log", "--reverse", "--format=%ai|%s", f"{merge_base}..{old_ref}")
    new_meta = git_lines("log", "--reverse", "--format=%ai|%s", f"{merge_base}..{new_ref}")[: len(old_meta)]
    if old_meta != new_meta:
        sys.exit("histories do not correspond 1:1 by (date, subject); zip mapping is unsafe")

    return dict(zip(old_shas, new_shas, strict=True))


def remap_file(path: Path, mapping: dict[str, str]) -> int:
    original_mode = path.stat().st_mode
    text = path.read_text()
    replaced = 0
    lines_out = []
    for line in text.splitlines():
        if not line.strip():
            lines_out.append(line)
            continue
        entry = json.loads(line)
        commits = entry.get("commits", [])
        new_commits = []
        for sha in commits:
            if sha in mapping:
                new_commits.append(mapping[sha])
                replaced += 1
            else:
                new_commits.append(sha)
        entry["commits"] = new_commits
        lines_out.append(json.dumps(entry, separators=(",", ":"), ensure_ascii=False))
    if replaced:
        path.chmod(original_mode | stat.S_IWUSR)
        path.write_text("\n".join(lines_out) + "\n")
        path.chmod(original_mode)
    return replaced


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-ref", required=True, help="Ref pointing at the pre-rewrite history")
    parser.add_argument("--new-ref", required=True, help="Ref pointing at the rewritten history")
    parser.add_argument("--changes-dir", required=True, help="Directory containing JSONL changelog files")
    parser.add_argument("--dry-run", action="store_true", help="Report replacements without writing")
    args = parser.parse_args()

    mapping = build_mapping(args.old_ref, args.new_ref)
    print(f"mapping built: {len(mapping)} old->new SHA pairs")

    changes_dir = Path(args.changes_dir)
    if not changes_dir.is_dir():
        sys.exit(f"not a directory: {changes_dir}")

    total = 0
    for jsonl in sorted(changes_dir.glob("*.jsonl")):
        if args.dry_run:
            text = jsonl.read_text()
            count = sum(1 for old in mapping if old in text)
            print(f"{jsonl.name}: {count} hashes would be replaced")
            total += count
        else:
            count = remap_file(jsonl, mapping)
            print(f"{jsonl.name}: {count} hashes replaced")
            total += count
    print(f"total: {total} hashes {'would be ' if args.dry_run else ''}replaced")


if __name__ == "__main__":
    main()
