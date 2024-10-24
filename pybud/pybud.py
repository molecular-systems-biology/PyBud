import numpy as np
import numpy.typing as npt
from typing import List
from .cell import Cell

class PyBud:

    def __init__(self, fitting_method='algebraic', selection_radius=10):
        self.fitting_method = fitting_method
        self.selection_radius = selection_radius
        
        self.cells: List[Cell] = []
        self.selections = {}

        self.img = None
        self.pixel_size = 0.0645
        self.bf_channel = 0
        self.fl_channels = [1]
        self.cell_radius = 4
        self.edge_size = 1
        self.edge_rel_min = 30

    def contains_selection(self, frame, x, y):
        if frame in self.selections:
            for i, (sx, sy) in enumerate(self.selections[frame]):
                if np.hypot(sx - x, sy - y) <= self.selection_radius:  # Check distance
                    return True
        return False

    def add_selection(self, frame, x, y):
        if frame not in self.selections:
            self.selections[frame] = []
        self.selections[frame].append((x, y))

    def remove_selection(self, frame, x, y):
        if frame in self.selections:
            for i, (sx, sy) in enumerate(self.selections[frame]):
                if np.hypot(sx - x, sy - y) <= self.selection_radius:  # Check distance
                    del self.selections[frame][i]
                    return True
        return False
    
    def clear(self):
        self.selections.clear()
        self.cells.clear()

    def fit_cells(self):
        self.cells = []

        cell_id = 1
        for start_frame, coordinates in self.selections.items():
            for x, y in coordinates:
                for frame in range(start_frame, self.img.shape[0]):

                    cell = Cell(self.img, self.pixel_size, self.bf_channel, self.fl_channels, frame, x, y, cell_id, int(np.ceil(self.cell_radius / self.pixel_size)), int(np.ceil(self.edge_size / self.pixel_size)), self.edge_rel_min, fitting_method=self.fitting_method)

                    if cell.cell_found:
                        self.cells.append(cell)
                        x = cell.ellipse.get_x_center()
                        y = cell.ellipse.get_y_center()
                        print(f"cell found on channel {self.bf_channel} at frame {frame} x {x} y {y}")
                    else:
                        break
                cell_id += 1

