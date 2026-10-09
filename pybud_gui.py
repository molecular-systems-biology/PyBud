import numpy as np
import tifffile as tiff
import csv
from openpyxl import Workbook
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QPointF, QMimeData
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QLabel, QWidget, QSplitter, QScrollArea,
    QScrollBar, QLineEdit, QPushButton, QHBoxLayout, QFormLayout, QFileDialog,
    QTableWidget, QAbstractItemView, QHeaderView, QTableWidgetItem, QMainWindow,
    QStatusBar, QMessageBox, QCheckBox, QDialog, QDialogButtonBox, QFrame,
    QComboBox, QGroupBox, QSpinBox, QDoubleSpinBox, QShortcut
)
from pybud import PyBud
import roifile
import json
import os

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

# Global PyBud instance that holds all settings and data
pybud = PyBud()


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


# ---------------------------------------------------------------------------
# Worker threads — run a PyBud tracking operation off the GUI thread
# ---------------------------------------------------------------------------
class _TrackingWorker(QThread):
    """
    Common shape for every worker below: emit ``frame_processed`` as frames
    complete, report any exception, and always emit ``finished`` exactly once.
    Subclasses only need to implement :meth:`_track`.
    """
    finished        = pyqtSignal()
    frame_processed = pyqtSignal(int)
    _error_label    = "tracking"   # subclasses override for a clearer error message

    def run(self):
        try:
            self._track()
        except Exception as e:
            print(f"Error during {self._error_label}: {e}")
        finally:
            self.finished.emit()

    def _track(self):
        raise NotImplementedError

    def _on_frame(self, frame_number):
        self.frame_processed.emit(frame_number)

    def stop(self):
        pybud.stop()


class FitCellsWorker(_TrackingWorker):
    _error_label = "fit_cells"

    def _track(self):
        pybud.fit_cells(self._on_frame)


class RefitRangeWorker(_TrackingWorker):
    _error_label = "refit_range"

    def __init__(self, start_frame, end_frame, cell_ids=None):
        super().__init__()
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.cell_ids = cell_ids

    def _track(self):
        pybud.refit_range(self.start_frame, self.end_frame, self._on_frame, self.cell_ids)


class AutoDetectWorker(_TrackingWorker):
    """Runs AutoDetect.detect() + pybud.fit_cells(); detection logic lives in pybud.autodetect."""
    _error_label  = "auto-detect"
    status_update = pyqtSignal(str)

    def _track(self):
        if pybud.img is None:
            return

        try:
            from pybud import AutoDetect
            # Trigger the optional import early so we can show a friendly error
            from scipy.ndimage import gaussian_filter   # noqa: F401
            from skimage.feature import canny           # noqa: F401
        except ImportError:
            self.status_update.emit(
                "Auto-detect requires scikit-image. Install with: pip install scikit-image"
            )
            return

        pybud._should_run = True
        pybud.clear()

        self.status_update.emit("Phase 1/2 — detecting cells …")
        AutoDetect().detect(pybud, frame_callback=self.frame_processed.emit)

        n_seeds = sum(len(v) for v in pybud.selections.values())
        self.status_update.emit(f"Phase 2/2 — tracking {n_seeds} seed(s) …")
        pybud.fit_cells(self._on_frame)


class FLOffsetWorker(QThread):
    """
    Runs PyBud.estimate_fl_offset() off the GUI thread — a one-shot analysis
    over already-fitted cells, not a frame-by-frame tracking run, so it
    doesn't need _TrackingWorker's frame_processed/callback shape.
    """
    finished = pyqtSignal()

    def __init__(self, fl_channel):
        super().__init__()
        self.fl_channel = fl_channel
        self.result = None   # (dx, dy, best_score, zero_score) or None — set before `finished`

    def run(self):
        try:
            self.result = pybud.estimate_fl_offset(self.fl_channel)
        except Exception as e:
            print(f"Error during estimate_fl_offset: {e}")
        finally:
            self.finished.emit()


# ---------------------------------------------------------------------------
# Column settings dialog
# ---------------------------------------------------------------------------
class ColumnSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Output Column Settings")

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignTop)

        self.checkboxes = {}
        for key, name, _ in COLUMN_DEFS:
            cb = QCheckBox(name)
            cb.setChecked(column_enabled[key])
            self.checkboxes[key] = cb
            container_layout.addWidget(cb)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(300, 520)

    def get_enabled(self):
        return {key: cb.isChecked() for key, cb in self.checkboxes.items()}


