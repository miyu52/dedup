#!/usr/bin/env python3
"""
dedup.py — Move duplicate files from source directories into a Dedup folder.

Strategy
────────
1. Walk source dirs; skip symlinks.
2. Group files by size. Only hash content when ≥2 files share a size.
3. SHA256 full-content comparison for same-size groups.
4. Priority chain: dirs given in order → later dirs sacrifice files first.
   Within the same source dir: lexicographically smaller path stays.
5. Moved files preserve relative path: Dedup/<basename_of_source>/<relpath>.
6. Dedup dir is never scanned (even if it lives inside a source dir).
7. Streaming: each size group is hashed and processed immediately —
   no waiting for all files to be hashed.

Usage
─────
  python3 dedup.py A/ B/ C/                     # real run
  python3 dedup.py --dry-run A/ B/ C/            # preview only
  python3 dedup.py -d ./Duplicates A/ B/ C/      # custom Dedup dir
  uv run dedup --dry-run A/ B/ C/               # via uv
"""

import argparse
import hashlib
import os
import sys
from collections import defaultdict
from typing import Dict, Iterator, List, Tuple

CHUNK_SIZE = 64 * 1024  # 64 KiB


# ── helpers ────────────────────────────────────────────────────────────────

def get_sha256(path: str) -> str:
    """Return hex SHA256 digest of *path*, reading in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def resolve_dedup_target(dedup_root: str, src_basename: str, rel: str) -> str:
    """Build destination path, adding _N suffix on collision."""
    base = os.path.join(dedup_root, src_basename, rel)
    if not os.path.exists(base):
        return base

    stem, ext = os.path.splitext(base)
    n = 1
    while True:
        candidate = f"{stem}_{n}{ext}"
        if not os.path.exists(candidate):
            return candidate
        n += 1


# ── scanning ────────────────────────────────────────────────────────────────

def scan_sources(source_dirs: List[str], dedup_real: str) -> Tuple[List[Tuple], int]:
    """
    Walk every source dir.  Return:
        files    [(full_path, source_basename, rel, size), …]
        skipped  count of files skipped (symlinks, unreadable, etc.)
    """
    files: List[Tuple] = []
    skipped = 0

    for src in source_dirs:
        src = os.path.abspath(src)
        src_basename = os.path.basename(src.rstrip("/")) or os.path.basename(src)
        for root, dirs, filenames in os.walk(src, followlinks=False):
            root_real = os.path.realpath(root)
            if root_real == dedup_real or root_real.startswith(dedup_real + os.sep):
                dirs.clear()
                continue

            for fn in filenames:
                fpath = os.path.join(root, fn)
                try:
                    if os.path.islink(fpath):
                        skipped += 1
                        continue
                    size = os.path.getsize(fpath)
                except OSError:
                    skipped += 1
                    continue
                rel = os.path.relpath(fpath, src)
                files.append((fpath, src_basename, rel, size))

    return files, skipped


# ── dedup logic ─────────────────────────────────────────────────────────────

def iter_duplicates(by_size: Dict[int, list], order_map: dict) -> Iterator[list]:
    """
    Yield duplicate groups as they are discovered.
    Each group is a list of (full_path, src_basename, rel), sorted so that
    the *first* entry is KEEP and the rest are MOVE.
    """
    for size_group in by_size.values():
        if len(size_group) < 2:
            continue

        by_hash: Dict[str, list] = defaultdict(list)
        for fp, bn, rel in size_group:
            try:
                h = get_sha256(fp)
            except OSError:
                continue
            by_hash[h].append((fp, bn, rel))

        for hash_group in by_hash.values():
            if len(hash_group) < 2:
                continue
            hash_group.sort(key=lambda x: (order_map[x[1]], x[2]))
            yield hash_group


def process_group(group, dedup_root, dry_run):
    """
    Process one duplicate group: first item KEEP, rest MOVE.
    Prints info immediately (streaming).
    Returns (moved_count, failed_count, moved_bytes).
    """
    keep = group[0]
    moves = group[1:]

    try:
        rep_size = os.path.getsize(keep[0])
    except OSError:
        rep_size = 0

    try:
        h = get_sha256(keep[0])
    except OSError:
        h = "???"

    print(f"\n[Group] SHA256: {h}  (size: {rep_size:,} bytes)")
    print(f"  KEEP  {keep[0]}")

    moved = 0
    failed = 0
    moved_bytes = 0

    for fp, bn, rel in moves:
        target = resolve_dedup_target(dedup_root, bn, rel)
        print(f"  MOVE  {fp}  →  {target}")

        if not dry_run:
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.rename(fp, target)
                moved += 1
                moved_bytes += rep_size
            except OSError as exc:
                print(f"         ⚠ FAILED: {exc}")
                failed += 1
        else:
            moved += 1
            moved_bytes += rep_size

    return moved, failed, moved_bytes


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find and move duplicate files (by SHA256 content) into a Dedup folder.",
    )
    parser.add_argument(
        "sources", nargs="+", metavar="DIR",
        help="Source directories to scan (priority order: first dir has highest priority).",
    )
    parser.add_argument(
        "-d", "--dedup", default="./Dedup",
        help="Dedup target directory (default: ./Dedup).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only; do not move any files.",
    )
    args = parser.parse_args()

    source_dirs = [os.path.abspath(d) for d in args.sources]
    dedup_root = os.path.abspath(args.dedup)

    # Warn if Dedup is inside a source dir (scanner will skip it)
    for sd in source_dirs:
        try:
            if dedup_root == sd or dedup_root.startswith(sd + os.sep):
                print(f"⚠ Dedup dir ({dedup_root}) is inside source dir ({sd}).")
                print(f"  It will be excluded from scanning to avoid loops.")
        except Exception:
            pass

    try:
        dedup_real = os.path.realpath(dedup_root)
    except OSError:
        dedup_real = dedup_root

    order_map = {
        os.path.basename(d.rstrip("/")) or os.path.basename(d): i
        for i, d in enumerate(source_dirs)
    }

    print(f"Sources: {', '.join(source_dirs)}")
    print(f"Dedup:   {dedup_root}")
    print(f"Mode:    {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    # ── phase 1: scan ──
    print("Scanning...", end=" ", flush=True)
    files, scan_skipped = scan_sources(source_dirs, dedup_real)
    print(f"{len(files)} files found ({scan_skipped} skipped)")

    # ── phase 2: group by size ──
    by_size: Dict[int, list] = defaultdict(list)
    for fp, bn, rel, sz in files:
        by_size[sz].append((fp, bn, rel))

    # Count how many size groups will need hashing (have ≥2 files)
    hash_candidates = sum(1 for g in by_size.values() if len(g) >= 2)
    total_files_to_hash = sum(len(g) for g in by_size.values() if len(g) >= 2)

    print(f"Size groups requiring hash: {hash_candidates} ({total_files_to_hash} files)")

    # ── phase 3: hash & process — streaming ──
    total_moved = 0
    total_failed = 0
    total_moved_bytes = 0
    dup_group_count = 0

    for group in iter_duplicates(by_size, order_map):
        m, f, mb = process_group(group, dedup_root, args.dry_run)
        total_moved += m
        total_failed += f
        total_moved_bytes += mb
        dup_group_count += 1

    # ── summary ──
    if dup_group_count == 0:
        print("\nNo duplicates found.")

    print(f"\n=== Summary ===")
    print(f"  Total files scanned:    {len(files)}")
    print(f"  Skipped (symlinks, etc):  {scan_skipped}")
    print(f"  Duplicate groups:       {dup_group_count}")
    print(f"  Files moved:            {total_moved}")
    print(f"  Files failed:           {total_failed}")
    print(f"  Space saved (approx):   {total_moved_bytes:,} bytes")

    if args.dry_run:
        print(f"\n  (Dry run — no files were actually moved)")

    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
