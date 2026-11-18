"""staging() publishes a whole store only on clean exit."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import tilearray.persist as persist_module
from tilearray import staging
from tilearray.errors import ValidationError
from tilearray.persist import _partial_path

pytestmark = pytest.mark.unit

zarr = pytest.importorskip("zarr")


def test_staging_success_publishes_final_path(tmp_path: Path) -> None:
    final = tmp_path / "out.zarr"
    with staging(final) as target:
        assert target == _partial_path(final)
        assert not final.exists()
        target.mkdir()
        (target / "marker").write_text("ok", encoding="utf-8")

    assert final.is_dir()
    assert (final / "marker").read_text(encoding="utf-8") == "ok"
    assert not _partial_path(final).exists()


def test_staging_exception_leaves_final_absent(tmp_path: Path) -> None:
    final = tmp_path / "out.zarr"
    with pytest.raises(RuntimeError, match="simulated abort"):
        with staging(final) as target:
            target.mkdir()
            (target / "marker").write_text("partial", encoding="utf-8")
            raise RuntimeError("simulated abort")

    assert not final.exists()
    assert not _partial_path(final).exists()


def test_staging_to_zarr_publishes_readable_store(tmp_path: Path) -> None:
    final = tmp_path / "elevation.zarr"
    da = xr.DataArray(
        np.arange(16, dtype=np.float32).reshape(4, 4),
        dims=("y", "x"),
        name="elevation",
    )

    with staging(final) as path:
        assert not final.exists()
        da.to_dataset(name="elevation").to_zarr(path, mode="w")

    assert final.exists()
    assert not _partial_path(final).exists()
    opened = xr.open_zarr(final)
    try:
        np.testing.assert_array_equal(opened["elevation"].values, da.values)
    finally:
        opened.close()


def test_staging_to_zarr_abort_leaves_final_absent(tmp_path: Path) -> None:
    final = tmp_path / "elevation.zarr"
    da = xr.DataArray(
        np.ones((2, 2), dtype=np.float32),
        dims=("y", "x"),
        name="elevation",
    )

    with pytest.raises(RuntimeError, match="abort after write"):
        with staging(final) as path:
            da.to_dataset(name="elevation").to_zarr(path, mode="w")
            raise RuntimeError("abort after write")

    assert not final.exists()
    # Temp may be discarded; must not look like a complete final store.
    assert not final.exists()


def test_staging_replaces_existing_final_store(tmp_path: Path) -> None:
    final = tmp_path / "out.zarr"
    final.mkdir()
    (final / "old").write_text("stale", encoding="utf-8")

    with staging(final) as target:
        target.mkdir()
        (target / "new").write_text("fresh", encoding="utf-8")

    assert final.is_dir()
    assert (final / "new").read_text(encoding="utf-8") == "fresh"
    assert not (final / "old").exists()
    assert not _partial_path(final).exists()
    assert not final.with_name(final.name + ".staging-trash").exists()


def test_staging_rejects_unsupported_kind(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="kind='raster'"):
        with staging(tmp_path / "out.tif", kind="raster"):
            pass

    with pytest.raises(ValidationError, match="Unsupported staging kind"):
        with staging(tmp_path / "out.zarr", kind="netcdf"):  # type: ignore[arg-type]
            pass


def test_staging_rejects_empty_publish(tmp_path: Path) -> None:
    final = tmp_path / "out.zarr"
    with pytest.raises(ValidationError, match="does not exist at publish"):
        with staging(final) as _target:
            pass
    assert not final.exists()


def test_staging_clears_leftover_partial_file(tmp_path: Path) -> None:
    final = tmp_path / "out.zarr"
    leftover = _partial_path(final)
    leftover.write_text("stale partial file", encoding="utf-8")

    with staging(final) as target:
        assert target == leftover
        assert not leftover.exists() or leftover.is_dir() is False
        target.mkdir()
        (target / "ok").write_text("1", encoding="utf-8")

    assert final.is_dir()
    assert not leftover.exists()


def test_staging_restores_final_when_publish_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = tmp_path / "out.zarr"
    final.mkdir()
    (final / "keep").write_text("previous", encoding="utf-8")

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls["n"] += 1
        # First call moves final → trash; second (temp → final) fails.
        if calls["n"] == 2:
            raise OSError("simulated replace failure")
        real_replace(src, dst)

    monkeypatch.setattr(persist_module.os, "replace", flaky_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        with staging(final) as target:
            target.mkdir()
            (target / "new").write_text("fresh", encoding="utf-8")

    assert final.is_dir()
    assert (final / "keep").read_text(encoding="utf-8") == "previous"
    assert not (final / "new").exists()


def test_staging_exception_keeps_previous_final_store(tmp_path: Path) -> None:
    """A failed rewrite leaves the previous published store in place (#198)."""

    final = tmp_path / "out.zarr"
    final.mkdir()
    (final / "marker").write_text("previous")
    with pytest.raises(RuntimeError, match="rewrite failed"):
        with staging(final) as temp:
            temp.mkdir()
            (temp / "marker").write_text("new")
            raise RuntimeError("rewrite failed")
    assert (final / "marker").read_text() == "previous"
    assert not (tmp_path / "out.zarr.partial").exists()
