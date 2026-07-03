import numpy as np
import tifffile as tiff
import csv
from openpyxl import Workbook
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QPointF, QMimeData
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QIcon
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QLabel, QWidget, QSplitter, QScrollArea,
    QScrollBar, QLineEdit, QPushButton, QHBoxLayout, QFormLayout, QFileDialog,
    QTableWidget, QAbstractItemView, QHeaderView, QTableWidgetItem, QMainWindow,
    QStatusBar, QMessageBox, QCheckBox, QDialog, QDialogButtonBox, QFrame,
    QComboBox, QGroupBox
)
from pybud import PyBud
import roifile
import json
import os

# ---------------------------------------------------------------------------
# Column definitions: (key, display_name, enabled_by_default)
# ---------------------------------------------------------------------------
COLUMN_DEFS = [
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
# Worker thread
# ---------------------------------------------------------------------------
class FitCellsWorker(QThread):
    finished = pyqtSignal()
    frame_processed = pyqtSignal(int)

    def __init__(self):
        super().__init__()

    def run(self):
        try:
            pybud.fit_cells(self._frame_processed)
        except Exception as e:
            print(f"Error during fit_cells: {e}")
        finally:
            self.finished.emit()

    def _frame_processed(self, frame_number):
        self.frame_processed.emit(frame_number)

    def stop(self):
        pybud.stop()


# ---------------------------------------------------------------------------
# Auto-detect worker
# ---------------------------------------------------------------------------
class AutoDetectWorker(QThread):
    """
    Qt worker that runs AutoDetect.detect() + pybud.fit_cells() off the GUI thread.
    All detection logic lives in pybud_autodetect.AutoDetect.
    """
    finished        = pyqtSignal()
    frame_processed = pyqtSignal(int)
    status_update   = pyqtSignal(str)

    def run(self):
        if pybud.img is None:
            self.finished.emit()
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
            self.finished.emit()
            return

        try:
            pybud._should_run = True
            pybud.clear()

            self.status_update.emit("Phase 1/2 — detecting cells …")
            AutoDetect().detect(pybud, frame_callback=self.frame_processed.emit)

            n_seeds = sum(len(v) for v in pybud.selections.values())
            self.status_update.emit(f"Phase 2/2 — tracking {n_seeds} seed(s) …")
            pybud.fit_cells(self._on_frame)
        except Exception as e:
            print(f"Error during auto-detect: {e}")
        finally:
            self.finished.emit()

    def _on_frame(self, frame):
        self.frame_processed.emit(frame)

    def stop(self):
        pybud.stop()


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

    def set_frame(self, frame):
        self.frame = frame
        self.update_image_display()

    def update_image_display(self):
        if pybud.img is None:
            self.setText("No image")
            return

        if self.frame >= pybud.img.shape[0]:
            self.frame = pybud.img.shape[0] - 1

        n_channels = pybud.img.shape[1]
        ch = self.display_channel if (self.display_channel is not None and
                                       self.display_channel < n_channels) else pybud.bf_channel
        if ch >= n_channels:
            ch = 0

        frame = pybud.img[self.frame, ch]

        if frame.dtype == np.uint8:
            height, width = frame.shape
            image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_Grayscale8)
        elif frame.dtype == np.uint16:
            frame_8bit = (frame / 256).astype(np.uint8)
            height, width = frame_8bit.shape
            image = QImage(frame_8bit.data, width, height, frame_8bit.strides[0], QImage.Format_Grayscale8)
        elif frame.dtype in (np.float32, np.float64):
            lo, hi = frame.min(), frame.max()
            frame_8bit = ((frame - lo) / max(hi - lo, 1e-9) * 255).astype(np.uint8)
            height, width = frame_8bit.shape
            image = QImage(frame_8bit.data, width, height, frame_8bit.strides[0], QImage.Format_Grayscale8)
        else:
            self.setText("Unsupported image format")
            return

        pixmap = QPixmap.fromImage(image)
        pixmap = pixmap.scaled(
            int(pixmap.width() * self.scale_factor),
            int(pixmap.height() * self.scale_factor),
            Qt.KeepAspectRatio
        )

        painter = QPainter(pixmap)

        # Draw green crosses for selections
        pen = QPen(QColor(0, 255, 0), 2)
        painter.setPen(pen)
        if self.frame in pybud.selections:
            for x, y in pybud.selections[self.frame]:
                x = int(x * self.scale_factor)
                y = int(y * self.scale_factor)
                painter.drawLine(x - 5, y - 5, x + 5, y + 5)
                painter.drawLine(x - 5, y + 5, x + 5, y - 5)

        # Draw fitted cells
        for cell in pybud.cells:
            if cell.frame == self.frame:

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

        # Draw mother-daughter lines (orange dashed)
        mother_ids = getattr(pybud, 'mother_ids', {})
        if mother_ids:
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

        # Draw scale bar (bottom-right corner)
        if pybud.pixel_size > 0:
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

        painter.end()
        self.setPixmap(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            if event.modifiers() & Qt.ShiftModifier:
                if self.scale_factor > 0.1:
                    self.scale_factor *= 0.75
                    self.update_image_display()
            elif self.scale_factor < 10:
                self.scale_factor *= 1.25
                self.update_image_display()

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

    def __init__(self):
        super().__init__()

        self.image_label = ClickableImageLabel()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.image_label)

        self.frame_number_label = QLabel("")
        self.edge_points_checkbox = QCheckBox("Show Edge Points")
        self.edge_points_checkbox.stateChanged.connect(self.show_edge_points)

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(130)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)

        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.setMinimum(0)
        self.scrollbar.valueChanged.connect(self.update_frame)

        self.auto_detect_button = QPushButton("Auto-Detect & Measure")
        self.auto_detect_button.clicked.connect(self.auto_detect_measure)

        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self.measure)

        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self.stop)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.auto_detect_button)
        button_layout.addWidget(self.measure_button)
        button_layout.addWidget(stop_button)

        self._channel_label = QLabel("Channel:")
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.frame_number_label)
        top_layout.addWidget(self._channel_label)
        top_layout.addWidget(self.channel_combo)
        top_layout.addStretch()
        top_layout.addWidget(self.edge_points_checkbox)

        layout = QVBoxLayout()
        layout.addLayout(top_layout)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.scrollbar)
        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.worker = None

    def update(self):
        if pybud.img is not None:
            self.scrollbar.setMaximum(pybud.img.shape[0] - 1)
            self._rebuild_channel_combo()
            self.update_frame(0)

    def _rebuild_channel_combo(self):
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        n_ch = pybud.img.shape[1] if pybud.img is not None else 0
        self.channel_combo.addItem("Brightfield", pybud.bf_channel)
        for i, fl_ch in enumerate(pybud.fl_channels):
            if 0 <= fl_ch < n_ch and fl_ch != pybud.bf_channel:
                self.channel_combo.addItem(f"FL Channel {i + 1}", fl_ch)
        self.channel_combo.blockSignals(False)
        self.image_label.display_channel = pybud.bf_channel
        # Hide the combo (and its label) when there is nothing to switch to
        has_choice = self.channel_combo.count() > 1
        self.channel_combo.setVisible(has_choice)
        self._channel_label.setVisible(has_choice)

    def _on_channel_changed(self, _idx):
        ch = self.channel_combo.currentData()
        if ch is not None:
            self.image_label.display_channel = ch
            self.image_label.update_image_display()

    def update_frame(self, frame=0):
        self.image_label.set_frame(frame)
        self.frame_number_label.setText(f"Frame: {frame + 1}")
        self.scrollbar.setValue(frame)

    def select_cell(self, frame, cell_id):
        self.image_label.highlighted_cell_id = cell_id
        self.update_frame(frame)

    def auto_detect_measure(self):
        if self.worker is not None or pybud.img is None:
            return
        self._set_buttons_enabled(False)
        self.worker = AutoDetectWorker()
        self.worker.finished.connect(self.on_fit_cells_finished)
        self.worker.frame_processed.connect(self.update_frame)
        self.worker.status_update.connect(self._set_status)
        self.worker.start()
        self.measurement_started.emit()

    def measure(self):
        if self.worker is not None or pybud.img is None:
            return
        self._set_buttons_enabled(False)
        self.worker = FitCellsWorker()
        self.worker.finished.connect(self.on_fit_cells_finished)
        self.worker.frame_processed.connect(self.update_frame)
        self.worker.start()
        self.measurement_started.emit()

    def _set_buttons_enabled(self, enabled: bool):
        self.measure_button.setEnabled(enabled)
        self.auto_detect_button.setEnabled(enabled)

    def _set_status(self, msg: str):
        self.status_message.emit(msg)

    def show_edge_points(self, state):
        self.image_label.show_edge_points = (state == Qt.Checked)
        self.image_label.update_image_display()

    def on_fit_cells_finished(self):
        self.measurements_changed.emit()
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

        # ── File ──────────────────────────────────────────────────────
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
        layout.addWidget(file_group)

        # ── Image ─────────────────────────────────────────────────────
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
        layout.addWidget(image_group)

        # ── Time ──────────────────────────────────────────────────────
        time_group = QGroupBox("Time")
        time_form = QFormLayout(time_group)
        self.time_step_line = QLineEdit("1.0")
        time_form.addRow("Time step (s):", self.time_step_line)
        layout.addWidget(time_group)

        # ── Cell Fitting ──────────────────────────────────────────────
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
        layout.addWidget(fitting_group)

        # ── Tracking ──────────────────────────────────────────────────
        tracking_group = QGroupBox("Tracking")
        tracking_form = QFormLayout(tracking_group)
        self.max_size_change_line = QLineEdit("50")
        tracking_form.addRow("Max size change (%):", self.max_size_change_line)
        self.max_gap_line = QLineEdit("1")
        tracking_form.addRow("Max frame gap:", self.max_gap_line)
        self.overlap_threshold_line = QLineEdit("10")
        tracking_form.addRow("Max overlap discard (%):", self.overlap_threshold_line)
        self.bud_distance_line = QLineEdit("1.2")
        tracking_form.addRow("Bud distance factor:", self.bud_distance_line)
        self.bud_size_ratio_line = QLineEdit("0.8")
        tracking_form.addRow("Bud size ratio:", self.bud_size_ratio_line)
        layout.addWidget(tracking_group)

        # ── Auto-Detection ────────────────────────────────────────────
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
        layout.addWidget(detect_group)

        # ── Buttons ───────────────────────────────────────────────────
        for label, slot in (
            ("Adjust Settings",         self.adjust_settings),
            ("Output Column Settings",  self.open_column_settings),
            ("Clear Selections",        self.clear_selections),
            ("Export Settings",         self.export_settings),
            ("Import Settings",         self.import_settings),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)

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
        self.settings_changed.emit()

    def adjust_settings(self):
        settings = self.get_settings_values()
        if settings is None:
            return

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
        pybud.max_gap               = settings["max_gap"]
        pybud.bud_distance_factor   = settings["bud_distance_factor"]
        pybud.bud_size_ratio        = settings["bud_size_ratio"]
        pybud.min_detect_radius_um  = settings["min_detect_radius_um"]
        pybud.max_detect_radius_um  = settings["max_detect_radius_um"]
        pybud.n_cells_max           = settings["n_cells_max"]
        pybud.hough_threshold       = settings["hough_threshold"]
        pybud.match_distance_um     = settings["match_distance_um"]

        self.settings_changed.emit()

    def clear_selections(self):
        pybud.stop()
        pybud.clear()
        self.settings_changed.emit()

    def export_settings(self):
        settings = self.get_settings_values()
        if settings is None:
            return

        settings["selections"] = pybud.selections
        settings["column_enabled"] = dict(column_enabled)

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

        self.settings_changed.emit()
        QMessageBox.information(self, "Success", "Settings imported successfully.")


