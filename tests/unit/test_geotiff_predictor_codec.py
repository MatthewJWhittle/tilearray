"""Float GeoTIFF PREDICTOR=3 missing codec → ConfigurationError (imagecodecs)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.hands_on_examples import PRED3_TIF
from tilearray.decode import read_geotiff_bytes
from tilearray.errors import ConfigurationError

pytestmark = pytest.mark.unit


def test_missing_float_predictor_codec_is_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tifffile's missing-codec ValueError becomes ConfigurationError."""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError(
            "<PREDICTOR.FLOATINGPOINT: 3> requires the 'imagecodecs' package"
        )

    import tifffile

    original_init = tifffile.TiffFile.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.asarray = boom  # type: ignore[method-assign]

    monkeypatch.setattr(tifffile.TiffFile, "__init__", patched_init)

    with pytest.raises(ConfigurationError, match="imagecodecs"):
        read_geotiff_bytes(PRED3_TIF.read_bytes())


def test_predictor3_fixture_decodes_or_configuration_error() -> None:
    """Real PREDICTOR=3 body: decode when imagecodecs is present, else ConfigurationError.

    Never a bare ValueError — that is the durable #153 contract.
    """
    raw = PRED3_TIF.read_bytes()
    assert len(raw) == 314
    try:
        import imagecodecs  # noqa: F401
    except ImportError:
        with pytest.raises(ConfigurationError, match="imagecodecs"):
            read_geotiff_bytes(raw)
        return
    arr = read_geotiff_bytes(raw)
    assert arr.size > 0
