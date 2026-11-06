"""
gui/settings_panel.py — the left-hand Settings panel: File/Image/Time/Cell
Fitting groups, the collapsible Advanced Settings (Display, FL Channel
Offset, Tracking, Auto-Detection), and Export/Import Settings.
"""
import os
import json
import numpy as np
import tifffile as tiff
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QWidget, QScrollArea,
    QLineEdit, QPushButton, QFormLayout, QFileDialog, QMessageBox,
    QCheckBox, QDialog, QComboBox, QGroupBox, QSpinBox, QProgressBar,
)

from .state import pybud
from .columns import column_enabled
from .workers import ImageLoadWorker
from .dialogs import ColumnSettingsDialog


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
        layout.setSpacing(4)
        layout.setContentsMargins(4, 2, 4, 2)

        layout.addWidget(self._build_file_group())
        layout.addWidget(self._build_image_group())
        layout.addWidget(self._build_time_group())
        layout.addWidget(self._build_fitting_group())
        self._build_advanced_section(layout)
        self._build_action_buttons(layout)

        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)
        self._content_container = container

    def content_height_hint(self):
        """Natural height of all non-Advanced settings content -- used by
        MainWindow to size the splitter so this panel doesn't need its own
        scrollbar at the default window size (Advanced Settings, collapsed
        here, can still scroll once expanded)."""
        return self._content_container.sizeHint().height()

    def _build_file_group(self):
        file_group = QGroupBox("File")
        file_form = QFormLayout(file_group)
        file_form.setVerticalSpacing(3)
        self.file_path = QLineEdit()
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.browse_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_path)
        file_row.addWidget(self.browse_button)
        file_row.setContentsMargins(0, 0, 0, 0)
        file_w = QWidget()
        file_w.setLayout(file_row)
        file_form.addRow(file_w)

        # A separate, word-wrapping label for "Loading <filename>" instead of
        # cramming that text into the progress bar itself -- the left panel is
        # often narrow, and long filenames there got silently clipped.
        self.load_status_label = QLabel("")
        self.load_status_label.setWordWrap(True)
        self.load_status_label.setVisible(False)
        file_form.addRow(self.load_status_label)

        self.load_progress_bar = QProgressBar()
        self.load_progress_bar.setVisible(False)
        self.load_progress_bar.setTextVisible(True)
        file_form.addRow(self.load_progress_bar)

        self._load_worker = None
        return file_group

    def _build_image_group(self):
        image_group = QGroupBox("Image")
        image_form = QFormLayout(image_group)
        image_form.setVerticalSpacing(3)
        self.pixel_size_line = QLineEdit("0.0645")
        image_form.addRow("Pixel size (µm/px):", self.pixel_size_line)
        self.brightfield_channel_line = QLineEdit("0")
        image_form.addRow("Brightfield channel:", self.brightfield_channel_line)
        self.fluorescent_channel1_line = QLineEdit("1")
        image_form.addRow("FL channel 1:", self.fluorescent_channel1_line)
        self.fluorescent_channel2_line = QLineEdit("-1")
        image_form.addRow("FL channel 2 (-1 = none):", self.fluorescent_channel2_line)
        return image_group

    @staticmethod
    def _build_fl_offset_row(x_label):
        """One FL-channel offset row: X/Y pixel-offset spin boxes + Auto button,
        correcting a fixed chromatic/optical misalignment between that channel
        and the brightfield-fitted outlines. Returns (row_widget, x_spin, y_spin,
        auto_button) -- wired up by the caller, which knows which channel this is."""
        x_spin = QSpinBox()
        x_spin.setRange(-50, 50)
        x_spin.setFixedWidth(50)
        x_spin.setToolTip(
            "Pixels to shift this channel's measurement region relative to "
            "the brightfield-fitted outline (positive = right)."
        )
        y_spin = QSpinBox()
        y_spin.setRange(-50, 50)
        y_spin.setFixedWidth(50)
        y_spin.setToolTip(
            "Pixels to shift this channel's measurement region relative to "
            "the brightfield-fitted outline (positive = down)."
        )
        auto_button = QPushButton("Auto")
        auto_button.setToolTip(
            "Estimate the offset automatically: tries small pixel shifts and "
            "picks the one that best lines up already-fitted cell outlines "
            "with this channel's signal. Run Measure first so there's "
            "something to compare against, then Measure (or Re-fit Frame "
            "Range) again afterwards to apply the correction."
        )

        row_layout = QHBoxLayout()
        row_layout.addWidget(QLabel(x_label))
        row_layout.addWidget(x_spin)
        row_layout.addWidget(QLabel("Y:"))
        row_layout.addWidget(y_spin)
        row_layout.addWidget(auto_button)
        row_layout.addStretch()   # without this, Qt stretches the "Y:" label and Auto button instead
        row_layout.setContentsMargins(0, 0, 0, 0)

        row = QWidget()
        row.setLayout(row_layout)
        row.setVisible(False)   # shown once that channel is enabled, see _refresh_fl_offset_rows()
        return row, x_spin, y_spin, auto_button

    @staticmethod
    def _parse_channel_field(line_edit):
        """Lenient channel-index parse for live visibility toggling -- unlike
        get_input_value(), never pops up a validation dialog while the user
        is still mid-edit; just treats anything invalid as 'disabled' (-1)."""
        try:
            return int(line_edit.text())
        except (ValueError, TypeError):
            return -1

    def _refresh_fl_offset_rows(self):
        """Show/hide each FL offset row based on whether that channel is enabled
        (index >= 0), and sync its spin boxes to the stored offset."""
        for which, line_edit, row, x_spin, y_spin in (
            (1, self.fluorescent_channel1_line, self.fl1_offset_row, self.fl1_offset_x_spin, self.fl1_offset_y_spin),
            (2, self.fluorescent_channel2_line, self.fl2_offset_row, self.fl2_offset_x_spin, self.fl2_offset_y_spin),
        ):
            ch = self._parse_channel_field(line_edit)
            enabled = ch >= 0
            row.setVisible(enabled)
            if not enabled:
                continue
            dx, dy = pybud.fl_channel_offsets.get(ch, (0, 0))
            x_spin.blockSignals(True)
            y_spin.blockSignals(True)
            x_spin.setValue(dx)
            y_spin.setValue(dy)
            x_spin.blockSignals(False)
            y_spin.blockSignals(False)

    def _on_fl_offset_spin_changed(self, which):
        line_edit = self.fluorescent_channel1_line if which == 1 else self.fluorescent_channel2_line
        ch = self._parse_channel_field(line_edit)
        if ch < 0 or pybud.img is None:
            return
        x_spin, y_spin = (self.fl1_offset_x_spin, self.fl1_offset_y_spin) if which == 1 \
            else (self.fl2_offset_x_spin, self.fl2_offset_y_spin)
        pybud.fl_channel_offsets[ch] = (x_spin.value(), y_spin.value())
        self.settings_changed.emit()

    def _build_time_group(self):
        time_group = QGroupBox("Time")
        time_form = QFormLayout(time_group)
        time_form.setVerticalSpacing(3)
        self.time_step_line = QLineEdit("1.0")
        time_form.addRow("Time step (s):", self.time_step_line)
        return time_group

    def _build_fitting_group(self):
        fitting_group = QGroupBox("Cell Fitting")
        fitting_form = QFormLayout(fitting_group)
        fitting_form.setVerticalSpacing(3)
        self.cell_radius_line = QLineEdit("4")
        self.cell_radius_px_label = QLabel("")
        fitting_form.addRow("Max cell radius (µm):", self._with_px_hint(self.cell_radius_line, self.cell_radius_px_label))

        self.tracking_radius_line = QLineEdit("")
        self.tracking_radius_line.setPlaceholderText("same as above")
        self.tracking_radius_line.setToolTip(
            "Per-frame search radius used while tracking an already-found cell, "
            "independent of 'Max cell radius'. Leave blank to use the same "
            "value. A smaller value tightens the search window around the "
            "previous frame's position, which can reduce false-positive edges "
            "from neighbouring cells or debris in a cluttered image -- but it "
            "also caps how far the edge detector can look, so don't set it "
            "smaller than the cell's actual radius."
        )
        self.tracking_radius_px_label = QLabel("")
        fitting_form.addRow("Tracking search radius (µm):",
                             self._with_px_hint(self.tracking_radius_line, self.tracking_radius_px_label))

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

        # Off by default -- BudJ doesn't model a growing search radius, so a
        # user porting settings/expectations from BudJ gets identical
        # behaviour until they explicitly opt in.
        self.allow_growth_checkbox = QCheckBox("Allow cell radius growth over time")
        self.allow_growth_checkbox.setChecked(False)
        self.allow_growth_checkbox.setToolTip(
            "Widens the per-frame search radius (and how much the fitted radius "
            "is allowed to change frame-to-frame) as a budding cell grows, so "
            "tracking doesn't lose it once it outgrows 'Max cell radius'."
        )
        fitting_form.addRow(self.allow_growth_checkbox)
        self.max_size_change_line = QLineEdit("50")
        fitting_form.addRow("Max cell radius increase (%):", self.max_size_change_line)
        self.max_growth_line = QLineEdit("5")
        fitting_form.addRow("Max cell radius increase per frame (%):", self.max_growth_line)
        self.allow_growth_checkbox.stateChanged.connect(self._on_allow_growth_toggled)
        self._on_allow_growth_toggled(Qt.Unchecked)

        self.cell_radius_line.textChanged.connect(self._refresh_radius_px_hints)
        self.tracking_radius_line.textChanged.connect(self._refresh_radius_px_hints)
        self.pixel_size_line.textChanged.connect(self._refresh_radius_px_hints)
        self._refresh_radius_px_hints()
        return fitting_group

    @staticmethod
    def _with_px_hint(line_edit, px_label):
        """Wrap a µm QLineEdit with a small label showing its pixel-count
        equivalent (kept in sync by _refresh_radius_px_hints) -- raw pixel
        counts are what the tracking/fitting algorithms actually use, and a
        µm value alone doesn't tell you that without doing the division yourself."""
        row = QHBoxLayout()
        row.addWidget(line_edit)
        row.addWidget(px_label)
        row.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(row)
        return w

    def _refresh_radius_px_hints(self):
        try:
            pixel_size = float(self.pixel_size_line.text())
        except (ValueError, TypeError):
            pixel_size = None

        def px_text(line_edit):
            if not pixel_size or pixel_size <= 0:
                return ""
            try:
                um = float(line_edit.text())
            except (ValueError, TypeError):
                return ""
            return f"≈ {int(np.ceil(um / pixel_size))} px"

        self.cell_radius_px_label.setText(px_text(self.cell_radius_line))
        self.tracking_radius_px_label.setText(
            px_text(self.tracking_radius_line) if self.tracking_radius_line.text().strip() else ""
        )

    def _on_allow_growth_toggled(self, state):
        enabled = (state == Qt.Checked)
        self.max_size_change_line.setEnabled(enabled)
        self.max_growth_line.setEnabled(enabled)

    def _build_advanced_section(self, layout):
        """
        Collapsible Tracking + Auto-Detection groups, appended directly to
        `layout`. These are tuned rarely (Tracking) or only matter for
        Auto-Detect + Measure (Hough), so they're tucked away by default to
        keep the commonly-used settings above the fold.
        """
        self.advanced_toggle = QPushButton("▸ Advanced Settings (Display, Tracking, Auto-Detection)")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setStyleSheet("text-align: left; padding-left: 4px;")
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        self.advanced_container = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_container)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)

        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout(display_group)
        display_layout.setSpacing(3)
        self.show_scale_bar_checkbox = QCheckBox("Show Scale Bar")
        self.show_scale_bar_checkbox.setChecked(True)
        self.show_scale_bar_checkbox.setToolTip(
            "Show the scale bar in the bottom-right corner of the image."
        )
        display_layout.addWidget(self.show_scale_bar_checkbox)
        self.show_masks_checkbox = QCheckBox("Show Masks")
        self.show_masks_checkbox.setChecked(True)
        self.show_masks_checkbox.setToolTip(
            "Fitted-ellipse outlines. Turn off to see raw edge points without "
            "the mask drawn over them."
        )
        display_layout.addWidget(self.show_masks_checkbox)
        self.edge_points_checkbox = QCheckBox("Show Edge Points")
        self.edge_points_checkbox.setToolTip(
            "Overlay the raw detected boundary points (red dots) used to fit each cell's ellipse."
        )
        display_layout.addWidget(self.edge_points_checkbox)
        advanced_layout.addWidget(display_group)

        # FL channel offsets live here (not in the image panel) so switching
        # the displayed channel there never shows/hides a row and shifts the
        # image underneath it, and here rather than always-on-screen so they
        # don't add to the clutter of commonly-used settings. Both channels
        # get their own row, independent of which channel happens to be on
        # screen right now.
        fl_offset_group = QGroupBox("FL Channel Offset")
        fl_offset_layout = QVBoxLayout(fl_offset_group)
        fl_offset_layout.setSpacing(3)
        self.fl1_offset_row, self.fl1_offset_x_spin, self.fl1_offset_y_spin, self.fl1_offset_auto_button = \
            self._build_fl_offset_row("FL1 Offset (px) — X:")
        fl_offset_layout.addWidget(self.fl1_offset_row)
        self.fl1_offset_x_spin.valueChanged.connect(lambda _v: self._on_fl_offset_spin_changed(1))
        self.fl1_offset_y_spin.valueChanged.connect(lambda _v: self._on_fl_offset_spin_changed(1))

        self.fl2_offset_row, self.fl2_offset_x_spin, self.fl2_offset_y_spin, self.fl2_offset_auto_button = \
            self._build_fl_offset_row("FL2 Offset (px) — X:")
        fl_offset_layout.addWidget(self.fl2_offset_row)
        self.fl2_offset_x_spin.valueChanged.connect(lambda _v: self._on_fl_offset_spin_changed(2))
        self.fl2_offset_y_spin.valueChanged.connect(lambda _v: self._on_fl_offset_spin_changed(2))

        self.fluorescent_channel1_line.textChanged.connect(self._refresh_fl_offset_rows)
        self.fluorescent_channel2_line.textChanged.connect(self._refresh_fl_offset_rows)
        self._refresh_fl_offset_rows()
        advanced_layout.addWidget(fl_offset_group)

        tracking_group = QGroupBox("Tracking")
        tracking_form = QFormLayout(tracking_group)
        tracking_form.setVerticalSpacing(3)
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
        detect_form.setVerticalSpacing(3)
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
        self.advanced_toggle.setText(f"{arrow} Advanced Settings (Display, Tracking, Auto-Detection)")

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

        tracking_radius_text = self.tracking_radius_line.text().strip()
        if tracking_radius_text:
            tracking_search_radius = self.get_input_value(self.tracking_radius_line, float,
                                                            "Tracking Search Radius", min_value=0)
            if tracking_search_radius is None: return None
        else:
            tracking_search_radius = 0.0   # blank -> use Max cell radius
        settings["tracking_search_radius"] = tracking_search_radius

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

        if self.allow_growth_checkbox.isChecked():
            max_size_change = self.get_input_value(self.max_size_change_line, float, "Max Cell Radius Increase")
            if max_size_change is None: return None
            settings["max_size_change"] = max(0.0, max_size_change) / 100.0  # % -> fraction

            max_growth_per_frame = self.get_input_value(self.max_growth_line, float,
                                                          "Max Cell Radius Increase Per Frame", min_value=0)
            if max_growth_per_frame is None: return None
            settings["max_growth_per_frame"] = max_growth_per_frame / 100.0  # % -> fraction
        else:
            # Toggle off -- BudJ-faithful behaviour (fixed search radius, no
            # size-change rejection), regardless of whatever's typed into the
            # (disabled) fields above.
            settings["max_size_change"] = 0.0
            settings["max_growth_per_frame"] = 0.0

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
        if self._load_worker is not None:
            return
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Select Measurement File", "",
            "TIF Files (*.tif *.tiff *.TIF *.TIFF);;All Files (*)"
        )
        if file_name:
            self.file_path.setText(file_name)
            self.load_image(file_name)

    # The "Time step" field is always in seconds (that's the unit PyBud computes
    # and displays time in throughout), so any metadata value found in a
    # different unit must be converted before it's dropped into that field.
    _SECONDS_PER_TIME_UNIT = {
        'ms': 0.001, 'millisecond': 0.001, 'milliseconds': 0.001,
        's': 1.0, 'sec': 1.0, 'secs': 1.0, 'second': 1.0, 'seconds': 1.0,
        'min': 60.0, 'mins': 60.0, 'minute': 60.0, 'minutes': 60.0,
        'h': 3600.0, 'hr': 3600.0, 'hrs': 3600.0, 'hour': 3600.0, 'hours': 3600.0,
    }

    def _read_tif_metadata(self, image_path):
        """Return dict with any of: pixel_size, time_step extracted from TIF tags.
        time_step is always converted to seconds, since that's the only unit the
        "Time step (s)" field (and the rest of PyBud) understands."""
        meta = {}
        try:
            with tiff.TiffFile(image_path) as tf:
                # --- ImageJ metadata -------------------------------------------
                # ImageJ's 'finterval' tag is defined to always be in seconds,
                # regardless of what 'tunit' says (that's only a display hint
                # for ImageJ's own dialogs) -- no conversion needed here.
                ij = tf.imagej_metadata or {}
                fi = ij.get('finterval')
                if fi is not None:
                    meta['time_step'] = float(fi)

                # --- OME-XML ---------------------------------------------------
                if tf.ome_metadata and 'time_step' not in meta:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(tf.ome_metadata)
                        pixels = root.find('.//{*}Pixels')
                        if pixels is not None:
                            ti = pixels.get('TimeIncrement')
                            if ti:
                                tiu = (pixels.get('TimeIncrementUnit') or 's').strip().lower()
                                seconds_per_unit = self._SECONDS_PER_TIME_UNIT.get(tiu, 1.0)
                                meta['time_step'] = float(ti) * seconds_per_unit
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
        """
        Reads the TIF off the GUI thread via ImageLoadWorker and shows real
        load progress -- large movies used to block the UI with no feedback
        at all while tiff.imread() ran synchronously.
        """
        if self._load_worker is not None:
            return
        self.reset_requested.emit()
        self.browse_button.setEnabled(False)
        self.load_status_label.setText(f"Loading {os.path.basename(image_path)}…")
        self.load_status_label.setToolTip(image_path)
        self.load_status_label.setVisible(True)
        self.load_progress_bar.setVisible(True)
        self.load_progress_bar.setRange(0, 0)   # indeterminate until the first progress signal
        self.load_progress_bar.setFormat("%p%")

        self._load_worker = ImageLoadWorker(image_path, self._read_tif_metadata)
        self._load_worker.progress.connect(self._on_load_progress)
        self._load_worker.finished.connect(self._on_image_load_finished)
        self._load_worker.start()

    def _on_load_progress(self, done, total):
        if total <= 0:
            self.load_progress_bar.setRange(0, 0)   # indeterminate: unusual file layout, no per-page progress
        else:
            self.load_progress_bar.setRange(0, total)
            self.load_progress_bar.setValue(done)
            self.load_progress_bar.setFormat(f"{done}/{total}")

    def _on_image_load_finished(self, tif_data, meta, error):
        self.load_progress_bar.setVisible(False)
        self.load_status_label.setVisible(False)
        self.browse_button.setEnabled(True)
        self._load_worker = None

        if error:
            QMessageBox.critical(self, "Error", f"Failed to load image: {error}")
            return

        # Auto-populate fields from embedded TIF metadata
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
        pybud.tracking_search_radius = settings["tracking_search_radius"]
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
        tracking_search_radius = settings.get("tracking_search_radius", 0.0)
        self.tracking_radius_line.setText(str(tracking_search_radius) if tracking_search_radius > 0 else "")
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
        # A settings file from before this toggle existed always has an explicit
        # (typically non-zero) value for these -- enable the toggle so importing
        # it reproduces the same effective behaviour. A truly absent key (or an
        # explicit 0) keeps the new off-by-default behaviour.
        self.allow_growth_checkbox.setChecked(
            settings.get("max_size_change", 0.0) > 0 or settings.get("max_growth_per_frame", 0.0) > 0
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

