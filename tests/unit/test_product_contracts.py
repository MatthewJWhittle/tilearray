"""PR-gated product-contract tests (Modes A–D).

Named surface for user-visible journeys: URL detection taxonomy, opaque-URL
inspect, soft-fail phases, and plot-ready mosaic shape. Keep these offline
(no ``net`` / ``slow`` markers) so PR green includes product validation, not
only didactic fixtures.

Wave scaffolding — concrete contracts land in Waves 1–3.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_product_contract_module_is_collectable() -> None:
    """Scaffold: module must appear in offline PR collection."""
    assert True
