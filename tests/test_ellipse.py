import pybud
import numpy as np
import roifile
import tifffile

def test_ellipse():

    roi_ellipse = roifile.roiread("tests/ellipse.roi")
    indices = range(0, roi_ellipse.n_coordinates, roi_ellipse.n_coordinates // 32)
    x = roi_ellipse.integer_coordinates[indices,0] + roi_ellipse.left + np.random.normal(0, 2, len(indices))
    y = roi_ellipse.integer_coordinates[indices,1] + roi_ellipse.top + np.random.normal(0, 2, len(indices))

    roi_points = roifile.ImagejRoi.frompoints(np.column_stack((x, y)))
    roi_points.roitype = roifile.ROI_TYPE.POINT
    roi_points.name = "points_with_noise"
    
    rois = [roi_ellipse, roi_points]

    for method in ['geometric', 'algebraic']:

        # Create Ellipse object
        ellipse = pybud.Ellipse(x, y, method=method)

        # Access ellipse parameters
        print("Center (X, Y):", ellipse.get_x_center(), ellipse.get_y_center())
        print("Semi-major axis:", ellipse.get_major())
        print("Semi-minor axis:", ellipse.get_minor())
        print("Rotation angle (degrees):", ellipse.get_angle())

        x_points, y_points = ellipse.generate_ellipse_points(100)

        roi_fitted = roifile.ImagejRoi.frompoints(np.column_stack((x_points, y_points)))
        roi_fitted.roitype = roifile.ROI_TYPE.POLYGON
        roi_fitted.name = f"ellipse_fitted_{method}"
        rois.append(roi_fitted)

        mask = ellipse.get_mask(100, 100)

        # Convert the boolean mask to an 8-bit unsigned integer (0 for False, 255 for True)
        mask_uint8 = (mask * 255).astype(np.uint8)
        # Save the mask as a TIFF file
        tifffile.imwrite(f"tests/mask-{method}.tif", mask_uint8)


    roifile.roiwrite("tests/ellipse_fitted.zip", rois, mode='w')


if __name__ == "__main__":
    test_ellipse()