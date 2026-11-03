"""
Count the corners  (Day 3 · Polygons)
-------------------------------------
Colours every city block by how many corners it has.

What counts as a block
  Every area enclosed by streets (drivable and pedestrian streets) is a block,
  unless it is mostly tagged as public open space in OSM (park, square,
  pedestrian area, public garden ...). Those are left out. Private gardens and
  fenced grounds stay blocks. Strips between the two carriageways of a
  boulevard are treated as street.

What shape a block gets
  - If the block's buildings fill it (their merged outline covers at least
    FILL of the block minus half the street width), the block is coloured and
    its shape is that merged outline. This keeps e.g. Barcelona's chamfered
    corners. Gaps between buildings, rear yards and notches are filled
    (closing by CLOSE metres), bumps such as bay windows are removed (opening
    by OPEN metres); courtyards never matter, only the outer edge is used.
  - Otherwise (a church, monument, university, one-sided building ...) the
    block stays empty and only its actual buildings are drawn, in grey, so
    locals can see what is there. Same for buildings in parks and squares.
  If streets are missing from the data, one street block can hold several real
  blocks. Separate, solid, block-shaped building clusters (CLUSTER_GAP, SOLID,
  CONVEX) are then treated as blocks of their own.
  Only coloured blocks count in the percentages.

Corners = points where the outline turns by more than CORNER_ANGLE, after
merging bends closer together than MIN_EDGE (a rounded corner counts once,
a 20 m chamfer twice).

Run:
    python -m pip install osmnx          # once, in your venv
    python block_corners.py barcelona    # or: nyc paris  (no name = all)
Outputs: <city>_corners.png and .svg, <city>_blocks.gpkg with two layers
("blocks" with a `corners` field, "grey_buildings"), cache_blocks/ for downloads.

Fixing things by hand
    Open <city>_blocks.gpkg in QGIS and edit it: change a block's `corners`
    value, delete a wrong block, reshape it, or copy a grey building into the
    "blocks" layer and give it a `corners` value. Then
        python block_corners.py barcelona --redraw
    redraws the map from your edited file (no recomputing), and the
    percentages in the legend follow your edits. Running without --redraw
    recomputes and OVERWRITES the GeoPackage, so keep a copy of edited files.
"""
import math, os, sys
import numpy as np
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import Polygon, MultiPolygon, Point, box
from shapely.ops import polygonize, unary_union

# ---- cities: centre of the window and its half-width in metres -------------
CITIES = {
    "barcelona": dict(title="Barcelona", centre=(41.3925, 2.1630), half=900,
                      sub="Eixample"),
    "nyc":       dict(title="New York", centre=(40.7549, -73.9840), half=900,
                      sub="Midtown Manhattan"),
    "paris":     dict(title="Paris", centre=(48.8738, 2.3300), half=900,
                      sub="Opéra – Saint-Lazare"),
}

# ---- shape settings ---------------------------------------------------------
CLOSE = 15.0         # m, fills gaps/notches between buildings up to ~2x this (within one block)
OPEN = 2.0           # m, removes bumps (bay windows, balconies) thinner than ~2x this
FILL = 0.7           # (per city: fill=...) buildings count as "filling" a block when their outline covers this share of it
INSET = 8.0          # m, street block shrunk by about half a street width (the FILL reference)
SIMPLIFY = 2.0       # m, Douglas-Peucker tolerance
MIN_EDGE = 8.0       # m, bends closer together than this are merged into one corner
CORNER_ANGLE = 30    # degrees of turn that count as a corner
MIN_AREA = 400       # m², smaller street blocks (traffic islands) are dropped
SLIVER_WIDTH = 16    # m, strips narrower than this are treated as street (medians)
EMPTY_STRIP_WIDTH = 35  # m, ... and so are building-free strips narrower than this (boulevard side lanes)
OUTLIER_GAP = 6.0    # m, buildings closer than this form one group when looking for outliers
SMALL_PART = 1200    # m², groups smaller than this ...
SMALL_SHARE = 0.25   # ... and smaller than this share of the biggest group are left out (drawn grey)
CLUSTER_GAP = 4.0    # m, buildings closer than this belong to the same cluster
BIG_CLUSTER = 1500   # m², a cluster at least this big ...
SOLID = 0.5          # ... at least this built up ...
CONVEX = 0.9         # ... and at least this close to its convex hull counts as a block of its own
OPEN_SHARE = 0.5     # blocks at least this much covered by public open space are left out

