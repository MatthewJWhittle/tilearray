# Usage guides

Visual walkthroughs: load tiles from public hosts, plot the array, and run a little analysis. For the mental model behind them, see {doc}`../how-it-works`.

Figures are produced by `make docs-artifacts` (How it works diagrams offline, then live mosaics via polite presets), then **committed** under `docs/_static/guides/`. Sphinx only embeds those assets — `make docs` does not contact tile hosts.

```{toctree}
:maxdepth: 1

osm-london
gibs-bluemarble
ea-lidar
```

| Guide | Host / area | What you see |
|-------|-------------|--------------|
| {doc}`osm-london` | OpenStreetMap XYZ · central London | Street mosaic (palette PNG → single-band today) |
| {doc}`gibs-bluemarble` | NASA GIBS XYZ · mid-Atlantic | RGB Blue Marble glance |
| {doc}`ea-lidar` | EA Lidar WCS · OSGB window | Elevation map + histogram |

Regenerate all committed docs assets:

```bash
make docs-artifacts   # network for live mosaics; diagrams are offline
```
