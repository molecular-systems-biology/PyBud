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
        self.processed_cells = {}

        self.img = None
        self.pixel_size = 0.0645
        self.bf_channel = 0
        self.fl_channels = [1]
        self.cell_radius = 4
        self.edge_size = 1
        self.edge_rel_min = 30

        self._should_run = False

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
        print("clear selections")
        self.selections.clear()
        self.cells.clear()
        self.processed_cells.clear()

    def fit_cells(self, callback=None):
        self._should_run = True

        #self.cells = []

        cell_id = 1
        for start_frame, coordinates in self.selections.items():
            for x, y in coordinates:
                for frame in range(start_frame, self.img.shape[0]):

                    if not self._should_run:
                        return
                    
                    if (frame, x, y) in self.processed_cells:
                        print(f"cell was already seen before on channel {self.bf_channel} at frame {frame} x {x} y {y}")
                        cell = self.processed_cells[(frame, x, y)]
                        
                        # it may be that we have seen (frame, x, y) before but that we could not accurately fit an ellipse
                        if not cell.cell_found:
                            break

                    else:
                        cell = Cell(self.img, self.pixel_size, self.bf_channel, self.fl_channels, frame, x, y, cell_id, int(np.ceil(self.cell_radius / self.pixel_size)), int(np.ceil(self.edge_size / self.pixel_size)), self.edge_rel_min, fitting_method=self.fitting_method)
                        self.cells.append(cell)
                        self.processed_cells[(frame, x, y)] = cell

                    if not cell.cell_found:
                        break
                    
                    x = cell.ellipse.get_x_center()
                    y = cell.ellipse.get_y_center()
                    print(f"cell found on channel {self.bf_channel} at frame {frame} x {x} y {y}")

                    # debug information
                    # print(f"number of found points {np.sum(cell.pixel_found)}")
                    # print(f"Background value : {cell.background}")
                    # print(f"pixel_val_rel_dif : {cell.pixel_val_rel_dif}")
                    # print(f"edge_size : {cell.edge_size}")
                    # print(f"cell_radius : {cell.cell_radius}")

                    if callback is not None:
                        callback(frame)
                    
                cell_id += 1

    def stop(self):
        self._should_run = False