# streets that cut the city into blocks: everything a car or a pedestrian street uses,
# but not footpaths, sidewalks or service driveways (they would cut blocks into pieces)
STREET_FILTER = ('["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|'
                 'residential|living_street|pedestrian|motorway_link|trunk_link|primary_link|'
                 'secondary_link|tertiary_link)$"]')
# public open space that is "clearly not a block"
OPEN_TAGS = {"leisure": ["park", "playground", "garden", "common", "pitch", "dog_park"],
             "landuse": ["grass", "recreation_ground", "village_green"],
             "place": ["square"],
             "highway": ["pedestrian"],        # pedestrian areas (plazas), not the street lines
             "area:highway": ["pedestrian", "footway"]}

# ---- look -------------------------------------------------------------------
BG, FG, MUTED, OTHER = "#EEE8DC", "#2A2622", "#857C70", "#D8D0C1"
LOOSE_BUILDING = "#D2C8B6"   # buildings in open space or in blocks they don't fill
CLASSES = [  # (corners, label, colour)
    (3, "3 · triangle", "#C8553D"),
    (4, "4 · quadrilateral", "#E8B04B"),
    (5, "5 · pentagon", "#8AA05A"),
    (6, "6 · hexagon", "#3F8F8B"),
    (7, "7 · heptagon", "#3B6EA5"),
    (8, "8 · octagon", "#7A4E9E"),
    (9, "9 or more", "#4A3B52"),
]
FOOTER = "#30DayMapChallenge · Day 3 · Polygons · Data © OpenStreetMap contributors"


def setup_font():
    for f in font_manager.findSystemFonts() + font_manager.findSystemFonts(fontpaths=["fonts"]):
        if "lato" in os.path.basename(f).lower():
            font_manager.fontManager.addfont(f)
    if "Lato" in {f.name for f in font_manager.fontManager.ttflist}:
        plt.rcParams["font.family"] = "Lato"


