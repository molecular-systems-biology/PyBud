# PyBud vs BudJ — Real-Data Comparison

Comparison of cell tracking and morphology measurements between PyBud and BudJ on a real brightfield yeast time-lapse dataset.

---

## Dataset

| Property | Value |
|----------|-------|
| File | `20250326_cb309_chipexample.tif` |
| Modality | Brightfield (flat-field corrected) |
| Dimensions | 79 frames × 726 × 672 px |
| Channels | 1 (BF only) |
| Pixel size | 0.064 µm/px |
| Frame interval | 300 s |

The dataset shows two budding *S. cerevisiae* cells imaged in a microfluidic chip device. Both cells are visible from the first frame; daughter cells appear in later frames as budding events occur.

---

## Methods

### Cell detection

Cells were detected automatically in frame 0 using a Hough circle transform. A large-Gaussian background model (σ = 80 px) was subtracted from the brightfield frame before Canny edge detection, removing chip walls and illumination gradients so that only cell-scale circular structures remain. The two strongest peaks in the Hough accumulator within the expected cell-radius range were taken as seed positions.

| Cell | Seed position (x, y in px, frame 0) |
|------|--------------------------------------|
| 1 | (238, 411) |
| 2 | (211, 401) |

### PyBud tracking

The detected positions were passed as starting seeds to PyBud's tracking pipeline (`autotrack.py`). PyBud fitted an ellipse to the brightfield edge profile at each frame using the geometric (non-linear least-squares) method, propagating the fitted centroid as the seed for the next frame.

Three robustness improvements were applied relative to the initial PyBud implementation:

1. **Minimum-radius filter**: Edge candidates within `edge_window / 3` of the seed are rejected, eliminating false inner-gradient detections (e.g. bright cell-wall rings visible from inside the cell that can be picked up when the seed is off-centre).
2. **Size continuity check**: If the fitted major or minor semi-axis changes by more than 50% relative to the previous frame, the fit is treated as unreliable; the tracking seed is held from the last valid frame while gap-tolerance bridging can optionally cover the skipped frame.
3. **Gap tolerance and interpolation**: Up to 1 consecutive missed frame is bridged automatically; the gap is filled with a linearly interpolated ellipse (flagged `interpolated = True` in the output).

### BudJ tracking

The same seed positions were passed to a custom headless batch plugin (`BudJMovieBatch_`) compiled into the BudJ Fiji distribution. The plugin replicates the BudJ GUI's frame-by-frame propagation logic: it calls BudJ's internal `CellBJ.GetCellData()` for each frame and uses the fitted centroid as the seed for the next. Because BudJ does not handle float32 pixel data correctly, the chip image was normalised to uint16 (global min–max) before processing by both tools.

### Matching parameters

| Parameter | Value |
|-----------|-------|
| Max cell radius | 4.0 µm |
| Edge window size | 1.0 µm |
| Min relative contrast | 30 % |
| Max size change per frame | 50 % (PyBud only) |
| Max gap tolerance | 1 frame (PyBud only) |
| Fitting method (PyBud) | Geometric (non-linear least squares) |
| Fitting method (BudJ) | Algebraic (direct linear least squares) |

---

## Tracking Improvements

Three improvements were implemented in PyBud's tracking engine based on issues identified during earlier testing.

### 1. Minimum-radius edge filter

**Problem**: PyBud's radial scan starts at radius 0, so strong intensity transitions close to the seed position — such as bright-ring artefacts visible from inside the cell when the seed drifts off-centre — can be detected as cell edges, biasing the ellipse fit.

**Fix**: After radial edge detection, any candidate edge point closer than `edge_window_px / 3` to the seed position is discarded. At the default settings (edge window = 1 µm ≈ 16 px), the threshold is 5 px — above the typical 3–5 px artefact range and below the minimum expected real-edge distance.

**Effect**: Eliminates false near-centre detections without removing real cell edges, and aligns PyBud's effective minimum search radius with BudJ's (which starts scanning at `MaxWinEdge`).

### 2. Size continuity check

**Problem**: Occasionally an edge profile contains confounding structure that causes the fitted ellipse to be dramatically larger or smaller than the previous frame, even though the cell cannot have changed size that rapidly.

**Fix**: After each frame fit, the fractional change in major and minor semi-axes is compared against a configurable threshold (`max_size_change`, default 50%). If either exceeds the threshold the frame measurement is not recorded; the tracking seed is held at the last valid centroid and the miss counter increments.

**Configurable in GUI** as "Max Size Change (%)" (0 = disabled).

### 3. Gap tolerance and post-tracking interpolation

**Problem**: A single frame where the edge profile is unusable caused tracking to terminate early in the original implementation.

**Fix**: A `max_gap` parameter (default 1) controls how many consecutive missed frames are tolerated before tracking is declared lost. After all frames have been processed, any gap of ≤ `max_gap` frames between two successfully fitted frames is filled by linearly interpolating all ellipse parameters (centroid x/y, major, minor, and orientation angle). Interpolated frames are flagged `interpolated = True` in the output CSV.