# ---------------------------------------------------------------------------
# Measurement table
# ---------------------------------------------------------------------------
class MeasurementTable(QWidget):
    cell_selected = pyqtSignal(int, int)   # frame, cell_id

    def __init__(self):
        super().__init__()
        self._cells = []   # parallel list to table rows

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_row_clicked)
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
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addWidget(self.export_rois_button)
        button_layout.addWidget(self.export_plots_button)
        layout.addLayout(button_layout)

    def populate_table(self):
        found_cells = [cell for cell in pybud.cells if cell.cell_found]
        self._cells = found_cells

        # Build active column list; "time" header includes the unit
        active_cols = []
        for key, name, _ in COLUMN_DEFS:
            if column_enabled[key]:
                if key == "time":
                    name = f"Time ({pybud.time_unit})"
                active_cols.append((key, name))

        self.table.setColumnCount(len(active_cols))
        self.table.setHorizontalHeaderLabels([name for _, name in active_cols])
        self.table.setRowCount(len(found_cells))

        for row, cell in enumerate(found_cells):
            values = get_cell_values(cell)
            for col, (key, _) in enumerate(active_cols):
                self.table.setItem(row, col, QTableWidgetItem(values.get(key, "")))

    def _on_row_clicked(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if row < len(self._cells):
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

    def _save_as_csv(self, file_name):
        with open(file_name, mode='w', newline='') as f:
            writer = csv.writer(f)
            headers = [self.table.horizontalHeaderItem(i).text()
                       for i in range(self.table.columnCount())]
            writer.writerow(headers)
            for row in range(self.table.rowCount()):
                writer.writerow([
                    (self.table.item(row, col).text() if self.table.item(row, col) else '')
                    for col in range(self.table.columnCount())
                ])
        print(f"Data saved to {file_name}")

    def _save_as_excel(self, file_name):
        wb = Workbook()
        ws = wb.active
        headers = [self.table.horizontalHeaderItem(i).text()
                   for i in range(self.table.columnCount())]
        ws.append(headers)
        for row in range(self.table.rowCount()):
            ws.append([
                (self.table.item(row, col).text() if self.table.item(row, col) else '')
                for col in range(self.table.columnCount())
            ])
        wb.save(file_name)
        print(f"Data saved to {file_name}")

    def copy_measurements(self):
        clipboard = QApplication.clipboard()
        mime_data = QMimeData()

        headers = [self.table.horizontalHeaderItem(i).text()
                   for i in range(self.table.columnCount())]
        lines = ["\t".join(headers)]
        for row in range(self.table.rowCount()):
            lines.append("\t".join(
                (self.table.item(row, col).text() if self.table.item(row, col) else '')
                for col in range(self.table.columnCount())
            ))

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
        for i, cell in enumerate(pybud.cells):
            if cell.cell_found:
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
        found = [c for c in pybud.cells if c.cell_found]
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

        self.image_viewer = ImageViewer()
        top_splitter.addWidget(self.image_viewer)

        self.settings.settings_changed.connect(self.image_viewer.update)

        main_splitter.addWidget(top_splitter)

        self.measurement_table = MeasurementTable()
        main_splitter.addWidget(self.measurement_table)

        self.image_viewer.measurement_started.connect(self.status_measuring)
        self.image_viewer.status_message.connect(lambda msg: self.statusBar.showMessage(msg))
        self.image_viewer.measurements_changed.connect(self.measurement_table.populate_table)
        self.image_viewer.measurements_changed.connect(self.image_viewer.update)
        self.image_viewer.measurements_changed.connect(self.clear_status)
        self.measurement_table.cell_selected.connect(self.image_viewer.select_cell)
        self.settings.settings_changed.connect(self.measurement_table.populate_table)

        layout = QVBoxLayout(central_widget)
        layout.addWidget(main_splitter)

        main_splitter.setSizes([400, 200])
        top_splitter.setSizes([200, 800])

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

    def status_measuring(self):
        self.statusBar.showMessage("Fitting Cells...")

    def clear_status(self):
        self.statusBar.clearMessage()


if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    window = MainWindow()
    window.setWindowIcon(QIcon("images/icon.png"))
    window.show()
    sys.exit(app.exec_())
