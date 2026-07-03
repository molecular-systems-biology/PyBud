#!/usr/bin/env python
"""
autotrack.py — Command-line pipeline for automated cell detection and tracking.

Pipeline
--------
1. Load a multi-frame TIF via ``PyBud.load()`` (reads pixel size and time
   interval from embedded metadata automatically).
2. Detect the brightfield channel by highest modal intensity (matching BudJ).
3. Normalise the stack to uint16 so PyBud's modal background estimator is
   robust for float32 source images.
4. Detect cell centres in frame 0 using ``AutoDetect.detect_frame()`` (circular
   Hough transform with background subtraction).
5. Seed PyBud at frame 0 and call ``fit_cells()`` to propagate tracks forward.
6. Save per-cell-per-frame measurements to a CSV file.

Usage
-----
    python autotrack.py <image.tif> [options]

Examples
--------
    python autotrack.py tests/BudJstack.tif --n-cells 3
    python autotrack.py movie.tif --pixel-size 0.064 --n-cells 2

Options
-------
    --pixel-size FLOAT       µm per pixel (read from TIF metadata if omitted)
    --n-cells INT            cells to detect in frame 0 (default: 2)
    --cell-radius FLOAT      max search radius in µm (default: 4.0)
    --edge-size FLOAT        edge-detection window in µm (default: 1.0)
    --edge-rel-min INT       min relative edge contrast % (default: 30)
    --fitting-method STR     algebraic or geometric (default: geometric)
    --min-cell-radius FLOAT  min expected cell radius for Hough search (default: 1.5)
    --max-cell-radius FLOAT  max expected cell radius for Hough search (default: 4.0)
    --bf-channel INT         override brightfield channel index (auto-detect if omitted)
    --max-size-change FLOAT  max fractional size change per frame — 0 to disable (default: 0.5)
    --max-gap INT            max consecutive missed frames to bridge (default: 1)
    --output PATH            output CSV path (default: <image>_tracks.csv)
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pybud import PyBud, AutoDetect  # noqa: E402


# ---------------------------------------------------------------------------
# CLI-specific helpers
# ---------------------------------------------------------------------------

def detect_bf_channel(img4d: np.ndarray) -> int:
    """
    Return the channel index with the highest modal intensity in frame 0.

    Uses a histogram-based mode (robust for any numeric dtype and much faster
    than ``scipy.stats.mode`` for large arrays).  Matches BudJ's brightfield
    identification algorithm (Stats.dmode comparison).
    """
    n_ch = img4d.shape[1]
    if n_ch == 1:
        return 0

    modes = []
    for c in range(n_ch):
        flat = img4d[0, c].ravel().astype(np.float64)
        counts, edges = np.histogram(flat, bins=1024)
        idx = int(np.argmax(counts))
        modes.append((edges[idx] + edges[idx + 1]) / 2.0)
        print(f"    Ch{c}: modal intensity = {modes[-1]:.0f}")

    bf = int(np.argmax(modes))
    print(f"  -> Brightfield channel: {bf}")
    return bf


def normalise_to_uint16(img4d: np.ndarray) -> np.ndarray:
    """
    Global min-max scale a ``(T, C, H, W)`` array to uint16.

    Global (not per-frame) scaling preserves relative intensity across frames
    and channels, and avoids the artificial zero-spike that would mislead
    PyBud's modal background estimator.
    """
    lo = float(img4d.min())
    hi = float(img4d.max())
    if hi <= lo:
        return np.zeros_like(img4d, dtype=np.uint16)
    scaled = (img4d.astype(np.float64) - lo) / (hi - lo) * 65535.0
    return np.clip(scaled, 0, 65535).astype(np.uint16)


def cells_to_df(cells: list, pixel_size: float, time_interval_s) -> pd.DataFrame:
    """Convert a list of PyBud ``Cell`` objects to a tidy ``pandas.DataFrame``."""
    rows = []
    for cell in cells:
        if not cell.cell_found:
            continue
        row = {"track_id": cell.id, "frame": cell.frame}
        if time_interval_s is not None:
            row["time_s"] = cell.frame * time_interval_s
        row["interpolated"] = getattr(cell, 'interpolated', False)
        row.update({
            "x_px":          cell.x_centroid / pixel_size,
            "y_px":          cell.y_centroid / pixel_size,
            "x_um":          cell.x_centroid,
            "y_um":          cell.y_centroid,
            "major_um":      cell.major,
            "minor_um":      cell.minor,
            "angle_deg":     cell.angle,
            "edge_width_um": cell.edge_width,
            "volume_um3":    cell.volume,
        })
        for ch_i, fl in enumerate(cell.fluorescence, 1):
            row[f"fl{ch_i}_mean"]    = fl.mean
            row[f"fl{ch_i}_mean_bg"] = fl.mean_bg_subtracted
            row[f"fl{ch_i}_median"]  = fl.median
            row[f"fl{ch_i}_sd"]      = fl.sd
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(image_path: str,
        pixel_size_override=None,
        n_cells: int           = 2,
        cell_radius_um: float  = 4.0,
        edge_size_um:   float  = 1.0,
        edge_rel_min:   int    = 30,
        fitting_method: str    = "geometric",
        min_cell_radius_um: float = 1.5,
        max_cell_radius_um: float = 4.0,
        bf_channel_override=None,
        max_size_change: float = 0.5,
        max_gap: int           = 1,
        output_path=None):
    """
    Run the full detection → tracking → export pipeline on *image_path*.

    Parameters
    ----------
    image_path : str
        Path to a multi-frame TIF file.
    pixel_size_override : float, optional
        µm per pixel.  Overrides metadata if provided.
    n_cells : int
        Number of cell centres to detect in frame 0.
    cell_radius_um : float
        PyBud max radial search distance in µm.
    edge_size_um : float
        PyBud edge-detection sliding-window width in µm.
    edge_rel_min : int
        Minimum relative edge contrast (%).
    fitting_method : str
        ``'algebraic'`` or ``'geometric'``.
    min_cell_radius_um : float
        Minimum expected cell radius for Hough detection.
    max_cell_radius_um : float
        Maximum expected cell radius for Hough detection.
    bf_channel_override : int, optional
        Force a specific brightfield channel index.
    max_size_change : float
        Maximum fractional change in cell radius per frame (0 = disabled).
    max_gap : int
        Maximum consecutive missed frames to bridge with interpolation.
    output_path : str, optional
        Destination CSV path.  Defaults to ``<image>_tracks.csv``.

    Returns
    -------
    pandas.DataFrame
        One row per cell per frame.
    """
    # ── Load ──────────────────────────────────────────────────────────────────
    print(f"Loading {image_path} ...")
    pb = PyBud(fitting_method=fitting_method)
    pb.load(image_path)
    T, C, H, W = pb.img.shape
    print(f"  Shape : {T} frames × {C} channels × {H}×{W} px")

    if pixel_size_override is not None:
        pb.pixel_size = pixel_size_override
        source = "override"
    else:
        source = "TIF metadata" if pb.pixel_size != 0.0645 else "default"
    print(f"  Pixel size: {pb.pixel_size:.5f} µm/px  [{source}]")
    if pb.time_step != 1.0:
        print(f"  Time interval: {pb.time_step:.1f} {pb.time_unit} / frame")

    # ── Brightfield channel ───────────────────────────────────────────────────
    print("\nDetecting brightfield channel ...")
    if bf_channel_override is not None:
        pb.bf_channel = bf_channel_override
        print(f"  -> Brightfield channel: {pb.bf_channel}  [user override]")
    else:
        pb.bf_channel = detect_bf_channel(pb.img)
    pb.fl_channels = [c for c in range(C) if c != pb.bf_channel]
    print(f"  Fluorescence channels: {pb.fl_channels}")

    # ── Normalise to uint16 ───────────────────────────────────────────────────
    print("\nNormalising to uint16 ...")
    pb.img = normalise_to_uint16(pb.img)

    # ── Detect cells in frame 0 ───────────────────────────────────────────────
    min_r_px = min_cell_radius_um / pb.pixel_size
    max_r_px = max_cell_radius_um / pb.pixel_size
    print(f"\nDetecting {n_cells} cell(s) in frame 0  "
          f"(Hough circles, radius {min_r_px:.0f}–{max_r_px:.0f} px) ...")

    seeds = AutoDetect.detect_frame(
        pb.img[0, pb.bf_channel], min_r_px, max_r_px, n_max=n_cells
    )
    print(f"  -> {len(seeds)} candidate(s) detected.")

    if not seeds:
        print("\nNo cells detected. Try adjusting --min-cell-radius / --max-cell-radius.")
        return pd.DataFrame()

    print("  Positions (x, y) in pixels:")
    for i, (x, y) in enumerate(seeds, 1):
        print(f"    Cell {i:>2}: ({x:.1f}, {y:.1f})")

    for x, y in seeds:
        pb.add_selection(0, x, y)

    # ── Run PyBud ─────────────────────────────────────────────────────────────
    pb.cell_radius   = cell_radius_um
    pb.edge_size     = edge_size_um
    pb.edge_rel_min  = edge_rel_min
    pb.max_size_change = max_size_change
    pb.max_gap       = max_gap

    print(f"\nRunning PyBud ({fitting_method} fit) ...")
    print(f"  cell_radius={pb.cell_radius / pb.pixel_size:.1f} px  "
          f"edge_size={pb.edge_size / pb.pixel_size:.1f} px  "
          f"edge_rel_min={pb.edge_rel_min}%")

    pb.fit_cells()

    found = sum(1 for c in pb.cells if c.cell_found)
    print(f"\n  {found}/{len(pb.cells)} cell-frame measurements fitted successfully.")

    # ── Per-track summary ─────────────────────────────────────────────────────
    time_interval_s = pb.time_step if pb.time_step != 1.0 else None
    df = cells_to_df(pb.cells, pb.pixel_size, time_interval_s)
    if df.empty:
        print("No successful fits — check parameters and try again.")
        return df

    summary = df.groupby("track_id")["frame"].agg(["min", "max", "count"])
    summary["expected"]      = summary["max"] - summary["min"] + 1
    summary["completeness_%"] = (summary["count"] / summary["expected"] * 100).round(1)
    print("\nPer-track summary:")
    print(f"  {'ID':>4}  {'first':>5}  {'last':>5}  {'fitted':>6}  {'complete%':>9}")
    for tid, row in summary.iterrows():
        print(f"  {tid:>4}  {row['min']:>5}  {row['max']:>5}  "
              f"{row['count']:>6}  {row['completeness_%']:>9}")

    # ── Save ─────────────────────────────────────────────────────────────────
    if output_path is None:
        output_path = os.path.splitext(image_path)[0] + "_tracks.csv"

    df.to_csv(output_path, index=False)
    print(f"\nSaved {len(df)} rows → {output_path}")
    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Automated PyBud cell detection and tracking.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("image",               help="Path to multi-frame TIF")
    ap.add_argument("--pixel-size",        type=float, default=None,
                    help="µm per pixel (read from TIF metadata if omitted)")
    ap.add_argument("--n-cells",           type=int,   default=2,
                    help="Number of cells to detect in frame 0")
    ap.add_argument("--cell-radius",       type=float, default=4.0,
                    help="PyBud max search radius (µm)")
    ap.add_argument("--edge-size",         type=float, default=1.0,
                    help="PyBud edge-detection window (µm)")
    ap.add_argument("--edge-rel-min",      type=int,   default=30,
                    help="Minimum relative edge contrast (%%)")
    ap.add_argument("--fitting-method",    default="geometric",
                    choices=["algebraic", "geometric"],
                    help="Ellipse fitting method")
    ap.add_argument("--min-cell-radius",   type=float, default=1.5,
                    help="Minimum cell radius for Hough detection (µm)")
    ap.add_argument("--max-cell-radius",   type=float, default=4.0,
                    help="Maximum cell radius for Hough detection (µm)")
    ap.add_argument("--bf-channel",        type=int,   default=None,
                    help="Override brightfield channel index (0-based)")
    ap.add_argument("--max-size-change",   type=float, default=0.5,
                    help="Max fractional radius change per frame (0 = disabled)")
    ap.add_argument("--max-gap",           type=int,   default=1,
                    help="Max consecutive missed frames to bridge")
    ap.add_argument("--output",            default=None,
                    help="Output CSV path")
    args = ap.parse_args()

    run(
        image_path          = args.image,
        pixel_size_override = args.pixel_size,
        n_cells             = args.n_cells,
        cell_radius_um      = args.cell_radius,
        edge_size_um        = args.edge_size,
        edge_rel_min        = args.edge_rel_min,
        fitting_method      = args.fitting_method,
        min_cell_radius_um  = args.min_cell_radius,
        max_cell_radius_um  = args.max_cell_radius,
        bf_channel_override = args.bf_channel,
        max_size_change     = args.max_size_change,
        max_gap             = args.max_gap,
        output_path         = args.output,
    )


if __name__ == "__main__":
    main()
