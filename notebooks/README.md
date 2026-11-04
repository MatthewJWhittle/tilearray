# Development Notebooks

Prefer the Sphinx **usage guides** under [`docs/guides/`](../docs/guides/) for published examples.

This directory is for local experimentation, maintainer stage-gates, and **driveable problem-shaped examples**. See [`docs/building-docs.md`](../docs/building-docs.md) if you need to rebuild the docs site or regenerate figures.

## Community problem-shaped examples

[`community_worked_examples.ipynb`](community_worked_examples.ipynb) — four problem classes (tile-grid AOI georef, WCS size habits, config/CRS mistakes, polite multi-tile fetch). Live public hosts where polite; offline only for CRS typo checks. Companion guide: [`docs/guides/problem-shaped-examples.md`](../docs/guides/problem-shaped-examples.md).

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

[`stage_gate_0_2.ipynb`](stage_gate_0_2.ipynb) is a hands-on scratch pad (pick a layer, set a point + box width, fetch/plot). Layers: OSM, EA Lidar, GIBS, EA VOM. It is **not** always-on CI.

Example stubs under [`examples/`](examples/) point at the Sphinx guides.