# ---- data -------------------------------------------------------------------
def download(slug, city):
    """Streets, buildings and public open space around the centre, in metres. Cached;
    each part is downloaded only if its cache file is missing."""
    import osmnx as ox
    ox.settings.requests_timeout = 300
    os.makedirs("cache_blocks", exist_ok=True)
    dist = city["half"] * 1.7                         # a margin so edge blocks are complete
    sp = f"cache_blocks/{slug}_streets_v2.gpkg"       # v2: includes pedestrian streets
    bp = f"cache_blocks/{slug}_buildings.gpkg"
    op = f"cache_blocks/{slug}_openspace.gpkg"
    if os.path.exists(sp):
        streets = gpd.read_file(sp)
    else:
        G = ox.graph_from_point(city["centre"], dist=dist, custom_filter=STREET_FILTER,
                                retain_all=True, simplify=True)
        G = ox.project_graph(G)
        streets = ox.graph_to_gdfs(G, nodes=False)[["geometry"]].reset_index(drop=True)
        streets.to_file(sp)
    if os.path.exists(bp):
        b = gpd.read_file(bp).to_crs(streets.crs)
    else:
        b = ox.features_from_point(city["centre"], tags={"building": True}, dist=dist)
        b = b[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"])][["geometry"]].reset_index(drop=True)
        b = b.to_crs(streets.crs)
        b.to_file(bp)
    if os.path.exists(op):
        o = gpd.read_file(op).to_crs(streets.crs)
    else:
        o = ox.features_from_point(city["centre"], tags=OPEN_TAGS, dist=dist)
        o = o[o.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        if "access" in o.columns:                     # private gardens and grounds stay blocks
            o = o[~o["access"].isin(["private", "no"])]
        keep = [c for c in ["leisure", "landuse", "place", "highway", "area:highway", "name"] if c in o.columns]
        o = o[keep + ["geometry"]].reset_index(drop=True).to_crs(streets.crs)
        o.to_file(op)
    return streets, b, o


def street_blocks(streets):
    merged = unary_union(list(streets.geometry))       # nodes lines at every crossing
    polys = [p for p in polygonize(merged) if p.area >= MIN_AREA]
    return gpd.GeoDataFrame(geometry=polys, crs=streets.crs)


def building_outline(street_block, buildings, close=CLOSE):
    """Merged, smoothed outer edge of the buildings in one street block (or None)."""
    if len(buildings) == 0:
        return None
    u = unary_union([g.intersection(street_block) for g in buildings])
    u = drop_outliers(u)
    if u.is_empty:
        return None
    u = u.buffer(close, join_style="mitre").buffer(-close, join_style="mitre")
    u = u.buffer(-OPEN, join_style="mitre").buffer(OPEN, join_style="mitre")
    if u.is_empty:
        return None
    if isinstance(u, MultiPolygon):
        u = max(u.geoms, key=lambda g: Polygon(g.exterior).area)
    return Polygon(u.exterior)


def drop_outliers(u):
    """Small, separate building groups (a kiosk on a plaza, a pavilion across a pedestrian
    street) would otherwise get bridged to the main block by the closing step and stick
    out as spikes. Drop groups that are small both in m² and relative to the largest."""
    grouped = u.buffer(OUTLIER_GAP / 2).buffer(-OUTLIER_GAP / 2)
    parts = [p for p in getattr(grouped, "geoms", [grouped]) if not p.is_empty]
    if len(parts) < 2:
        return u
    biggest = max(Polygon(p.exterior).area for p in parts)
    keep = [p for p in parts
            if Polygon(p.exterior).area >= SMALL_PART or Polygon(p.exterior).area >= SMALL_SHARE * biggest]
    return u.intersection(unary_union(keep).buffer(0.5))


def raw_convexity(part):
    """Area of the cluster's outer edge (courtyards filled, no smoothing) / its convex hull."""
    g = max(getattr(part, "geoms", [part]), key=lambda x: x.area)
    outer = Polygon(g.exterior)
    return outer.area / outer.convex_hull.area


def solid_clusters(street_block, buildings, close=CLOSE):
    """Groups of buildings that touch or nearly touch (gaps < CLUSTER_GAP). Returns the
    smoothed outlines of the big, solid ones, ignoring any lying inside another."""
    if len(buildings) < 2:
        return []
    u = unary_union([g.intersection(street_block) for g in buildings])
    grouped = u.buffer(CLUSTER_GAP / 2).buffer(-CLUSTER_GAP / 2)
    parts = list(getattr(grouped, "geoms", [grouped]))
    out = []
    for part in parts:
        if part.is_empty:
            continue
        o = part.buffer(close, join_style="mitre").buffer(-close, join_style="mitre")
        o = o.buffer(-OPEN, join_style="mitre").buffer(OPEN, join_style="mitre")
        if o.is_empty:
            continue
        if isinstance(o, MultiPolygon):
            o = max(o.geoms, key=lambda g: g.area)
        o = Polygon(o.exterior)
        # big, densely built, and block-shaped (close to its convex hull, so not an
        # L-shaped wing or a cross-shaped church)
        if (o.area >= BIG_CLUSTER and u.intersection(o).area >= SOLID * o.area
                and raw_convexity(part) >= CONVEX):
            out.append(o)
    out = [o for o in out if not any(o is not p and p.contains(o.representative_point()) for p in out)]
    return out


def inset_block(street_block):
    p = street_block.buffer(-INSET, join_style="mitre")
    if p.is_empty:
        return None
    if isinstance(p, MultiPolygon):
        p = max(p.geoms, key=lambda g: g.area)
    return Polygon(p.exterior)


def count_corners(poly):
    pts = np.asarray(poly.exterior.coords)[:-1]
    if len(pts) < 3:
        return 0
    # vertices with their signed turn angle
    verts = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1, v2 = b - a, c - b
        turn = math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1 @ v2))
        verts.append([b.copy(), turn])
    # merge bends closer than MIN_EDGE into one (sum their turns)
    changed = True
    while changed and len(verts) > 3:
        changed = False
        for i in range(len(verts)):
            j = (i + 1) % len(verts)
            if np.hypot(*(verts[j][0] - verts[i][0])) < MIN_EDGE:
                verts[i] = [(verts[i][0] + verts[j][0]) / 2, verts[i][1] + verts[j][1]]
                del verts[j]
                changed = True
                break
    return sum(1 for _, t in verts if abs(t) > CORNER_ANGLE)


