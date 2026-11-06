"""
gui/main_window.py — top-level window: wires the Settings panel, image
viewer, and measurement table together, plus the status bar and its
tracking-progress indicator.
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter, QStatusBar, QProgressBar,
)

from .settings_panel import Settings
from .image_viewer import ImageViewer
from .measurement_table import MeasurementTable


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

        # Floor the settings panel's height at its natural (non-Advanced)
        # content size, so it never needs its own scrollbar at the default
        # window size regardless of screen/title-bar chrome -- QSplitter
        # scales setSizes() proportionally to whatever space is actually
        # available, so a fixed pixel guess there isn't reliable, but it
        # does always respect a child widget's minimum size. Advanced
        # Settings (collapsed by default) can still make it scroll once
        # expanded, same as before.
        self.settings.setMinimumHeight(self.settings.content_height_hint() + 20)
        main_splitter.setSizes([850, 250])
        top_splitter.setSizes([200, 800])

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

        # Tracking many seed points at once gives no visual feedback on how
        # far along it is otherwise -- shown as a permanent widget at the
        # right of the status bar, visible only while Measure/Auto-Detect/
        # Re-fit is actually running.
        self.tracking_progress_bar = QProgressBar()
        self.tracking_progress_bar.setMaximumWidth(240)
        self.tracking_progress_bar.setTextVisible(True)
        self.tracking_progress_bar.setVisible(False)
        self.statusBar.addPermanentWidget(self.tracking_progress_bar)

        self.image_viewer.tracking_started.connect(self._on_tracking_started)
        self.image_viewer.progress_updated.connect(self._on_tracking_progress)
        self.image_viewer.measurements_changed.connect(self._on_tracking_finished)

    def status_measuring(self):
        self.statusBar.showMessage("Fitting Cells...")

    def _on_tracking_started(self):
        self.tracking_progress_bar.setRange(0, 0)   # indeterminate until the first update arrives
        self.tracking_progress_bar.setFormat("Tracking…")
        self.tracking_progress_bar.setVisible(True)

    def _on_tracking_progress(self, done, total):
        if total <= 0:
            return
        self.tracking_progress_bar.setRange(0, total)
        self.tracking_progress_bar.setValue(min(done, total))
        self.tracking_progress_bar.setFormat(f"Tracking… {done}/{total} frames")

    def _on_tracking_finished(self):
        self.tracking_progress_bar.setVisible(False)

