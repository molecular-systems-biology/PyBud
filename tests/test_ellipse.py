"""
test_ellipse.py — Unit test for ellipse fitting.

Reads a saved ellipse ROI from Fiji/ImageJ, sub-samples the contour
points and adds Gaussian noise, then fits both algebraic and geometric
ellipse models.  Writes the fitted contours as an ImageJ ROI ZIP so
you can open them in Fiji to visually inspect the fit quality.

Run from the project root:
    python tests/test_ellipse.py
"""
from pathlib import Path

import numpy as np
import roifile
import tifffile

from pybud import Ellipse


def test_ellipse_fitting():
    tests_dir = Path(__file__).parent
    roi_path  = tests_dir / "ellipse.roi"
    assert roi_path.exists(), f"Test ROI not found: {roi_path}"

    roi_ellipse = roifile.roiread(str(roi_path))

    # Sub-sample the contour and add realistic noise
    indices = range(0, roi_ellipse.n_coordinates, roi_ellipse.n_coordinates // 32)
    rng = np.random.default_rng(seed=42)
    x = (roi_ellipse.integer_coordinates[indices, 0]
         + roi_ellipse.left
         + rng.normal(0, 2, len(indices)))
    y = (roi_ellipse.integer_coordinates[indices, 1]
         + roi_ellipse.top
         + rng.normal(0, 2, len(indices)))

    rois = [roi_ellipse]

    for method in ("algebraic", "geometric"):
        ellipse = Ellipse(x, y, method=method)

        print(f"\n[{method}]")
        print(f"  Centre : ({ellipse.get_x_center():.2f}, {ellipse.get_y_center():.2f}) px")
        print(f"  Major  : {ellipse.get_major():.2f} px")
        print(f"  Minor  : {ellipse.get_minor():.2f} px")
        print(f"  Angle  : {ellipse.get_angle():.2f}°")

        # Fitted contour as an ImageJ ROI
        x_fit, y_fit = ellipse.generate_ellipse_points(200)
        roi_fit = roifile.ImagejRoi.frompoints(np.column_stack((x_fit, y_fit)))
        roi_fit.roitype = roifile.ROI_TYPE.POLYGON
        roi_fit.name    = f"ellipse_{method}"
        rois.append(roi_fit)

        # Binary mask
        mask = ellipse.get_mask(200, 200)
        assert mask.any(), f"{method} mask is empty"
        tifffile.imwrite(str(tests_dir / f"mask-{method}.tif"),
                         (mask * 255).astype(np.uint8))

    roifile.roiwrite(str(tests_dir / "ellipse_fitted.zip"), rois, mode='w')
    print(f"\nWrote ellipse_fitted.zip and mask-*.tif to {tests_dir}")


if __name__ == "__main__":
    test_ellipse_fitting()
