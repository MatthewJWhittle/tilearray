# Install

Python **3.9+**. Prefer [uv](https://github.com/astral-sh/uv):

```bash
uv add tilearray
```

or:

```bash
pip install tilearray
```

Documented against **tilearray 0.3.1** (`compute_mosaic`, `preview_array`, `bbox_crs=`, `attrs["mosaic_crs"]`).

Plotting with xarray (`arr.plot.imshow` and similar) needs **matplotlib**. It is not a core tilearray dependency — install it separately before the quickstart plot:

```bash
pip install matplotlib
```

or `conda install matplotlib` / `uv add matplotlib`.

```{note}
The five-minute first run and host guides fetch live public tiles. You need network access; if a host returns 403 or times out, see {doc}`troubleshooting` or {doc}`example-sources`.
```

Hosted docs: https://matthewjwhittle.github.io/tilearray/

From a source checkout:

```bash
git clone https://github.com/MatthewJWhittle/tilearray.git
cd tilearray
uv sync --dev
```

(`uv sync --dev` already pulls matplotlib via the `dev` extra.)

Next: {doc}`quickstart` (five-minute OSM first run with plot + CRS).
