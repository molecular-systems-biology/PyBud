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
                 min_valid_points = 150,
                 rescue_missing_angles = False,
                 rescue_margin_px = None,
                 rescue_min_provisional_points = 30,
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
        self.min_valid_points = min_valid_points
        # Opt-in: use a provisional ellipse fit on the main scan's points to
        # predict where the edge should be for angles that found nothing (or
        # got filtered out), then give just those angles one more, narrow,
        # targeted look -- see _rescue_missing_angles() below. Strictly
        # additive: only ever ADDS points for angles that had none;
        # already-accepted points are never touched or replaced.
        self.rescue_missing_angles = rescue_missing_angles
        self.rescue_margin_px = rescue_margin_px
        self.rescue_min_provisional_points = rescue_min_provisional_points
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

        if self.rescue_missing_angles and np.sum(self.pixel_found) >= self.rescue_min_provisional_points:
            # A provisional fit, even if too sparse/incomplete to accept yet,
            # is already a far better per-angle radius estimate than the
            # single blind cell_radius guess every angle had to search within
            # on the main scan -- use it to rescue angles that found nothing.
            try:
                provisional = Ellipse(self.found_x[self.pixel_found], self.found_y[self.pixel_found],
                                       method=self.fitting_method)
            except Exception:
                provisional = None
            if provisional is not None:
                self.ellipse = provisional
                # Snapshot pre-rescue state so a rescue that pulls the final
                # fit too far off course can be reverted rather than accepted
                # unconditionally -- see _rescue_fit_is_plausible below.
                backup = (self.pixel_found.copy(), self.found_x.copy(), self.found_y.copy(),
                          self.found_rad.copy(), self.found_edge.copy())
                if self._rescue_missing_angles():
                    self._finalize_cell_found()  # re-check threshold/border with the enlarged point set
                    if self.cell_found:
                        try:
                            post_rescue = Ellipse(self.found_x[self.pixel_found], self.found_y[self.pixel_found],
                                                   method=self.fitting_method)
                        except Exception:
                            post_rescue = None
                        if post_rescue is None or not self._rescue_fit_is_plausible(provisional, post_rescue):
                            # Revert to pre-rescue state. May still end up
                            # found (if it already was before rescue) or not
                            # (rescue was this cell's only chance) --
                            # re-finalize against the restored point set.
                            (self.pixel_found, self.found_x, self.found_y,
                             self.found_rad, self.found_edge) = backup
                            self._finalize_cell_found()

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

        if R < W:
            # The sliding window can't fit in a profile this short (e.g. a
            # tracking search radius set smaller than the edge window) --
            # report "no edge found" for every angle instead of letting
            # sliding_window_view raise.
            self.pixel_found      = np.zeros(360, dtype=bool)
            self.found_x          = np.zeros(360, dtype=np.float64)
            self.found_y          = np.zeros(360, dtype=np.float64)
            self.found_rad        = np.zeros(360, dtype=np.float64)
            self.found_dif        = np.zeros(360, dtype=np.float64)
            self.found_edge       = np.zeros(360, dtype=np.float64)
            self.found_slope      = np.zeros(360, dtype=np.float64)
            self.pixel_val_rel_dif = np.zeros(360, dtype=np.float64)
            return

        # ── Build all 360 radial profiles at once ─────────────────────────
        # angles: (360,)   radii: (R+1,)
        angles = np.arange(360, dtype=np.float64) * (np.pi / 180.0)
        radii  = np.arange(R + 1, dtype=np.float64)

        # Pixel coordinates for every (angle, radius): shape (360, R+1)
        xs_f = self.x_selected + np.outer(np.cos(angles), radii)
        ys_f = self.y_selected + np.outer(np.sin(angles), radii)
        xs   = np.round(xs_f).astype(np.int32)   # unclipped — kept for found_x/y
        ys   = np.round(ys_f).astype(np.int32)

        # Nearest-neighbor sampling, matching BudJ (CellBJ.java rounds to the
        # nearest pixel then does a direct getPixel() lookup — no
        # interpolation). Bilinear interpolation was tried and measured
        # *worse* on 257 real cells (65.4% vs 71.6% of cells well-tracked,
        # same ballpark volume accuracy either way) — softening the sharp
        # dark->bright transition at a real edge lowers its apparent
        # contrast just enough to drop some borderline-but-real edges below
        # edge_rel_min. See benchmark_alitsios/benchmark/README.md.
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
        """Require >= min_valid_points valid edges, confirm they're within
        image bounds, and compute the mean edge width if the cell is accepted."""
        self.cell_found = np.sum(self.pixel_found) >= self.min_valid_points

        if self.cell_found:
            x_valid = (self.found_x[self.pixel_found] >= 2) & (self.found_x[self.pixel_found] <= self.img_width - 2)
            y_valid = (self.found_y[self.pixel_found] >= 2) & (self.found_y[self.pixel_found] <= self.img_height - 2)
            self.cell_found = np.all(x_valid & y_valid)

        if self.cell_found:
            self.mean_edge = np.mean(self.found_edge[self.pixel_found])

    # ------------------------------------------------------------------
    # Opt-in missing-angle rescue (self.rescue_missing_angles)
    # ------------------------------------------------------------------
    #
    # The main scan searches every angle blind over [0, cell_radius] --
    # cell_radius is just a configured upper bound, not a per-angle
    # estimate of where the edge actually is. Once a (possibly provisional,
    # possibly already-accepted) ellipse fit exists, it's a far better
    # per-angle radius estimate than that single shared guess. For any
    # angle that found nothing in the main scan (no window cleared
    # edge_rel_min) or got filtered out by the outlier/difference/slope/jump
    # filters, this uses the fit to predict where the edge should be for
    # that specific angle and gives it one more, much narrower, targeted
    # look. Strictly additive: an angle that already has an accepted point
    # is never touched or re-searched.

    def _predict_radius_along_seed_rays(self, ellipse, angles_rad):
        """
        For each angle (as a ray from the seed point x_selected,y_selected,
        matching the main scan's own parametrization), the radius at which
        that ray crosses `ellipse`'s boundary -- found analytically by
        substituting the ray into the ellipse's implicit equation and
        solving the resulting quadratic for every angle at once. NaN where
        the ray doesn't hit the ellipse going outward (degenerate case: the
        seed point ended up outside the ellipse).
        """
        a, b = ellipse.get_major(), ellipse.get_minor()
        xc, yc = ellipse.get_x_center(), ellipse.get_y_center()
        phi = np.radians(ellipse.get_angle())
        cphi, sphi = np.cos(phi), np.sin(phi)

        # seed point in the ellipse's canonical (centered, unrotated) frame
        dx0, dy0 = self.x_selected - xc, self.y_selected - yc
        x0 = dx0 * cphi + dy0 * sphi
        y0 = -dx0 * sphi + dy0 * cphi

        # ray direction in the same canonical frame
        dx, dy = np.cos(angles_rad), np.sin(angles_rad)
        ddx = dx * cphi + dy * sphi
        ddy = -dx * sphi + dy * cphi

        A = (ddx / a) ** 2 + (ddy / b) ** 2
        B = 2.0 * (x0 * ddx / a ** 2 + y0 * ddy / b ** 2)
        C = (x0 / a) ** 2 + (y0 / b) ** 2 - 1.0

        disc = B ** 2 - 4 * A * C
        valid = disc >= 0
        sqrt_disc = np.sqrt(np.clip(disc, 0, None))
        safe_A = np.where(A != 0, A, 1.0)
        t1 = (-B - sqrt_disc) / (2 * safe_A)
        t2 = (-B + sqrt_disc) / (2 * safe_A)
        eps = 1e-6
        # smallest positive root = first crossing moving outward from the seed
        t = np.where(t1 > eps, t1, np.where(t2 > eps, t2, np.nan))
        return np.where(valid & (A != 0), t, np.nan)

    def _rescan_near_radius(self, angles_rad, r_center, margin_px, window):
        """
        Like the main scan, but searching only a narrow window of radii
        around r_center[i] (one centre per angle) instead of the shared
        [0, cell_radius] range. Same windowed dark->bright detection,
        nearest-neighbor pixel sampling (bilinear interpolation was tried
        for the main scan too and measured worse -- see README.md).
        Returns (found, edge_x, edge_y, edge_width), one entry per input angle.
        """
        from numpy.lib.stride_tricks import sliding_window_view

        n = len(angles_rad)
        offsets = np.arange(-margin_px, margin_px + 1, dtype=np.float64)
        M = len(offsets)
        if n == 0 or M < window:
            return (np.zeros(n, dtype=bool), np.zeros(n), np.zeros(n), np.zeros(n))

        selected_image = self.img[self.frame, self.bf_channel, :, :]
        radii = np.clip(r_center[:, None] + offsets[None, :], 0, None)   # (n, M)
        xs_f = self.x_selected + np.cos(angles_rad)[:, None] * radii
        ys_f = self.y_selected + np.sin(angles_rad)[:, None] * radii
        xs = np.round(xs_f).astype(np.int32)
        ys = np.round(ys_f).astype(np.int32)

        in_bounds = ((xs >= 0) & (xs < self.img_width) &
                     (ys >= 0) & (ys < self.img_height))
        xs_c = np.clip(xs, 0, self.img_width - 1)
        ys_c = np.clip(ys, 0, self.img_height - 1)
        profiles = selected_image[ys_c, xs_c].astype(np.float64)
        profiles[~in_bounds] = self.background

        windows = sliding_window_view(profiles, window_shape=window, axis=1)
        pos_max = np.argmax(windows, axis=2)
        pos_min = np.argmin(windows, axis=2)
        idx = np.arange(n)[:, None]
        n_idx = np.arange(M - window + 1)[None, :]
        val_max = windows[idx, n_idx, pos_max]
        val_min = windows[idx, n_idx, pos_min]
        dif = val_max - val_min
        rel_dif = 100.0 * dif / self.background
        valid = (pos_max > pos_min) & (rel_dif > self.edge_rel_min)
        masked_dif = np.where(valid, dif, 0.0)

        best_n = np.argmax(masked_dif, axis=1)
        found = np.any(valid, axis=1)
        all_idx = np.arange(n)
        pm = pos_max[all_idx, best_n]
        pn = pos_min[all_idx, best_n]
        lp = best_n + (pm + pn) // 2

        edge_x = np.where(found, xs[all_idx, lp].astype(np.float64), 0.0)
        edge_y = np.where(found, ys[all_idx, lp].astype(np.float64), 0.0)
        edge_w = np.where(found, (pm - pn).astype(np.float64), 0.0)
        return found, edge_x, edge_y, edge_w

    def _rescue_missing_angles(self):
        """
        Fill in angles with no accepted point using self.ellipse (a
        provisional or already-accepted fit) as a radius prior. Mutates
        pixel_found/found_x/found_y/found_rad/found_edge in place for
        rescued angles only. Returns True if anything was rescued (caller
        should re-run _finalize_cell_found to pick up the enlarged set),
        False otherwise.
        """
        missing = ~self.pixel_found
        n_missing = int(np.sum(missing))
        if n_missing == 0:
            return False

        missing_idx_all = np.nonzero(missing)[0]
        angles_rad = missing_idx_all.astype(np.float64) * (np.pi / 180.0)
        r_pred = self._predict_radius_along_seed_rays(self.ellipse, angles_rad)
        has_pred = ~np.isnan(r_pred)
        if not np.any(has_pred):
            return False

        # Default margin: tighter than edge_size (tried first, see README.md)
        # -- a few slide positions around the predicted radius rather than a
        # window as wide as the whole edge-detection scan. (edge_size // 2) +
        # 2 keeps 2*margin+1 comfortably above edge_size (so the sliding
        # window still fits) while roughly halving the search range.
        margin = (self.rescue_margin_px if self.rescue_margin_px is not None
                  else max(3, (self.edge_size // 2) + 2))
        target_idx = missing_idx_all[has_pred]
        found, ex, ey, ew = self._rescan_near_radius(
            angles_rad[has_pred], r_pred[has_pred], margin, self.edge_size)
        if not np.any(found):
            return False

        rescued = target_idx[found]
        ex_f, ey_f, ew_f = ex[found], ey[found], ew[found]
        if len(rescued) == 0:
            return False

        self.found_x[rescued] = ex_f
        self.found_y[rescued] = ey_f
        dxr = ex_f - self.x_selected
        dyr = ey_f - self.y_selected
        self.found_rad[rescued] = np.hypot(dxr, dyr)
        self.found_edge[rescued] = ew_f
        self.pixel_found[rescued] = True
        return True

    def _rescue_fit_is_plausible(self, before, after):
        """
        Sanity check comparing the post-rescue refit against the
        pre-rescue (provisional) fit, for the same reason max_size_change
        exists at the tracking level: rescued points are, by construction,
        from the "hard" angles the main scan couldn't resolve on its own,
        so blindly trusting whatever they pull the fit toward is exactly
        the kind of self-referential-update risk that caused the
        max_growth_per_frame bug. Same bound as the removed
        outline-refinement feature used.
        """
        dx = after.get_x_center() - before.get_x_center()
        dy = after.get_y_center() - before.get_y_center()
        shift = np.hypot(dx, dy)
        orig_major = before.get_major()
        new_major = after.get_major()
        if orig_major <= 0 or new_major <= 0:
            return False
        if shift > 0.5 * orig_major:
            return False
        if new_major > 2.0 * orig_major or new_major < 0.5 * orig_major:
            return False
        return True

    def __str__(self):
        return f"Cell ID: {self.id}, Pos: ({self.x_selected:.2f}, {self.y_selected:.2f}), Centroid: ({self.x_centroid:.2f}, {self.y_centroid:.2f}), Major: {self.major:.2f} µm, Minor: {self.minor:.2f} µm, Angle: {self.angle:.2f}°, Edge Width: {self.edge_width:.2f} µm"
