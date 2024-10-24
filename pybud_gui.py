import numpy as np
import tifffile as tiff
import csv
from PyQt5.QtCore import Qt, pyqtSignal, QThread,  QPointF, QMimeData
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QIcon
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QLabel, QWidget, QSplitter, QTextEdit, QScrollArea, QScrollBar, QLineEdit, QPushButton, QHBoxLayout, QFormLayout, QFileDialog, QTableWidget, QAbstractItemView, QHeaderView, QTableWidgetItem, QMainWindow, QStatusBar
from pybud import PyBud
import roifile


# then pybud object keeps track of all the settings
pybud = PyBud()

# Worker thread for running fit_cells in the background
class FitCellsWorker(QThread):
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()

    def run(self):
        pybud.fit_cells()
        self.finished.emit()

class ClickableImageLabel(QLabel):
    def __init__(self, parent=None):
        super(ClickableImageLabel, self).__init__(parent)
        self.tif_data = None
        self.frame = 0
        self.scale_factor = 1
        self.offset_x = 0
        self.offset_y = 0
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)

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

        painter.end()

        # draw all fitted cells
        for cell in pybud.cells:
            if cell.frame == self.frame:
                ellipse = cell.ellipse

                x = ellipse.get_x_center() * self.scale_factor
                y = ellipse.get_y_center() * self.scale_factor
                major = ellipse.get_major() * self.scale_factor
                minor = ellipse.get_minor() * self.scale_factor
                angle = ellipse.get_angle()

                self.draw_ellipse(pixmap, x, y, major, minor, angle)

        self.setPixmap(pixmap)

    def draw_ellipse(self, pixmap, x, y, major, minor, angle):
        with QPainter(pixmap) as painter:
            painter.setPen(QPen(QColor(255, 255, 0, 128), 2))
            painter.translate(x, y)
            painter.rotate(angle)
            painter.drawEllipse(QPointF(0, 0), major, minor)

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

        # Horizontal ScrollBar to scroll through frames
        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.setMinimum(0)
        self.scrollbar.valueChanged.connect(self.update_frame)

        measure_button = QPushButton("Measure")
        measure_button.clicked.connect(self.measure)

        layout = QVBoxLayout()
        layout.addWidget(self.scroll_area)        
        layout.addWidget(self.scrollbar)
        layout.addWidget(measure_button)

        self.setLayout(layout)

    def update(self):
        if pybud.img is not None:
            self.scrollbar.setMaximum(pybud.img.shape[0] - 1)
            self.update_frame(0)    # go to first frame and update

    def update_frame(self, frame):
        self.image_label.set_frame(frame)

    def measure(self):
        # Create a worker to run the fit_cells function in a background thread
        self.worker = FitCellsWorker()
        self.worker.finished.connect(self.on_fit_cells_finished)
        self.worker.start()
        self.measurement_started.emit()
    
    def on_fit_cells_finished(self):
        self.measurements_changed.emit()

class Settings(QWidget):
    # Signal that emits the new file path when a file is selected
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

    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select Measurement File", "", "TIF Files (*.tif);;All Files (*)")
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
        Validate all inputs using the helper function and proceed if all are valid.
        """
        # Validate pixel size (float)
        pixel_size = self.get_input_value(self.pixel_size_line, float, "Pixel Size")
        if pixel_size is None: return

        # Validate maximum cell radius (int)
        cell_radius = self.get_input_value(self.cell_radius_line, int, "Maximum Cell Radius")
        if cell_radius is None: return

        # Validate cell edge size (int)
        edge_size = self.get_input_value(self.cell_edge_size_line, int, "Cell Edge Size")
        if edge_size is None: return

        # Validate brightfield channel (int)
        brightfield_channel = self.get_input_value(self.brightfield_channel_line, int, "Brightfield Channel")
        if brightfield_channel is None: return

        # Validate fluorescent channel 1 (int, must be >= 0)
        fluorescent_channel1 = self.get_input_value(self.fluorescent_channel1_line, int, "Fluorescent Channel 1", min_value=0)
        if fluorescent_channel1 is None: return

        # Validate fluorescent channel 2 (int)
        fluorescent_channel2 = self.get_input_value(self.fluorescent_channel2_line, int, "Fluorescent Channel 2")
        if fluorescent_channel2 is None: return

        # Validate relative minimum edge difference (float)
        edge_rel_min = self.get_input_value(self.edge_rel_min_line, float, "Relative Minimum Edge Difference")
        if edge_rel_min is None: return

        fl_channels = [fluorescent_channel1]
        if fluorescent_channel2 >= 0:
            fl_channels.append(fluorescent_channel2)

        pybud.pixel_size = pixel_size
        pybud.cell_radius = cell_radius
        pybud.edge_size = edge_size
        pybud.bf_channel = brightfield_channel
        pybud.fl_channels = fl_channels
        pybud.edge_rel_min = edge_rel_min
        self.settings_changed.emit()

class MeasurementTable(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        
        # Spreadsheet
        self.table = QTableWidget(10, 10)  # 10 rows, 4 columns
        self.table.setHorizontalHeaderLabels(["Cell", "Frame", "X", "Y", "Major", "Minor", "Angle", "Volume", "Fluorescence1", "Fluorescence2"])
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
        self.table.setRowCount(len(pybud.cells))

        for row, cell in enumerate(pybud.cells):
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
        # Open a file dialog to select where to save the CSV
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(self, "Save CSV File", "", "CSV Files (*.csv);;All Files (*)", options=options)
        
        if file_name:
            # Ensure the file has the right extension
            if not file_name.endswith('.csv'):
                file_name += '.csv'

            # Open the file and write the table content to it
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
                x_points, y_points = cell.ellipse.generate_ellipse_points(100)

                roi_fitted = roifile.ImagejRoi.frompoints(np.column_stack((x_points, y_points)))
                roi_fitted.roitype = roifile.ROI_TYPE.POLYGON
                roi_fitted.t_position = cell.frame + 1
                roi_fitted.name = f"{i}_cell{cell.id}_{pybud.fitting_method}"
                print("debug", cell.frame + 1)
                rois.append(roi_fitted)

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
