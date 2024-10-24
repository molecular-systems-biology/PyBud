import numpy as np
from .ellipse import Ellipse

class Fluorescence:
    def __init__(self, img: np.ndarray, ellipse: Ellipse):
        height, width = img.shape
        mask = ellipse.get_mask(height, width)
        pixels_inside_ellipse = img[mask]
        self.mean = np.mean(pixels_inside_ellipse)
        self.sd = np.std(pixels_inside_ellipse)
        self.median = np.median(pixels_inside_ellipse)
