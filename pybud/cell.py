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

        # Select the image for the given brightfield channel and timepoint
        selected_image = self.img[self.frame, self.bf_channel, :, :]
        
        # Define the region of interest (ROI) within (50,50)-(width-100,height-100)
        roi = selected_image[50:self.img_height-100, 50:self.img_width-100]

        # Compute the mode of the pixel values in the ROI (background)
        #background = stats.mode(roi, axis=None).mode
        background = np.median(roi)

        self.pixel_found = np.full(360, False)
        self.found_x = np.zeros(360)
        self.found_y = np.zeros(360)
        self.found_rad = np.zeros(360)
        self.found_dif = np.zeros(360)
        self.found_edge = np.zeros(360)
        self.found_slope = np.zeros(360)

        vector_x = np.zeros(self.cell_radius + 1, dtype=np.int32)
        vector_y = np.zeros(self.cell_radius + 1, dtype=np.int32)
        vector_pixel_value = np.zeros(self.cell_radius + 1)

        # Iterate over each angle from 0 to 359 degrees
        for vector_angle in range(360):

            alpha = vector_angle * np.pi / 180.0
            cosalpha, sinalpha = np.cos(alpha), np.sin(alpha)

            # Compute pixel coordinates along the angle with boundary checking
            for i in range(self.cell_radius + 1):

                vector_x[i] = self.x_selected + int(round(i * cosalpha))
                vector_y[i] = self.y_selected + int(round(i * sinalpha))
                
                # Check if the coordinates are within the image bounds
                if 0 <= vector_x[i] < self.img_width and 0 <= vector_y[i] < self.img_height:

                    # Set the pixel value if the coordinates are valid
                    vector_pixel_value[i] = self.img[self.frame, self.bf_channel, vector_y[i], vector_x[i]]

                else:

                    # Set pixel value to background value if the coordinates are out of bounds
                    vector_pixel_value[i] = background

            limit_ptr = 0
            max_dif = 0

            # Find the max difference in pixel values along the vector
            for i in range(self.cell_radius - self.edge_size + 1):

                # Determine the min and max pixel values within the window
                window_pixels = vector_pixel_value[i:i+self.edge_size]
                current_min = np.min(window_pixels)
                current_max = np.max(window_pixels)
                current_dif = current_max - current_min

                # Calculate relative difference based on background
                pixel_val_rel_dif = (100 * current_dif) / background

                # Update the maximum difference and edge location
                if current_dif > max_dif and pixel_val_rel_dif > self.edge_rel_min:
                    self.pixel_found[vector_angle] = True
                    limit_ptr = i + (np.argmax(window_pixels) + np.argmin(window_pixels)) // 2
                    max_dif = current_dif
                    edge = np.argmax(window_pixels) - np.argmin(window_pixels)

            # If an edge is found, record its properties
            if self.pixel_found[vector_angle]:
                self.found_x[vector_angle] = vector_x[limit_ptr]
                self.found_y[vector_angle] = vector_y[limit_ptr]
                self.found_rad[vector_angle] = np.linalg.norm([self.found_x[vector_angle] - self.x_selected, self.found_y[vector_angle] - self.y_selected])  # Inline Euclidean distance
                self.found_dif[vector_angle] = max_dif
                self.found_edge[vector_angle] = edge
                self.found_slope[vector_angle] = max_dif / edge
            else:
                # Reset values for no edge found
                self.found_x[vector_angle] = self.found_y[vector_angle] = self.found_rad[vector_angle] = 0
                self.found_dif[vector_angle] = self.found_edge[vector_angle] = self.found_slope[vector_angle] = 0

        # Calculate mean and standard deviation for found radii, excluding zeros
        mean_rad = np.mean(self.found_rad[self.pixel_found])
        sdev_rad = np.std(self.found_rad[self.pixel_found])

        # Remove outliers in radii
        rad_mask = (self.found_rad >= mean_rad - 2 * sdev_rad) & (self.found_rad <= mean_rad + 2 * sdev_rad)
        self.pixel_found &= rad_mask

        # Calculate mean and standard deviation for differences
        mean_dif = np.mean(self.found_dif[self.pixel_found])
        sdev_dif = np.std(self.found_dif[self.pixel_found])

        # Filter out low differences
        dif_mask = self.found_dif >= mean_dif - sdev_dif
        self.pixel_found &= dif_mask

        # Calculate mean and standard deviation for slopes
        mean_slope = np.mean(self.found_slope[self.pixel_found])
        sdev_slope = np.std(self.found_slope[self.pixel_found])

        # Filter based on slope values
        slope_mask = self.found_slope >= mean_slope - sdev_slope
        self.pixel_found &= slope_mask

        # The Slope Consistency Filter refines edge detection by retaining only the pixels whose
        # intensity slopes consistently follow the overall trend (positive or negative),
        # eliminating outliers for more accurate cell boundary identification.
        slope_median = np.median(self.found_slope)
        if slope_median < 0:
            slope_mask = self.found_slope < 0
        else:
            slope_mask = self.found_slope >= 0
        self.pixel_found &= slope_mask

        # Check if enough pixels were found
        self.cell_found = np.sum(self.pixel_found) >= 150

        if self.cell_found:

            # Ensure edge coordinates are within the bounds of the image
            within_bounds = (self.found_x[self.pixel_found] >= 2)
            within_bounds &= (self.found_x[self.pixel_found] <= self.img_width - 2)
            within_bounds &= (self.found_y[self.pixel_found] >= 2)
            within_bounds &= (self.found_y[self.pixel_found] <= self.img_height - 2)
            self.cell_found = np.all(within_bounds)

            # Calculate the mean edge if the slice is valid
            self.mean_edge = np.mean(self.found_edge[self.pixel_found])

    def __str__(self):
        return f"Cell ID: {self.id}, Pos: ({self.x_selected:.2f}, {self.y_selected:.2f}), Centroid: ({self.x_centroid:.2f}, {self.y_centroid:.2f}), Major: {self.major:.2f} µm, Minor: {self.minor:.2f} µm, Angle: {self.angle:.2f}°, Edge Width: {self.edge_width:.2f} µm"
