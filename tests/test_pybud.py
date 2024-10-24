from pybud import PyBud
import tifffile as tiff

pb = PyBud()

file_name = r"tests/BudJstack.tif"

# set all parameters
pb.img = tiff.imread(file_name)
pb.pixel_size = 0.0645  # microns per pixel
pb.bf_channel = 0
pb.fl_channels = [1]
pb.cell_radius = 4 # micrometer
pb.edge_size = 1 # micrometer
pb.edge_rel_min = 8 # 30% of the background

# add selections
pb.add_selection(0, 90, 94)
pb.add_selection(0, 119, 153)
pb.add_selection(0, 177, 97)
pb.add_selection(29, 124, 96)

# fit cells
pb.fit_cells()

for cell in pb.cells:
    print(cell)