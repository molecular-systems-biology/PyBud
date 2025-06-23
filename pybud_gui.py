import numpy as np
import tifffile as tiff
import csv
from openpyxl import Workbook
from PyQt5.QtCore import Qt, pyqtSignal, QThread,  QPointF, QMimeData
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QIcon, QFont
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QLabel, QWidget, QSplitter, QTextEdit, QScrollArea, QScrollBar, QLineEdit, QPushButton, QHBoxLayout, QFormLayout, QFileDialog, QTableWidget, QAbstractItemView, QHeaderView, QTableWidgetItem, QMainWindow, QStatusBar, QMessageBox, QCheckBox
from pybud import PyBud
import roifile
import json
import os

# then pybud object keeps track of all the settings
pybud = PyBud()

# Worker thread for running fit_cells in the background
class FitCellsWorker(QThread):
    finished = pyqtSignal()
    frame_processed = pyqtSignal(int)

    def __init__(self):
        super().__init__()

    def run(self):
        pybud.fit_cells(self._frame_processed)
        self.finished.emit()

    def _frame_processed(self, frame_number):
        self.frame_processed.emit(frame_number)

    def stop(self):
        pybud.stop()

class ClickableImageLabel(QLabel):
    def __init__(self, parent=None):
        super(ClickableImageLabel, self).__init__(parent)
        self.tif_data = None
        self.frame = 0
        self.scale_factor = 1
        self.offset_x = 0
        self.offset_y = 0
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.show_edge_points = False

    def set_frame(self, frame):
        self.frame = frame
        self.update_image_display()
    
    def update_image_display(self):
        if pybud.img is None:
            self.setText("No image")
            return
        
        # validate values
        if self.frame >= pybud.img.shape[0]: self.frame = pybud.img.shape[0] - 1
        if pybud.bf_channel >= pybud.img.shape[1]: pybud.bf_channel = 0

        frame = pybud.img[self.frame, pybud.bf_channel]

        if frame.dtype == np.uint8:
            height, width = frame.shape
            image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_Grayscale8)
        elif frame.dtype == np.uint16:
            frame_8bit = (frame / 256).astype(np.uint8)
            height, width = frame_8bit.shape
            image = QImage(frame_8bit.data, width, height, frame_8bit.strides[0], QImage.Format_Grayscale8)
        else:
            self.setText("Unsupported image format")
            return
               
        # Convert to a pixmap for display
        pixmap = QPixmap.fromImage(image)
        pixmap = pixmap.scaled(int(pixmap.width() * self.scale_factor), int(pixmap.height() * self.scale_factor), Qt.KeepAspectRatio)

        # Draw green crosses for selections instead of red circles
        painter = QPainter(pixmap)
        pen = QPen(QColor(0, 255, 0), 2)  # Green color for the crosses
        painter.setPen(pen)

        if self.frame in pybud.selections:
            for x, y in pybud.selections[self.frame]:
                x = int(x * self.scale_factor)
                y = int(y * self.scale_factor)
                painter.drawLine(x - 5, y - 5, x + 5, y + 5)
                painter.drawLine(x - 5, y + 5, x + 5, y - 5)

        # draw all fitted cells
        for cell in pybud.cells:
            if cell.frame == self.frame:

                if self.show_edge_points:
                    # Draw edge points as small dots
                    pen.setColor(QColor(255, 0, 0))  # Red color for edge points
                    painter.setPen(pen)

                    for x, y in zip(cell.found_x[cell.pixel_found], cell.found_y[cell.pixel_found]):
                        x = int(x * self.scale_factor)
                        y = int(y * self.scale_factor)
                        painter.drawPoint(x, y)  # Draw a small point

                if cell.cell_found:
                    ellipse = cell.ellipse

                    x = ellipse.get_x_center() * self.scale_factor
                    y = ellipse.get_y_center() * self.scale_factor
                    major = ellipse.get_major() * self.scale_factor
                    minor = ellipse.get_minor() * self.scale_factor
                    angle = ellipse.get_angle()

                    painter.save()
                    painter.setPen(QPen(QColor(255, 255, 0, 128), 2))
                    painter.translate(x, y)
                    painter.rotate(angle)
                    painter.drawEllipse(QPointF(0, 0), major, minor)
                    painter.restore()
                    

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
             # Get the click position relative to the QLabel
            click_position = event.pos()

            # Get the pixmap and its dimensions
            pixmap = self.pixmap()

            if pixmap is not None:
                pixmap_width = pixmap.width()
                pixmap_height = pixmap.height()

                x = click_position.x()
                y = click_position.y()

                # Ensure the click is within the image boundaries
                if 0 <= x <= pixmap_width and 0 <= y <= pixmap_height:
                    # Now scale back to original image coordinates
                    original_width = pybud.img.shape[3]
                    original_height = pybud.img.shape[2]

                    image_x = int(x * (original_width / pixmap_width))
                    image_y = int(y * (original_height / pixmap_height))

                    # Add or remove selection at this position
                    if pybud.contains_selection(self.frame, image_x, image_y):
                        pybud.remove_selection(self.frame, image_x, image_y)
                    else:
                        pybud.add_selection(self.frame, image_x, image_y)

                    self.update_image_display()  # Redraw the frame

