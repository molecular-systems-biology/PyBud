"""
gui/help_dialog.py — in-app reference card covering mouse/keyboard controls,
what each button does, and what every settings parameter means.

Section order here deliberately follows the settings panel's own top-to-
bottom order (Image/Time -> Cell Fitting -> Advanced Settings' own groups in
their displayed order) so a reader can cross-reference the dialog against
the live panel without the two disagreeing about what comes first.
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QVBoxLayout, QLabel, QWidget, QScrollArea, QFormLayout, QDialog, QDialogButtonBox


class HelpDialog(QDialog):
    """Reference card covering mouse/keyboard controls, what each button does,
    and what every settings parameter means."""

    _SECTIONS = [
        ("Mouse & Keyboard", [
            ("Left-click the image (unfocused)", "Focus the image panel for keyboard shortcuts "
                                        "(blue border appears), without placing or removing a seed"),
            ("Left-click a cell (focused)", "Place or remove a seed (green cross) on the current frame"),
            ("Space + arrow keys (focused)", "Hold Space (cursor becomes an open hand) and press an "
                                        "arrow key to pan the view in that direction, instead of "
                                        "stepping frame/channel — handy when zoomed in. (Same as "
                                        "Ctrl + arrow keys below, just under Space instead.)"),
            ("Left / Right arrow",     "Previous / next frame"),
            ("Up / Down arrow",        "Previous / next channel"),
            ("Alt + Up / Down arrow",  "Previous / next channel (matches Alt + scroll wheel)"),
            ("Ctrl + arrow keys",      "Scroll the image in that direction (handy when zoomed in)"),
            ("Alt + scroll wheel",     "Previous / next channel"),
            ("Ctrl + scroll wheel",    "Zoom in/out, centred on the cursor"),
            ("+ / − keys",             "Zoom in / out"),
            ("Click a table row",      "Jump the image to that row's frame, pan to its cell, and "
                                        "highlight it (arrow keys in the table do the same)"),
        ]),
        ("Image Panel Buttons", [
            ("Auto-Detect This Frame", "Run circle detection on just the CURRENT frame and place a "
                                        "seed cross for anything found. Fast, and doesn't track or "
                                        "measure anything — review/adjust the crosses, then click "
                                        "Measure."),
            ("Auto-Detect + Measure (Whole Movie)", "Scan every frame of the movie for new cells "
                                        "(including ones that only appear partway through), then "
                                        "track and fit everything found in one go. Can take a while "
                                        "on long movies."),
            ("Measure", "Track every current seed forward through the movie and fit an ellipse to "
                        "each frame."),
            ("Stop", "Interrupt a running detection or measurement; results computed so far are kept."),
            ("Tracking progress bar (status bar)", "Shows how many of the total frames across all "
                                        "active seeds have been attempted while Measure/Auto-Detect/"
                                        "Re-fit is running — most useful when tracking many seed "
                                        "points at once, where the image itself can't show progress "
                                        "for every seed simultaneously."),
            ("Re-fit Frame Range — From / To / cell(s)", "Re-run tracking for just the given frame "
                                        "range, using the current settings. Leave 'cell(s)' blank to "
                                        "re-fit every cell active in that range, or list specific cell "
                                        "IDs (e.g. 1,3) to re-fit only those — every other cell's "
                                        "existing results stay untouched. Useful for fixing one "
                                        "problem stretch of a movie without redoing everything."),
            ("Zoom − / + / Reset Zoom", "Zoom out, zoom in, or reset to 100%. Current zoom level is "
                                        "shown as a percentage between the buttons."),
            ("Adjust Contrast", "Click to reveal the histogram, Min/Max fields, and Auto button for "
                                        "the current channel's display contrast (display only — never "
                                        "affects measurements); click again to hide them. The histogram "
                                        "shows the current frame's pixel distribution — drag its green "
                                        "(black-point) or red (white-point) grip triangle (the cursor "
                                        "becomes a resize arrow near one), or type exact values in "
                                        "Min/Max. Auto re-stretches to the channel's 0.5-99.5 "
                                        "percentile range."),
            ("Pixel readout", "Hover over the image to see the raw pixel value under the cursor, "
                                        "for the current frame and channel, shown next to the frame "
                                        "counter."),
        ]),
        ("Settings Panel Buttons", [
            ("Browse", "Open a multi-channel, multi-frame .tif movie. Pixel size and time step are "
                       "auto-read from the file's metadata when available. Settings fields take "
                       "effect automatically whenever you click Measure, an Auto-Detect button, or "
                       "Re-fit Frame Range — no separate 'apply' step needed."),
            ("Output Column Settings", "Choose which columns appear in the results table."),
            ("Clear Selections", "Remove all seeds, fitted results, and the results table's contents. "
                       "The loaded movie itself is kept."),
            ("Export Settings / Import Settings", "Save all current parameter values (and seeds) to "
                       "a JSON file, or restore them later — handy for keeping consistent settings "
                       "across experiments."),
        ]),
        ("Results Table Buttons", [
            ("Save to File", "Export the table as CSV or Excel (.xlsx)."),
            ("Copy to Clipboard", "Copy the table as tab-separated text, ready to paste into a "
                       "spreadsheet."),
            ("Export ROIs", "Save every ellipse currently shown in the table as an ImageJ-compatible "
                       "ROI .zip file."),
            ("Export Plots", "Generate one time-series PNG per cell track shown in the table."),
            ("Delete Selected Rows", "Remove the selected row(s) from the table (ctrl/shift-click to "
                       "select more than one)."),
            ("Clear Table", "Empty the table without affecting seeds or tracking state."),
        ]),
        ("Image / Time Parameters", [
            ("Pixel size (µm/px)", "Physical size of one pixel. Auto-read from TIF metadata when "
                       "available."),
            ("Brightfield channel", "Zero-based channel index used for edge detection."),
            ("FL channel 1 / 2", "Fluorescence channel(s) to measure mean intensity inside each "
                       "fitted ellipse. Set channel 2 to −1 to disable it."),
            ("Time step (s)", "Time between frames, used as the X-axis in exported plots. "
                       "Auto-read from TIF metadata when available."),
        ]),
        ("Cell Fitting Parameters", [
            ("Max cell radius (µm)", "Maximum expected cell radius — radial intensity profiles are "
                       "sampled out to this distance from the seed point. The pixel count shown next "
                       "to the field (based on the current pixel size) is what the algorithm actually "
                       "uses internally."),
            ("Tracking search radius (µm)", "Per-frame search radius used while tracking an "
                       "already-found cell, independent of 'Max cell radius'. Leave blank to use the "
                       "same value. A smaller value tightens the search window around the previous "
                       "frame's position, which can reduce false-positive edges from neighbouring "
                       "cells or debris in a cluttered image — but it also caps how far the edge "
                       "detector can look, so don't set it smaller than the cell's actual radius."),
            ("Edge window (µm)", "Width of the sliding window used to detect the dark→bright "
                       "transition at the cell wall."),
            ("Min edge contrast (%)", "Minimum contrast (relative to background) for an edge to be "
                       "accepted. Increase if false edges appear inside the cell."),
            ("Fitting method", "Geometric (recommended): non-linear least-squares fit, more accurate. "
                       "Algebraic: direct linear fit, faster but less robust for imperfect edges."),
            ("BF background correction", "Subtracts a Gaussian background from the brightfield "
                       "channel before edge detection — useful for uneven illumination."),
            ("Correction sigma (µm)", "Spatial scale of the Gaussian background estimate. Should be "
                       "larger than the cell diameter."),
            ("Allow cell radius growth over time", "Off by default, matching BudJ (which doesn't "
                       "model this). When on, the two fields below widen the per-frame search radius "
                       "as a budding cell grows, so tracking doesn't lose a cell that outgrows 'Max "
                       "cell radius' partway through the movie. When off, both are fixed at 0 "
                       "(disabled) regardless of what's typed into them."),
            ("Max cell radius increase (%)", "Maximum permitted change in major/minor axis between "
                       "consecutive frames; larger changes are treated as missed frames."),
            ("Max cell radius increase per frame (%)", "How much a budding cell is allowed to grow "
                       "each frame, used to widen the search radius beyond 'Max cell radius' as the "
                       "cell gets bigger."),
        ]),
        ("Display Parameters (under Advanced Settings)", [
            ("Show Scale Bar", "Show the scale bar in the bottom-right corner of the image. "
                       "On by default."),
            ("Show Masks", "Toggle the fitted-ellipse outlines off to see raw edge points without "
                       "the mask drawn over them."),
            ("Show Edge Points", "Overlay the raw detected boundary points (red dots) used to fit "
                       "each cell's ellipse."),
        ]),
        ("FL Channel Offset Parameters (under Advanced Settings)", [
            ("FL1/FL2 Offset — X / Y / Auto", "Shown once that channel is enabled (not tied to which "
                       "channel is currently displayed in the image panel, so switching channels "
                       "there never moves anything here). Corrects a fixed pixel misalignment "
                       "between that channel and the brightfield outlines (common with a second "
                       "camera or filter cube) — when non-zero, the image panel also overlays the "
                       "faint, un-shifted brightfield position as a reference alongside the normal "
                       "(full-strength) outline at the as-sampled, shifted position — the shifted "
                       "one is what fluorescence is actually measured from. Auto estimates it from "
                       "already-fitted cells — run Measure first, "
                       "then Auto, then Measure (or Re-fit Frame Range) again so the correction is "
                       "actually applied to the measurements. The X/Y fields can also be set by hand."),
        ]),
        ("Tracking Parameters (under Advanced Settings)", [
            ("Max frame gap", "Consecutive missed frames tolerated before a track ends. Missed "
                       "frames are filled in by linear interpolation."),
            ("Max overlap discard (%)", "If two fitted ellipses in the same frame overlap by more "
                       "than this fraction of the smaller one's area, the higher-ID one is discarded."),
            ("Bud distance factor", "Mother-daughter proximity multiplier: (r_mother + r_daughter) × "
                       "factor. Increase if buds appear further from the mother's edge."),
            ("Bud size ratio", "A daughter must be smaller than r_mother × this ratio to be "
                       "considered a bud. Decrease to require a bigger size difference."),
        ]),
        ("Auto-Detection / Hough Parameters (under Advanced Settings)", [
            ("Min / Max cell radius (µm)", "Radius range searched by the circular Hough transform."),
            ("Max cells per frame", "Upper limit on candidate circles per frame — set to the "
                       "expected number of real cells to reduce false positives."),
            ("Detection threshold (0–1)", "Minimum Hough accumulator score. Raise toward 1.0 to "
                       "accept only strong, well-defined circles."),
            ("Match distance (µm)", "A detected candidate is linked to an existing track if its "
                       "centroid is within this distance of the track's last known position."),
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyBud Help")

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        for title, entries in self._SECTIONS:
            header = QLabel(f"<span style='font-size:11pt; font-weight:bold'>{title}</span>")
            layout.addWidget(header)

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
            for name, description in entries:
                name_label = QLabel(f"<b>{name}</b>")
                name_label.setWordWrap(True)
                desc_label = QLabel(description)
                desc_label.setWordWrap(True)
                form.addRow(name_label, desc_label)
            layout.addLayout(form)

        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

        self.resize(640, 700)