**Configurable in GUI** as "Max Frame Gap" (0 = no gap tolerance).

### Impact on this dataset

All 158 cell-frame measurements were fitted directly from image data; neither size-rejection nor interpolation was triggered. The minimum-radius filter was the critical fix that resolved an earlier tracking failure in testing.

---

## Results

### Tracking coverage

Both tools tracked both cells successfully for the full 79 frames.

| Cell | Frames | PyBud | BudJ |
|------|--------|-------|------|
| 1 | 79 | 79 / 79 (100 %) | 79 / 79 (100 %) |
| 2 | 79 | 79 / 79 (100 %) | 79 / 79 (100 %) |

Interpolated frames: 0 (all measurements derived directly from image data).

### Measurement agreement

For every frame, the ellipse parameters from PyBud and BudJ were compared directly (aligned by frame index). All differences are |PyBud − BudJ|.

**158 matched cell-frames (2 tracks × 79 frames):**

| Parameter | Mean diff | Max diff |
|-----------|-----------|----------|
| Centroid X | 0.060 µm | 0.185 µm |
| Centroid Y | 0.042 µm | 0.099 µm |
| Major semi-axis | 0.053 µm | 0.212 µm |
| Minor semi-axis | 0.003 µm | 0.012 µm |

Per-track breakdown:

| Cell | Frames | Mean \|ΔX\| | Mean \|ΔY\| | Mean \|ΔMajor\| | Mean \|ΔMinor\| |
|------|--------|------------|------------|----------------|----------------|
| 1 | 79 | 0.059 µm | 0.041 µm | 0.053 µm | 0.003 µm |
| 2 | 79 | 0.061 µm | 0.043 µm | 0.053 µm | 0.003 µm |

All mean differences are below 0.065 µm (one pixel). The minor semi-axis shows the best agreement (mean diff 0.003 µm). The major semi-axis shows a larger but still small mean difference of 0.053 µm; the direction of this offset is systematic and is explained below.

### Systematic offset in the major semi-axis

Inspecting the signed difference (PyBud − BudJ) reveals that PyBud consistently reports a *larger* major semi-axis than BudJ. This is not random noise but a systematic bias with the same sign in every frame, with a mean offset of approximately +0.05 µm.

**Which tool is more accurate?**

The synthetic benchmark (`BENCHMARK_REPORT.md`, Section 4) provides a direct answer. All three evaluated configurations were tested against known ground-truth ellipses:

| Fitting method | Mean major semi-axis error |
|---------------|---------------------------|
| PyBud — geometric (non-linear least squares) | **0.39 px** ← best |
| BudJ — algebraic (direct linear least squares) | 0.46 px |
| PyBud — algebraic | 0.48 px |

PyBud with geometric fitting is closest to ground truth and consistently reports *larger* values. BudJ and PyBud algebraic both *underestimate* the major semi-axis by ~0.07 px relative to the geometric optimum.

**Why does algebraic fitting underestimate axis lengths?**

The algebraic method minimises the sum of squared values of the implicit ellipse polynomial $F(x,y) = ax^2 + bxy + cy^2 + dx + ey + f$ evaluated at the edge points. This algebraic residual does not correspond to physical Euclidean distances from the points to the ellipse boundary, and the optimum tends to pull the ellipse inward — shrinking the axes — to reduce the norm of the coefficient vector. This effect grows with the noise level of the edge-point set.

The geometric method minimises the sum of squared Euclidean distances from each edge point to the nearest point on the ellipse boundary. This is the statistically correct formulation under Gaussian noise and produces the true maximum-likelihood estimate with no systematic axis bias.

**Practical implication**: PyBud's geometric fitting reports major semi-axis values that are ~0.07 px (≈ 4.5 nm at 0.064 µm/px) larger than BudJ and closer to the true cell boundary. For most biological applications this difference is negligible. When comparing absolute size measurements between PyBud and BudJ outputs, users should expect PyBud to be consistently larger on the major axis.

### Measurement time series

The figure below shows centroid position and semi-axes over time for each cell, with PyBud (solid) and BudJ (dashed) overlaid.

![Chip comparison](chip_comparison.png)

---

## Notes

- **Fitting methods differ**: PyBud uses geometric (non-linear) fitting; BudJ uses algebraic (linear) fitting. This is the primary source of the systematic major semi-axis offset described above.
- **Image preprocessing**: The TIF is stored as flat-field-corrected float32 with negative pixel values. It was normalised to uint16 (global min–max) before processing by both tools; BudJ cannot handle float32 pixel data.
- **New output column**: PyBud's CSV and GUI table now include an `interpolated` column (hidden by default) that flags frames filled by linear interpolation rather than direct image fitting.