class ImageViewer(QWidget):
    # Signal that emits the new measurements are available
    measurement_started = pyqtSignal()
    measurements_changed = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.image_label = ClickableImageLabel()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.image_label)

        # Create a QLabel to display the frame number above the image
        self.frame_number_label = QLabel("")
        self.edge_points_checkbox = QCheckBox("Show Edge Points")
        self.edge_points_checkbox.stateChanged.connect(self.show_edge_points)

        # Horizontal ScrollBar to scroll through frames
        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.setMinimum(0)
        self.scrollbar.valueChanged.connect(self.update_frame)

        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self.measure)

        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self.stop)

        # Create a horizontal layout for the buttons
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.measure_button)
        button_layout.addWidget(stop_button)

        top_layout = QHBoxLayout()
        top_layout.addWidget(self.frame_number_label)
        top_layout.addWidget(self.edge_points_checkbox)

        layout = QVBoxLayout()
        layout.addLayout(top_layout)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.scrollbar)
        layout.addLayout(button_layout)  # Add the button layout here

        self.setLayout(layout)

        self.worker = None

    def update(self):
        if pybud.img is not None:
            self.scrollbar.setMaximum(pybud.img.shape[0] - 1)
            self.update_frame(0)    # go to first frame and update

    def update_frame(self, frame=0):
        self.image_label.set_frame(frame)
        self.frame_number_label.setText(f"Frame: {frame + 1}")
        self.scrollbar.setValue(frame)

    def measure(self):
        # Create a worker to run the fit_cells function in a background thread
        self.worker = FitCellsWorker()
        self.worker.finished.connect(self.on_fit_cells_finished)
        self.worker.frame_processed.connect(self.update_frame)
        self.worker.start()
        self.measurement_started.emit()
        self.measure_button.setEnabled(False)

    def show_edge_points(self, state):
        self.image_label.show_edge_points = (state == Qt.Checked)
        self.image_label.update_image_display()

    def on_fit_cells_finished(self):
        self.measurements_changed.emit()
        self.worker = None
        self.measure_button.setEnabled(True)

    def stop(self):
        if self.worker is not None:
            self.worker.stop()


