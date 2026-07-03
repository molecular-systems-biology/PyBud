"""
test_get_cell_edge.py — Unit test for radial edge detection.

Loads BudJstack.tif and a set of hand-placed seed ROIs, runs the
radial edge-detection algorithm for each cell, and writes the detected
boundary points plus fitted ellipses as an ImageJ ROI ZIP.

Open the resulting cell_fitted.zip in Fiji alongside BudJstack.tif
to visually verify that edge detection is working correctly.

Run from the project root:
    python tests/test_get_cell_edge.py
"""
from pathlib import Path

import numpy as np
import roifile
import tifffile

from pybud import Cell, Ellipse


def test_cell_edge_detection():
    tests_dir  = Path(__file__).parent
    stack_path = tests_dir / "BudJstack.tif"
    roi_path   = tests_dir / "cell_points.zip"

    assert stack_path.exists(), f"Test image not found: {stack_path}"
    assert roi_path.exists(),   f"Seed ROIs not found: {roi_path}"

    raw = tifffile.imread(str(stack_path))

    # Normalise to (T, C, H, W) — tifffile returns varying shapes depending
    # on how the TIFF was written.
    if raw.ndim == 3:
        img = raw[:, np.newaxis]          # (T, H, W) → (T, 1, H, W)
    elif raw.ndim == 4:
        img = raw                          # already (T, C, H, W)
    else:
        raise ValueError(f"Unexpected image shape: {raw.shape}")

    pixel_size  = 0.0645
    bf_channel  = 0
    fl_channels = [1] if img.shape[1] > 1 else []

    rois_cells = roifile.roiread(str(roi_path))
    output_rois = []

    for i, roi_seed in enumerate(rois_cells):
        cell_x = float(roi_seed.left)
        cell_y = float(roi_seed.top)
        frame  = roi_seed.t_position - 1   # ROIs use 1-based frame index

        cell = Cell(img, pixel_size, bf_channel, fl_channels, frame, cell_x, cell_y)
        print(f"\nCell {i + 1}  (frame {frame}, seed {cell_x:.0f},{cell_y:.0f}): "
              f"found={cell.cell_found}")

        if not cell.cell_found:
            continue

        x_edge = cell.found_x[cell.pixel_found]
        y_edge = cell.found_y[cell.pixel_found]

        edge_roi = roifile.ImagejRoi.frompoints(np.column_stack((x_edge, y_edge)))
        edge_roi.roitype   = roifile.ROI_TYPE.POINT
        edge_roi.t_position = roi_seed.t_position
        edge_roi.name      = f"cell{i + 1}_edge"
        output_rois.append(edge_roi)

        for method in ("algebraic", "geometric"):
            ellipse = Ellipse(x_edge, y_edge, method=method)
            print(f"  [{method}]  major={ellipse.get_major():.2f} px  "
                  f"minor={ellipse.get_minor():.2f} px  "
                  f"angle={ellipse.get_angle():.1f}°")

            x_fit, y_fit = ellipse.generate_ellipse_points(200)
            fit_roi = roifile.ImagejRoi.frompoints(np.column_stack((x_fit, y_fit)))
            fit_roi.roitype    = roifile.ROI_TYPE.POLYGON
            fit_roi.t_position = roi_seed.t_position
            fit_roi.name       = f"cell{i + 1}_ellipse_{method}"
            output_rois.append(fit_roi)

    out_path = tests_dir / "cell_fitted.zip"
    roifile.roiwrite(str(out_path), output_rois, mode='w')
    print(f"\nWrote {len(output_rois)} ROIs → {out_path}")


if __name__ == "__main__":
    test_cell_edge_detection()