# ---------------------------------------------------------------------------
# Help reference: controls, buttons, and settings parameters
# ---------------------------------------------------------------------------
class HelpDialog(QDialog):
    """Reference card covering mouse/keyboard controls, what each button does,
    and what every settings parameter means."""

    _SECTIONS = [
        ("Mouse & Keyboard", [
            ("Left-click on a cell",   "Place or remove a seed (green cross) on the current frame"),
            ("Left / Right arrow",     "Previous / next frame"),
            ("Up / Down arrow",        "Scroll the image up/down (handy when zoomed in)"),
            ("Ctrl or Alt + Up / Down arrow", "Previous / next channel"),
            ("Alt + scroll wheel",     "Previous / next channel"),
            ("Ctrl + scroll wheel",    "Zoom in/out, centred on the cursor"),
            ("+ / − keys",             "Zoom in / out"),
            ("Click a table row",      "Jump the image to that row's frame and highlight its cell "
                                        "(arrow keys in the table do the same)"),
        ]),
        ("Image Panel Buttons", [
            ("Auto-Detect This Frame", "Run circle detection on just the CURRENT frame and place a "
                                        "seed cross for anything found. Fast, and doesn't track or "
                                        "measure anything — review/adjust the crosses, then click "
                                        "Measure."),
            ("Auto-Detect & Measure (Whole Movie)", "Scan every frame of the movie for new cells "
                                        "(including ones that only appear partway through), then "
                                        "track and fit everything found in one go. Can take a while "
                                        "on long movies."),
            ("Measure", "Track every current seed forward through the movie and fit an ellipse to "
                        "each frame."),
            ("Stop", "Interrupt a running detection or measurement; results computed so far are kept."),
            ("Re-fit Frame Range — From / To / cell(s)", "Re-run tracking for just the given frame "
                                        "range, using the current settings. Leave 'cell(s)' blank to "
                                        "re-fit every cell active in that range, or list specific cell "
                                        "IDs (e.g. 1,3) to re-fit only those — every other cell's "
                                        "existing results stay untouched. Useful for fixing one "
                                        "problem stretch of a movie without redoing everything."),
            ("Zoom − / + / Reset Zoom", "Zoom out, zoom in, or reset to 100%. Current zoom level is "
                                        "shown as a percentage between the buttons."),
            ("Contrast — Min / Max / Auto", "Adjust the brightness range used to display the current "
                                        "channel (display only — never affects measurements). Auto "
                                        "re-stretches it to the channel's 0.5-99.5 percentile range."),
            ("FL Offset — X / Y / Auto", "Only shown for a fluorescence channel. Corrects a fixed "
                                        "pixel misalignment between this channel and the brightfield "
                                        "outlines (common with a second camera or filter cube). Auto "
                                        "estimates it from already-fitted cells — run Measure first, "
                                        "then Auto, then Measure (or Re-fit Frame Range) again so the "
                                        "correction is actually applied to the measurements. The X/Y "
                                        "fields can also be set by hand."),
            ("Show Edge Points", "Overlay the raw detected boundary points (red dots) used to fit "
                                        "each cell's ellipse."),
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
                       "sampled out to this distance from the seed point."),
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
        ]),
        ("Tracking Parameters (under Advanced Settings)", [
            ("Max size change (%)", "Maximum permitted change in major/minor axis between "
                       "consecutive frames; larger changes are treated as missed frames."),
            ("Max growth per frame (%)", "How much a budding cell is allowed to grow each frame, "
                       "used to widen the search radius beyond 'Max cell radius' as the cell gets "
                       "bigger — without this, a cell that outgrows that fixed radius becomes "
                       "unfindable partway through the movie. Set to 0 to disable (fixed radius, "
                       "the old behaviour)."),
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


# ---------------------------------------------------------------------------
# Image viewer
# ---------------------------------------------------------------------------
class ClickableImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tif_data = None
        self.frame = 0
        self.scale_factor = 1
        self.highlighted_cell_id = None
        self.display_channel = None   # None → use pybud.bf_channel
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.show_edge_points = False

        self.scroll_area = None          # set by ImageViewer, needed to anchor zoom on the cursor
        self.zoom_changed_callback = None
        self.min_scale_factor = 0.1
        self.max_scale_factor = 10.0

        # Keyboard focus target when the image is clicked, so it's a sensible
        # default for ImageViewer's keyboard shortcuts (see ImageViewer.__init__)
        # — those fire from anywhere in the panel, not just when this label
        # specifically has focus.
        self.setFocusPolicy(Qt.StrongFocus)

        # Set by ImageViewer; used by wheelEvent for Alt+scroll channel stepping
        # (wheel events have no QShortcut equivalent, unlike the keyboard shortcuts).
        self.channel_step_callback = None

        # Per-channel display contrast (raw pixel value -> 0..255 stretch).
        # Display-only: never affects measurement, which always reads raw pybud.img.
        # {channel: (lo, hi)}; a channel is auto-stretched (0.5-99.5 percentile) the
        # first time it's shown, then stays fixed until changed via the UI.
        self.display_ranges = {}

    def set_frame(self, frame):
        self.frame = frame
        self.update_image_display()

    def update_image_display(self):
        if pybud.img is None:
            self.setText("No image")
            return

        if self.frame >= pybud.img.shape[0]:
            self.frame = pybud.img.shape[0] - 1

        pixmap = self._build_scaled_pixmap()
        if pixmap is None:
            return   # unsupported dtype — _build_scaled_pixmap already set the error text

        painter = QPainter(pixmap)
        self._draw_selection_crosses(painter)
        self._draw_fitted_cells(painter)
        self._draw_mother_daughter_lines(painter)
        self._draw_scale_bar(painter, pixmap)
        painter.end()

        self.setPixmap(pixmap)

    def _build_scaled_pixmap(self):
        """Convert the current frame/channel to a QPixmap at the current zoom level, or
        None (after setting an error label) if the array dtype isn't numeric."""
        n_channels = pybud.img.shape[1]
        ch = self.display_channel if (self.display_channel is not None and
                                       self.display_channel < n_channels) else pybud.bf_channel
        if ch >= n_channels:
            ch = 0

        frame = pybud.img[self.frame, ch]
        if not np.issubdtype(frame.dtype, np.number):
            self.setText("Unsupported image format")
            return None

        lo, hi = self.display_range_for_channel(ch, frame)
        frame_8bit = np.clip((frame.astype(np.float64) - lo) / max(hi - lo, 1e-9) * 255,
                             0, 255).astype(np.uint8)
        height, width = frame_8bit.shape
        image = QImage(frame_8bit.data, width, height, frame_8bit.strides[0], QImage.Format_Grayscale8)

        pixmap = QPixmap.fromImage(image)
        return pixmap.scaled(
            int(pixmap.width() * self.scale_factor),
            int(pixmap.height() * self.scale_factor),
            Qt.KeepAspectRatio
        )

    def display_range_for_channel(self, ch, frame_data):
        """Raw pixel (lo, hi) stretched to 0..255 for display on channel `ch`. Auto-computed
        (0.5-99.5 percentile) and cached the first time a channel is shown; stays fixed after
        that until overridden (see ImageViewer's contrast controls)."""
        if ch not in self.display_ranges:
            lo = float(np.percentile(frame_data, 0.5))
            hi = float(np.percentile(frame_data, 99.5))
            if hi <= lo:
                lo, hi = float(frame_data.min()), float(frame_data.max())
            if hi <= lo:
                hi = lo + 1.0
            self.display_ranges[ch] = (lo, hi)
        return self.display_ranges[ch]

    def _draw_selection_crosses(self, painter):
        """Green crosses for manual selections, labelled with the cell number they'll
        receive once fitting runs."""
        pen = QPen(QColor(0, 255, 0), 2)
        painter.setPen(pen)
        if self.frame not in pybud.selections:
            return

        pending_ids = pybud.get_pending_ids()
        for sx, sy in pybud.selections[self.frame]:
            cell_id = pending_ids.get((self.frame, sx, sy))
            x = int(sx * self.scale_factor)
            y = int(sy * self.scale_factor)
            painter.setPen(pen)
            painter.drawLine(x - 5, y - 5, x + 5, y + 5)
            painter.drawLine(x - 5, y + 5, x + 5, y - 5)
            if cell_id is not None:
                painter.setPen(QColor(0, 0, 0))
                painter.drawText(x + 8, y - 6, str(cell_id))
                painter.setPen(QColor(0, 255, 0))
                painter.drawText(x + 7, y - 7, str(cell_id))

    def _draw_fitted_cells(self, painter):
        """Edge points (optional) and fitted ellipses for every cell found on this frame."""
        pen = QPen(QColor(0, 255, 0), 2)
        for cell in pybud.cells:
            if cell.frame != self.frame:
                continue

            if self.show_edge_points:
                pen.setColor(QColor(255, 0, 0))
                painter.setPen(pen)
                for x, y in zip(cell.found_x[cell.pixel_found], cell.found_y[cell.pixel_found]):
                    painter.drawPoint(int(x * self.scale_factor), int(y * self.scale_factor))

            if cell.cell_found:
                ellipse = cell.ellipse
                x = ellipse.get_x_center() * self.scale_factor
                y = ellipse.get_y_center() * self.scale_factor
                major = ellipse.get_major() * self.scale_factor
                minor = ellipse.get_minor() * self.scale_factor
                angle = ellipse.get_angle()

                highlighted = (cell.id == self.highlighted_cell_id)
                color = QColor(0, 220, 255, 220) if highlighted else QColor(255, 255, 0, 128)
                width = 3 if highlighted else 2

                painter.save()
                painter.setPen(QPen(color, width))
                painter.translate(x, y)
                painter.rotate(angle)
                painter.drawEllipse(QPointF(0, 0), major, minor)
                painter.restore()

                # Cell number, placed to the right of the ellipse (unrotated, so it stays legible)
                label_x = int(x + major + 4)
                label_y = int(y + 4)
                painter.setPen(QColor(0, 0, 0))
                painter.drawText(label_x + 1, label_y + 1, str(cell.id))
                painter.setPen(QColor(30, 100, 255))
                painter.drawText(label_x, label_y, str(cell.id))

    def _draw_mother_daughter_lines(self, painter):
        """Orange dashed line from each daughter cell to its mother, if any."""
        mother_ids = getattr(pybud, 'mother_ids', {})
        if not mother_ids:
            return

        frame_pos = {}   # cell_id -> (display_x, display_y)
        for cell in pybud.cells:
            if cell.frame == self.frame and cell.cell_found:
                frame_pos[cell.id] = (
                    cell.ellipse.get_x_center() * self.scale_factor,
                    cell.ellipse.get_y_center() * self.scale_factor,
                )

        pen_link = QPen(QColor(255, 165, 0), 2)
        pen_link.setStyle(Qt.DashLine)
        painter.setPen(pen_link)
        for child_id, mother_id in mother_ids.items():
            if mother_id >= 0 and child_id in frame_pos and mother_id in frame_pos:
                cx, cy = frame_pos[child_id]
                mx, my = frame_pos[mother_id]
                painter.drawLine(int(mx), int(my), int(cx), int(cy))

    def _draw_scale_bar(self, painter, pixmap):
        """White scale bar with a µm label in the bottom-right corner."""
        if pybud.pixel_size <= 0:
            return

        # Pick the largest "nice" length that fits in ~15% of image width
        target_px = pixmap.width() * 0.15
        bar_um = 1
        for candidate in (1, 2, 5, 10, 20, 50, 100, 200, 500):
            if candidate / pybud.pixel_size * self.scale_factor <= target_px:
                bar_um = candidate
            else:
                break
        bar_w = int(bar_um / pybud.pixel_size * self.scale_factor)
        bar_h = max(4, int(self.scale_factor * 3))

        margin  = 10
        bar_x   = pixmap.width()  - margin - bar_w
        bar_y   = pixmap.height() - margin - bar_h - 16

        # Solid white bar with thin black border
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRect(bar_x, bar_y, bar_w, bar_h)

        # Label centred below the bar, with a 1-px dark shadow for contrast
        label = f"{bar_um} µm"
        font  = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        fm    = painter.fontMetrics()
        tx    = bar_x + (bar_w - fm.horizontalAdvance(label)) // 2
        ty    = bar_y + bar_h + 12
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(tx + 1, ty + 1, label)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(tx, ty, label)

    def set_zoom(self, new_scale, anchor_pos=None):
        """
        Change the zoom level, optionally keeping the image point under
        ``anchor_pos`` (label-local coordinates, e.g. from a wheel event)
        fixed under the cursor.
        """
        new_scale = max(self.min_scale_factor, min(self.max_scale_factor, new_scale))
        old_scale = self.scale_factor
        if abs(new_scale - old_scale) < 1e-9:
            return

        hbar = vbar = None
        new_hval = new_vval = 0
        if anchor_pos is not None and self.scroll_area is not None:
            ratio = new_scale / old_scale
            hbar = self.scroll_area.horizontalScrollBar()
            vbar = self.scroll_area.verticalScrollBar()
            new_hval = hbar.value() + anchor_pos.x() * (ratio - 1)
            new_vval = vbar.value() + anchor_pos.y() * (ratio - 1)

        self.scale_factor = new_scale
        self.update_image_display()

        if hbar is not None:
            hbar.setValue(int(round(new_hval)))
            vbar.setValue(int(round(new_vval)))

        if self.zoom_changed_callback is not None:
            self.zoom_changed_callback(self.scale_factor)

    def wheelEvent(self, event):
        if pybud.img is None:
            super().wheelEvent(event)
            return

        vertical, horizontal = event.angleDelta().y(), event.angleDelta().x()
        if vertical == 0 and horizontal == 0:
            super().wheelEvent(event)
            return

        if vertical != 0 and (event.modifiers() & Qt.ControlModifier):
            factor = 1.15 if vertical > 0 else 1 / 1.15
            self.set_zoom(self.scale_factor * factor, event.pos())
            event.accept()
        elif horizontal != 0 or (event.modifiers() & Qt.AltModifier):
            # Alt+wheel is commonly delivered by the OS as a horizontal wheel
            # event (e.g. Windows routes it to a "horizontal scroll" tilt
            # action), sometimes without the Alt modifier bit even surviving —
            # so a horizontal delta alone is also treated as "change channel".
            step = horizontal if horizontal != 0 else vertical
            if self.channel_step_callback is not None:
                self.channel_step_callback(1 if step > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.LeftButton:
            click_position = event.pos()
            pixmap = self.pixmap()

            if pixmap is not None:
                x = click_position.x()
                y = click_position.y()

                if 0 <= x <= pixmap.width() and 0 <= y <= pixmap.height():
                    original_width = pybud.img.shape[3]
                    original_height = pybud.img.shape[2]
                    image_x = int(x * (original_width / pixmap.width()))
                    image_y = int(y * (original_height / pixmap.height()))

                    if pybud.contains_selection(self.frame, image_x, image_y):
                        pybud.remove_selection(self.frame, image_x, image_y)
                    else:
                        pybud.add_selection(self.frame, image_x, image_y)

                    self.update_image_display()


class ImageViewer(QWidget):
    measurement_started  = pyqtSignal()
    measurements_changed = pyqtSignal()
    status_message       = pyqtSignal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings   # used to sync current field values before every fitting run
        self.worker = None

        self.image_label = ClickableImageLabel()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.image_label)
        self.image_label.scroll_area = self.scroll_area
        self.image_label.zoom_changed_callback = self._on_zoom_changed
        self.image_label.channel_step_callback = self._step_channel   # Alt+wheel (see wheelEvent)
        self._register_panel_shortcuts()

        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.setMinimum(0)
        self.scrollbar.valueChanged.connect(self.update_frame)

        layout = QVBoxLayout()
        layout.addLayout(self._build_top_bar())
        layout.addLayout(self._build_contrast_bar())
        layout.addWidget(self._build_fl_offset_bar())
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.scrollbar)
        layout.addLayout(self._build_action_buttons())
        layout.addLayout(self._build_refit_bar())
        self.setLayout(layout)

    def _register_panel_shortcuts(self):
        """
        Left/Right steps frames; Up/Down scrolls the (possibly zoomed) image
        vertically; Ctrl+Up/Down and Alt+Up/Down both step channels (Alt to
        match Alt+wheel); +/- zoom in/out. All scoped to this panel
        (WidgetWithChildrenShortcut) so they fire no matter which control
        inside it currently has focus — e.g. after clicking the channel
        dropdown, not just when the image label itself is focused — while text
        fields (here and in Settings) keep their normal editing behaviour.
        """
        for key, handler in (
            (Qt.Key_Left,             lambda: self._step_frame(-1)),
            (Qt.Key_Right,            lambda: self._step_frame(1)),
            (Qt.Key_Up,               lambda: self._pan_vertical(-1)),
            (Qt.Key_Down,             lambda: self._pan_vertical(1)),
            (Qt.CTRL + Qt.Key_Up,     lambda: self._step_channel(-1)),
            (Qt.CTRL + Qt.Key_Down,   lambda: self._step_channel(1)),
            (Qt.ALT + Qt.Key_Up,      lambda: self._step_channel(-1)),
            (Qt.ALT + Qt.Key_Down,    lambda: self._step_channel(1)),
            (Qt.Key_Plus,             lambda: self._zoom_in()),
            (Qt.Key_Equal,            lambda: self._zoom_in()),
            (Qt.Key_Minus,            lambda: self._zoom_out()),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(handler)

    def _build_top_bar(self):
        """Frame counter, channel selector, zoom controls, edge-points toggle, help button."""
        self.frame_number_label = QLabel("")
        self.edge_points_checkbox = QCheckBox("Show Edge Points")
        self.edge_points_checkbox.stateChanged.connect(self.show_edge_points)

        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setFixedWidth(28)
        self.zoom_out_button.setToolTip("Zoom out (or Ctrl+scroll / the '-' key on the image)")
        self.zoom_out_button.clicked.connect(self._zoom_out)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignCenter)

        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setFixedWidth(28)
        self.zoom_in_button.setToolTip("Zoom in (or Ctrl+scroll / the '+' key on the image)")
        self.zoom_in_button.clicked.connect(self._zoom_in)

        self.zoom_reset_button = QPushButton("Reset Zoom")
        self.zoom_reset_button.clicked.connect(lambda: self.image_label.set_zoom(1.0))

        self.help_button = QPushButton("Help")
        self.help_button.setToolTip("Show a reference for controls, buttons, and settings")
        self.help_button.clicked.connect(self._show_help)

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(130)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        self._channel_label = QLabel("Channel:")

        top_layout = QHBoxLayout()
        top_layout.addWidget(self.frame_number_label)
        top_layout.addWidget(self._channel_label)
        top_layout.addWidget(self.channel_combo)
        top_layout.addSpacing(16)
        top_layout.addWidget(self.zoom_out_button)
        top_layout.addWidget(self.zoom_label)
        top_layout.addWidget(self.zoom_in_button)
        top_layout.addWidget(self.zoom_reset_button)
        top_layout.addStretch()
        top_layout.addWidget(self.edge_points_checkbox)
        top_layout.addWidget(self.help_button)
        return top_layout

    def _build_contrast_bar(self):
        """Min/Max display-range spin boxes + Auto button for the current channel's contrast."""
        self.contrast_min_spin = QDoubleSpinBox()
        self.contrast_min_spin.setRange(-1e7, 1e7)
        self.contrast_min_spin.setDecimals(1)
        self.contrast_min_spin.setFixedWidth(80)
        self.contrast_min_spin.setToolTip("Display black-point: raw pixel values at or below this show as black")
        self.contrast_min_spin.valueChanged.connect(self._on_contrast_changed)

        self.contrast_max_spin = QDoubleSpinBox()
        self.contrast_max_spin.setRange(-1e7, 1e7)
        self.contrast_max_spin.setDecimals(1)
        self.contrast_max_spin.setFixedWidth(80)
        self.contrast_max_spin.setToolTip("Display white-point: raw pixel values at or above this show as white")
        self.contrast_max_spin.valueChanged.connect(self._on_contrast_changed)

        self.contrast_auto_button = QPushButton("Auto")
        self.contrast_auto_button.setToolTip(
            "Re-stretch contrast for the current channel to its 0.5-99.5 percentile "
            "range. Display only — never affects measurements."
        )
        self.contrast_auto_button.clicked.connect(self._auto_contrast)

        contrast_layout = QHBoxLayout()
        contrast_layout.addWidget(QLabel("Contrast — min:"))
        contrast_layout.addWidget(self.contrast_min_spin)
        contrast_layout.addWidget(QLabel("max:"))
        contrast_layout.addWidget(self.contrast_max_spin)
        contrast_layout.addWidget(self.contrast_auto_button)
        contrast_layout.addStretch()
        return contrast_layout

    def _build_fl_offset_bar(self):
        """
        X/Y pixel-offset spin boxes + Auto button, correcting a fixed
        chromatic/optical misalignment between the current fluorescence
        channel and the brightfield-fitted outlines. Wrapped in a widget (not
        just a layout) so it can be hidden entirely while viewing Brightfield,
        where an offset is meaningless — see _refresh_fl_offset_controls().
        """
        self.fl_offset_x_spin = QSpinBox()
        self.fl_offset_x_spin.setRange(-50, 50)
        self.fl_offset_x_spin.setFixedWidth(60)
        self.fl_offset_x_spin.setToolTip(
            "Pixels to shift this channel's measurement region relative to "
            "the brightfield-fitted outline (positive = right)."
        )
        self.fl_offset_x_spin.valueChanged.connect(self._on_fl_offset_changed)

        self.fl_offset_y_spin = QSpinBox()
        self.fl_offset_y_spin.setRange(-50, 50)
        self.fl_offset_y_spin.setFixedWidth(60)
        self.fl_offset_y_spin.setToolTip(
            "Pixels to shift this channel's measurement region relative to "
            "the brightfield-fitted outline (positive = down)."
        )
        self.fl_offset_y_spin.valueChanged.connect(self._on_fl_offset_changed)

        self.fl_offset_auto_button = QPushButton("Auto")
        self.fl_offset_auto_button.setToolTip(
            "Estimate the offset automatically: tries small pixel shifts and "
            "picks the one that best lines up already-fitted cell outlines "
            "with this channel's signal. Run Measure first so there's "
            "something to compare against, then Measure (or Re-fit Frame "
            "Range) again afterwards to apply the correction."
        )
        self.fl_offset_auto_button.clicked.connect(self.estimate_fl_offset)

        fl_offset_layout = QHBoxLayout()
        fl_offset_layout.addWidget(QLabel("FL Offset (px) — X:"))
        fl_offset_layout.addWidget(self.fl_offset_x_spin)
        fl_offset_layout.addWidget(QLabel("Y:"))
        fl_offset_layout.addWidget(self.fl_offset_y_spin)
        fl_offset_layout.addWidget(self.fl_offset_auto_button)
        fl_offset_layout.addStretch()
        fl_offset_layout.setContentsMargins(0, 0, 0, 0)

        self.fl_offset_row = QWidget()
        self.fl_offset_row.setLayout(fl_offset_layout)
        self.fl_offset_row.setVisible(False)   # shown once a non-brightfield channel is selected
        return self.fl_offset_row

    def _build_action_buttons(self):
        """Auto-Detect / Measure / Stop row."""
        self.auto_detect_frame_button = QPushButton("Auto-Detect This Frame")
        self.auto_detect_frame_button.clicked.connect(self.auto_detect_current_frame)

        self.auto_detect_button = QPushButton("Auto-Detect & Measure (Whole Movie)")
        self.auto_detect_button.clicked.connect(self.auto_detect_measure)

        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self.measure)

        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self.stop)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.auto_detect_frame_button)
        button_layout.addWidget(self.auto_detect_button)
        button_layout.addWidget(self.measure_button)
        button_layout.addWidget(stop_button)
        return button_layout

    def _build_refit_bar(self):
        """From/To/cell(s) fields + Re-fit Frame Range button."""
        self.refit_from_spin = QSpinBox()
        self.refit_from_spin.setMinimum(1)
        self.refit_to_spin = QSpinBox()
        self.refit_to_spin.setMinimum(1)
        self.refit_cells_line = QLineEdit()
        self.refit_cells_line.setPlaceholderText("all cells")
        self.refit_cells_line.setFixedWidth(90)
        self.refit_cells_line.setToolTip(
            "Comma-separated cell IDs to re-fit, e.g. 1,3 (see the Cell column "
            "in the results table, or the numbers next to each ellipse). Every "
            "other cell's existing results in the range are left untouched. "
            "Leave blank to re-fit every cell active in the range."
        )
        self.refit_button = QPushButton("Re-fit Frame Range")
        self.refit_button.setToolTip(
            "Re-run tracking for frames [From, To] only, using the current "
            "settings. Existing tracks are continued from just before 'From'; "
            "new crosses placed inside the range start new tracks. Only the "
            "cell(s) listed (or every active cell, if left blank) are "
            "re-fit — all other frames/cells are left untouched."
        )
        self.refit_button.clicked.connect(self.refit_range)

        refit_layout = QHBoxLayout()
        refit_layout.addWidget(QLabel("Re-fit frames:"))
        refit_layout.addWidget(self.refit_from_spin)
        refit_layout.addWidget(QLabel("to"))
        refit_layout.addWidget(self.refit_to_spin)
        refit_layout.addWidget(QLabel("cell(s):"))
        refit_layout.addWidget(self.refit_cells_line)
        refit_layout.addWidget(self.refit_button)
        refit_layout.addStretch()
        return refit_layout

    def update(self):
        """Full reinitialize — call when a brand-new image has just been loaded
        (resets the frame, channel selection, and scrollbar/spin-box ranges)."""
        if pybud.img is not None:
            self.scrollbar.setMaximum(pybud.img.shape[0] - 1)
            n_frames = pybud.img.shape[0]
            for spin in (self.refit_from_spin, self.refit_to_spin):
                spin.setMaximum(n_frames)
            self._rebuild_channel_combo()
            self.update_frame(0)
            self._refresh_contrast_controls()
            self._refresh_fl_offset_controls()
            self.image_label.setFocus()   # so arrow-key frame/channel stepping works immediately

    def refresh(self):
        """
        Lightweight redraw after a settings tweak on the SAME image (including
        the automatic settings sync before Measure/Auto-Detect/Re-fit) — keeps
        the current frame, channel, and zoom intact instead of resetting them.
        """
        if pybud.img is None:
            return
        self._rebuild_channel_combo()
        self._refresh_fl_offset_controls()   # e.g. picks up fl_channel_offsets from Import Settings
        self.image_label.update_image_display()

    def reset_contrast(self):
        """Clear all per-channel contrast overrides — called when a new file is loaded,
        since a previous channel's display range won't make sense for different data."""
        self.image_label.display_ranges.clear()

    def _current_channel(self):
        return self.image_label.display_channel if self.image_label.display_channel is not None else pybud.bf_channel

    def _refresh_contrast_controls(self):
        """Sync the Min/Max spin boxes to the current channel's (auto or overridden) display range."""
        if pybud.img is None:
            return
        ch = self._current_channel()
        frame_idx = min(self.image_label.frame, pybud.img.shape[0] - 1)
        lo, hi = self.image_label.display_range_for_channel(ch, pybud.img[frame_idx, ch])
        self.contrast_min_spin.blockSignals(True)
        self.contrast_max_spin.blockSignals(True)
        self.contrast_min_spin.setValue(lo)
        self.contrast_max_spin.setValue(hi)
        self.contrast_min_spin.blockSignals(False)
        self.contrast_max_spin.blockSignals(False)

    def _on_contrast_changed(self, _value):
        self.image_label.display_ranges[self._current_channel()] = (
            self.contrast_min_spin.value(), self.contrast_max_spin.value()
        )
        self.image_label.update_image_display()

    def _auto_contrast(self):
        self.image_label.display_ranges.pop(self._current_channel(), None)
        self._refresh_contrast_controls()
        self.image_label.update_image_display()

    def _refresh_fl_offset_controls(self):
        """Show the FL Offset row only for a fluorescence channel (not Brightfield,
        where an offset is meaningless), and sync its spin boxes to that channel's
        currently stored offset."""
        ch = self._current_channel()
        is_fl_channel = pybud.img is not None and ch != pybud.bf_channel
        self.fl_offset_row.setVisible(is_fl_channel)
        if not is_fl_channel:
            return
        dx, dy = pybud.fl_channel_offsets.get(ch, (0, 0))
        self.fl_offset_x_spin.blockSignals(True)
        self.fl_offset_y_spin.blockSignals(True)
        self.fl_offset_x_spin.setValue(dx)
        self.fl_offset_y_spin.setValue(dy)
        self.fl_offset_x_spin.blockSignals(False)
        self.fl_offset_y_spin.blockSignals(False)

    def _on_fl_offset_changed(self, _value):
        ch = self._current_channel()
        if pybud.img is None or ch == pybud.bf_channel:
            return
        pybud.fl_channel_offsets[ch] = (self.fl_offset_x_spin.value(), self.fl_offset_y_spin.value())

    def estimate_fl_offset(self):
        """
        Auto-estimate the pixel offset for the current (fluorescence) channel
        from already-fitted cells, store it, and sync the spin boxes. Runs in
        a worker thread since it can take a few seconds. Does not retroactively
        change already-fitted cells — run Measure/Re-fit again to apply it.
        """
        if self.worker is not None or pybud.img is None:
            return
        ch = self._current_channel()
        if ch == pybud.bf_channel:
            return
        if not any(c.cell_found and not getattr(c, 'interpolated', False) for c in pybud.cells):
            QMessageBox.information(
                self, "No Measurements Yet",
                "Run Measure first so there are fitted cell outlines to "
                "compare against this channel's signal."
            )
            return

        self._set_buttons_enabled(False)
        self.worker = FLOffsetWorker(ch)
        self.worker.finished.connect(self._on_fl_offset_estimated)
        self.worker.start()
        self.measurement_started.emit()
        self.status_message.emit("Estimating channel offset …")

    def _on_fl_offset_estimated(self):
        worker = self.worker
        self.worker = None
        self._set_buttons_enabled(True)

        result = worker.result
        if result is None:
            self.status_message.emit(
                "Couldn't estimate an offset — not enough fitted cells to compare against."
            )
            return

        dx, dy, best_score, zero_score = result
        pybud.fl_channel_offsets[worker.fl_channel] = (dx, dy)
        if self._current_channel() == worker.fl_channel:
            self._refresh_fl_offset_controls()
        self.status_message.emit(
            f"Estimated offset: ({dx}, {dy}) px (signal contrast {best_score:.0f} vs "
            f"{zero_score:.0f} uncorrected). Click Measure (or Re-fit Frame Range) "
            f"again to apply it."
        )

    def _on_zoom_changed(self, scale_factor):
        self.zoom_label.setText(f"{round(scale_factor * 100)}%")

    def _step_frame(self, delta):
        if pybud.img is None:
            return
        new_frame = max(0, min(pybud.img.shape[0] - 1, self.image_label.frame + delta))
        self.update_frame(new_frame)

    def _step_channel(self, delta):
        n = self.channel_combo.count()
        if n <= 1:
            return
        self.channel_combo.setCurrentIndex((self.channel_combo.currentIndex() + delta) % n)

    def _pan_vertical(self, direction):
        """Scroll the (possibly zoomed) image view up/down. direction: -1 up, +1 down."""
        vbar = self.scroll_area.verticalScrollBar()
        step = max(vbar.singleStep(), 20) * 4
        vbar.setValue(vbar.value() + direction * step)

    def _zoom_in(self):
        self.image_label.set_zoom(self.image_label.scale_factor * 1.25)

    def _zoom_out(self):
        self.image_label.set_zoom(self.image_label.scale_factor / 1.25)

    def _show_help(self):
        HelpDialog(self).exec_()

    def _rebuild_channel_combo(self):
        """Rebuild the channel list from pybud.bf_channel/fl_channels, preserving the
        currently displayed channel if it's still one of the options (falls back to
        Brightfield otherwise — e.g. the very first time an image is shown)."""
        previous = self.image_label.display_channel

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        n_ch = pybud.img.shape[1] if pybud.img is not None else 0
        self.channel_combo.addItem("Brightfield", pybud.bf_channel)
        for i, fl_ch in enumerate(pybud.fl_channels):
            if 0 <= fl_ch < n_ch and fl_ch != pybud.bf_channel:
                self.channel_combo.addItem(f"FL Channel {i + 1}", fl_ch)

        idx = self.channel_combo.findData(previous) if previous is not None else -1
        if idx < 0:
            idx = 0
        self.channel_combo.setCurrentIndex(idx)
        self.image_label.display_channel = self.channel_combo.itemData(idx)
        self.channel_combo.blockSignals(False)

        # Hide the combo (and its label) when there is nothing to switch to
        has_choice = self.channel_combo.count() > 1
        self.channel_combo.setVisible(has_choice)
        self._channel_label.setVisible(has_choice)

    def _on_channel_changed(self, _idx):
        ch = self.channel_combo.currentData()
        if ch is not None:
            self.image_label.display_channel = ch
            self._refresh_contrast_controls()
            self._refresh_fl_offset_controls()
            self.image_label.update_image_display()

    def update_frame(self, frame=0):
        self.image_label.set_frame(frame)
        if pybud.img is not None:
            self.frame_number_label.setText(f"Frame: {frame + 1}/{pybud.img.shape[0]}")
        else:
            self.frame_number_label.setText(f"Frame: {frame + 1}")
        self.scrollbar.setValue(frame)

    def select_cell(self, frame, cell_id):
        self.image_label.highlighted_cell_id = cell_id
        self.update_frame(frame)

    def _ready_to_run(self):
        """
        Common pre-flight check for Measure/Auto-Detect/Re-fit: no other run
        already in progress, an image loaded, and the current settings-panel
        values synced into pybud. Returns False (and leaves any invalid-field
        warning dialog on screen) if it's not safe to start.
        """
        if self.worker is not None or pybud.img is None:
            return False
        return self.settings.adjust_settings()

    def auto_detect_measure(self):
        if not self._ready_to_run():
            return
        worker = AutoDetectWorker()
        worker.status_update.connect(self._set_status)
        self._start_worker(worker)

    def auto_detect_current_frame(self):
        """
        Run Hough circle detection on just the displayed frame and place a
        seed cross for each new candidate (skipping any that already have a
        seed nearby). Fast, synchronous, and doesn't track/measure anything —
        click Measure afterwards to track the placed seeds forward. For
        finding cells that first appear partway through the movie, use
        Auto-Detect & Measure (Whole Movie) instead.
        """
        if not self._ready_to_run():
            return

        try:
            from pybud import AutoDetect
            from scipy.ndimage import gaussian_filter   # noqa: F401 — early import check
            from skimage.feature import canny           # noqa: F401
        except ImportError:
            self.status_message.emit(
                "Auto-detect requires scikit-image. Install with: pip install scikit-image"
            )
            return

        frame = self.image_label.frame
        min_r_px = pybud.min_detect_radius_um / pybud.pixel_size
        max_r_px = pybud.max_detect_radius_um / pybud.pixel_size

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            candidates = AutoDetect.detect_frame(
                pybud.img[frame, pybud.bf_channel], min_r_px, max_r_px,
                pybud.n_cells_max, pybud.hough_threshold,
            )
        finally:
            QApplication.restoreOverrideCursor()

        added = 0
        for cx, cy in candidates:
            if not pybud.contains_selection(frame, float(cx), float(cy)):
                pybud.add_selection(frame, float(cx), float(cy))
                added += 1

        self.image_label.update_image_display()
        skipped = len(candidates) - added
        skipped_note = f" ({skipped} already had a seed)" if skipped else ""
        self.status_message.emit(
            f"Auto-detected {added} new cell(s) on frame {frame + 1}{skipped_note}. "
            f"Click Measure to track them."
        )

    def measure(self):
        if not self._ready_to_run():
            return
        self._start_worker(FitCellsWorker())

    def refit_range(self):
        if not self._ready_to_run():
            return
        start = self.refit_from_spin.value() - 1
        end = self.refit_to_spin.value() - 1
        if start > end:
            QMessageBox.warning(self, "Invalid Range",
                                "'From' frame must not be greater than 'To' frame.")
            return

        cell_ids = None
        text = self.refit_cells_line.text().strip()
        if text:
            try:
                cell_ids = {int(tok) for tok in text.replace(',', ' ').split()}
            except ValueError:
                QMessageBox.warning(self, "Invalid Cell List",
                                    "Enter cell IDs as numbers separated by commas "
                                    "(e.g. 1,3), or leave blank to re-fit every cell.")
                return

        self._start_worker(RefitRangeWorker(start, end, cell_ids))
        scope = f"cell(s) {', '.join(str(c) for c in sorted(cell_ids))}" if cell_ids else "all cells"
        self.status_message.emit(f"Re-fitting frames {start + 1}–{end + 1} ({scope}) …")

    def _start_worker(self, worker):
        """Wire up and launch a tracking worker started by Measure/Auto-Detect/Re-fit."""
        self._set_buttons_enabled(False)
        self.worker = worker
        self.worker.finished.connect(self.on_fit_cells_finished)
        self.worker.frame_processed.connect(self.update_frame)
        self.worker.start()
        self.measurement_started.emit()

    def _set_buttons_enabled(self, enabled: bool):
        self.measure_button.setEnabled(enabled)
        self.auto_detect_button.setEnabled(enabled)
        self.auto_detect_frame_button.setEnabled(enabled)
        self.refit_button.setEnabled(enabled)
        self.refit_from_spin.setEnabled(enabled)
        self.refit_to_spin.setEnabled(enabled)
        self.refit_cells_line.setEnabled(enabled)
        self.fl_offset_auto_button.setEnabled(enabled)
        # Also frozen during a run: fl_channel_offsets is read live by Cell
        # construction as each frame is processed, so changing it mid-run
        # could give different cells in the same run different offsets.
        self.fl_offset_x_spin.setEnabled(enabled)
        self.fl_offset_y_spin.setEnabled(enabled)

    def _set_status(self, msg: str):
        self.status_message.emit(msg)

    def show_edge_points(self, state):
        self.image_label.show_edge_points = (state == Qt.Checked)
        self.image_label.update_image_display()

    def on_fit_cells_finished(self):
        self.measurements_changed.emit()
        if pybud.lost_cells:
            parts = [f"Cell {cid} lost at frame {frame + 1} ({reason})"
                     for cid, frame, reason in pybud.lost_cells]
            self.status_message.emit("Finished — " + "; ".join(parts))
        else:
            self.status_message.emit("")
        self.worker = None
        self._set_buttons_enabled(True)

    def stop(self):
        if self.worker is not None:
            self.worker.stop()


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------
class Settings(QWidget):
    settings_changed = pyqtSignal()
    reset_requested  = pyqtSignal()   # a new image was loaded, or selections/results were cleared
    image_loaded     = pyqtSignal()   # specifically: a new file was loaded (not just cleared)

    def __init__(self):
        super().__init__()

        # Scroll area so all groups are reachable in a narrow panel
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(6)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(self._build_file_group())
        layout.addWidget(self._build_image_group())
        layout.addWidget(self._build_time_group())
        layout.addWidget(self._build_fitting_group())
        self._build_advanced_section(layout)
        self._build_action_buttons(layout)

        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _build_file_group(self):
        file_group = QGroupBox("File")
        file_form = QFormLayout(file_group)
        self.file_path = QLineEdit()
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_path)
        file_row.addWidget(browse_button)
        file_row.setContentsMargins(0, 0, 0, 0)
        file_w = QWidget()
        file_w.setLayout(file_row)
        file_form.addRow(file_w)
        return file_group

    def _build_image_group(self):
        image_group = QGroupBox("Image")
        image_form = QFormLayout(image_group)
        self.pixel_size_line = QLineEdit("0.0645")
        image_form.addRow("Pixel size (µm/px):", self.pixel_size_line)
        self.brightfield_channel_line = QLineEdit("0")
        image_form.addRow("Brightfield channel:", self.brightfield_channel_line)
        self.fluorescent_channel1_line = QLineEdit("1")
        image_form.addRow("FL channel 1:", self.fluorescent_channel1_line)
        self.fluorescent_channel2_line = QLineEdit("-1")
        image_form.addRow("FL channel 2 (-1 = none):", self.fluorescent_channel2_line)
        return image_group

    def _build_time_group(self):
        time_group = QGroupBox("Time")
        time_form = QFormLayout(time_group)
        self.time_step_line = QLineEdit("1.0")
        time_form.addRow("Time step (s):", self.time_step_line)
        return time_group

    def _build_fitting_group(self):
        fitting_group = QGroupBox("Cell Fitting")
        fitting_form = QFormLayout(fitting_group)
        self.cell_radius_line = QLineEdit("4")
        fitting_form.addRow("Max cell radius (µm):", self.cell_radius_line)
        self.cell_edge_size_line = QLineEdit("1")
        fitting_form.addRow("Edge window (µm):", self.cell_edge_size_line)
        self.edge_rel_min_line = QLineEdit("30")
        fitting_form.addRow("Min edge contrast (%):", self.edge_rel_min_line)
        self.fitting_method_combo = QComboBox()
        self.fitting_method_combo.addItem("Geometric (recommended)", "geometric")
        self.fitting_method_combo.addItem("Algebraic", "algebraic")
        fitting_form.addRow("Fitting method:", self.fitting_method_combo)
        self.bg_correction_check = QCheckBox("Enable")
        fitting_form.addRow("BF background correction:", self.bg_correction_check)
        self.bg_sigma_line = QLineEdit("5.0")
        self.bg_sigma_line.setEnabled(False)
        fitting_form.addRow("Correction sigma (µm):", self.bg_sigma_line)
        self.bg_correction_check.stateChanged.connect(
            lambda state: self.bg_sigma_line.setEnabled(state == Qt.Checked)
        )
        return fitting_group

    def _build_advanced_section(self, layout):
        """
        Collapsible Tracking + Auto-Detection groups, appended directly to
        `layout`. These are tuned rarely (Tracking) or only matter for
        Auto-Detect & Measure (Hough), so they're tucked away by default to
        keep the commonly-used settings above the fold.
        """
        self.advanced_toggle = QPushButton("▸ Advanced Settings (Tracking, Auto-Detection)")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setStyleSheet("text-align: left; padding-left: 4px;")
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        self.advanced_container = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_container)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)

        tracking_group = QGroupBox("Tracking")
        tracking_form = QFormLayout(tracking_group)
        self.max_size_change_line = QLineEdit("50")
        tracking_form.addRow("Max size change (%):", self.max_size_change_line)
        self.max_growth_line = QLineEdit("5")
        tracking_form.addRow("Max growth per frame (%):", self.max_growth_line)
        self.max_gap_line = QLineEdit("1")
        tracking_form.addRow("Max frame gap:", self.max_gap_line)
        self.overlap_threshold_line = QLineEdit("10")
        tracking_form.addRow("Max overlap discard (%):", self.overlap_threshold_line)
        self.bud_distance_line = QLineEdit("1.2")
        tracking_form.addRow("Bud distance factor:", self.bud_distance_line)
        self.bud_size_ratio_line = QLineEdit("0.8")
        tracking_form.addRow("Bud size ratio:", self.bud_size_ratio_line)
        advanced_layout.addWidget(tracking_group)

        detect_group = QGroupBox("Auto-Detection (Hough)")
        detect_form = QFormLayout(detect_group)
        self.min_detect_radius_line = QLineEdit("1.5")
        detect_form.addRow("Min cell radius (µm):", self.min_detect_radius_line)
        self.max_detect_radius_line = QLineEdit("4.0")
        detect_form.addRow("Max cell radius (µm):", self.max_detect_radius_line)
        self.n_cells_max_line = QLineEdit("10")
        detect_form.addRow("Max cells per frame:", self.n_cells_max_line)
        self.hough_threshold_line = QLineEdit("0.5")
        detect_form.addRow("Detection threshold (0–1):", self.hough_threshold_line)
        self.match_distance_line = QLineEdit("8.0")
        detect_form.addRow("Match distance (µm):", self.match_distance_line)
        advanced_layout.addWidget(detect_group)

        self.advanced_container.setVisible(False)
        layout.addWidget(self.advanced_container)

    def _build_action_buttons(self, layout):
        """
        Appended directly to `layout`. No "Adjust Settings" button: Measure /
        Auto-Detect / Re-fit Frame Range already sync these field values into
        pybud before they run.
        """
        for label, slot in (
            ("Output Column Settings",  self.open_column_settings),
            ("Clear Selections",        self.clear_selections),
            ("Export Settings",         self.export_settings),
            ("Import Settings",         self.import_settings),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def _toggle_advanced(self, checked):
        self.advanced_container.setVisible(checked)
        arrow = "▾" if checked else "▸"
        self.advanced_toggle.setText(f"{arrow} Advanced Settings (Tracking, Auto-Detection)")

    def open_column_settings(self):
        dialog = ColumnSettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            column_enabled.update(dialog.get_enabled())
            self.settings_changed.emit()

    def get_input_value(self, line_edit, value_type, error_message, min_value=None):
        try:
            value = value_type(line_edit.text())
            if min_value is not None and value < min_value:
                QMessageBox.warning(self, "Input Error",
                                    f"{error_message} cannot be less than {min_value}.")
                return None
            return value
        except ValueError:
            QMessageBox.warning(self, "Input Error",
                                f"{error_message} must be a valid {value_type.__name__}.")
            return None

    def get_settings_values(self):
        settings = {}

        settings["file_path"] = self.file_path.text()

        pixel_size = self.get_input_value(self.pixel_size_line, float, "Pixel Size")
        if pixel_size is None: return None
        settings["pixel_size"] = pixel_size

        cell_radius = self.get_input_value(self.cell_radius_line, float, "Maximum Cell Radius")
        if cell_radius is None: return None
        settings["cell_radius"] = cell_radius

        edge_size = self.get_input_value(self.cell_edge_size_line, float, "Cell Edge Size")
        if edge_size is None: return None
        settings["edge_size"] = edge_size

        brightfield_channel = self.get_input_value(self.brightfield_channel_line, int, "Brightfield Channel")
        if brightfield_channel is None: return None
        settings["brightfield_channel"] = brightfield_channel

        fluorescent_channel1 = self.get_input_value(self.fluorescent_channel1_line, int,
                                                     "Fluorescent Channel 1", min_value=0)
        if fluorescent_channel1 is None: return None
        settings["fluorescent_channel1"] = fluorescent_channel1

        fluorescent_channel2 = self.get_input_value(self.fluorescent_channel2_line, int, "Fluorescent Channel 2")
        if fluorescent_channel2 is None: return None
        settings["fluorescent_channel2"] = fluorescent_channel2

        edge_rel_min = self.get_input_value(self.edge_rel_min_line, float, "Relative Minimum Edge Difference")
        if edge_rel_min is None: return None
        settings["edge_rel_min"] = edge_rel_min

        time_step = self.get_input_value(self.time_step_line, float, "Time Step")
        if time_step is None: return None
        settings["time_step"] = time_step

        settings["time_unit"] = "s"

        settings["fitting_method"] = self.fitting_method_combo.currentData()

        if self.bg_correction_check.isChecked():
            bg_sigma = self.get_input_value(self.bg_sigma_line, float,
                                            "BF Background Correction Sigma", min_value=0.1)
            if bg_sigma is None: return None
            settings["bg_correction_sigma"] = bg_sigma
        else:
            settings["bg_correction_sigma"] = 0.0

        max_size_change = self.get_input_value(self.max_size_change_line, float, "Max Size Change")
        if max_size_change is None: return None
        settings["max_size_change"] = max(0.0, max_size_change) / 100.0  # % -> fraction

        max_growth_per_frame = self.get_input_value(self.max_growth_line, float, "Max Growth Per Frame", min_value=0)
        if max_growth_per_frame is None: return None
        settings["max_growth_per_frame"] = max_growth_per_frame / 100.0  # % -> fraction

        max_gap = self.get_input_value(self.max_gap_line, int, "Max Frame Gap", min_value=0)
        if max_gap is None: return None
        settings["max_gap"] = max_gap

        overlap = self.get_input_value(self.overlap_threshold_line, float,
                                       "Max Overlap Before Discard", min_value=0)
        if overlap is None: return None
        settings["overlap_threshold"] = overlap / 100.0

        bud_dist = self.get_input_value(self.bud_distance_line, float, "Bud Distance Factor", min_value=0)
        if bud_dist is None: return None
        settings["bud_distance_factor"] = bud_dist

        bud_size = self.get_input_value(self.bud_size_ratio_line, float, "Bud Size Ratio", min_value=0)
        if bud_size is None: return None
        settings["bud_size_ratio"] = bud_size

        min_detect_radius = self.get_input_value(self.min_detect_radius_line, float,
                                                  "Min Cell Radius", min_value=0)
        if min_detect_radius is None: return None
        settings["min_detect_radius_um"] = min_detect_radius

        max_detect_radius = self.get_input_value(self.max_detect_radius_line, float,
                                                  "Max Cell Radius", min_value=0)
        if max_detect_radius is None: return None
        settings["max_detect_radius_um"] = max_detect_radius

        n_cells_max = self.get_input_value(self.n_cells_max_line, int,
                                            "Max Cells per Frame", min_value=1)
        if n_cells_max is None: return None
        settings["n_cells_max"] = n_cells_max

        hough_threshold = self.get_input_value(self.hough_threshold_line, float,
                                                "Detection Threshold")
        if hough_threshold is None: return None
        settings["hough_threshold"] = max(0.0, min(1.0, hough_threshold))

        match_distance = self.get_input_value(self.match_distance_line, float,
                                               "Cell Match Distance", min_value=0)
        if match_distance is None: return None
        settings["match_distance_um"] = match_distance

        return settings

    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Select Measurement File", "",
            "TIF Files (*.tif *.tiff *.TIF *.TIFF);;All Files (*)"
        )
        if file_name:
            self.file_path.setText(file_name)
            self.load_image(file_name)

    def _read_tif_metadata(self, image_path):
        """Return dict with any of: pixel_size, time_step, time_unit extracted from TIF tags."""
        meta = {}
        try:
            with tiff.TiffFile(image_path) as tf:
                # --- ImageJ metadata -------------------------------------------
                ij = tf.imagej_metadata or {}
                fi = ij.get('finterval')
                if fi is not None:
                    meta['time_step'] = float(fi)
                tu = ij.get('tunit') or ij.get('unit')
                if tu and tu not in ('micron', 'um', 'µm', 'pixel'):
                    meta['time_unit'] = tu

                # --- OME-XML ---------------------------------------------------
                if tf.ome_metadata and 'time_step' not in meta:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(tf.ome_metadata)
                        pixels = root.find('.//{*}Pixels')
                        if pixels is not None:
                            ti = pixels.get('TimeIncrement')
                            if ti:
                                meta['time_step'] = float(ti)
                            tiu = pixels.get('TimeIncrementUnit')
                            if tiu:
                                meta['time_unit'] = tiu
                            px = pixels.get('PhysicalSizeX')
                            if px:
                                meta['pixel_size'] = float(px)
                    except Exception:
                        pass

                # --- XResolution tag + spatial unit ---------------------------
                if 'pixel_size' not in meta and tf.pages:
                    page = tf.pages[0]
                    try:
                        xres_tag = page.tags.get('XResolution')
                        ru_tag   = page.tags.get('ResolutionUnit')
                        unit_str = (ij.get('unit') or '').lower()
                        if xres_tag:
                            num, den = xres_tag.value
                            if den and num:
                                px_per_unit = num / den
                                ru = ru_tag.value if ru_tag else None
                                if unit_str in ('micron', 'um', 'µm'):
                                    meta['pixel_size'] = 1.0 / px_per_unit
                                elif ru and str(ru) in ('RESUNIT.CENTIMETER', '3'):
                                    meta['pixel_size'] = 10000.0 / px_per_unit
                                elif ru and str(ru) in ('RESUNIT.INCH', '2'):
                                    meta['pixel_size'] = 25400.0 / px_per_unit
                    except Exception:
                        pass
        except Exception:
            pass
        return meta

    def load_image(self, image_path):
        self.reset_requested.emit()
        tif_data = tiff.imread(image_path)

        # Auto-populate fields from embedded TIF metadata
        meta = self._read_tif_metadata(image_path)
        if 'pixel_size' in meta:
            self.pixel_size_line.setText(f"{meta['pixel_size']:.4f}")
        if 'time_step' in meta:
            self.time_step_line.setText(str(meta['time_step']))

        if tif_data.ndim == 3:
            tif_data = np.reshape(tif_data, (tif_data.shape[0], 1, tif_data.shape[1], tif_data.shape[2]))
            self.fluorescent_channel1_line.setText("0")

        self.adjust_settings()
        pybud.clear()
        pybud.img = tif_data
        self.image_loaded.emit()   # clears stale contrast ranges, then fully reinitializes the viewer

    def adjust_settings(self):
        """Push the current field values into the shared `pybud` instance. Returns
        True on success, False if a field is invalid (a warning dialog is already
        shown by get_settings_values() in that case)."""
        settings = self.get_settings_values()
        if settings is None:
            return False

        fl_channels = [settings["fluorescent_channel1"]]
        if settings["fluorescent_channel2"] >= 0:
            fl_channels.append(settings["fluorescent_channel2"])

        pybud.fitting_method        = settings["fitting_method"]
        pybud.bg_correction_sigma   = settings["bg_correction_sigma"]
        pybud.overlap_threshold     = settings["overlap_threshold"]
        pybud.pixel_size = settings["pixel_size"]
        pybud.cell_radius = settings["cell_radius"]
        pybud.edge_size = settings["edge_size"]
        pybud.bf_channel = settings["brightfield_channel"]
        pybud.fl_channels = fl_channels
        pybud.edge_rel_min = settings["edge_rel_min"]
        pybud.time_step             = settings["time_step"]
        pybud.time_unit             = settings["time_unit"]
        pybud.max_size_change       = settings["max_size_change"]
        pybud.max_growth_per_frame  = settings["max_growth_per_frame"]
        pybud.max_gap               = settings["max_gap"]
        pybud.bud_distance_factor   = settings["bud_distance_factor"]
        pybud.bud_size_ratio        = settings["bud_size_ratio"]
        pybud.min_detect_radius_um  = settings["min_detect_radius_um"]
        pybud.max_detect_radius_um  = settings["max_detect_radius_um"]
        pybud.n_cells_max           = settings["n_cells_max"]
        pybud.hough_threshold       = settings["hough_threshold"]
        pybud.match_distance_um     = settings["match_distance_um"]

        self.settings_changed.emit()
        return True

    def clear_selections(self):
        pybud.stop()
        pybud.clear()
        self.reset_requested.emit()
        self.settings_changed.emit()

    def export_settings(self):
        settings = self.get_settings_values()
        if settings is None:
            return

        settings["selections"] = pybud.selections
        settings["column_enabled"] = dict(column_enabled)
        # JSON object keys must be strings; restored back to int channel indices on import.
        settings["fl_channel_offsets"] = {str(ch): list(off) for ch, off in pybud.fl_channel_offsets.items()}

        file_name, _ = QFileDialog.getSaveFileName(
            self, "Save Settings", "", "JSON Files (*.json);;All Files (*)"
        )
        if not file_name:
            return

        if not file_name.endswith(".json"):
            file_name += ".json"

        try:
            with open(file_name, 'w') as f:
                json.dump(settings, f, indent=4)
            QMessageBox.information(self, "Success",
                                    f"Settings saved successfully as {os.path.basename(file_name)}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {str(e)}")

    def import_settings(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Load Settings", "", "JSON Files (*.json);;All Files (*)"
        )
        if not file_name:
            return

        try:
            with open(file_name, 'r') as f:
                settings = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load settings: {str(e)}")
            return

        self.file_path.setText(settings.get("file_path", ""))
        self.pixel_size_line.setText(str(settings.get("pixel_size", "0.0645")))
        self.cell_radius_line.setText(str(settings.get("cell_radius", "4")))
        self.cell_edge_size_line.setText(str(settings.get("edge_size", "1")))
        self.brightfield_channel_line.setText(str(settings.get("brightfield_channel", "0")))
        self.fluorescent_channel1_line.setText(str(settings.get("fluorescent_channel1", "1")))
        self.fluorescent_channel2_line.setText(str(settings.get("fluorescent_channel2", "-1")))
        self.edge_rel_min_line.setText(str(settings.get("edge_rel_min", "30")))
        self.time_step_line.setText(str(settings.get("time_step", "1.0")))
        self.max_size_change_line.setText(
            str(int(settings.get("max_size_change", 0.5) * 100))
        )
        self.max_growth_line.setText(
            str(int(settings.get("max_growth_per_frame", 0.05) * 100))
        )
        self.max_gap_line.setText(str(settings.get("max_gap", 1)))
        fitting_method = settings.get("fitting_method", "geometric")
        idx = self.fitting_method_combo.findData(fitting_method)
        if idx >= 0:
            self.fitting_method_combo.setCurrentIndex(idx)
        bg_sigma = settings.get("bg_correction_sigma", 0.0)
        self.bg_correction_check.setChecked(bg_sigma > 0)
        self.bg_sigma_line.setText(str(bg_sigma if bg_sigma > 0 else 5.0))
        self.bg_sigma_line.setEnabled(bg_sigma > 0)
        self.overlap_threshold_line.setText(
            str(int(round(settings.get("overlap_threshold", 0.1) * 100)))
        )
        self.bud_distance_line.setText(str(settings.get("bud_distance_factor", 1.2)))
        self.bud_size_ratio_line.setText(str(settings.get("bud_size_ratio", 0.8)))
        self.min_detect_radius_line.setText(
            str(settings.get("min_detect_radius_um", 1.5))
        )
        self.max_detect_radius_line.setText(
            str(settings.get("max_detect_radius_um", 4.0))
        )
        self.n_cells_max_line.setText(str(settings.get("n_cells_max", 10)))
        self.hough_threshold_line.setText(str(settings.get("hough_threshold", 0.5)))
        self.match_distance_line.setText(str(settings.get("match_distance_um", 8.0)))

        pybud.selections = settings.get("selections", {})

        if "column_enabled" in settings:
            for key, val in settings["column_enabled"].items():
                if key in column_enabled:
                    column_enabled[key] = bool(val)

        pybud.fl_channel_offsets = {
            int(ch): tuple(off) for ch, off in settings.get("fl_channel_offsets", {}).items()
        }

        self.settings_changed.emit()
        QMessageBox.information(self, "Success", "Settings imported successfully.")