class Settings(QWidget):
    # Signal that emits when settings have changed
    settings_changed = pyqtSignal()
    
    def __init__(self):
        super().__init__()

        layout = QFormLayout(self)
        # Add form fields
        self.file_path = QLineEdit()
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_file)
        file_layout = QHBoxLayout()
        file_layout.addWidget(self.file_path)
        file_layout.addWidget(browse_button)
        file_container = QWidget()
        file_container.setLayout(file_layout)
        layout.addRow("Measurement File:", file_container)

        self.pixel_size_line = QLineEdit("0.0645")
        layout.addRow("Pixel Size (um/pixel):", self.pixel_size_line)

        self.cell_radius_line = QLineEdit("4")
        layout.addRow("Maximum Cell Radius (um):", self.cell_radius_line)

        self.cell_edge_size_line = QLineEdit("1")
        layout.addRow("Cell Edge Size (um):", self.cell_edge_size_line)

        self.brightfield_channel_line = QLineEdit("0")
        layout.addRow("Brightfield Channel:", self.brightfield_channel_line)

        self.fluorescent_channel1_line = QLineEdit("1")
        layout.addRow("Fluorescent Channel 1:", self.fluorescent_channel1_line)

        self.fluorescent_channel2_line = QLineEdit("-1")
        layout.addRow("Fluorescent Channel 2 (-1 if none):", self.fluorescent_channel2_line)

        self.edge_rel_min_line = QLineEdit("30")
        layout.addRow("Relative Minimum Edge Difference (%)", self.edge_rel_min_line)

        adjust_button = QPushButton("Adjust Setting")
        adjust_button.clicked.connect(self.adjust_settings)
        layout.addWidget(adjust_button)

        clear_button = QPushButton("Clear Selections")
        clear_button.clicked.connect(self.clear_selections)
        layout.addWidget(clear_button)

        export_settings_button = QPushButton("Export Settings")
        export_settings_button.clicked.connect(self.export_settings)
        layout.addWidget(export_settings_button)

        import_settings_button = QPushButton("Import Settings")
        import_settings_button.clicked.connect(self.import_settings)
        layout.addWidget(import_settings_button)


    def get_input_value(self, input_widget, value_type, field_name, min_value=None):
        """
        Helper method to validate and retrieve input values.
        """
        try:
            value = value_type(input_widget.text())
            if min_value is not None and value < min_value:
                raise ValueError(f"{field_name} must be >= {min_value}.")
            return value
        except ValueError as e:
            QMessageBox.warning(self, "Input Error", str(e))
            return None
    
    def get_settings_values(self):
        """
        Retrieve and validate all settings values.
        Returns a dictionary of settings or None if validation fails.
        """
        settings = {}

        # Add file_path to settings
        settings["file_path"] = self.file_path.text()

        # Validate pixel size (float)
        pixel_size = self.get_input_value(self.pixel_size_line, float, "Pixel Size")
        if pixel_size is None: return None
        settings["pixel_size"] = pixel_size

        # Validate maximum cell radius (float)
        cell_radius = self.get_input_value(self.cell_radius_line, float, "Maximum Cell Radius")
        if cell_radius is None: return None
        settings["cell_radius"] = cell_radius

        # Validate cell edge size (float)
        edge_size = self.get_input_value(self.cell_edge_size_line, float, "Cell Edge Size")
        if edge_size is None: return None
        settings["edge_size"] = edge_size

        # Validate brightfield channel (int)
        brightfield_channel = self.get_input_value(self.brightfield_channel_line, int, "Brightfield Channel")
        if brightfield_channel is None: return None
        settings["brightfield_channel"] = brightfield_channel

        # Validate fluorescent channel 1 (int, must be >= 0)
        fluorescent_channel1 = self.get_input_value(self.fluorescent_channel1_line, int, "Fluorescent Channel 1", min_value=0)
        if fluorescent_channel1 is None: return None
        settings["fluorescent_channel1"] = fluorescent_channel1

        # Validate fluorescent channel 2 (int)
        fluorescent_channel2 = self.get_input_value(self.fluorescent_channel2_line, int, "Fluorescent Channel 2")
        if fluorescent_channel2 is None: return None
        settings["fluorescent_channel2"] = fluorescent_channel2

        # Validate relative minimum edge difference (float)
        edge_rel_min = self.get_input_value(self.edge_rel_min_line, float, "Relative Minimum Edge Difference")
        if edge_rel_min is None: return None
        settings["edge_rel_min"] = edge_rel_min

        return settings
    
    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select Measurement File", "", "TIF Files (*.tif *.tiff *.TIF *.TIFF);;All Files (*)")
        if file_name:
            self.file_path.setText(file_name)
            self.load_image(file_name)

    def load_image(self, image_path):
        tif_data = tiff.imread(image_path)

        # Check the shape of the loaded data
        if tif_data.ndim == 3:  # This means it has the shape (frames, height, width)
            tif_data = np.reshape(tif_data, (tif_data.shape[0], 1, tif_data.shape[1], tif_data.shape[2]))
            self.fluorescent_channel1_line.setText("0")
            self.adjust_settings()

        pybud.clear()
        pybud.img = tif_data
        self.settings_changed.emit()

    def get_input_value(self, line_edit, value_type, error_message, min_value=None):
        """
        Helper function to get and validate the value from a QLineEdit.
        
        Parameters:
        - line_edit: The QLineEdit widget to extract the value from.
        - value_type: The type to which the input should be converted (e.g., int or float).
        - error_message: The error message to display in case of a ValueError.
        - min_value: Optional, the minimum allowed value for the input.
        
        Returns:
        - The converted value if valid, otherwise None.
        """
        try:
            value = value_type(line_edit.text())
            if min_value is not None and value < min_value:
                self.show_error_message(f"{error_message} cannot be less than {min_value}.")
                return None
            return value
        except ValueError:
            self.show_error_message(f"{error_message} must be a valid {value_type.__name__}.")
            return None
    
    def adjust_settings(self):
        """
        Validate all inputs using the get_settings_values method and proceed if all are valid.
        """
        # Retrieve settings values using the get_settings_values method
        settings = self.get_settings_values()
        if settings is None:
            return  # Validation failed, do not proceed

        # Extract values from the settings dictionary
        pixel_size = settings["pixel_size"]
        cell_radius = settings["cell_radius"]
        edge_size = settings["edge_size"]
        brightfield_channel = settings["brightfield_channel"]
        fluorescent_channel1 = settings["fluorescent_channel1"]
        fluorescent_channel2 = settings["fluorescent_channel2"]
        edge_rel_min = settings["edge_rel_min"]

        # Prepare fluorescent channels list
        fl_channels = [fluorescent_channel1]
        if fluorescent_channel2 >= 0:
            fl_channels.append(fluorescent_channel2)

        # Update global or class-level settings
        pybud.pixel_size = pixel_size
        pybud.cell_radius = cell_radius
        pybud.edge_size = edge_size
        pybud.bf_channel = brightfield_channel
        pybud.fl_channels = fl_channels
        pybud.edge_rel_min = edge_rel_min

        # Emit signal to indicate settings have changed
        self.settings_changed.emit()

    def clear_selections(self):
        pybud.stop()
        pybud.clear()
        self.settings_changed.emit()

    def export_settings(self):
        """
        Export the settings to a user-selected JSON file.
        If the file exists, overwrite it with the new settings.
        """
        # Retrieve settings values
        settings = self.get_settings_values()
        if settings is None:
            return  # Validation failed, do not proceed

        # Include selections
        settings["selections"] = pybud.selections

        # Open a file dialog to select a file
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Settings", "", "JSON Files (*.json);;All Files (*)")
        if not file_name:
            return  # User canceled the file dialog

        # Ensure the file has a .json extension
        if not file_name.endswith(".json"):
            file_name += ".json"

        # Save the settings as JSON
        try:
            with open(file_name, 'w') as file:
                json.dump(settings, file, indent=4)  # Pretty-print with indentation

            QMessageBox.information(self, "Success", f"Settings saved successfully as {os.path.basename(file_name)}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {str(e)}")
    
    def import_settings(self):
        """
        Import settings from a user-selected JSON file and update the UI fields accordingly.
        """
        # Open a file dialog to select a JSON file
        file_name, _ = QFileDialog.getOpenFileName(self, "Load Settings", "", "JSON Files (*.json);;All Files (*)")
        if not file_name:
            return  # User canceled the file dialog

        # Load the settings from the selected JSON file
        try:
            with open(file_name, 'r') as file:
                settings = json.load(file)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load settings: {str(e)}")
            return

        # Update UI fields with the loaded settings
        self.file_path.setText(settings.get("file_path", ""))
        self.pixel_size_line.setText(str(settings.get("pixel_size", "0.0645")))
        self.cell_radius_line.setText(str(settings.get("cell_radius", "4")))
        self.cell_edge_size_line.setText(str(settings.get("edge_size", "1")))
        self.brightfield_channel_line.setText(str(settings.get("brightfield_channel", "0")))
        self.fluorescent_channel1_line.setText(str(settings.get("fluorescent_channel1", "1")))
        self.fluorescent_channel2_line.setText(str(settings.get("fluorescent_channel2", "-1")))
        self.edge_rel_min_line.setText(str(settings.get("edge_rel_min", "30")))
        
        # Load selections if available
        pybud.selections = settings.get("selections", {})

        # Emit signal to indicate settings have changed
        self.settings_changed.emit()
        
        QMessageBox.information(self, "Success", "Settings imported successfully.")


