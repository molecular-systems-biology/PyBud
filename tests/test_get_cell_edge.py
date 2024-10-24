import pybud
import tifffile as tiff
import roifile
import numpy as np

def test_get_cell_edge():
    # Load the image stack
    stack_path = 'tests/BudJstack.tif'
    roi_path = 'tests/cell_points.zip'
    
    pixel_size = 0.0645000

    img = tiff.imread(stack_path)
    rois_cells = roifile.roiread(roi_path)
    rois = []

    for i, roi_cell in enumerate(rois_cells):
        cell_x = roi_cell.left
        cell_y = roi_cell.top

        cell = pybud.Cell(img, pixel_size, 0, [1], roi_cell.t_position - 1, cell_x, cell_y)
        cell.get_cell_edge()
        
        print("Cell found:", cell.cell_found)

        x = cell.found_x[cell.pixel_found]
        y = cell.found_y[cell.pixel_found]

        roi_edge_points = roifile.ImagejRoi.frompoints(np.column_stack((x, y)))
        roi_edge_points.roitype = roifile.ROI_TYPE.POINT
        roi_edge_points.t_position = roi_cell.t_position
        roi_edge_points.name = f"cell{i+1}_edge_points"
        
        rois.append(roi_edge_points)
        
        for method in ['geometric', 'algebraic']:
            ellipse = pybud.Ellipse(x, y, method=method)

            # Access ellipse parameters
            print("Center (X, Y):", ellipse.get_x_center(), ellipse.get_y_center())
            print("Semi-major axis:", ellipse.get_major())
            print("Semi-minor axis:", ellipse.get_minor())
            print("Rotation angle (degrees):", ellipse.get_angle())

            x_points, y_points = ellipse.generate_ellipse_points(100)

            roi_fitted = roifile.ImagejRoi.frompoints(np.column_stack((x_points, y_points)))
            roi_fitted.roitype = roifile.ROI_TYPE.POLYGON
            roi_fitted.t_position = roi_cell.t_position
            roi_fitted.name = f"cell{i+1}_ellipse_fitted_{method}"

            rois.append(roi_fitted)

    roifile.roiwrite("tests/cell_fitted.zip", rois, mode='w')

if __name__ == "__main__":
    test_get_cell_edge()