# ---------------------------------------------------------------------------
# Measurement table
# ---------------------------------------------------------------------------
class MeasurementTable(QWidget):
    cell_selected = pyqtSignal(int, int)   # frame, cell_id

    def __init__(self):
        super().__init__()
        self._cells = []   # parallel list to table rows — accumulates across measure runs
        self._runs  = []   # parallel list: which run each row came from
        self._run_counter = 0

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Selecting a row — by click or arrow keys — jumps to its frame and
        # highlights its cell. Still works fine with multi-select (ctrl/shift-
        # click) for Delete Selected Rows below; it just also moves the view.
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save to File")
        self.save_button.clicked.connect(self.save_measurements)
        self.copy_button = QPushButton("Copy to Clipboard")
        self.copy_button.clicked.connect(self.copy_measurements)
        self.export_rois_button = QPushButton("Export ROIs")
        self.export_rois_button.clicked.connect(self.export_rois)
        self.export_plots_button = QPushButton("Export Plots")
        self.export_plots_button.clicked.connect(self.export_plots)
        self.delete_rows_button = QPushButton("Delete Selected Rows")
        self.delete_rows_button.setToolTip("Remove the selected row(s) from the output table (ctrl/shift-click to select more than one)")
        self.delete_rows_button.clicked.connect(self.delete_selected_rows)
        self.clear_table_button = QPushButton("Clear Table")
        self.clear_table_button.clicked.connect(self.clear_table)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addWidget(self.export_rois_button)
        button_layout.addWidget(self.export_plots_button)
        button_layout.addWidget(self.delete_rows_button)
        button_layout.addWidget(self.clear_table_button)
        layout.addLayout(button_layout)

    def append_measurements(self):
        """Add the cells from the measurement run that just finished as new rows, below any already shown."""
        found_cells = [cell for cell in pybud.cells if cell.cell_found]
        if not found_cells:
            return
        self._run_counter += 1
        self._cells.extend(found_cells)
        self._runs.extend([self._run_counter] * len(found_cells))
        self.redraw()

    def clear_table(self):
        """Discard all accumulated rows (does not affect the measurement/tracking state itself)."""
        self._cells = []
        self._runs = []
        self._run_counter = 0
        self.redraw()

    def delete_selected_rows(self):
        rows = sorted((idx.row() for idx in self.table.selectionModel().selectedRows()), reverse=True)
        if not rows:
            return
        for row in rows:
            del self._cells[row]
            del self._runs[row]
        self.redraw()

    def redraw(self):
        """Rebuild the visible table from the accumulated (cell, run) rows — doesn't change the data itself."""
        # Build active column list; "time" header includes the unit
        active_cols = []
        for key, name, _ in COLUMN_DEFS:
            if column_enabled[key]:
                if key == "time":
                    name = f"Time ({pybud.time_unit})"
                active_cols.append((key, name))

        self.table.setColumnCount(len(active_cols))
        self.table.setHorizontalHeaderLabels([name for _, name in active_cols])
        self.table.setRowCount(len(self._cells))

        for row, (cell, run) in enumerate(zip(self._cells, self._runs)):
            values = get_cell_values(cell)
            values["run"] = str(run)
            for col, (key, _) in enumerate(active_cols):
                self.table.setItem(row, col, QTableWidgetItem(values.get(key, "")))

    def _on_selection_changed(self):
        """Jump the image to the (first) selected row's frame and highlight its cell."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._cells):
            cell = self._cells[row]
            self.cell_selected.emit(cell.frame, cell.id)

    def save_measurements(self):
        options = QFileDialog.Options()
        file_name, selected_filter = QFileDialog.getSaveFileName(
            self, "Save File", "",
            "CSV Files (*.csv);;Excel Files (*.xlsx);;All Files (*)",
            options=options
        )
        if not file_name:
            return

        if selected_filter == "Excel Files (*.xlsx)":
            if not file_name.endswith('.xlsx'):
                file_name += '.xlsx'
            self._save_as_excel(file_name)
        else:
            if not file_name.endswith('.csv'):
                file_name += '.csv'
            self._save_as_csv(file_name)

    def _table_headers(self):
        return [self.table.horizontalHeaderItem(i).text()
                for i in range(self.table.columnCount())]

    def _table_rows(self):
        return [
            [(self.table.item(row, col).text() if self.table.item(row, col) else '')
             for col in range(self.table.columnCount())]
            for row in range(self.table.rowCount())
        ]

    def _save_as_csv(self, file_name):
        with open(file_name, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(self._table_headers())
            writer.writerows(self._table_rows())
        print(f"Data saved to {file_name}")

    def _save_as_excel(self, file_name):
        wb = Workbook()
        ws = wb.active
        ws.append(self._table_headers())
        for row in self._table_rows():
            ws.append(row)
        wb.save(file_name)
        print(f"Data saved to {file_name}")

    def copy_measurements(self):
        clipboard = QApplication.clipboard()
        mime_data = QMimeData()

        lines = ["\t".join(self._table_headers())]
        lines.extend("\t".join(row) for row in self._table_rows())

        mime_data.setText("\n".join(lines))
        clipboard.setMimeData(mime_data)
        print("Data copied to clipboard")

    def export_rois(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self, "Save ZIP File", "", "ZIP Files (*.zip);;All Files (*)", options=options
        )
        if not file_name:
            return

        rois = []
        for i, cell in enumerate(self._cells):
            x_points, y_points = cell.ellipse.generate_ellipse_points(100)
            roi_fitted = roifile.ImagejRoi.frompoints(np.column_stack((x_points, y_points)))
            roi_fitted.roitype = roifile.ROI_TYPE.POLYGON
            roi_fitted.t_position = cell.frame + 1
            roi_fitted.name = f"{i}_cell{cell.id}_{pybud.fitting_method}"
            rois.append(roi_fitted)

        for frame, selections in pybud.selections.items():
            for j, (x, y) in enumerate(selections):
                roi_point = roifile.ImagejRoi.frompoints([[x, y]])
                roi_point.roitype = roifile.ROI_TYPE.POINT
                roi_point.t_position = frame + 1
                roi_point.name = f"frame{frame}_point{j}"
                rois.append(roi_point)

        roifile.roiwrite(file_name, rois, mode='w')
        print("ROIs exported")

    def export_plots(self):
        found = self._cells
        if not found:
            QMessageBox.warning(self, "No data", "No measurements to plot.")
            return

        try:
            import matplotlib  # noqa: F401 — availability check only
        except ImportError:
            QMessageBox.critical(self, "Error",
                                 "matplotlib is required. Install with: pip install matplotlib")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Select Output Directory for Plots")
        if not out_dir:
            return

        from pybud import Plots
        saved = Plots.export_cell_plots(found, pybud.time_step, pybud.time_unit, out_dir,
                                        img=pybud.img, bf_channel=pybud.bf_channel,
                                        pixel_size=pybud.pixel_size)
        QMessageBox.information(self, "Done", f"Exported {saved} plot(s) to:\n{out_dir}")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyBud Measurement Tool")
        self.setGeometry(100, 100, 1920, 1080)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_splitter = QSplitter(Qt.Vertical)
        top_splitter = QSplitter(Qt.Horizontal)

        self.settings = Settings()
        top_splitter.addWidget(self.settings)

        self.image_viewer = ImageViewer(self.settings)
        top_splitter.addWidget(self.image_viewer)

        self.settings.settings_changed.connect(self.image_viewer.refresh)

        main_splitter.addWidget(top_splitter)

        self.measurement_table = MeasurementTable()
        main_splitter.addWidget(self.measurement_table)

        self.image_viewer.measurement_started.connect(self.status_measuring)
        self.image_viewer.status_message.connect(lambda msg: self.statusBar.showMessage(msg))
        self.image_viewer.measurements_changed.connect(self.measurement_table.append_measurements)
        self.image_viewer.measurements_changed.connect(self.image_viewer.update)
        self.measurement_table.cell_selected.connect(self.image_viewer.select_cell)
        self.settings.settings_changed.connect(self.measurement_table.redraw)
        self.settings.reset_requested.connect(self.measurement_table.clear_table)
        # Order matters: contrast must be cleared before the full reinit below
        # recomputes auto-contrast ranges for the newly loaded image.
        self.settings.image_loaded.connect(self.image_viewer.reset_contrast)
        self.settings.image_loaded.connect(self.image_viewer.update)

        layout = QVBoxLayout(central_widget)
        layout.addWidget(main_splitter)

        main_splitter.setSizes([400, 200])
        top_splitter.setSizes([200, 800])

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

    def status_measuring(self):
        self.statusBar.showMessage("Fitting Cells...")


if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    window = MainWindow()
    window.setWindowIcon(QIcon("images/icon.png"))
    window.show()
    sys.exit(app.exec_())