def is_sliver(poly):
    """Strips between the two carriageways of a boulevard, medians, traffic islands:
    nothing is left of them after shrinking by SLIVER_WIDTH/2."""
    return poly.buffer(-SLIVER_WIDTH / 2).is_empty


def blocks_with_corners(streets, buildings, openspace=None, close=CLOSE, fill=FILL):
    sb = street_blocks(streets)
    pts = buildings.copy()
    pts["geometry"] = buildings.geometry.representative_point()
    groups = gpd.sjoin(pts, sb, predicate="within").groupby("index_right").groups
    open_union = unary_union(list(openspace.geometry)) if openspace is not None and len(openspace) else None
    rows, loose = [], []
    for idx, block in sb.geometry.items():
        bl = list(buildings.geometry.loc[groups[idx]]) if idx in groups else []
        if is_sliver(block) or (not bl and block.buffer(-EMPTY_STRIP_WIDTH / 2).is_empty):
            continue                                    # median / strip: part of the street
        open_share = block.intersection(open_union).area / block.area if open_union is not None else 0.0
        if open_share >= OPEN_SHARE:
            loose.extend(bl)                            # e.g. a pavilion in a park
            continue
        # several separate, solid building clusters in one street block usually mean a
        # street is missing from the data: then each cluster is a block of its own
        split = solid_clusters(block, bl, close)
        if len(split) >= 2 or (len(split) == 1 and building_outline(block, bl, close) is not None
                               and building_outline(block, bl, close).area < fill * (inset_block(block) or block).area):
            for shape in split:
                shape = shape.simplify(SIMPLIFY)
                k = count_corners(shape)
                if k >= 3:
                    rows.append(dict(corners=k, open_share=open_share, split=True, geometry=shape))
            covered = unary_union(split).buffer(1)
            loose.extend(g for g in bl if not g.representative_point().within(covered))
            continue
        shape = building_outline(block, bl, close)
        ref = inset_block(block)                        # street block without the half-streets
        if shape is None or ref is None or shape.area < fill * ref.area:
            # buildings don't define the block (church, monument, one-sided building ...):
            # the block stays empty and only its actual buildings are drawn, in grey
            loose.extend(bl)
            continue
        source = "buildings"
        inside = shape.buffer(1)
        loose.extend(g for g in bl if not g.representative_point().within(inside))
        shape = shape.simplify(SIMPLIFY)
        k = count_corners(shape)
        if k < 3:
            continue                                    # degenerate leftovers (thin strips)
        rows.append(dict(corners=k, open_share=open_share, split=False, geometry=shape))
    blocks = gpd.GeoDataFrame(rows, crs=streets.crs)
    other = gpd.GeoDataFrame(geometry=loose, crs=streets.crs)
    return blocks, other


# ---- drawing ----------------------------------------------------------------
def colour_for(k):
    if k < 3:
        return OTHER
    for c, _, col in CLASSES:
        if k == c or (c == 9 and k >= 9):
            return col
    return OTHER


def icon(ax, x, y, k, r, ry, colour):
    """Regular k-gon, flat side down; ry corrects for the legend axes' aspect."""
    if k >= 9:
        k = 10
    # odd: one vertex at the top; even: a flat side at the bottom
    ang = np.linspace(0, 2 * np.pi, k, endpoint=False) + (np.pi / 2 if k % 2 else -np.pi / 2 + np.pi / k)
    ax.add_patch(MplPolygon(np.c_[x + r * np.cos(ang), y + ry * np.sin(ang)], color=colour, lw=0))


