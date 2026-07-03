"""
test_pybud.py — Integration test for manual cell tracking.

Loads BudJstack.tif, places four seed points (three in frame 0,
one in frame 29 for a bud that appears mid-movie), runs the tracker,
and prints a per-cell summary.

Run from the project root:
    python tests/test_pybud.py
"""
from pathlib import Path
from pybud import PyBud


def test_manual_tracking():
    tif_path = Path(__file__).parent / "BudJstack.tif"
    assert tif_path.exists(), f"Test image not found: {tif_path}"

    pb = PyBud()
    pb.load(str(tif_path))

    # Override metadata values for this specific test image
    pb.pixel_size   = 0.0645  # µm / pixel
    pb.bf_channel   = 0
    pb.fl_channels  = [1]
    pb.cell_radius  = 4       # µm  max radial search
    pb.edge_size    = 1       # µm  edge-detection window
    pb.edge_rel_min = 8       # %   minimum edge contrast

    # Three cells present from frame 0; one bud appears at frame 29
    pb.add_selection(0,   90,  94)
    pb.add_selection(0,  119, 153)
    pb.add_selection(0,  177,  97)
    pb.add_selection(29, 124,  96)

    pb.fit_cells()

    found = sum(1 for c in pb.cells if c.cell_found)
    total = len(pb.cells)
    print(f"\n{found}/{total} frames fitted successfully\n")

    for cell in pb.cells:
        print(cell)

    # Basic sanity checks
    assert found > 0, "No cells were fitted — check image and parameters"
    assert len(pb.mother_ids) > 0, "Mother-daughter detection did not run"

    print("\nMother-daughter assignments:")
    for child_id, mother_id in pb.mother_ids.items():
        label = f"cell {mother_id}" if mother_id >= 0 else "none"
        print(f"  Cell {child_id} → mother: {label}")


if __name__ == "__main__":
    test_manual_tracking()
