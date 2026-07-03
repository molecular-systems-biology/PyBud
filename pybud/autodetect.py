"""
autodetect.py — Circular Hough-transform cell detection for PyBud.

Can be used standalone from scripts (no Qt dependency) or via the GUI's
Auto-Detect & Measure button, which calls AutoDetect().detect() internally.
"""

import numpy as np


class AutoDetect:
    """
    Detect cells in brightfield time-lapse images using the circular Hough
    transform and link detections across frames into tracks.

    **Two-phase process**

    1. *Per-frame detection* — for every frame, candidate circles are found
       with :meth:`detect_frame`.
    2. *Cross-frame linking* — candidates are matched to existing tracks via
       nearest-neighbour search.  A seed is added to ``pb.selections`` **only**
       when a candidate does not match any active track (i.e. a new cell
       appeared).  This prevents re-seeding a cell that drifted and came back
       within ``pb.max_gap`` frames.

    Example
    -------
    >>> from pybud import PyBud, AutoDetect
    >>>
    >>> pb = PyBud().load("movie.tif")
    >>> pb.pixel_size = 0.0645
    >>> pb.bf_channel = 0
    >>> pb.fl_channels = [1]
    >>>
    >>> n_seeds = AutoDetect().detect(pb)
    >>> print(f"Placed {n_seeds} seed(s)")
    >>> pb.fit_cells()
    """

    def detect(self, pb, frame_callback=None):
        """
        Populate ``pb.selections`` with one seed per new cell track and return
        the total number of seeds placed.

        Parameters
        ----------
        pb : PyBud
            Must have ``img`` loaded.  The following attributes are used:

            * ``bf_channel`` — brightfield channel index
            * ``pixel_size`` — µm per pixel
            * ``min_detect_radius_um`` — minimum cell radius for Hough search
            * ``max_detect_radius_um`` — maximum cell radius for Hough search
            * ``n_cells_max`` — maximum candidates per frame
            * ``hough_threshold`` — minimum Hough accumulator score (0–1)
            * ``match_distance_um`` — max centroid displacement to count as
              the same track between frames
            * ``max_gap`` — frames a track may be absent before it is retired
            * ``_should_run`` — set to ``False`` to abort early (thread-safe)

        frame_callback : callable(int), optional
            Called with the current frame index after each frame is processed.
            Use this to update a progress bar or status label.

        Returns
        -------
        int
            Number of seed points placed in ``pb.selections``.
        """
        min_r_px = pb.min_detect_radius_um / pb.pixel_size
        max_r_px = pb.max_detect_radius_um / pb.pixel_size
        match_px = pb.match_distance_um    / pb.pixel_size
        T        = pb.img.shape[0]

        active_tracks = []  # list of [cx, cy, last_seen_frame]

        for t in range(T):
            if not pb._should_run:
                break

            candidates = self.detect_frame(
                pb.img[t, pb.bf_channel],
                min_r_px, max_r_px,
                pb.n_cells_max,
                pb.hough_threshold,
            )

            # Retire tracks absent for longer than max_gap
            active_tracks = [tr for tr in active_tracks if t - tr[2] <= pb.max_gap]

            for cx, cy in candidates:
                matched = False
                if active_tracks:
                    dists  = [np.hypot(cx - tr[0], cy - tr[1]) for tr in active_tracks]
                    i_best = int(np.argmin(dists))
                    if dists[i_best] <= match_px:
                        active_tracks[i_best] = [cx, cy, t]
                        matched = True
                if not matched:
                    pb.add_selection(t, float(cx), float(cy))
                    active_tracks.append([cx, cy, t])

            if frame_callback is not None:
                frame_callback(t)

        return sum(len(v) for v in pb.selections.values())

    @staticmethod
    def detect_frame(bf_frame, min_r_px, max_r_px, n_max=10, threshold=0.5):
        """
        Detect circles in a single brightfield frame.

        The frame is pre-processed in two steps before edge detection:

        1. Percentile normalisation (1–99 %) to uint8.
        2. Large-scale background subtraction (Gaussian σ = 80 px) to suppress
           illumination gradients from chip walls and agarose.

        Canny edges are then passed to the circular Hough transform.

        Parameters
        ----------
        bf_frame : ndarray (H, W)
            Single brightfield frame (any numeric dtype).
        min_r_px : float
            Minimum expected cell radius in pixels.
        max_r_px : float
            Maximum expected cell radius in pixels.
        n_max : int
            Maximum number of circles to return per frame.
        threshold : float
            Minimum Hough score, as a fraction of the highest peak (0–1).
            Higher values return fewer, more confident detections.

        Returns
        -------
        list of (x, y) tuples
            Pixel coordinates of detected circle centres (column, row order).
        """
        from scipy.ndimage import gaussian_filter
        from skimage.feature import canny
        from skimage.transform import hough_circle, hough_circle_peaks

        lo, hi = np.percentile(bf_frame, 1), np.percentile(bf_frame, 99)
        if hi <= lo:
            return []

        u8 = np.clip(
            (bf_frame.astype(np.float64) - lo) / (hi - lo) * 255,
            0, 255,
        ).astype(np.uint8)

        # Suppress large-scale background (chip walls, illumination gradient)
        bg   = gaussian_filter(u8.astype(np.float64), sigma=80)
        corr = u8.astype(np.float64) - bg
        lo2, hi2 = corr.min(), corr.max()
        norm = np.clip(
            (corr - lo2) / max(hi2 - lo2, 1) * 255, 0, 255,
        ).astype(np.uint8)

        edges = canny(norm, sigma=3, low_threshold=10, high_threshold=30)

        radii = np.arange(max(5, int(min_r_px)), int(max_r_px) + 1, 2)
        if len(radii) == 0:
            radii = np.array([int((min_r_px + max_r_px) / 2)])

        hough_res    = hough_circle(edges, radii)
        _, cx, cy, _ = hough_circle_peaks(
            hough_res, radii,
            total_num_peaks = n_max,
            threshold       = threshold,
            min_xdistance   = max(10, int(min_r_px)),
            min_ydistance   = max(10, int(min_r_px)),
        )
        return list(zip(cx.tolist(), cy.tolist()))