def draw(blocks, city, centre_xy, path, other=None):
    fig = plt.figure(figsize=(5.4, 6.75), dpi=200, facecolor=BG)
    w = 0.78
    ax = fig.add_axes([(1 - w) / 2, 0.205, w, w * 5.4 / 6.75], facecolor=BG)
    cx, cy = centre_xy
    h = city["half"]
    view = box(cx - h, cy - h, cx + h, cy + h)
    shown = blocks[blocks.intersects(view)]
    built = shown[shown.corners >= 0]
    if other is not None and len(other):
        o = other[other.intersects(view)]
        if len(o):
            o.plot(ax=ax, color=LOOSE_BUILDING, linewidth=0)
    built.plot(ax=ax, color=[colour_for(k) for k in built.corners], linewidth=0)
    ax.set_xlim(cx - h, cx + h); ax.set_ylim(cy - h, cy + h)
    ax.set_aspect("equal"); ax.axis("off")

    fig.text(0.06, 0.965, city["title"], color=FG, fontsize=26, fontweight="black", va="top")
    fig.text(0.06, 0.895, "How many corners does a city block have?", color=FG, fontsize=9.5, va="top")
    fig.text(0.06, 0.867, f"{city['sub']} · every block coloured by its number of corners",
             color=MUTED, fontsize=7, va="top")

    # legend with polygon icons and the share of blocks in each class
    counted = shown[shown.corners >= 3]
    lbox = [0.06, 0.088, 0.88, 0.1]
    axl = fig.add_axes(lbox); axl.axis("off")
    axl.set_xlim(0, 7); axl.set_ylim(0, 1)
    yscale = (lbox[2] * 5.4 / 7) / (lbox[3] * 6.75)   # inches per x unit / inches per y unit
    for i, (k, label, col) in enumerate(CLASSES):
        share = ((counted.corners >= 9) if k == 9 else (counted.corners == k)).mean() if len(counted) else 0
        icon(axl, i + 0.5, 0.72, k, 0.2, 0.2 * yscale, col)
        axl.text(i + 0.5, 0.28, "9+" if k == 9 else str(k), color=FG, fontsize=7,
                 fontweight="bold", ha="center", va="center")
        axl.text(i + 0.5, 0.06, f"{share:.0%}", color=MUTED, fontsize=5.8, ha="center", va="center")
    fig.text(0.06, 0.058, "Grey: buildings that don't fill their block (churches, monuments, buildings in parks). "
                          "Bumps like bay windows smoothed out; "
                          "a corner is a turn of more than 30°.", color=MUTED, fontsize=5.2)
    fig.text(0.06, 0.04, "Blocks from the OSM street network, shaped by the merged buildings that fill them. "
                         "Empty: parks, squares and open space.",
             color=MUTED, fontsize=5.2)
    fig.text(0.06, 0.02, FOOTER, color=MUTED, fontsize=5.2)
    fig.savefig(path, dpi=200, facecolor=BG)
    plt.rcParams["svg.fonttype"] = "none"            # keep text as editable text in the SVG
    fig.savefig(path.replace(".png", ".svg"), facecolor=BG)
    plt.close(fig)
    return counted


def run(slug, city, streets=None, buildings=None):
    gp = f"{slug}_blocks.gpkg"
    if REDRAW:
        # redraw from your (possibly hand-edited) GeoPackage instead of recomputing
        blocks = gpd.read_file(gp, layer="blocks")
        other = gpd.read_file(gp, layer="grey_buildings")
        crs = blocks.crs
    else:
        if streets is None:
            streets, buildings, openspace = download(slug, city)
        else:
            openspace = None
        blocks, other = blocks_with_corners(streets, buildings, openspace,
                                            city.get("close", CLOSE), city.get("fill", FILL))
        blocks.to_file(gp, layer="blocks")
        other.to_file(gp, layer="grey_buildings")
        crs = streets.crs
    c = gpd.GeoSeries([Point(city["centre"][1], city["centre"][0])], crs=4326).to_crs(crs).iloc[0]
    counted = draw(blocks, city, (c.x, c.y), f"{slug}_corners.png", other)
    dist = counted.corners.clip(upper=9).value_counts().sort_index()
    print(f"{slug:10s} blocks={len(counted)}  " + "  ".join(f"{k}:{v}" for k, v in dist.items()))


REDRAW = False

if __name__ == "__main__":
    setup_font()
    REDRAW = "--redraw" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or list(CITIES)
    for slug in wanted:
        try:
            run(slug, CITIES[slug])
        except Exception as e:
            print(slug, "failed:", e)
