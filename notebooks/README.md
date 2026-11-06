# Development Notebooks

Prefer the Sphinx **usage guides** under [`docs/guides/`](../docs/guides/) for published examples.

This directory is for local experimentation, maintainer checkout notebooks, and interactive worked examples. See [`docs/building-docs.md`](../docs/building-docs.md) if you need to rebuild the docs site or regenerate figures.

## Worked examples

[`community_worked_examples.ipynb`](community_worked_examples.ipynb) — four jobs (tile-grid AOI georef, WCS size habits, config/CRS mistakes, polite multi-tile fetch). Live public hosts where polite; offline only for CRS typo checks. Companion guide: [`docs/guides/worked-examples.md`](../docs/guides/worked-examples.md).

```bash
uv sync --dev
uv run jupyter lab   # open community_worked_examples.ipynb
```

Not always-on CI. Keep bboxes small; respect OSMF / GIBS / EA terms.

## Local notebooks

```bash
uv sync --dev
uv run jupyter lab
```

[`stage_gate_0_3.ipynb`](stage_gate_0_3.ipynb) is the checkout pad: OSM five-minute path plus simplified Kartverket WMTS (`inspect_endpoint` → `target_resolution`, no URL templates), editable knobs, `plan_array` / `fetch_summary`, and georef checks. Plotting needs a separate `matplotlib` install. [`stage_gate_0_2.ipynb`](stage_gate_0_2.ipynb) is the broader multi-layer pad (OSM, EA Lidar, GIBS, EA VOM). Neither is always-on CI.

Example stubs under [`examples/`](examples/) point at the Sphinx guides.
