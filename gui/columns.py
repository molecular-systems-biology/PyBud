"""
gui/columns.py — results-table column definitions shared by the measurement
table itself and the "Output Column Settings" dialog that toggles them.
"""
from .state import pybud

# ---------------------------------------------------------------------------
# Column definitions: (key, display_name, enabled_by_default)
# ---------------------------------------------------------------------------
COLUMN_DEFS = [
    ("run",          "Run #",                   True),   # value filled in by MeasurementTable, not get_cell_values
    ("cell",         "Cell",                    True),
    ("mother",       "Mother Cell",             False),
    ("frame",        "Frame",                   True),
    ("time",         "Time",                    False),   # header filled dynamically with unit
    ("x",            "X (µm)",                  True),
    ("y",            "Y (µm)",                  True),
    ("major",        "Major (µm)",              True),
    ("minor",        "Minor (µm)",              True),
    ("angle",        "Angle",                   True),
    ("edge_width",   "Edge Width (µm)",         False),
    ("volume",       "Volume",                  True),
    ("fl1_mean",     "FL1 Mean",                True),
    ("fl1_mean_bg",  "FL1 BG-Sub Mean",         False),
    ("fl1_bg",       "FL1 Background",          False),
    ("fl1_sd",       "FL1 SD",                  False),
    ("fl1_median",   "FL1 Median",              False),
    ("fl1_area",     "FL1 Area",                False),
    ("fl1_intden",   "FL1 Integrated Density",  False),
    ("fl1_min",      "FL1 Min",                 False),
    ("fl1_max",      "FL1 Max",                 False),
    ("fl1_b10",      "FL1 10% Brightest",       False),
    ("fl1_b25",      "FL1 25% Brightest",       False),
    ("fl1_b50",      "FL1 50% Brightest",       False),
    ("fl2_mean",     "FL2 Mean",                True),
    ("fl2_mean_bg",  "FL2 BG-Sub Mean",         False),
    ("fl2_bg",       "FL2 Background",          False),
    ("fl2_sd",       "FL2 SD",                  False),
    ("fl2_median",   "FL2 Median",              False),
    ("fl2_area",     "FL2 Area",                False),
    ("fl2_intden",   "FL2 Integrated Density",  False),
    ("fl2_min",      "FL2 Min",                 False),
    ("fl2_max",      "FL2 Max",                 False),
    ("fl2_b10",      "FL2 10% Brightest",       False),
    ("fl2_b25",      "FL2 25% Brightest",       False),
    ("fl2_b50",      "FL2 50% Brightest",       False),
    ("interpolated", "Interpolated",           False),
]

# Module-level dict tracking which columns are currently enabled
column_enabled = {key: default for key, _, default in COLUMN_DEFS}


# ---------------------------------------------------------------------------
# Helper: extract all possible column values from a single cell
# ---------------------------------------------------------------------------
def get_cell_values(cell):
    def _fl(fl, attr, fmt="{:.2f}"):
        if fl is None:
            return "0.00"
        return fmt.format(getattr(fl, attr, 0.0))

    fl1 = cell.fluorescence[0] if len(cell.fluorescence) > 0 else None
    fl2 = cell.fluorescence[1] if len(cell.fluorescence) > 1 else None

    mid = getattr(cell, 'mother_id', -1)
    return {
        "cell":         str(cell.id),
        "mother":       str(mid) if mid >= 0 else "-",
        "frame":        str(cell.frame),
        "time":         f"{cell.frame * pybud.time_step:.2f}",
        "x":            f"{cell.x_centroid:.2f}",
        "y":            f"{cell.y_centroid:.2f}",
        "major":        f"{cell.major:.2f}",
        "minor":        f"{cell.minor:.2f}",
        "angle":        f"{cell.angle:.2f}",
        "edge_width":   f"{cell.edge_width:.2f}",
        "volume":       f"{cell.volume:.2f}",
        "fl1_mean":     _fl(fl1, "mean"),
        "fl1_mean_bg":  _fl(fl1, "mean_bg_subtracted"),
        "fl1_bg":       _fl(fl1, "background"),
        "fl1_sd":       _fl(fl1, "sd"),
        "fl1_median":   _fl(fl1, "median"),
        "fl1_area":     _fl(fl1, "area", "{:.0f}"),
        "fl1_intden":   _fl(fl1, "integrated_density"),
        "fl1_min":      _fl(fl1, "min"),
        "fl1_max":      _fl(fl1, "max"),
        "fl1_b10":      _fl(fl1, "brightest_10"),
        "fl1_b25":      _fl(fl1, "brightest_25"),
        "fl1_b50":      _fl(fl1, "brightest_50"),
        "fl2_mean":     _fl(fl2, "mean"),
        "fl2_mean_bg":  _fl(fl2, "mean_bg_subtracted"),
        "fl2_bg":       _fl(fl2, "background"),
        "fl2_sd":       _fl(fl2, "sd"),
        "fl2_median":   _fl(fl2, "median"),
        "fl2_area":     _fl(fl2, "area", "{:.0f}"),
        "fl2_intden":   _fl(fl2, "integrated_density"),
        "fl2_min":      _fl(fl2, "min"),
        "fl2_max":      _fl(fl2, "max"),
        "fl2_b10":      _fl(fl2, "brightest_10"),
        "fl2_b25":      _fl(fl2, "brightest_25"),
        "fl2_b50":      _fl(fl2, "brightest_50"),
        "interpolated": str(getattr(cell, 'interpolated', False)),
    }
