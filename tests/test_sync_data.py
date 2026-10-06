# Tests for the `scripts/sync_data.py` adapter (local <-> archive data movement).
#
# Covered: argument parsing, archive-root resolution precedence (flag > env >
# config file) and its --save side effect, component-group validation, the
# repo -> sync-item adapter's classification/globbing, and the --release
# confirmation gate (the destructive part). The copy/verify/release behaviour
# itself lives in the engine and is pinned by test_sync.py.

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sync_data as sd          # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repo root with the sync-config path redirected into it."""
    monkeypatch.setattr(sd, "REPO_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TestParser:

    def _parse(self, argv):
        return sd.build_parser().parse_args(argv)

    def test_status(self):
        args = self._parse(["status"])
        assert args.action == "status" and args.checksum is False

    def test_pull_defaults(self):
        args = self._parse(["pull"])
        assert args.action == "pull"
        assert args.release is False and args.quick is False and args.dry_run is False

    def test_push_release_flags(self):
        args = self._parse(["push", "--release", "--yes"])
        assert args.release is True and args.yes is True

    def test_archive_root_and_save(self):
        args = self._parse(["status", "--archive-root", "/mnt/x", "--save"])
        assert args.archive_root == "/mnt/x" and args.save is True

    def test_invalid_action_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["frobnicate"])


# ---------------------------------------------------------------------------
# Archive-root resolution
# ---------------------------------------------------------------------------

class TestArchiveRoot:

    def test_flag_takes_precedence(self, repo, monkeypatch):
        monkeypatch.setenv(sd._SYNC_ARCHIVE_ENV, str(repo / "from_env"))
        args = argparse.Namespace(archive_root=str(repo / "from_flag"), save=False)
        root = sd._get_archive_root(args, must_exist=False)
        assert root == (repo / "from_flag").resolve()

    def test_env_used_when_no_flag(self, repo, monkeypatch):
        monkeypatch.setenv(sd._SYNC_ARCHIVE_ENV, str(repo / "from_env"))
        args = argparse.Namespace(archive_root=None, save=False)
        root = sd._get_archive_root(args, must_exist=False)
        assert root == (repo / "from_env").resolve()

    def test_config_file_used_when_no_flag_or_env(self, repo, monkeypatch):
        monkeypatch.delenv(sd._SYNC_ARCHIVE_ENV, raising=False)
        (repo / sd._SYNC_CONFIG_FILENAME).write_text(
            json.dumps({"archive_root": str(repo / "from_cfg")})
        )
        args = argparse.Namespace(archive_root=None, save=False)
        root = sd._get_archive_root(args, must_exist=False)
        assert root == (repo / "from_cfg").resolve()

    def test_unset_dies(self, repo, monkeypatch):
        monkeypatch.delenv(sd._SYNC_ARCHIVE_ENV, raising=False)
        args = argparse.Namespace(archive_root=None, save=False)
        with pytest.raises(SystemExit):
            sd._get_archive_root(args, must_exist=False)

    def test_save_persists_to_config(self, repo, monkeypatch):
        monkeypatch.delenv(sd._SYNC_ARCHIVE_ENV, raising=False)
        target = repo / "ssd" / "Inrix"
        args = argparse.Namespace(archive_root=str(target), save=True)
        sd._get_archive_root(args, must_exist=False)

        saved = json.loads((repo / sd._SYNC_CONFIG_FILENAME).read_text())
        assert saved["archive_root"] == str(target.resolve())

    def test_must_exist_dies_when_absent(self, repo, monkeypatch):
        monkeypatch.delenv(sd._SYNC_ARCHIVE_ENV, raising=False)
        args = argparse.Namespace(archive_root=str(repo / "nope"), save=False)
        with pytest.raises(SystemExit):
            sd._get_archive_root(args, must_exist=True)


# ---------------------------------------------------------------------------
# Component parsing
# ---------------------------------------------------------------------------

class TestParseComponents:

    def test_all_means_none(self):
        assert sd._parse_sync_components("all", default="db") is None

    def test_default_used_when_none(self):
        assert sd._parse_sync_components(None, default="db") == {"db"}

    def test_explicit_subset(self):
        assert sd._parse_sync_components("geometry,outputs", default="all") == {"geometry", "outputs"}

    def test_unknown_group_dies(self):
        with pytest.raises(SystemExit):
            sd._parse_sync_components("db,bogus", default="all")


# ---------------------------------------------------------------------------
# Repo -> sync-item adapter
# ---------------------------------------------------------------------------

class TestBuildSyncItems:

    def test_classifies_and_globs_every_group(self, tmp_path):
        repo = tmp_path / "repo"
        arch = tmp_path / "arch"
        (repo).mkdir()
        (repo / "d3_store.duckdb").write_bytes(b"db")
        (repo / "d3_store.duckdb.wal").write_bytes(b"wal")   # sidecar, not its own item
        (repo / "inrix_store.duckdb").write_bytes(b"db2")
        (repo / "stale.duckdb.synctmp").write_bytes(b"tmp")  # interrupted copy, ignored
        (repo / "geometry_cache").mkdir()
        (repo / "out").mkdir()
        (repo / "data").mkdir()
        (repo / "Cumulative_AADT.zip").write_bytes(b"zip")

        items = {(it.group, it.name): it
                 for it in sd.build_sync_items(arch, repo_root=repo)}

        assert items[("db", "d3_store.duckdb")].companions == (".wal",)
        assert items[("db", "d3_store.duckdb")].kind == "file"
        assert ("db", "inrix_store.duckdb") in items
        assert ("db", "d3_store.duckdb.wal") not in items        # carried as companion
        assert not any(n.endswith(".synctmp") for _g, n in items)  # tmp ignored
        assert items[("geometry", "geometry_cache")].kind == "dir"
        assert items[("outputs", "out")].kind == "dir"
        assert items[("raw", "data")].kind == "dir"
        assert items[("raw", "Cumulative_AADT.zip")].kind == "file"
        # Archive paths mirror the repo root.
        assert items[("db", "d3_store.duckdb")].archive == arch / "d3_store.duckdb"

    def test_unions_local_and_archive_entries(self, tmp_path):
        repo = tmp_path / "repo"
        arch = tmp_path / "arch"
        repo.mkdir()
        (repo / "d1_store.duckdb").write_bytes(b"db")
        arch.mkdir()
        (arch / "d2_store.duckdb").write_bytes(b"db")   # released locally, archive-only

        names = {it.name for it in sd.build_sync_items(arch, repo_root=repo)}
        assert {"d1_store.duckdb", "d2_store.duckdb"} <= names


# ---------------------------------------------------------------------------
# Release confirmation gate
# ---------------------------------------------------------------------------

def _dummy_items(n=1):
    return [sd.SyncItem(f"d{i}_store.duckdb", "db", Path("l"), Path("a"), "file")
            for i in range(n)]


class TestConfirmRelease:

    def test_assume_yes_skips_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input",
                            lambda _: pytest.fail("must not prompt with --yes"))
        sd._confirm_release(_dummy_items(), assume_yes=True)
        assert capsys.readouterr().out == ""

    def test_decline_cancels(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO())
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda _: "n")
        with pytest.raises(SystemExit):
            sd._confirm_release(_dummy_items(), assume_yes=False)

    def test_non_interactive_refuses_without_yes(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO())
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        with pytest.raises(SystemExit):
            sd._confirm_release(_dummy_items(), assume_yes=False)
        assert "--yes" in capsys.readouterr().err
