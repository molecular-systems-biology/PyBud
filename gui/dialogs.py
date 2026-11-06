"""
gui/dialogs.py — small standalone dialogs used by the settings panel
(ColumnSettingsDialog) and the measurement table (ExportPlotsDialog).
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QWidget, QScrollArea, QLineEdit,
    QCheckBox, QDialog, QDialogButtonBox,
)

from .columns import COLUMN_DEFS, column_enabled


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


class ExportPlotsDialog(QDialog):
    """Lets the user name the exported files and choose which quantities to
    plot, instead of always writing a fixed X/Y/Major/Minor 2x2 figure named
    cell_<id>.png."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Plots")

        from pybud import Plots

        layout = QVBoxLayout(self)

        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("Filename prefix:"))
        self.prefix_line = QLineEdit("cell")
        self.prefix_line.setToolTip("Each file is saved as \"<prefix>_<cell id>.png\"")
        prefix_row.addWidget(self.prefix_line)
        layout.addLayout(prefix_row)

        layout.addWidget(QLabel("Quantities to plot (one panel each):"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignTop)

        self.checkboxes = {}
        for key, label in Plots.available_quantities():
            cb = QCheckBox(label)
            cb.setChecked(key in Plots.DEFAULT_QUANTITIES)
            self.checkboxes[key] = cb
            container_layout.addWidget(cb)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(320, 420)

    def get_prefix(self):
        prefix = self.prefix_line.text().strip().replace("/", "_").replace("\\", "_")
        return prefix or "cell"

    def get_quantities(self):
        return [key for key, cb in self.checkboxes.items() if cb.isChecked()]
