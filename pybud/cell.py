import numpy as np
from .ellipse import Ellipse
from .fluorescence import Fluorescence

class Cell:
    def __init__(self,
                 img,
                 pixel_size,
                 bf_channel,
                 fl_channels,
                 frame,
                 x,
                 y,
                 cell_id = -1,
                 cell_radius = 50,
                 edge_size = 15,
                 edge_rel_min = 30,
                 fitting_method='algebraic',
                 min_cell_radius_px = 0,
                 fl_channel_offsets = None,
                 ):

        self.img = img
        self.pixel_size = pixel_size
        self.bf_channel = bf_channel
        self.fl_channels = fl_channels
        self.frame = frame
        self.x_selected = x
        self.y_selected = y
        self.id = cell_id
        self.cell_radius = cell_radius
        self.edge_size = edge_size
        self.edge_rel_min = edge_rel_min
        self.fitting_method = fitting_method
        self.min_cell_radius_px = min_cell_radius_px
        # {fl_channel: (dx, dy)} pixel offset correcting for a fixed chromatic/
        # optical misalignment between that channel and the brightfield-fitted
        # outline — see PyBud.estimate_fl_offset(). Missing entries mean (0, 0).
        self.fl_channel_offsets = fl_channel_offsets or {}
        self.img_height, self.img_width = img.shape[2], img.shape[3]

        self.cell_found = False
        self.mean_edge = 0
        self.ellipse = None

        self.fluorescence = []
        self.get_cell_data()

    def get_cell_data(self):

        self.get_cell_edge()

        if not self.cell_found:
            print(f"cell not found on frame {self.frame} x {self.x_selected} y {self.y_selected}")
            return

        self.ellipse = Ellipse(self.found_x[self.pixel_found], self.found_y[self.pixel_found], method=self.fitting_method)

        x, y = self.ellipse.generate_ellipse_points(360)

        self.x_centroid = self.pixel_size * self.ellipse.get_x_center()
        self.y_centroid = self.pixel_size * self.ellipse.get_y_center()
        self.major = self.pixel_size * self.ellipse.get_major()
        self.minor = self.pixel_size * self.ellipse.get_minor()
        self.angle = self.ellipse.get_angle()
        self.edge_width = self.pixel_size * self.mean_edge
        self.volume = 4 * np.pi * ((self.major + self.minor) / 2) ** 3 / 3

        for fl_channel in self.fl_channels:
            offset = self.fl_channel_offsets.get(fl_channel, (0.0, 0.0))
            self.fluorescence.append(
                Fluorescence(self.img[self.frame, fl_channel, :, :], self.ellipse, offset=offset)
            )

    def get_cell_edge(self):
        """
        Detects the cell boundary in a brightfield image using radial edge detection
        and several filtering steps to eliminate false positives, matching BudJ's
        algorithm. Each step is a dedicated method below, run in order:

        1. :meth:`_estimate_background` — modal intensity of the full image.
        2. :meth:`_detect_radial_edges` — sample pixels radially from the selected
           centre for all 360 angles at once and find the strongest dark→bright
           transition in each radial profile (both fully vectorised).
        3. :meth:`_filter_radius_outliers` — global radius outlier removal (mean ± 2σ).
        4. :meth:`_filter_weak_edges` — difference filter (mean − 1σ).
        5. :meth:`_filter_shallow_slopes` — slope filter (mean − 1σ).
        6. :meth:`_filter_local_radius_jumps` — local radius jump filter (sliding
           window of 20).
        7. :meth:`_finalize_cell_found` — require ≥ 180 valid edges, confirm all
           detected points are within image bounds, and compute the mean edge width.

        Any step that filters out every remaining candidate point stops the method
        early, leaving ``cell_found`` ``False``.
        """
        self._estimate_background()
        self._detect_radial_edges()

        if not np.any(self.pixel_found):
            return
        self._filter_radius_outliers()

        if not np.any(self.pixel_found):
            return
        self._filter_weak_edges()

        if not np.any(self.pixel_found):
            return
        self._filter_shallow_slopes()

        self._filter_local_radius_jumps()
        self._finalize_cell_found()

    def _estimate_background(self):
        """Background: modal intensity via histogram (robust for float and uint images,
        and ~10x faster than scipy.stats.mode which requires a full sort)."""
        selected_image = self.img[self.frame, self.bf_channel, :, :]

        flat = selected_image.ravel().astype(np.float64)
        counts, edges = np.histogram(flat, bins=512)
        peak = int(np.argmax(counts))
        self.background = float((edges[peak] + edges[peak + 1]) / 2.0)
        if self.background == 0:
            self.background = 1.0

    def _detect_radial_edges(self):
        """
        Sample 360 radial profiles around the selected centre and find the
        strongest dark->bright transition in each, using a vectorised sliding
        window scan (numpy.lib.stride_tricks.sliding_window_view) instead of a
        per-angle Python loop.
        """
        from numpy.lib.stride_tricks import sliding_window_view

        selected_image = self.img[self.frame, self.bf_channel, :, :]
        R = self.cell_radius
        W = self.edge_size

        # ── Build all 360 radial profiles at once ─────────────────────────
        # angles: (360,)   radii: (R+1,)
        angles = np.arange(360, dtype=np.float64) * (np.pi / 180.0)
        radii  = np.arange(R + 1, dtype=np.float64)

        # Pixel coordinates for every (angle, radius): shape (360, R+1)
        xs_f = self.x_selected + np.outer(np.cos(angles), radii)
        ys_f = self.y_selected + np.outer(np.sin(angles), radii)
        xs   = np.round(xs_f).astype(np.int32)   # unclipped — kept for found_x/y
        ys   = np.round(ys_f).astype(np.int32)

        in_bounds = ((xs >= 0) & (xs < self.img_width) &
                     (ys >= 0) & (ys < self.img_height))
        xs_c = np.clip(xs, 0, self.img_width  - 1)
        ys_c = np.clip(ys, 0, self.img_height - 1)

        profiles = selected_image[ys_c, xs_c].astype(np.float64)
        profiles[~in_bounds] = self.background   # out-of-bounds pixels → background

        # ── Vectorised sliding-window edge detection ───────────────────────
        # windows: (360, R-W+1, W)  — matches range(R-W+1) in a per-angle loop
        # Use profiles[:, :R] so the last index in any window is R-1, matching BudJ.
        windows = sliding_window_view(profiles[:, :R], window_shape=W, axis=1)

        pos_max = np.argmax(windows, axis=2)   # (360, R-W+1)
        pos_min = np.argmin(windows, axis=2)   # (360, R-W+1)

        a_idx = np.arange(360)[:, None]
        n_idx = np.arange(R - W + 1)[None, :]
        val_max = windows[a_idx, n_idx, pos_max]   # (360, R-W+1)
        val_min = windows[a_idx, n_idx, pos_min]   # (360, R-W+1)

        dif     = val_max - val_min
        rel_dif = 100.0 * dif / self.background

        # Valid: dark->bright (pos_max > pos_min) and contrast strong enough
        valid      = (pos_max > pos_min) & (rel_dif > self.edge_rel_min)
        masked_dif = np.where(valid, dif, 0.0)

        # Best window per angle: highest contrast among valid windows
        best_n    = np.argmax(masked_dif, axis=1)    # (360,)
        any_valid = np.any(valid, axis=1)             # (360,) bool

        a_all = np.arange(360)
        pm = pos_max[a_all, best_n]            # max position within best window
        pn = pos_min[a_all, best_n]            # min position within best window
        lp = best_n + (pm + pn) // 2          # edge radius index (limit_ptr)

        # ── Populate result arrays ─────────────────────────────────────────
        self.pixel_found       = any_valid.copy()
        self.found_x           = np.where(any_valid, xs[a_all, lp], 0).astype(np.float64)
        self.found_y           = np.where(any_valid, ys[a_all, lp], 0).astype(np.float64)
        dx                     = self.found_x - self.x_selected
        dy                     = self.found_y - self.y_selected
        self.found_rad         = np.where(any_valid, np.sqrt(dx*dx + dy*dy), 0.0)

        # Reject edges closer to the seed than the minimum expected cell radius.
        # This prevents strong interior gradients (bud necks, DIC phase artefacts)
        # from producing false near-centre edge detections that bias the ellipse fit.
        if self.min_cell_radius_px > 0:
            self.pixel_found &= (self.found_rad >= self.min_cell_radius_px)

        self.found_dif         = np.where(any_valid, masked_dif[a_all, best_n], 0.0)
        edge_w                 = np.where(any_valid, (pm - pn).astype(np.float64), 0.0)
        self.found_edge        = edge_w
        safe_edge_w            = np.where(edge_w > 0, edge_w, 1.0)
        self.found_slope       = np.where(any_valid & (edge_w > 0),
                                          self.found_dif / safe_edge_w, 0.0)
        self.pixel_val_rel_dif = np.where(any_valid,
                                          100.0 * self.found_dif / self.background, 0.0)

    def _filter_radius_outliers(self):
        """Global radius-based outlier removal (mean ± 2σ). The radius standard
        deviation computed here is reused by :meth:`_filter_local_radius_jumps`."""
        mean_rad = np.mean(self.found_rad[self.pixel_found])
        self._radius_sdev = np.std(self.found_rad[self.pixel_found])
        rad_mask = (self.found_rad >= mean_rad - 2 * self._radius_sdev) & \
                   (self.found_rad <= mean_rad + 2 * self._radius_sdev)
        self.pixel_found &= rad_mask

    def _filter_weak_edges(self):
        """Remove edges whose contrast is more than 1σ below the mean (weak edges)."""
        mean_dif = np.mean(self.found_dif[self.pixel_found])
        sdev_dif = np.std(self.found_dif[self.pixel_found])
        dif_mask = self.found_dif >= mean_dif - sdev_dif
        self.pixel_found &= dif_mask

    def _filter_shallow_slopes(self):
        """Remove edges whose slope is more than 1σ below the mean (shallow edges)."""
        mean_slope = np.mean(self.found_slope[self.pixel_found])
        sdev_slope = np.std(self.found_slope[self.pixel_found])
        slope_mask = self.found_slope >= mean_slope - sdev_slope
        self.pixel_found &= slope_mask

    def _filter_local_radius_jumps(self):
        """
        Local radius-jump outlier removal: slide a 20-point window (in valid
        edges) around the circle and drop any point whose radius exceeds the
        window's mean by more than the global radius σ from
        :meth:`_filter_radius_outliers`.
        """
        sdev_rad = self._radius_sdev
        vector_angle = 0
        while vector_angle < 359:
            win_found_ctr = 0
            mean_win_rad = 0.0
            inc = 0

            while win_found_ctr < 20 and (vector_angle + inc) < 359:
                if self.pixel_found[vector_angle + inc]:
                    mean_win_rad += self.found_rad[vector_angle + inc]
                    win_found_ctr += 1
                inc += 1

            if win_found_ctr > 0:
                mean_win_rad /= win_found_ctr
                pixel_jumped_ctr = inc
                for j in range(pixel_jumped_ctr + 1):
                    idx = vector_angle + j
                    if idx < 360 and self.pixel_found[idx]:
                        if self.found_rad[idx] > mean_win_rad + sdev_rad:
                            self.pixel_found[idx] = False

                vector_angle += round(pixel_jumped_ctr / 2) + 1
            else:
                vector_angle += 1

    def _finalize_cell_found(self):
        """Require >= 180 valid edges, confirm they're within image bounds, and
        compute the mean edge width if the cell is accepted."""
        self.cell_found = np.sum(self.pixel_found) >= 180

        if self.cell_found:
            x_valid = (self.found_x[self.pixel_found] >= 2) & (self.found_x[self.pixel_found] <= self.img_width - 2)
            y_valid = (self.found_y[self.pixel_found] >= 2) & (self.found_y[self.pixel_found] <= self.img_height - 2)
            self.cell_found = np.all(x_valid & y_valid)

        if self.cell_found:
            self.mean_edge = np.mean(self.found_edge[self.pixel_found])

    def __str__(self):
        return f"Cell ID: {self.id}, Pos: ({self.x_selected:.2f}, {self.y_selected:.2f}), Centroid: ({self.x_centroid:.2f}, {self.y_centroid:.2f}), Major: {self.major:.2f} µm, Minor: {self.minor:.2f} µm, Angle: {self.angle:.2f}°, Edge Width: {self.edge_width:.2f} µm"
