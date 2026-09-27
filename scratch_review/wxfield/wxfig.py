"""Figure helper for the local-weather measurements (SAFETY SEMANTICS 17,
2026-09-27). NOT product code.

dump(path, label, title, spec, ens_off, ens_on, field, clients, now) saves
one domain's picture data (P50 / P10 relative hours, zones, the points and
their wind at +12 h, the client-like points and their P10 change) to an
.npz in the gitignored out/wxfield/; compose(npz_paths, png) draws one panel
per domain:

  * pale background = the Voronoi weather zones, black dots = the forecast
    points (big = the fire centroid), arrows = where each point's wind
    blows at +12 h (length ~ speed), the number = m/s;
  * burned by +48 h on the ensemble MEDIAN (P50): grey = both, red = only
    with local weather, blue = only with the one-point forecast;
  * client-like points: triangles, red = the P10 arrival is earlier with
    local weather (or newly reached), blue = later (or no longer reached
    within the horizon), white = unchanged / never reached.

    py -3 scratch_review/wxfield/wxfig.py out/wxfield/fig_*.npz OUT.png
"""
import math
import sys

import numpy as np


def _rel_hours(raster, now, shape):
    a = np.array(raster.data, dtype=np.float64).reshape(shape)
    return np.where(np.isfinite(a), (a - now) / 3600.0, np.nan).astype(np.float32)


def dump(path, label, title, spec, ens_off, ens_on, field, clients, now):
    """clients: [(id, lat, lon, p10_off_h | None, p10_on_h | None)]."""
    shape = (spec.nrows, spec.ncols)
    pts = []
    for p, fc in zip(field.points, field.forecasts):
        s = fc.at(now + 12 * 3600.0)
        pts.append((p.lat, p.lon, 1.0 if p.ok else 0.0, s.wind_speed_mps,
                    s.wind_dir_deg))
    cl = np.array([(la, lo, np.nan if a is None else a, np.nan if b is None else b)
                   for _i, la, lo, a, b in clients] or np.zeros((0, 4)),
                  dtype=np.float64)
    np.savez_compressed(
        path, label=label, title=title,
        spec=np.array([spec.nrows, spec.ncols, spec.cell_m, spec.north_lat,
                       spec.west_lon], dtype=np.float64),
        p50_off=_rel_hours(ens_off.p50, now, shape),
        p50_on=_rel_hours(ens_on.p50, now, shape),
        p10_off=_rel_hours(ens_off.p10, now, shape),
        p10_on=_rel_hours(ens_on.p10, now, shape),
        zones=np.frombuffer(field.zone_of, dtype=np.uint8).reshape(shape),
        points=np.array(pts, dtype=np.float64), clients=cl,
        spacing_km=field.spacing_km)


def _panel(ax, d, lead_h=48.0):
    import matplotlib.colors as mcolors
    nrows, ncols, cell, north, west = d["spec"]
    nrows, ncols = int(nrows), int(ncols)
    km = cell / 1000.0
    ext = (0, ncols * km, 0, nrows * km)
    zones = d["zones"]
    ax.imshow(zones % 8, cmap="Pastel2", origin="upper", extent=ext,
              alpha=0.45, interpolation="nearest")
    off = np.nan_to_num(d["p50_off"], nan=1e9) <= lead_h
    on = np.nan_to_num(d["p50_on"], nan=1e9) <= lead_h
    img = np.zeros((nrows, ncols), dtype=np.int8)
    img[off & on] = 1
    img[on & ~off] = 2
    img[off & ~on] = 3
    cmap = mcolors.ListedColormap([(0, 0, 0, 0), (0.35, 0.35, 0.35, 0.85),
                                   (0.84, 0.15, 0.24, 0.9),
                                   (0.12, 0.35, 0.75, 0.9)])
    ax.imshow(img, cmap=cmap, origin="upper", extent=ext, vmin=0, vmax=3,
              interpolation="nearest")
    south = north - nrows * cell / 111_194.93
    lat_mid = (north + south) / 2.0
    kx = 111_194.93 * math.cos(math.radians(lat_mid)) / 1000.0
    ky = 111_194.93 / 1000.0
    for i, (la, lo, ok, spd, wdir) in enumerate(d["points"]):
        x, y = (lo - west) * kx, (la - south) * ky
        ax.plot([x], [y], "o", color="k" if ok else "grey",
                ms=7 if i == 0 else 4.5, zorder=5)
        b = math.radians((wdir + 180.0) % 360.0)
        L = 1.1 * spd
        ax.annotate("", xy=(x + L * math.sin(b), y + L * math.cos(b)),
                    xytext=(x, y), zorder=6,
                    arrowprops=dict(arrowstyle="->", color="k", lw=1.3))
        ax.text(x + 0.5, y + 0.5, f"{spd:.1f}", fontsize=7, zorder=6)
    for la, lo, a, b in d["clients"]:
        x, y = (lo - west) * kx, (la - south) * ky
        fa, fb = np.isfinite(a), np.isfinite(b)
        if fa and fb:
            col = ("#d7263d" if b < a - 0.5 else "#1f5bc0" if b > a + 0.5
                   else "white")
        elif fb:
            col = "#d7263d"
        elif fa:
            col = "#1f5bc0"
        else:
            col = "white"
        ax.plot([x], [y], "^", color=col, mec="k", mew=0.6, ms=5.5, zorder=7)
    ax.set_xlim(0, ncols * km)
    ax.set_ylim(0, nrows * km)
    ax.set_xlabel("km east")
    ax.set_ylabel("km north")
    ax.set_title(str(d["title"]), fontsize=9)


def compose(paths, out_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ds = [np.load(p, allow_pickle=False) for p in paths]
    fig, axes = plt.subplots(1, len(ds), figsize=(7.8 * len(ds), 7.4))
    if len(ds) == 1:
        axes = [axes]
    for ax, d in zip(axes, ds):
        _panel(ax, d)
    fig.suptitle("Local weather (SAFETY SEMANTICS 17): burned by +48 h on the "
                 "ensemble median — grey both, red only with local weather, "
                 "blue only with the one-point forecast\ntriangles = client-"
                 "like points (red: P10 earlier / newly reached, blue: later / "
                 "no longer reached); dots = forecast points, arrows = their "
                 "wind at +12 h (m/s); pale tiles = the weather zones",
                 fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_png, dpi=110)
    print("figure:", out_png)


if __name__ == "__main__":
    compose(sys.argv[1:-1], sys.argv[-1])
