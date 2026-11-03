# Development Notebooks

Prefer the Sphinx **usage guides** under [`docs/guides/`](../docs/guides/) for published examples.

This directory is for local experimentation and maintainer stage-gates. See [`docs/building-docs.md`](../docs/building-docs.md) if you need to rebuild the docs site or regenerate figures.

## Maintainer stage-gate (0.2)

Before cutting **0.2.0**, check out the stage-gate PR branch and drive the library yourself:

[`stage_gate_0_2.ipynb`](stage_gate_0_2.ipynb) — live OSM XYZ + EA DSP WCS, politeness knobs, soft-fail attrs, timing / attrs checklist.

```bash
# from repo root, on the PR branch
git fetch origin && git checkout <pr-branch>

uv sync --dev
# or: python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

uv run jupyter lab
# or: uv run jupyter notebook
# or: jupyter lab  (with the venv activated)
```

Open `notebooks/stage_gate_0_2.ipynb`, edit the **config** cells near the top, then run.

**What to look for while running**

- API clarity: does `preset → create_array → compute_with_policy` feel like one habit?
- Progress / timing: wall clock, shape/dtype/CRS attrs, `tilearray_fetch_policy`
- Politeness: preset ceilings vs lower `max_concurrent_requests` (call-local)
- Soft-fail: `tilearray_tile_errors` after `compute_with_policy` (`armed`, `soft_failed`, …)
- Small bugs / awkwardness — fill the checklist at the bottom of the notebook

matplotlib and jupyter are already in the `[dev]` extra. This notebook is **not** always-on CI.

## Everyday local notebooks

```bash
uv sync --dev
uv run jupyter lab
```

Example stubs under [`examples/`](examples/) point at the Sphinx guides.
