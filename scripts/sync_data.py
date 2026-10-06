#!/usr/bin/env python3
"""Move bulky Inrix data between the local drive and an archive drive (SSD).

Wiring only. The copy/verify/release engine is :mod:`inrix_tools.sync`, which is
project-agnostic; everything here is the Inrix adapter — it enumerates which
repo files are sync units and resolves the per-host archive root.

The heavy DuckDB stores (``d{1..6}_store.duckdb``, ``inrix_store.duckdb``) are
~13 GB and cannot be queried over the 9p ChromeOS removable-media share, so the
workflow is: pull a store to local disk, run the screening tools against it,
push it back, and — on the space-constrained Chromebook — release the local
copy to reclaim space::

    # One-time per machine: point at the repo's mirror on the archive drive.
    python scripts/sync_data.py status \\
        --archive-root "/mnt/chromeos/removable/Lexar/Local_Archives/Inrix" --save

    python scripts/sync_data.py status                 # where does each unit live?
    python scripts/sync_data.py push --release         # offload everything, free local
    python scripts/sync_data.py push --components db --release   # just the stores
    python scripts/sync_data.py pull --components db    # fetch the stores back to work

Archive root resolves ``--archive-root`` flag > ``INRIX_SYNC_ARCHIVE_ROOT`` env
> the ``archive_root`` key in ``.inrix_sync.json`` (gitignored, per-host). The
archive root mirrors *this repo's root*: a unit at ``<repo>/d3_store.duckdb``
lives at ``<archive_root>/d3_store.duckdb``.

Sync groups (``--components``, comma-separated, or ``all``):

    db        the top-level DuckDB stores (``*.duckdb``), with their ``.wal`` sidecars
    geometry  ``geometry_cache/`` (regenerable GIS subset caches)
    outputs   ``out/`` (generated maps / CSVs — regenerable from the tools)
    raw       ``data/`` and the top-level raw ``*.zip`` downloads

All of this data is gitignored (see ``.gitignore``); moving it changes nothing
that git tracks. The archive is never pruned, copies are size+SHA-256 verified
before trust, and ``--release`` deletes a local copy only after its archive copy
verifies by checksum.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from inrix_tools import sync as _sync             # noqa: E402
from inrix_tools.sync import SyncItem             # noqa: E402


_SYNC_CONFIG_FILENAME = ".inrix_sync.json"
_SYNC_ARCHIVE_ENV = "INRIX_SYNC_ARCHIVE_ROOT"
_SYNC_GROUPS = ("db", "geometry", "outputs", "raw")

# (group, kind, glob) — each match under the repo root (and/or archive root)
# becomes one SyncItem. Patterns are top-level only; the archive mirrors the
# repo root, so a unit's relative path is just its name.
_SYNC_SPEC = (
    ("db", "file", "*.duckdb"),
    ("geometry", "dir", "geometry_cache"),
    ("outputs", "dir", "out"),
    ("raw", "dir", "data"),
    ("raw", "file", "*.zip"),
)

# DuckDB keeps an optional ``<store>.wal`` write-ahead log beside the store; it
# must travel with (and be freed with) its parent.
_GROUP_COMPANIONS = {"db": (".wal",)}


def _die(msg: str) -> None:
    """Print an error to stderr and exit non-zero."""
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# Archive-root resolution (flag > env > per-host config file)
# ---------------------------------------------------------------------------

def _sync_config_path() -> Path:
    """Return the path to the per-host sync config file (may not exist)."""
    return REPO_ROOT / _SYNC_CONFIG_FILENAME


def _load_sync_config() -> dict:
    """Read ``.inrix_sync.json`` from the repo root, or ``{}`` if absent."""
    path = _sync_config_path()
    if not path.exists():
        return {}
    try:
        with path.open() as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_sync_config(cfg: dict) -> None:
    """Write ``cfg`` to ``.inrix_sync.json`` in the repo root."""
    with _sync_config_path().open("w") as fh:
        json.dump(cfg, fh, indent=4)


def _get_archive_root(args: argparse.Namespace, *, must_exist: bool) -> Path:
    """Resolve the archive-drive root (the mirror of this repo's root).

    Precedence: ``--archive-root`` flag > ``INRIX_SYNC_ARCHIVE_ROOT`` env var >
    the ``archive_root`` key in ``.inrix_sync.json``.  ``~`` and ``$VARS`` are
    expanded.  With ``--save`` the resolved path is persisted to the config file
    so each machine sets its own archive root once.

    Args:
        args:       Parsed CLI arguments (reads ``archive_root`` and ``save``).
        must_exist: Exit with an error if the resolved root does not exist.

    Returns:
        Absolute Path to the archive root.

    Raises:
        SystemExit: If no archive root is configured, or ``must_exist`` fails.
    """
    raw = getattr(args, "archive_root", None) or os.environ.get(_SYNC_ARCHIVE_ENV)
    if not raw:
        raw = _load_sync_config().get("archive_root")
    if not raw:
        _die(
            "No archive root configured.\n"
            "    Set it once with:\n"
            "      python scripts/sync_data.py status "
            "--archive-root /path/to/archive/Inrix --save\n"
            f"    or export {_SYNC_ARCHIVE_ENV}=/path/to/archive/Inrix\n"
            "    (point it at the directory that mirrors THIS repo's root on "
            "the archive drive)."
        )

    root = Path(os.path.expanduser(os.path.expandvars(str(raw)))).resolve()

    if getattr(args, "save", False):
        _save_sync_config({**_load_sync_config(), "archive_root": str(root)})
        print(f"saved archive root to {_sync_config_path().name}: {root}")

    if must_exist and not root.exists():
        _die(
            f"archive root does not exist: {root}\n"
            "    Check that the archive drive is mounted and the path is correct."
        )
    return root


# ---------------------------------------------------------------------------
# Repo -> sync-item adapter
# ---------------------------------------------------------------------------

def build_sync_items(archive_root: Path, *, repo_root: Path = REPO_ROOT) -> list:
    """Enumerate the sync units for this repo, as a list of :class:`SyncItem`.

    Each spec glob is resolved against the union of the repo root and the
    archive root, so a unit that currently lives *only* on the archive (already
    released locally) is still listed and can be pulled back.  Stale
    ``*.synctmp`` sidecars left by an interrupted copy are ignored.

    Args:
        archive_root: Directory on the archive drive that mirrors ``repo_root``.
        repo_root:    Local repo root (overridable for tests).

    Returns:
        A list of :class:`SyncItem`, one per resolved unit.
    """
    items = []
    seen = set()
    for group, kind, pattern in _SYNC_SPEC:
        names = set()
        for base in (repo_root, archive_root):
            if base.exists():
                for p in base.glob(pattern):
                    if p.name.endswith(".synctmp"):
                        continue
                    names.add(p.name)
        for name in sorted(names):
            key = (group, name)
            if key in seen:
                continue
            seen.add(key)
            items.append(SyncItem(
                name=name,
                group=group,
                local=repo_root / name,
                archive=archive_root / name,
                kind=kind,
                companions=_GROUP_COMPANIONS.get(group, ()),
            ))
    return items


def _parse_sync_components(raw, default: str):
    """Parse a ``--components`` value into a set of groups, or ``None`` = all.

    Args:
        raw:     The ``--components`` argument (or ``None`` to use ``default``).
        default: Fallback value when ``raw`` is ``None``.

    Returns:
        A set of group names, or ``None`` meaning "every group".

    Raises:
        SystemExit: If any requested group is not a valid sync group.
    """
    value = (raw if raw is not None else default).strip()
    if value.lower() == "all":
        return None
    groups = {g.strip() for g in value.split(",") if g.strip()}
    bad = groups - set(_SYNC_GROUPS)
    if bad:
        _die(
            f"unknown component(s): {', '.join(sorted(bad))}.\n"
            f"    valid groups: {', '.join(_SYNC_GROUPS)}, or 'all'."
        )
    return groups


def _select_items(archive_root: Path, raw_components, default: str) -> list:
    """Build the sync items and filter them by the requested component groups."""
    groups = _parse_sync_components(raw_components, default=default)
    items = build_sync_items(archive_root)
    if groups is not None:
        items = [it for it in items if it.group in groups]
    return items


# ---------------------------------------------------------------------------
# Release confirmation gate (the one destructive path)
# ---------------------------------------------------------------------------

def _confirm_release(items: list, assume_yes: bool) -> None:
    """Gate ``--release`` (local deletion after a verified copy) behind a prompt.

    Asked once for the whole run; refuses on a non-interactive stdin unless
    ``--yes`` is given.

    Args:
        items:      The sync units whose local copies may be freed.
        assume_yes: Skip the prompt (``--yes``).

    Raises:
        SystemExit: If the user declines, or stdin is non-interactive without
            ``--yes``.
    """
    if assume_yes:
        return

    print(
        f"\n--release DELETES the local copy of {len(items)} unit(s) after each "
        "is copied to the archive and checksum-verified:"
    )
    for it in items:
        print(f"      - {it.group:<9} {it.name}")
    print(
        "    Only units that verify are deleted locally; the archive copy is "
        "never pruned.\n"
        "    Re-fetch later with 'python scripts/sync_data.py pull'."
    )
    sys.stdout.flush()

    if not sys.stdin.isatty():
        _die(
            "refusing to release without confirmation on a non-interactive "
            "stdin. Re-run with --yes to proceed."
        )
    if input("    Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        _die("release cancelled.")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace, archive_root: Path) -> None:
    """Print, per unit, where data lives and whether the copies agree."""
    items = _select_items(archive_root, args.components, default="all")

    print(f"\narchive root: {archive_root}")
    if not items:
        print("    (no sync units found locally or in the archive)")
        return

    for it in items:
        st = _sync.item_state(it, checksum=args.checksum)
        local_str = _sync.human_bytes(st.local_bytes) if st.local_exists else "-"
        arch_str = _sync.human_bytes(st.archive_bytes) if st.archive_exists else "-"
        print(
            f"    {it.group:<9} {it.name:<28} "
            f"local {local_str:>9}   archive {arch_str:>9}   {st.state}"
        )


def cmd_transfer(args: argparse.Namespace, archive_root: Path, *, direction: str) -> None:
    """Pull (archive→local) or push (local→archive) every selected unit."""
    default_components = "db" if direction == "pull" else "all"
    items = _select_items(archive_root, args.components, default=default_components)

    release = direction == "push" and getattr(args, "release", False)
    checksum = True if release else (not args.quick)

    if not items:
        print("\n(no sync units match the requested components)")
        return

    if release and not args.dry_run:
        _confirm_release(items, assume_yes=getattr(args, "yes", False))

    verb = "pull" if direction == "pull" else ("release" if release else "push")
    print(f"\narchive root: {archive_root}")
    print(f"{verb}: {len(items)} unit(s)")

    log = print if args.verbose else (lambda _m: None)
    total_bytes = 0
    failures = 0
    for it in items:
        res = _sync.sync_item(
            it, direction=direction, release=release,
            checksum=checksum, dry_run=args.dry_run, log=log,
        )
        total_bytes += res.n_bytes
        mark = "ok " if res.ok else "FAIL"
        if not res.ok:
            failures += 1
        size = f" ({_sync.human_bytes(res.n_bytes)})" if res.n_bytes else ""
        print(f"    [{mark}] {it.group:<9} {it.name:<28} {res.action}{size} — {res.detail}")

    tag = "would transfer" if args.dry_run else "transferred"
    line = f"    — {tag} {_sync.human_bytes(total_bytes)}"
    if failures:
        line += f", {failures} failure(s)"
    print(line)
    if failures and not args.dry_run:
        _die(f"{failures} unit(s) failed to sync.")


# ---------------------------------------------------------------------------
# Parser / entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the ``sync_data`` argument parser."""
    p = argparse.ArgumentParser(
        prog="sync_data.py",
        description=(
            "Copy bulky Inrix data between this machine's local drive and an\n"
            "archive drive (external SSD), verifying every copy.\n\n"
            "  status  Show where each unit lives and whether copies agree.\n"
            "  pull    Copy archive -> local (default: db) to work on a store.\n"
            "  push    Copy local -> archive (default: all). With --release the\n"
            "          verified local copy is deleted afterward to free space.\n\n"
            "Set the archive root once per machine with --archive-root ... --save,\n"
            "or export INRIX_SYNC_ARCHIVE_ROOT. It must point at the directory\n"
            "that mirrors this repo's root on the archive drive."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "action",
        choices=["status", "pull", "push"],
        metavar="{status,pull,push}",
        help="Which sync operation to perform.",
    )
    p.add_argument(
        "--archive-root", metavar="PATH", default=None,
        help="Archive mirror of this repo's root (overrides env var and config).",
    )
    p.add_argument(
        "--save", action="store_true",
        help="Persist the resolved --archive-root to .inrix_sync.json for next time.",
    )
    p.add_argument(
        "--components", metavar="LIST", default=None,
        help=(
            "Comma-separated groups to sync, or 'all'. Groups: "
            f"{', '.join(_SYNC_GROUPS)}. "
            "Default: 'db' for pull, 'all' for push and status."
        ),
    )
    p.add_argument(
        "--release", action="store_true",
        help="push only: delete the local copy after a checksum-verified push (frees space).",
    )
    p.add_argument(
        "--quick", action="store_true",
        help="Verify by file size only, skipping SHA-256 (faster; ignored for --release).",
    )
    p.add_argument(
        "--checksum", action="store_true",
        help="status only: compare SHA-256 as well as size (slower, byte-exact).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be copied/released without changing anything.",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt for --release.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Print per-file progress and full tracebacks on error.",
    )
    return p


def main(argv=None) -> None:
    """Resolve the archive root and dispatch to the requested action."""
    args = build_parser().parse_args(argv)
    archive_root = _get_archive_root(args, must_exist=(args.action == "pull"))

    try:
        if args.action == "status":
            cmd_status(args, archive_root)
        else:
            cmd_transfer(args, archive_root, direction=args.action)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - top-level safety net
        print(f"\nunexpected error: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
