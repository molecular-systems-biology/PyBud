import numpy as np
from scipy import stats
from .ellipse import Ellipse

class Fluorescence:
    def __init__(self, img: np.ndarray, ellipse: Ellipse):
        height, width = img.shape
        mask = ellipse.get_mask(height, width)
        pixels_inside_ellipse = img[mask]

        # Background: modal value of the full fluorescence image (matching BudJ)
        self.background = float(stats.mode(img, axis=None).mode)

        # Basic statistics
        self.mean = float(np.mean(pixels_inside_ellipse))       # raw average intensity
        self.sd = float(np.std(pixels_inside_ellipse))
        self.median = float(np.median(pixels_inside_ellipse))
        self.mean_bg_subtracted = self.mean - self.background

        # Area and integrated density
        self.area = int(np.sum(mask))
        self.integrated_density = self.area * self.mean

        # Brightest-pixel statistics (only pixels above background, matching BudJ)
        pixels_above_bg = np.sort(pixels_inside_ellipse[pixels_inside_ellipse > self.background])
        n = len(pixels_above_bg)

        if n > 0:
            self.min = float(pixels_above_bg[0])
            self.max = float(pixels_above_bg[-1])
            n10 = max(1, int(n * 0.10))
            n25 = max(1, int(n * 0.25))
            n50 = max(1, int(n * 0.50))
            self.brightest_10 = float(np.mean(pixels_above_bg[-n10:]))
            self.brightest_25 = float(np.mean(pixels_above_bg[-n25:]))
            self.brightest_50 = float(np.mean(pixels_above_bg[-n50:]))
        else:
            self.min = 0.0
            self.max = 0.0
            self.brightest_10 = 0.0
            self.brightest_25 = 0.0
            self.brightest_50 = 0.0
