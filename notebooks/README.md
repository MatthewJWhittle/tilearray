# Development Notebooks

Prefer the Sphinx **usage guides** under [`docs/guides/`](../docs/guides/) for published examples.

This directory is for local experimentation. See [`docs/building-docs.md`](../docs/building-docs.md) if you need to rebuild the docs site or regenerate figures.

## Local notebooks

```bash
uv sync --dev
uv run jupyter lab
```

[`stage_gate_0_2.ipynb`](stage_gate_0_2.ipynb) is a hands-on scratch pad (pick a layer, set a point + box width, fetch/plot). Layers: OSM, EA Lidar, GIBS, EA VOM. It is **not** always-on CI.

Example stubs under [`examples/`](examples/) point at the Sphinx guides.
