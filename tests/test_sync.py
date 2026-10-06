# Tests for the project-agnostic sync engine (inrix_tools.sync).
#
# The engine moves bulky data between a local drive and an archive drive and,
# on request, deletes the local source to free space. The parts worth pinning
# are the ones that protect data: copies are verified before trust, the local
# source is never deleted behind an unverified copy, and an interrupted copy
# leaves no corrupt file at the destination.

import pytest

from inrix_tools import sync
from inrix_tools.sync import SyncItem, copy_verify_file, item_state, sync_item


def _file_item(tmp_path, name="d3_store.duckdb", group="db", companions=(".wal",)):
    return SyncItem(
        name=name, group=group,
        local=tmp_path / "local" / name,
        archive=tmp_path / "arch" / name,
        kind="file", companions=companions,
    )


def _write(path, data=b"hello-inrix"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# copy_verify_file
# ---------------------------------------------------------------------------

class TestCopyVerifyFile:

    def test_happy_path_copies_and_leaves_no_tmp(self, tmp_path):
        src = tmp_path / "a.duckdb"
        dst = tmp_path / "out" / "a.duckdb"
        _write(src, b"x" * 5000)

        ok, nb, detail = copy_verify_file(src, dst, checksum=True)

        assert ok and nb == 5000 and detail == "ok"
        assert dst.read_bytes() == src.read_bytes()
        assert not (dst.parent / "a.duckdb.synctmp").exists()

    def test_corrupt_copy_is_rejected_and_cleaned_up(self, tmp_path, monkeypatch):
        src = tmp_path / "a.duckdb"
        dst = tmp_path / "out" / "a.duckdb"
        _write(src, b"x" * 5000)

        # Simulate a bad transfer: the copy writes fewer bytes than the source.
        def _bad_copyfile(s, d, *a, **k):
            with open(d, "wb") as fh:
                fh.write(b"truncated")

        monkeypatch.setattr(sync.shutil, "copyfile", _bad_copyfile)

        ok, nb, detail = copy_verify_file(src, dst, checksum=True)

        assert not ok
        assert "size mismatch" in detail
        assert not dst.exists()                       # no corrupt file left behind
        assert not (dst.parent / "a.duckdb.synctmp").exists()

    def test_missing_source(self, tmp_path):
        ok, nb, detail = copy_verify_file(tmp_path / "nope.duckdb", tmp_path / "out.duckdb")
        assert not ok and nb == 0


# ---------------------------------------------------------------------------
# item_state
# ---------------------------------------------------------------------------

class TestItemState:

    def test_local_only(self, tmp_path):
        it = _file_item(tmp_path)
        _write(it.local, b"data")
        st = item_state(it)
        assert st.state == "local-only" and st.local_exists and not st.archive_exists

    def test_in_sync_and_differ(self, tmp_path):
        it = _file_item(tmp_path)
        _write(it.local, b"same")
        _write(it.archive, b"same")
        assert item_state(it, checksum=True).state == "in-sync"

        _write(it.archive, b"different-bytes")
        assert item_state(it).state == "differ"

    def test_dir_in_sync_symmetric(self, tmp_path):
        it = SyncItem("geometry_cache", "geometry", tmp_path / "l", tmp_path / "a", "dir")
        _write(it.local / "f1.parquet", b"one")
        _write(it.archive / "f1.parquet", b"one")
        assert item_state(it, checksum=True).state == "in-sync"

        # An extra file only on the local side must read as differ.
        _write(it.local / "f2.parquet", b"two")
        assert item_state(it).state == "differ"


# ---------------------------------------------------------------------------
# sync_item: push / pull / release
# ---------------------------------------------------------------------------

class TestSyncItem:

    def test_push_file_with_companions(self, tmp_path):
        it = _file_item(tmp_path)
        _write(it.local, b"db-bytes")
        _write(tmp_path / "local" / "d3_store.duckdb.wal", b"wal-bytes")

        res = sync_item(it, direction="push", checksum=True)

        assert res.ok and res.action == "copied"
        assert it.archive.read_bytes() == b"db-bytes"
        assert (tmp_path / "arch" / "d3_store.duckdb.wal").read_bytes() == b"wal-bytes"

    def test_pull_round_trips(self, tmp_path):
        it = _file_item(tmp_path, companions=())
        _write(it.archive, b"archived")
        res = sync_item(it, direction="pull", checksum=True)
        assert res.ok and it.local.read_bytes() == b"archived"

    def test_push_release_deletes_local_after_verify(self, tmp_path):
        it = _file_item(tmp_path)
        _write(it.local, b"heavy-db")
        _write(tmp_path / "local" / "d3_store.duckdb.wal", b"w")

        res = sync_item(it, direction="push", release=True, checksum=True)

        assert res.ok and res.action == "copied+released"
        assert not it.local.exists()                                    # freed
        assert not (tmp_path / "local" / "d3_store.duckdb.wal").exists() # companion freed too
        assert it.archive.read_bytes() == b"heavy-db"                   # safe in archive

    def test_release_without_checksum_is_refused(self, tmp_path):
        it = _file_item(tmp_path, companions=())
        _write(it.local, b"precious")
        res = sync_item(it, direction="push", release=True, checksum=False)
        assert not res.ok and res.action == "verify-failed"
        assert it.local.exists()        # never deleted behind an unverified copy

    def test_dry_run_changes_nothing(self, tmp_path):
        it = _file_item(tmp_path, companions=())
        _write(it.local, b"data")
        res = sync_item(it, direction="push", release=True, checksum=True, dry_run=True)
        assert res.ok and res.action == "dry-run"
        assert it.local.exists() and not it.archive.exists()

    def test_push_dir_is_additive_and_skips_identical(self, tmp_path):
        it = SyncItem("out", "outputs", tmp_path / "l", tmp_path / "a", "dir")
        _write(it.local / "a.html", b"aaa")
        _write(it.local / "sub" / "b.html", b"bbb")
        # Pre-existing archive file must survive (archive is never pruned).
        _write(it.archive / "keep.html", b"keep")

        res = sync_item(it, direction="push", checksum=True)

        assert res.ok
        assert (it.archive / "a.html").read_bytes() == b"aaa"
        assert (it.archive / "sub" / "b.html").read_bytes() == b"bbb"
        assert (it.archive / "keep.html").read_bytes() == b"keep"

    def test_already_in_sync_is_noop(self, tmp_path):
        it = _file_item(tmp_path, companions=())
        _write(it.local, b"identical")
        _write(it.archive, b"identical")
        res = sync_item(it, direction="push", checksum=True)
        assert res.ok and res.action == "in-sync"

    def test_bad_direction_raises(self, tmp_path):
        it = _file_item(tmp_path, companions=())
        _write(it.local, b"x")
        with pytest.raises(ValueError):
            sync_item(it, direction="sideways")


def test_human_bytes():
    assert sync.human_bytes(0) == "0 B"
    assert sync.human_bytes(1536).endswith("KB")
    assert sync.human_bytes(5 * 1024 ** 3).startswith("5.0 GB")