class MeasurementTable(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        
        # Spreadsheet
        self.table = QTableWidget(10, 10)  # 10 rows, 4 columns
        self.table.setHorizontalHeaderLabels(["Cell", "Frame", "X (µm)", "Y (µm)", "Major (µm)", "Minor (µm)", "Angle", "Volume", "Fluorescence1", "Fluorescence2"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)  # Disable editing
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)  # Make columns stretch
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save to File")
        self.save_button.clicked.connect(self.save_measurements)
        self.copy_button = QPushButton("Copy to Clipboard")
        self.copy_button.clicked.connect(self.copy_measurements)
        self.export_rois_button = QPushButton("Export ROI's")
        self.export_rois_button.clicked.connect(self.export_rois)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addWidget(self.export_rois_button)
        layout.addLayout(button_layout)

    def populate_table(self):
        # Set the table to have as many rows as there are fitted cells
        found_cells = [cell for cell in pybud.cells if cell.cell_found]
        self.table.setRowCount(len(found_cells))

        for row, cell in enumerate(found_cells):
            fl1 = cell.fluorescence[0].mean
            fl2 = cell.fluorescence[1].mean if len(cell.fl_channels) > 1 else 0

            self.table.setItem(row, 0, QTableWidgetItem(str(cell.id)))
            self.table.setItem(row, 1, QTableWidgetItem(str(cell.frame)))
            self.table.setItem(row, 2, QTableWidgetItem(f"{cell.x_centroid:.2f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{cell.y_centroid:.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{cell.major:.2f}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{cell.minor:.2f}"))
            self.table.setItem(row, 6, QTableWidgetItem(f"{cell.angle:.2f}"))
            self.table.setItem(row, 7, QTableWidgetItem(f"{cell.volume:.2f}"))
            self.table.setItem(row, 8, QTableWidgetItem(f"{fl1:.2f}"))
            self.table.setItem(row, 9, QTableWidgetItem(f"{fl2:.2f}"))

    def save_measurements(self):
        # Open a file dialog to select where to save the file
        options = QFileDialog.Options()
        file_name, selected_filter = QFileDialog.getSaveFileName(
            self, "Save File", "", "CSV Files (*.csv);;Excel Files (*.xlsx);;All Files (*)", options=options
        )

        if file_name:
            if selected_filter == "CSV Files (*.csv)":
                if not file_name.endswith('.csv'):
                    file_name += '.csv'
                self._save_as_csv(file_name)
            elif selected_filter == "Excel Files (*.xlsx)":
                if not file_name.endswith('.xlsx'):
                    file_name += '.xlsx'
                self._save_as_excel(file_name)
            else:
                # Default to CSV if the filter is not recognized
                if not file_name.endswith('.csv'):
                    file_name += '.csv'
                self._save_as_csv(file_name)

    def _save_as_csv(self, file_name):
        """Save the table data to a CSV file."""
        with open(file_name, mode='w', newline='') as file:
            writer = csv.writer(file)
            # Write headers
            headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
            writer.writerow(headers)

            # Write rows
            for row in range(self.table.rowCount()):
                row_data = []
                for column in range(self.table.columnCount()):
                    item = self.table.item(row, column)
                    row_data.append(item.text() if item else '')
                writer.writerow(row_data)

        print(f"Data saved to {file_name}")

    def _save_as_excel(self, file_name):
        """Save the table data to an Excel file."""
        workbook = Workbook()
        sheet = workbook.active

        # Write headers
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        sheet.append(headers)

        # Write rows
        for row in range(self.table.rowCount()):
            row_data = []
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                row_data.append(item.text() if item else '')
            sheet.append(row_data)

        # Save the workbook
        workbook.save(file_name)
        print(f"Data saved to {file_name}")

    def copy_measurements(self):
        # Prepare the clipboard data
        clipboard = QApplication.clipboard()
        mime_data = QMimeData()

        # Gather table content
        table_data = ""
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        table_data += "\t".join(headers) + "\n"
        
        for row in range(self.table.rowCount()):
            row_data = []
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                row_data.append(item.text() if item else '')
            table_data += "\t".join(row_data) + "\n"

        # Set the clipboard text
        mime_data.setText(table_data)
        clipboard.setMimeData(mime_data)
        
        print("Data copied to clipboard")

    def export_rois(self):
        # Open a file dialog to select where to save the roi's
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(self, "Save ZIP File", "", "ZIP Files (*.zip);;All Files (*)", options=options)
        
        if file_name:
            rois = []

            for i, cell in enumerate(pybud.cells):
                if cell.cell_found:
                    x_points, y_points = cell.ellipse.generate_ellipse_points(100)

                    roi_fitted = roifile.ImagejRoi.frompoints(np.column_stack((x_points, y_points)))
                    roi_fitted.roitype = roifile.ROI_TYPE.POLYGON
                    roi_fitted.t_position = cell.frame + 1
                    roi_fitted.name = f"{i}_cell{cell.id}_{pybud.fitting_method}"
                    print("debug", cell.frame + 1)
                    rois.append(roi_fitted)

            # Export selection points as point ROIs
            for frame, selections in pybud.selections.items():
                for j, (x, y) in enumerate(selections):
                    roi_point = roifile.ImagejRoi.frompoints([[x, y]])
                    roi_point.roitype = roifile.ROI_TYPE.POINT
                    roi_point.t_position = frame + 1
                    roi_point.name = f"frame{frame}_point{j}"
                    rois.append(roi_point)

            roifile.roiwrite(file_name, rois, mode='w')
            print("ROi's exported")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyBud Measurement Tool")
        self.setGeometry(100, 100, 1920, 1080)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Create the top and bottom splitters
        main_splitter = QSplitter(Qt.Vertical)
        top_splitter = QSplitter(Qt.Horizontal)

        # Left panel: Settings
        self.settings = Settings()
        top_splitter.addWidget(self.settings)

        # Right panel: The zoomable image
        self.image_viewer = ImageViewer()
        top_splitter.addWidget(self.image_viewer)

        # Update image viewer when the settings changed
        self.settings.settings_changed.connect(self.image_viewer.update)

        # Add the top splitter to the main splitter
        main_splitter.addWidget(top_splitter)

        # Bottom panel: Could be any widget (e.g., more content, another text area)
        self.measurement_table = MeasurementTable()
        main_splitter.addWidget(self.measurement_table)

        # Update table when there are new measurements
        self.image_viewer.measurement_started.connect(self.status_measuring)
        self.image_viewer.measurements_changed.connect(self.measurement_table.populate_table)
        self.image_viewer.measurements_changed.connect(self.image_viewer.update)
        self.image_viewer.measurements_changed.connect(self.clear_status)

       # Set up the layout for the central widget
        layout = QVBoxLayout(central_widget)
        layout.addWidget(main_splitter)

        # Set initial sizes for the splitter panels
        main_splitter.setSizes([400, 200])  # [Top, Bottom] size ratios
        top_splitter.setSizes([200, 400])   # [Left, Right] size ratios

        # Create a status bar
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
