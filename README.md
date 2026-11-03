# How many corners does a city block have?

Every city block coloured by its number of corners — a small experiment in urban morphology, made for the [#30DayMapChallenge](https://30daymapchallenge.com) 2026 (Day 3 · Polygons).

| Barcelona | New York | Paris |
|---|---|---|
| ![Barcelona](images/barcelona_corners.png) | ![New York](images/nyc_corners.png) | ![Paris](images/paris_corners.png) |

Barcelona's Eixample is a field of octagons (Cerdà's chamfered corners), Midtown Manhattan is mostly plain rectangles, and Haussmann's boulevards cut Paris into wedges and triangles.

> The images in this repo are early previews from the script. The posted versions were cleaned up by hand afterwards (see [Limitations](#limitations)).

## How it works

1. **Blocks from streets.** The OpenStreetMap street network (drivable and pedestrian streets) cuts the area into street blocks.
2. **Shape from buildings.** All buildings in a block are merged into one outline. That keeps details street centre lines don't have, like Barcelona's chamfers. Gaps between buildings and rear yards are filled, small bumps such as bay windows are smoothed out, and courtyards are ignored (only the outer edge counts).
3. **Counting corners.** A corner is any point where the outline turns by more than 30°. Bends closer together than 8 m are merged, so a rounded corner counts once and a 20 m chamfer counts twice.
4. **What isn't a block.**
   - Areas mostly tagged as public open space (parks, squares, pedestrian plazas) stay empty.
   - Blocks whose buildings don't fill them (churches, monuments, one-sided buildings) stay empty too; their actual buildings are drawn in grey so you can still see what's there.
   - Narrow strips between boulevard carriageways count as street.
5. **Missing streets.** If a street is missing from the data, separate, solid, block-shaped building clusters are still recognised as blocks of their own. Small detached buildings (kiosks on a plaza) are left out of the block shape so they don't create spikes.

## Usage

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python block_corners.py barcelona nyc paris     # no city name = all cities
```

Outputs per city:

- `<city>_corners.png` — 1080 × 1350 image (Instagram 4:5)
- `<city>_corners.svg` — same, with editable text
- `<city>_blocks.gpkg` — two layers, `blocks` (with a `corners` field) and `grey_buildings`, for inspecting in QGIS

Downloads are cached in `cache_blocks/`, so re-runs are fast. Delete a city's files there to download again.

### Your own city

Add an entry to `CITIES` at the top of `block_corners.py`:

```python
"vienna": dict(title="Vienna", centre=(48.2085, 16.3731), half=900, sub="Innere Stadt"),
```

`centre` is (lat, lon), `half` is half the width of the square in metres. Larger areas mean much bigger downloads (double the width ≈ four times the buildings).

### Fixing things by hand

OSM data is never perfect, so some blocks will come out wrong. Two ways to fix them:

- **SVG:** open `<city>_corners.svg` in Inkscape or Illustrator and recolour individual blocks. The legend percentages won't update.
- **QGIS:** edit `<city>_blocks.gpkg` (change a `corners` value, delete or reshape a block, copy a grey building into `blocks`), then redraw with correct percentages:

  ```bash
  python block_corners.py barcelona --redraw
  ```

  ⚠️ Running *without* `--redraw` recalculates and overwrites the GeoPackage, so keep a copy of edited files.

### Settings

All thresholds are at the top of `block_corners.py`, for example `CORNER_ANGLE` (what counts as a corner), `OPEN` (size of bumps that get smoothed out), `FILL` (how much of a block buildings must cover) and `CONVEX` (how block-shaped a building cluster must be to count as a block). Values can be overridden per city with `close=` and `fill=` in `CITIES`.

## Font

The maps use [Lato](https://fonts.google.com/specimen/Lato) (SIL Open Font License). Install it, or put the `.ttf` files into a `fonts/` folder next to the script. Without it, matplotlib's default font is used.

## Limitations

- Results depend on how well buildings and streets are mapped in OpenStreetMap. Missing pedestrian streets, partly mapped buildings and unusual tagging lead to merged or missing blocks.
- "Corner" is a modelling choice: the 30° threshold and the smoothing settings change the counts.
- The rules are heuristics. Expect roughly 5–10% of blocks to need a manual check.

## Credits

- Data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL)
- Built with [OSMnx](https://github.com/gboeing/osmnx), GeoPandas, Shapely and Matplotlib
- Code written with help from Claude (Anthropic)

Made by Martin Bangratz · [martincreates.com](https://martincreates.com)

## License

Code: MIT (see [LICENSE](LICENSE)). Map data: ODbL, © OpenStreetMap contributors.
