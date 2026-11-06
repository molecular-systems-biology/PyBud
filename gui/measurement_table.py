"""
gui/measurement_table.py — the results table at the bottom of the window:
accumulates fitted-cell rows across runs, and the Save/Copy/Export/Delete/
Clear actions beneath it.
"""
import csv
import numpy as np
import roifile
from openpyxl import Workbook
from PyQt5.QtCore import Qt, pyqtSignal, QMimeData
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QFileDialog, QMessageBox,
    QTableWidget, QAbstractItemView, QHeaderView, QTableWidgetItem,
    QApplication, QDialog, QShortcut,
)
from PyQt5.QtGui import QKeySequence

from .state import pybud
from .columns import COLUMN_DEFS, column_enabled, get_cell_values
from .dialogs import ExportPlotsDialog


class MeasurementTable(QWidget):
    cell_selected = pyqtSignal(int, int, float, float)   # frame, cell_id, x, y

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

        # QTableWidget has no built-in Delete/Ctrl+C/Ctrl+A handling -- Delete
        # did nothing, Ctrl+C fell through to whatever single-cell edit
        # widget happened to have focus instead of copying the selection, and
        # Ctrl+A did nothing either.
        delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self.table)
        delete_shortcut.setContext(Qt.WidgetShortcut)
        delete_shortcut.activated.connect(self.delete_selected_rows)
        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.setContext(Qt.WidgetShortcut)
        copy_shortcut.activated.connect(self.copy_measurements)
        select_all_shortcut = QShortcut(QKeySequence.SelectAll, self.table)
        select_all_shortcut.setContext(Qt.WidgetShortcut)
        select_all_shortcut.activated.connect(self.table.selectAll)
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
        """Jump the image to the (first) selected row's frame, pan to its cell, and highlight it."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._cells):
            cell = self._cells[row]
            self.cell_selected.emit(cell.frame, cell.id,
                                     cell.ellipse.get_x_center(), cell.ellipse.get_y_center())

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

    def _table_rows(self, rows=None):
        if rows is None:
            rows = range(self.table.rowCount())
        return [
            [(self.table.item(row, col).text() if self.table.item(row, col) else '')
             for col in range(self.table.columnCount())]
            for row in rows
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
        """Copy the selected rows (tab-separated, with a header row) to the
        clipboard for pasting into Excel. Falls back to every row when
        nothing is selected -- e.g. clicking the Copy button, or pressing
        Ctrl+C with no selection -- so Ctrl+A then Ctrl+C copies everything."""
        clipboard = QApplication.clipboard()
        mime_data = QMimeData()

        selected_rows = sorted(idx.row() for idx in self.table.selectionModel().selectedRows())
        rows = self._table_rows(selected_rows) if selected_rows else self._table_rows()

        lines = ["\t".join(self._table_headers())]
        lines.extend("\t".join(row) for row in rows)

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

        dialog = ExportPlotsDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        quantities = dialog.get_quantities()
        if not quantities:
            QMessageBox.warning(self, "No quantities selected", "Select at least one quantity to plot.")
            return
        prefix = dialog.get_prefix()

        out_dir = QFileDialog.getExistingDirectory(self, "Select Output Directory for Plots")
        if not out_dir:
            return

        from pybud import Plots
        saved = Plots.export_cell_plots(found, pybud.time_step, pybud.time_unit, out_dir,
                                        img=pybud.img, bf_channel=pybud.bf_channel,
                                        pixel_size=pybud.pixel_size,
                                        quantities=quantities, filename_prefix=prefix)
        QMessageBox.information(self, "Done", f"Exported {saved} plot(s) to:\n{out_dir}")

