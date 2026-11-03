# Development Notebooks

Prefer the Sphinx **usage guides** under [`docs/guides/`](../docs/guides/) for published examples.

This directory is for local experimentation and maintainer stage-gates. See [`docs/building-docs.md`](../docs/building-docs.md) if you need to rebuild the docs site or regenerate figures.

## Maintainer stage-gate (0.2)

Before cutting **0.2.0**, check out the stage-gate PR branch and drive the library yourself:

[`stage_gate_0_2.ipynb`](stage_gate_0_2.ipynb) — pick a layer, set a point + box width, fetch/plot, nudge the AOI. Layers: OSM, EA Lidar, GIBS, EA VOM.

```bash
# from repo root, on the PR branch
git fetch origin && git checkout <pr-branch>

uv sync --dev
# or: python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

uv run jupyter lab
```

Open the notebook → edit `LAYER` / centre / `WIDTH` → Run. Scratch cells are the same pattern without the layer switch.

**What to look for:** API feel moving AOIs/layers, timing/attrs, politeness, soft-fail peek, anything blocking a 0.2.0 cut (checklist at the bottom).

matplotlib and jupyter are already in the `[dev]` extra. This notebook is **not** always-on CI.

## Everyday local notebooks

```bash
uv sync --dev
uv run jupyter lab
```

Example stubs under [`examples/`](examples/) point at the Sphinx guides.
