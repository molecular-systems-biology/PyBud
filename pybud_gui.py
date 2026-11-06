"""
pybud_gui.py — PyQt5 GUI entry point for PyBud.

The implementation lives in the gui/ package, split by responsibility:

    gui/state.py             the shared PyBud instance
    gui/columns.py           results-table column definitions
    gui/workers.py           QThread workers (tracking, FL offset, TIF load)
    gui/dialogs.py           ColumnSettingsDialog, ExportPlotsDialog
    gui/help_dialog.py        HelpDialog
    gui/image_viewer.py        ContrastHistogram, ClickableImageLabel, ImageViewer
    gui/settings_panel.py      Settings
    gui/measurement_table.py   MeasurementTable
    gui/main_window.py          MainWindow

This file re-exports the commonly used names (handy for interactive/
scripted use, e.g. `import pybud_gui as g; g.pybud...`) and is the script
you run directly to launch the app.
"""
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon

from gui.state import pybud   # noqa: F401 — re-exported for interactive/scripted use
from gui.columns import COLUMN_DEFS, column_enabled, get_cell_values   # noqa: F401
from gui.workers import (   # noqa: F401
    FitCellsWorker, RefitRangeWorker, AutoDetectWorker, FLOffsetWorker, ImageLoadWorker,
)
from gui.dialogs import ColumnSettingsDialog, ExportPlotsDialog   # noqa: F401
from gui.help_dialog import HelpDialog   # noqa: F401
from gui.image_viewer import ContrastHistogram, ClickableImageLabel, ImageViewer   # noqa: F401
from gui.settings_panel import Settings   # noqa: F401
from gui.measurement_table import MeasurementTable   # noqa: F401
from gui.main_window import MainWindow

if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    window = MainWindow()
    window.setWindowIcon(QIcon("images/icon.png"))
    window.show()
    sys.exit(app.exec_())
