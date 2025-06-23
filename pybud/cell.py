import numpy as np
from scipy import stats
from .ellipse import Ellipse
from .fluorescence import Fluorescence

class Cell:
    def __init__(self,
                 img,
                 pixel_size,            # pixel size
                 bf_channel,
                 fl_channels,
                 frame,
                 x,                     # x coordinate in pixels
                 y,                     # y coordinate in pixels
                 id = -1,               # cell id
                 cell_radius = 50,      # maximum cell radius in pixels
                 edge_size = 15,        # maximum edge size in pixels
                 edge_rel_min = 30,     # edge relative minimum difference (30%)
                 fitting_method='algebraic'
                 ):
        
        self.img = img
        self.pixel_size = pixel_size
        self.bf_channel = bf_channel
        self.fl_channels = fl_channels
        self.frame = frame
        self.x_selected = x
        self.y_selected = y
        self.id = id
        self.cell_radius = cell_radius
        self.edge_size = edge_size
        self.edge_rel_min = edge_rel_min
        self.fitting_method = fitting_method
        self.img_height, self.img_width = img.shape[2], img.shape[3]

        # output values
        self.cell_found = False
        self.mean_edge = 0
        self.ellipse = None

        self.fluorescence = []
        self.get_cell_data()

    def get_cell_data(self):

        self.get_cell_edge()

        if not self.cell_found:
            print(f"cell not found on frame {self.frame} x {self.x_selected} y {self.y_selected}")
            return
        
        # fit ellipse using the found edge coordinates
        self.ellipse = Ellipse(self.found_x[self.pixel_found], self.found_y[self.pixel_found], method=self.fitting_method)

        x, y = self.ellipse.generate_ellipse_points(360)

        self.x_centroid = self.pixel_size * self.ellipse.get_x_center()
        self.y_centroid = self.pixel_size * self.ellipse.get_y_center()
        self.major = self.pixel_size * self.ellipse.get_major()
        self.minor = self.pixel_size * self.ellipse.get_minor()
        self.angle = self.ellipse.get_angle()
        self.edge_width = self.pixel_size * self.mean_edge
        self.volume = 4 * np.pi * np.pow((self.major + self.minor) / 2, 3) / 3
        
        for fl_channel in self.fl_channels:
            self.fluorescence.append(Fluorescence(self.img[self.frame, fl_channel, :, :], self.ellipse))

    def get_cell_edge(self):
        """
        Detects the cell boundary in a brightfield image using radial edge detection
        and several filtering steps to eliminate false positives.

        Steps:
        1. Extract a region of interest (ROI) to estimate background intensity.
        2. For each of 360 angles, sample pixels radially from the selected center.
        3. Along each radial line, detect the strongest intensity drop (edge).
        4. Store edge location, distance from center, difference, and slope.
        5. Apply a sequence of filters to remove noisy or invalid detections:
        a. Global radius outlier removal (based on mean ± 2*std deviation).
        b. Local radius jump filtering (removes sudden jumps using a window).
        c. Difference filter (removes weak edges).
        d. Slope filter (removes shallow edge slopes).
        6. Check if a sufficient number of valid edges were detected (≥ 150).
        7. Confirm all detected points are within valid image bounds.
        8. Compute the mean edge width if the cell was successfully found.
        """

        # Extract the selected brightfield image slice
        selected_image = self.img[self.frame, self.bf_channel, :, :]

        # Define the ROI (region of interest) excluding a 50-pixel margin on all sides
        roi = selected_image[50:self.img_height-100, 50:self.img_width-100]

        # Estimate background using the mode; fallback to median if mode is zero
        self.background = stats.mode(roi, axis=None).mode
        if self.background == 0:
            self.background = np.median(roi)

        # Initialize data structures for storing edge information
        self.pixel_found = np.full(360, False)
        self.found_x = np.zeros(360)
        self.found_y = np.zeros(360)
        self.found_rad = np.zeros(360)
        self.found_dif = np.zeros(360)
        self.found_edge = np.zeros(360)
        self.found_slope = np.zeros(360)
        self.pixel_val_rel_dif = np.zeros(360)  # Store relative difference for each angle

        # Preallocate buffers for line sampling along each angle
        vector_x = np.zeros(self.cell_radius + 1, dtype=np.int32)
        vector_y = np.zeros(self.cell_radius + 1, dtype=np.int32)
        vector_pixel_value = np.zeros(self.cell_radius + 1)

        # Iterate over all 360 degrees around the selected center point
        for vector_angle in range(360):

            # Convert angle to radians
            alpha = vector_angle * np.pi / 180.0
            cosalpha, sinalpha = np.cos(alpha), np.sin(alpha)

            # Sample pixel intensities along the radial line at the given angle
            for i in range(self.cell_radius + 1):
                vector_x[i] = self.x_selected + int(round(i * cosalpha))
                vector_y[i] = self.y_selected + int(round(i * sinalpha))

                if 0 <= vector_x[i] < self.img_width and 0 <= vector_y[i] < self.img_height:
                    vector_pixel_value[i] = self.img[self.frame, self.bf_channel, vector_y[i], vector_x[i]]
                else:
                    vector_pixel_value[i] = self.background

            limit_ptr = 0
            max_dif = 0

            # Slide a window along the radial line to find maximum intensity jump
            for i in range(self.cell_radius - self.edge_size + 1):
                window_pixels = vector_pixel_value[i:i+self.edge_size]
                position_max = np.argmax(window_pixels)
                position_min = np.argmin(window_pixels[position_max:]) + position_max

                if position_max < position_min:
                    current_max = window_pixels[position_max]
                    current_min = window_pixels[position_min]
                    current_dif = current_max - current_min
                    rel_dif = (100 * current_dif) / self.background

                    if current_dif > max_dif and rel_dif > self.edge_rel_min:
                        self.pixel_found[vector_angle] = True
                        limit_ptr = i + (position_max + position_min) // 2
                        max_dif = current_dif
                        edge = position_min - position_max
                        self.pixel_val_rel_dif[vector_angle] = rel_dif

            # Store the pixel and edge features if a valid edge is found
            if self.pixel_found[vector_angle]:
                self.found_x[vector_angle] = vector_x[limit_ptr]
                self.found_y[vector_angle] = vector_y[limit_ptr]
                dx = self.found_x[vector_angle] - self.x_selected
                dy = self.found_y[vector_angle] - self.y_selected
                self.found_rad[vector_angle] = np.sqrt(dx * dx + dy * dy)
                self.found_dif[vector_angle] = max_dif
                self.found_edge[vector_angle] = edge
                self.found_slope[vector_angle] = max_dif / edge
            else:
                self.found_x[vector_angle] = 0
                self.found_y[vector_angle] = 0
                self.found_rad[vector_angle] = 0
                self.found_dif[vector_angle] = 0
                self.found_edge[vector_angle] = 0
                self.found_slope[vector_angle] = 0

        # Step 1: Radius-based global outlier removal
        mean_rad = np.mean(self.found_rad[self.pixel_found])
        sdev_rad = np.std(self.found_rad[self.pixel_found])
        rad_mask = (self.found_rad >= mean_rad - 2 * sdev_rad) & (self.found_rad <= mean_rad + 2 * sdev_rad)
        self.pixel_found &= rad_mask

        # Step 2: Jump-based local radius outlier removal using sliding window
        vector_angle = 0
        while vector_angle < 359:
            win_found_ctr = 0
            mean_win_rad = 0.0
            inc = 0

            # Accumulate up to 20 valid points from the current angle
            while win_found_ctr < 20 and (vector_angle + inc) < 359:
                if self.pixel_found[vector_angle + inc]:
                    mean_win_rad += self.found_rad[vector_angle + inc]
                    win_found_ctr += 1
                inc += 1

            if win_found_ctr > 0:
                mean_win_rad /= win_found_ctr
                pixel_jumped_ctr = inc
                for j in range(pixel_jumped_ctr + 1):
                    idx = vector_angle + j
                    if idx < 360 and self.pixel_found[idx]:
                        if self.found_rad[idx] > mean_win_rad + sdev_rad:
                            self.pixel_found[idx] = False

                # Advance window by half jump length + 1
                vector_angle += round(pixel_jumped_ctr / 2) + 1
            else:
                vector_angle += 1

        # Step 3: Difference filter (remove weak edges)
        mean_dif = np.mean(self.found_dif[self.pixel_found])
        sdev_dif = np.std(self.found_dif[self.pixel_found])
        dif_mask = self.found_dif >= mean_dif - sdev_dif
        self.pixel_found &= dif_mask

        # Step 4: Slope filter (remove shallow slopes)
        mean_slope = np.mean(self.found_slope[self.pixel_found])
        sdev_slope = np.std(self.found_slope[self.pixel_found])
        slope_mask = self.found_slope >= mean_slope - sdev_slope
        self.pixel_found &= slope_mask

        # Step 5: Validate if enough edge pixels were found
        self.cell_found = np.sum(self.pixel_found) >= 150

        # Step 6: Check if all valid points are within image bounds
        if self.cell_found:
            x_valid = (self.found_x[self.pixel_found] >= 2) & (self.found_x[self.pixel_found] <= self.img_width - 2)
            y_valid = (self.found_y[self.pixel_found] >= 2) & (self.found_y[self.pixel_found] <= self.img_height - 2)
            self.cell_found = np.all(x_valid & y_valid)

        # Step 7: Compute mean edge width if cell is valid
        if self.cell_found:
            self.mean_edge = np.mean(self.found_edge[self.pixel_found])


    def __str__(self):
        return f"Cell ID: {self.id}, Pos: ({self.x_selected:.2f}, {self.y_selected:.2f}), Centroid: ({self.x_centroid:.2f}, {self.y_centroid:.2f}), Major: {self.major:.2f} µm, Minor: {self.minor:.2f} µm, Angle: {self.angle:.2f}°, Edge Width: {self.edge_width:.2f} µm"
