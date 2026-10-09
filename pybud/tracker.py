"""
tracker.py — Core cell-tracking engine for PyBud.

The public API centres on :class:`PyBud`: load an image, place seed points,
call :meth:`~PyBud.fit_cells`, then read back results from :attr:`~PyBud.cells`.
"""

import threading
from typing import List

import numpy as np

from .cell import Cell
from concurrent.futures import ThreadPoolExecutor, as_completed


# ---------------------------------------------------------------------------
# Lightweight helpers for gap-fill frames
# ---------------------------------------------------------------------------

class _SyntheticEllipse:
    """
    Drop-in replacement for :class:`~pybud.ellipse.Ellipse` that holds
    pre-computed parameters without re-fitting.  Used for linearly
    interpolated gap frames so downstream code can treat them identically.
    """

    def __init__(self, xc, yc, major, minor, angle_deg):
        self._xc    = xc
        self._yc    = yc
        self._major = major
        self._minor = minor
        self._angle = angle_deg

    def get_x_center(self): return self._xc
    def get_y_center(self): return self._yc
    def get_major(self):    return self._major
    def get_minor(self):    return self._minor
    def get_angle(self):    return self._angle

    def generate_ellipse_points(self, n_points=100):
        theta  = np.linspace(0, 2 * np.pi, n_points)
        angle  = np.radians(self._angle)
        x = self._major * np.cos(theta)
        y = self._minor * np.sin(theta)
        ca, sa = np.cos(angle), np.sin(angle)
        return self._xc + x * ca - y * sa, self._yc + x * sa + y * ca

    def get_mask(self, img_height, img_width):
        yg, xg = np.ogrid[:img_height, :img_width]
        dx, dy = xg - self._xc, yg - self._yc
        a  = np.radians(self._angle)
        ca, sa = np.cos(a), np.sin(a)
        xr =  dx * ca + dy * sa
        yr = -dx * sa + dy * ca
        return (xr / self._major) ** 2 + (yr / self._minor) ** 2 <= 1


class _InterpolatedCell:
    """
    Minimal Cell-compatible object for linearly interpolated gap frames.
    ``cell.interpolated`` is always ``True`` so callers can distinguish
    these from directly fitted measurements.
    """

    def __init__(self, cell_id, frame, pixel_size,
                 xc_px, yc_px, major_px, minor_px, angle_deg):
        self.id           = cell_id
        self.frame        = frame
        self.cell_found   = True
        self.interpolated = True

        self.ellipse    = _SyntheticEllipse(xc_px, yc_px, major_px, minor_px, angle_deg)
        self.x_centroid = xc_px  * pixel_size
        self.y_centroid = yc_px  * pixel_size
        self.major      = major_px * pixel_size
        self.minor      = minor_px * pixel_size
        self.angle      = angle_deg
        self.edge_width = 0.0
        self.volume     = 4 * np.pi * ((self.major + self.minor) / 2) ** 3 / 3
        self.fluorescence = []

        # Empty arrays so GUI "show edge points" code is harmless
        self.found_x     = np.array([])
        self.found_y     = np.array([])
        self.pixel_found = np.array([], dtype=bool)


# ---------------------------------------------------------------------------
# PyBud — public API
# ---------------------------------------------------------------------------

class PyBud:
    """
    Main entry point for PyBud cell tracking.

    **Typical usage**::

        from pybud import PyBud

        pb = PyBud()
        pb.load("movie.tif")          # auto-reads pixel size and time step
        pb.add_selection(0, 150, 200) # seed a cell at frame 0, pixel (150, 200)
        pb.fit_cells()                # track forward through all frames

        for cell in pb.cells:
            if cell.cell_found:
                print(cell.id, cell.frame, cell.major)

    **Auto-detection** (no manual seeds required)::

        from pybud import PyBud, AutoDetect

        pb = PyBud().load("movie.tif")
        AutoDetect().detect(pb)
        pb.fit_cells()

    Parameters
    ----------
    fitting_method : ``'algebraic'`` | ``'geometric'``
        Ellipse fitting strategy.  Algebraic (default) is faster; geometric
        minimises the true geometric residual and is slightly more accurate.
    selection_radius : int
        Pixel radius used to match a click to an existing seed when calling
        :meth:`contains_selection` or :meth:`remove_selection`.

    Attributes
    ----------
    img : ndarray (T, C, H, W) or None
        Loaded image stack.
    cells : list of Cell
        All cell-frame measurements produced by :meth:`fit_cells`.
    selections : dict {frame: [(x, y), ...]}
        Seed points added via :meth:`add_selection`.
    mother_ids : dict {child_track_id: mother_track_id}
        Mother-daughter assignments computed by :meth:`fit_cells`.
        Values are −1 when no mother was identified.
    """

    def __init__(self, fitting_method='algebraic', selection_radius=10):
        self.fitting_method   = fitting_method
        self.selection_radius = selection_radius

        self.cells: List[Cell] = []
        self.selections        = {}
        self.processed_cells   = {}

        self.img          = None
        self.pixel_size   = 0.0645   # µm per pixel
        self.bf_channel   = 0
        self.fl_channels  = [1]
        # {fl_channel: (dx, dy)} pixel offset correcting a fixed chromatic/
        # optical misalignment between that channel and the brightfield-fitted
        # outline. Set manually or via estimate_fl_offset(); applied to newly
        # fitted cells only (existing Cell objects already baked theirs in).
        self.fl_channel_offsets = {}
        self.cell_radius  = 4        # µm — max radial search distance from seed
        self.edge_size    = 1        # µm — edge-detection sliding-window width
        self.edge_rel_min = 30       # %  — minimum relative edge contrast
        self.time_step    = 1.0      # seconds per frame
        self.time_unit    = "s"

        # Tracking robustness
        self.max_gap           = 1    # consecutive missed frames before stopping (0 = immediate)
        self.max_size_change   = 0.5  # max fractional change in radius per frame (0 = disabled)
        self.overlap_threshold = 0.1  # IoU-like fraction above which a duplicate is discarded
        # Budding yeast keep growing: without this, the radial search window
        # (cell_radius) stays fixed at its starting size for the whole track,
        # so a cell that eventually grows past it simply becomes unfindable —
        # not caught by max_size_change, which only rejects implausible jumps
        # *after* a fit, it doesn't widen the search before one. Each frame's
        # effective search radius is max(cell_radius, last_known_major *
        # (1 + max_growth_per_frame) ** frames_since_last_fit) — compounding
        # across any gap frames. 0 = disabled (fixed cell_radius, old behaviour).
        self.max_growth_per_frame = 0.05

        # Background correction applied to the BF channel before edge detection
        self.bg_correction_sigma = 0.0  # Gaussian sigma in µm; 0 = disabled

        # Auto-detection parameters (Hough circle transform)
        self.min_detect_radius_um = 1.5   # minimum expected cell radius
        self.max_detect_radius_um = 4.0   # maximum expected cell radius
        self.n_cells_max          = 10    # max candidates per frame
        self.hough_threshold      = 0.5   # min peak height as fraction of strongest peak
        self.match_distance_um    = 8.0   # max inter-frame movement to keep as same track

        # Mother-daughter (bud) detection
        # A track is flagged as a daughter at the frame it first appears if:
        #   distance(daughter, mother) <= (r_mother + r_daughter) * bud_distance_factor
        #   AND  r_daughter < r_mother * bud_size_ratio
        self.bud_distance_factor = 1.2
        self.bud_size_ratio      = 0.8
        self.mother_ids          = {}   # populated by fit_cells()

        self.lost_cells  = []   # [(cell_id, frame, reason), ...] populated by fit_cells()

        self._should_run = False
        self._lock       = threading.Lock()

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def load(self, path):
        """
        Load a TIFF stack and populate :attr:`img`, :attr:`pixel_size`, and
        :attr:`time_step` from the file's embedded metadata.

        Metadata priority (highest wins): OME-XML → ImageJ → TIFF XResolution.

        The array is always normalised to shape ``(T, C, H, W)``:

        * ``(H, W)``       → ``(1, 1, H, W)``
        * ``(T, H, W)``    → ``(T, 1, H, W)``
        * ``(T, C, H, W)`` → kept as-is

        Parameters
        ----------
        path : str or pathlib.Path

        Returns
        -------
        self
            Enables method chaining: ``pb = PyBud().load("movie.tif")``.
        """
        import tifffile

        with tifffile.TiffFile(path) as tif:
            data = tif.asarray()

            # ImageJ metadata
            try:
                ij = tif.imagej_metadata or {}
                if 'finterval' in ij:
                    self.time_step = float(ij['finterval'])
                if 'tunit' in ij:
                    self.time_unit = ij['tunit']
            except Exception:
                pass

            # TIFF XResolution tag: pixels-per-unit → µm/pixel
            try:
                tag = tif.pages[0].tags.get('XResolution')
                if tag:
                    num, den = tag.value
                    if num > 0:
                        self.pixel_size = den / num
            except Exception:
                pass

            # OME-XML (highest priority — overrides the two above)
            try:
                ome = tif.ome_metadata
                if ome:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(ome)
                    ns   = root.tag.split('}')[0].lstrip('{') if '}' in root.tag else ''
                    tag  = f'{{{ns}}}Pixels' if ns else 'Pixels'
                    px   = root.find(f'.//{tag}')
                    if px is not None:
                        if 'PhysicalSizeX' in px.attrib:
                            self.pixel_size = float(px.attrib['PhysicalSizeX'])
                        if 'TimeIncrement' in px.attrib:
                            self.time_step = float(px.attrib['TimeIncrement'])
            except Exception:
                pass

        # Normalise to (T, C, H, W)
        if data.ndim == 2:
            data = data[np.newaxis, np.newaxis]
        elif data.ndim == 3:
            data = data[:, np.newaxis]

        self.img = data
        self.clear()
        return self

    # ------------------------------------------------------------------
    # Seed management
    # ------------------------------------------------------------------

    def add_selection(self, frame, x, y):
        """Add a seed point at pixel ``(x, y)`` on ``frame``."""
        if frame not in self.selections:
            self.selections[frame] = []
        self.selections[frame].append((x, y))

    def remove_selection(self, frame, x, y):
        """
        Remove the seed closest to ``(x, y)`` on ``frame`` (within
        :attr:`selection_radius` pixels).  Returns ``True`` if removed.
        """
        if frame in self.selections:
            for i, (sx, sy) in enumerate(self.selections[frame]):
                if np.hypot(sx - x, sy - y) <= self.selection_radius:
                    del self.selections[frame][i]
                    return True
        return False

    def contains_selection(self, frame, x, y):
        """Return ``True`` if a seed exists within :attr:`selection_radius` of ``(x, y)``."""
        if frame in self.selections:
            for sx, sy in self.selections[frame]:
                if np.hypot(sx - x, sy - y) <= self.selection_radius:
                    return True
        return False

    def clear(self):
        """Reset all seeds, measurements, and mother-daughter assignments."""
        self.selections.clear()
        self.cells.clear()
        self.processed_cells.clear()
        self.mother_ids.clear()
        self.lost_cells.clear()

    def stop(self):
        """Signal any running :meth:`fit_cells` call to stop after the current frame."""
        self._should_run = False

    def get_pending_ids(self):
        """
        Map each not-yet-fitted seed to the cell id it will receive when
        :meth:`fit_cells` runs, using the same enumeration order as
        :meth:`fit_cells` itself: frame order, then insertion order within
        a frame, starting at 1.
        """
        ids = {}
        cell_id = 1
        for start_frame, coords in self.selections.items():
            for x, y in coords:
                ids[(start_frame, x, y)] = cell_id
                cell_id += 1
        return ids

    # ------------------------------------------------------------------
    # Tracking pipeline
    # ------------------------------------------------------------------

    def fit_cells(self, callback=None):
        """
        Track every seed forward through :attr:`img` and populate :attr:`cells`.

        Each seed spawns an independent track that propagates frame-by-frame:
        the fitted centroid of frame *t* becomes the seed for frame *t+1*.
        Tracks run in parallel using a thread pool.

        After fitting, the pipeline runs three post-processing steps:
        gap filling (linear interpolation over short drop-outs), overlap
        filtering (discard duplicate detections), and mother-daughter
        detection.

        Parameters
        ----------
        callback : callable(int), optional
            Called with the current frame index after each frame is processed.
            Intended for progress-bar updates from a GUI thread.
        """
        self.cells.clear()
        self.processed_cells.clear()
        self.lost_cells.clear()

        seeds = {
            cid: (start_frame, float(x), float(y), None)   # no known size yet for a fresh seed
            for (start_frame, x, y), cid in self.get_pending_ids().items()
        }
        self._run_tracking(seeds, callback)

    def refit_range(self, start_frame, end_frame, callback=None, cell_ids=None):
        """
        Re-run tracking for frames ``[start_frame, end_frame]`` only
        (inclusive, 0-based), leaving every other frame's results untouched.

        Use this after tweaking fitting/tracking settings to fix a specific
        problematic stretch of a movie without re-measuring everything.
        Existing tracks are continued from their last fitted position just
        before ``start_frame``; any seed manually placed inside the window
        (via :meth:`add_selection`) starts a new track there.

        Parameters
        ----------
        start_frame, end_frame : int
            Inclusive 0-based frame range to re-fit.
        callback : callable(int), optional
            Called with the current frame index after each frame is processed.
        cell_ids : iterable[int], optional
            Restrict the re-fit to these track IDs — every other cell's
            results inside the window are left exactly as they were, even
            if the new settings would have changed how it fits. Omit (or
            ``None``) to re-fit every cell active in the window, as before.
        """
        if self.img is None:
            return

        start_frame = max(0, int(start_frame))
        end_frame   = min(self.img.shape[0] - 1, int(end_frame))
        if start_frame > end_frame:
            return

        cell_ids = set(cell_ids) if cell_ids is not None else None

        seeds = self._collect_refit_seeds(start_frame, end_frame, cell_ids)
        if not seeds:
            return

        self._clear_frame_range(start_frame, end_frame, cell_ids)
        self._run_tracking(seeds, callback, end_frame=end_frame)

    def _collect_refit_seeds(self, start_frame, end_frame, cell_ids=None):
        """
        Seeds for :meth:`refit_range`: every track alive just before
        ``start_frame`` (continued from its last fitted position) plus any
        manual seed placed inside the window (numbered as :meth:`fit_cells`
        would number it) — restricted to ``cell_ids`` if given. Returns
        ``{cell_id: (seed_frame, x, y, seed_major)}`` — continuing tracks carry
        their last known size so growth-aware search radius (see
        :attr:`max_growth_per_frame`) applies from the window's first frame
        too, not just frames 2+; fresh seeds carry ``None`` (no size known yet).
        """
        seeds = {}
        for cell in self.cells:
            if (cell.cell_found and cell.frame == start_frame - 1
                    and not getattr(cell, 'interpolated', False)
                    and (cell_ids is None or cell.id in cell_ids)):
                seeds[cell.id] = (start_frame,
                                  cell.ellipse.get_x_center(),
                                  cell.ellipse.get_y_center(),
                                  cell.ellipse.get_major())

        for (sf, x, y), cid in self.get_pending_ids().items():
            if start_frame <= sf <= end_frame and (cell_ids is None or cid in cell_ids):
                seeds.setdefault(cid, (sf, float(x), float(y), None))

        return seeds

    def _clear_frame_range(self, start_frame, end_frame, cell_ids=None):
        """
        Drop cached/measured results inside ``[start_frame, end_frame]`` so
        they get redone — restricted to ``cell_ids`` if given, so untouched
        cells' existing results in the window survive even though the
        window as a whole is being re-fit.
        """
        def in_scope(cid):
            return cell_ids is None or cid in cell_ids

        self.cells = [c for c in self.cells
                      if not (start_frame <= c.frame <= end_frame and in_scope(c.id))]

        for key in list(self.processed_cells.keys()):
            frame = key[0]
            if start_frame <= frame <= end_frame and in_scope(self.processed_cells[key].id):
                del self.processed_cells[key]

        self.lost_cells = [lc for lc in self.lost_cells
                           if not (start_frame <= lc[1] <= end_frame and in_scope(lc[0]))]

    def _run_tracking(self, seeds, callback, end_frame=None):
        """
        Shared pipeline for :meth:`fit_cells` and :meth:`refit_range`: track
        every seed in parallel, then gap-fill, overlap-filter, and re-detect
        mother-daughter relationships over the (possibly partial) result.
        """
        self._should_run = True
        self._work_img = (self._apply_bg_correction()
                          if self.bg_correction_sigma > 0
                          else self.img)

        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(self._track_cell, cid, sf, x, y, callback, end_frame, seed_major)
                for cid, (sf, x, y, seed_major) in seeds.items()
            ]
            for future in as_completed(futures):
                future.result()

        self.cells.sort(key=lambda c: (c.id, c.frame))

        if self.max_gap > 0:
            self._fill_gaps()
            self.cells.sort(key=lambda c: (c.id, c.frame))

        self._filter_overlapping()
        self._detect_mother_daughter()

    def _track_cell(self, cell_id, start_frame, x, y, callback, end_frame=None, seed_major=None):
        """
        Propagate a single cell seed forward through frames
        ``[start_frame, end_frame]``. ``seed_major`` is the cell's already-known
        size (pixels) when continuing an existing track — e.g. from
        :meth:`refit_range` — so the very first frame of the run also benefits
        from growth-aware radius expansion, not just frames 2+.
        """
        consecutive_misses = 0
        prev_major = None          # drives the max_size_change rejection check, below
        prev_minor = None
        last_known_major = seed_major   # drives growth-aware search-radius expansion only

        last_frame = (self._work_img.shape[0] - 1 if end_frame is None
                      else min(end_frame, self._work_img.shape[0] - 1))

        for frame in range(start_frame, last_frame + 1):
            if not self._should_run:
                return

            key = (frame, x, y)

            with self._lock:
                cached = self.processed_cells.get(key)

            if cached is not None:
                if not cached.cell_found:
                    consecutive_misses += 1
                    if consecutive_misses > self.max_gap:
                        self._report_lost(cell_id, frame, "no further match found")
                        return
                    continue
                consecutive_misses = 0
                x          = cached.ellipse.get_x_center()
                y          = cached.ellipse.get_y_center()
                prev_major = cached.ellipse.get_major()
                prev_minor = cached.ellipse.get_minor()
                last_known_major = prev_major
                if callback is not None:
                    callback(frame)
                continue

            base_radius_px = int(np.ceil(self.cell_radius / self.pixel_size))
            edge_size_px = int(np.ceil(self.edge_size / self.pixel_size))
            if last_known_major is not None and self.max_growth_per_frame > 0:
                # Compound over any missed frames since the last successful fit,
                # so a gap doesn't leave the window too tight for how much the
                # cell could plausibly have grown in the meantime. The fitted
                # major is the cell's actual edge, not the search radius that
                # found it -- the radial edge detector needs the window to
                # extend roughly edge_size_px *past* the true edge to see a
                # clean dark-to-bright transition (the same margin a
                # fixed cell_radius has to leave by hand), so project that
                # same margin forward or the window stays one step too tight.
                growth = (1 + self.max_growth_per_frame) ** (consecutive_misses + 1)
                projected_px = int(np.ceil(last_known_major * growth)) + edge_size_px
                search_radius_px = max(base_radius_px, projected_px)
            else:
                search_radius_px = base_radius_px

            cell = Cell(
                self._work_img, self.pixel_size, self.bf_channel, self.fl_channels,
                frame, x, y, cell_id,
                search_radius_px,
                edge_size_px,
                self.edge_rel_min,
                fitting_method=self.fitting_method,
                # Reject near-centre edges (bud necks, DIC artefacts) matching BudJ behaviour
                min_cell_radius_px=max(1, edge_size_px // 3),
                fl_channel_offsets=self.fl_channel_offsets,
            )

            with self._lock:
                if key not in self.processed_cells:
                    self.processed_cells[key] = cell
                else:
                    cell = self.processed_cells[key]

            frame_ok      = cell.cell_found
            reject_reason = None

            if frame_ok:
                new_x = cell.ellipse.get_x_center()
                new_y = cell.ellipse.get_y_center()
                H, W  = self._work_img.shape[2], self._work_img.shape[3]

                if not (0 <= new_x <= W and 0 <= new_y <= H):
                    frame_ok      = False
                    reject_reason = (f"centroid outside image "
                                     f"({new_x * self.pixel_size:.1f}, "
                                     f"{new_y * self.pixel_size:.1f} µm)")

                elif prev_major is not None and self.max_size_change > 0:
                    new_major = cell.ellipse.get_major()
                    new_minor = cell.ellipse.get_minor()
                    major_chg = abs(new_major - prev_major) / prev_major
                    minor_chg = abs(new_minor - prev_minor) / prev_minor if prev_minor > 0 else 0
                    if major_chg > self.max_size_change or minor_chg > self.max_size_change:
                        frame_ok      = False
                        reject_reason = (f"size jump: major "
                                         f"{prev_major * self.pixel_size:.2f}→"
                                         f"{new_major  * self.pixel_size:.2f} µm")

            if frame_ok:
                consecutive_misses = 0
                with self._lock:
                    self.cells.append(cell)
                x          = cell.ellipse.get_x_center()
                y          = cell.ellipse.get_y_center()
                prev_major = cell.ellipse.get_major()
                prev_minor = cell.ellipse.get_minor()
                last_known_major = prev_major
                print(f"cell found on channel {self.bf_channel} "
                      f"at frame {frame} x {x:.1f} y {y:.1f}")
            else:
                consecutive_misses += 1
                if reject_reason:
                    print(f"WARNING: frame {frame} skipped — {reject_reason}. "
                          f"Consecutive misses: {consecutive_misses}/{self.max_gap}")
                if consecutive_misses > self.max_gap:
                    self._report_lost(cell_id, frame, reject_reason or "no edge found")
                    return

            if callback is not None:
                callback(frame)

    def _report_lost(self, cell_id, frame, reason):
        """Record that a track was given up on at ``frame`` (too many consecutive misses)."""
        print(f"Cell {cell_id} lost at frame {frame + 1} ({reason})")
        with self._lock:
            self.lost_cells.append((cell_id, frame, reason))

    def _fill_gaps(self):
        """
        Linearly interpolate ellipse parameters across gaps of up to
        :attr:`max_gap` consecutive missed frames.  Interpolated records are
        appended to :attr:`cells` with ``cell.interpolated = True``.
        """
        from collections import defaultdict

        tracks = defaultdict(list)
        for cell in self.cells:
            if cell.cell_found:
                tracks[cell.id].append(cell)

        new_cells = []
        for tid, track_cells in tracks.items():
            track_cells.sort(key=lambda c: c.frame)
            for i in range(len(track_cells) - 1):
                c0  = track_cells[i]
                c1  = track_cells[i + 1]
                gap = c1.frame - c0.frame   # 1 = no gap

                if gap < 2 or gap > self.max_gap + 1:
                    continue

                x0, y0     = c0.ellipse.get_x_center(), c0.ellipse.get_y_center()
                x1, y1     = c1.ellipse.get_x_center(), c1.ellipse.get_y_center()
                maj0, min0 = c0.ellipse.get_major(),    c0.ellipse.get_minor()
                maj1, min1 = c1.ellipse.get_major(),    c1.ellipse.get_minor()
                a0,   a1   = c0.ellipse.get_angle(),    c1.ellipse.get_angle()

                # Shortest angular path on the 180° circle
                da = ((a1 - a0) + 90.0) % 180.0 - 90.0

                for fill_frame in range(c0.frame + 1, c1.frame):
                    t = (fill_frame - c0.frame) / gap
                    new_cells.append(_InterpolatedCell(
                        cell_id    = tid,
                        frame      = fill_frame,
                        pixel_size = self.pixel_size,
                        xc_px      = x0  + t * (x1   - x0),
                        yc_px      = y0  + t * (y1   - y0),
                        major_px   = maj0 + t * (maj1 - maj0),
                        minor_px   = min0 + t * (min1 - min0),
                        angle_deg  = a0  + t * da,
                    ))

        self.cells.extend(new_cells)

    def _detect_mother_daughter(self):
        """
        Identify mother-daughter (bud) relationships between tracks and
        populate :attr:`mother_ids`.

        For each track, the frame where it first appears is examined. A
        candidate mother must have been active before that frame, be
        sufficiently larger than the daughter, and have its centroid within
        ``(r_mother + r_daughter) * bud_distance_factor`` of the daughter.
        The closest qualifying candidate wins.

        Results are also stamped onto every :class:`~pybud.cell.Cell` as
        ``cell.mother_id`` (−1 if no mother found).
        """
        from collections import defaultdict

        by_track = defaultdict(list)
        for cell in self.cells:
            if cell.cell_found and not getattr(cell, 'interpolated', False):
                by_track[cell.id].append(cell)

        if not by_track:
            return

        track_first    = {tid: min(cells, key=lambda c: c.frame)
                          for tid, cells in by_track.items()}
        track_at_frame = {tid: {c.frame: c for c in cells}
                          for tid, cells in by_track.items()}

        self.mother_ids = {}

        for tid, first in track_first.items():
            t0         = first.frame
            r_d        = first.ellipse.get_major()
            cx_d, cy_d = first.ellipse.get_x_center(), first.ellipse.get_y_center()

            best_mother = -1
            best_dist   = float('inf')

            for other_tid, other_first in track_first.items():
                if other_tid == tid or other_first.frame > t0:
                    continue

                if t0 in track_at_frame[other_tid]:
                    other_cell = track_at_frame[other_tid][t0]
                else:
                    earlier = [f for f in track_at_frame[other_tid] if f <= t0]
                    if not earlier:
                        continue
                    other_cell = track_at_frame[other_tid][max(earlier)]

                r_m        = other_cell.ellipse.get_major()
                cx_m, cy_m = other_cell.ellipse.get_x_center(), other_cell.ellipse.get_y_center()

                if r_d >= r_m * self.bud_size_ratio:
                    continue

                dist      = np.hypot(cx_d - cx_m, cy_d - cy_m)
                threshold = (r_m + r_d) * self.bud_distance_factor

                if dist <= threshold and dist < best_dist:
                    best_dist   = dist
                    best_mother = other_tid

            self.mother_ids[tid] = best_mother

        for cell in self.cells:
            cell.mother_id = self.mother_ids.get(cell.id, -1)

    def _filter_overlapping(self):
        """
        Per-frame: if two cells overlap by more than :attr:`overlap_threshold`
        (as a fraction of the smaller cell's area), discard the one with the
        higher ID.  Uses bounding-box masks to avoid full-image allocation.
        """
        if self.overlap_threshold <= 0 or not self.cells:
            return

        from collections import defaultdict
        H, W = self._work_img.shape[2], self._work_img.shape[3]

        by_frame = defaultdict(list)
        for cell in self.cells:
            if cell.cell_found:
                by_frame[cell.frame].append(cell)

        remove = set()
        for frame_cells in by_frame.values():
            frame_cells.sort(key=lambda c: c.id)
            local_areas = {}
            for i, c1 in enumerate(frame_cells):
                if (c1.frame, c1.id) in remove:
                    continue
                for c2 in frame_cells[i + 1:]:
                    if (c2.frame, c2.id) in remove:
                        continue
                    dx = c1.ellipse.get_x_center() - c2.ellipse.get_x_center()
                    dy = c1.ellipse.get_y_center() - c2.ellipse.get_y_center()
                    if np.hypot(dx, dy) > c1.ellipse.get_major() + c2.ellipse.get_major():
                        continue
                    x0 = max(0, int(min(c1.ellipse.get_x_center() - c1.ellipse.get_major(),
                                        c2.ellipse.get_x_center() - c2.ellipse.get_major())))
                    x1 = min(W, int(max(c1.ellipse.get_x_center() + c1.ellipse.get_major(),
                                        c2.ellipse.get_x_center() + c2.ellipse.get_major())) + 1)
                    y0 = max(0, int(min(c1.ellipse.get_y_center() - c1.ellipse.get_major(),
                                        c2.ellipse.get_y_center() - c2.ellipse.get_major())))
                    y1 = min(H, int(max(c1.ellipse.get_y_center() + c1.ellipse.get_major(),
                                        c2.ellipse.get_y_center() + c2.ellipse.get_major())) + 1)
                    m1 = self._ellipse_mask_crop(c1.ellipse, y0, y1, x0, x1)
                    m2 = self._ellipse_mask_crop(c2.ellipse, y0, y1, x0, x1)
                    if c1.id not in local_areas:
                        local_areas[c1.id] = int(m1.sum())
                    if c2.id not in local_areas:
                        local_areas[c2.id] = int(m2.sum())
                    intersection = int((m1 & m2).sum())
                    min_area = min(local_areas[c1.id], local_areas[c2.id])
                    if min_area > 0 and intersection / min_area > self.overlap_threshold:
                        remove.add((c2.frame, c2.id))

        if remove:
            self.cells = [c for c in self.cells
                          if not (c.cell_found and (c.frame, c.id) in remove)]
            print(f"Overlap filter removed {len(remove)} cell-frame measurement(s).")

    def _apply_bg_correction(self):
        """
        Return a float32 copy of :attr:`img` with the BF channel
        Gaussian-background-subtracted.  FL channels are copied unchanged so
        fluorescence measurements use original pixel values.
        """
        from scipy.ndimage import gaussian_filter
        sigma_px = self.bg_correction_sigma / self.pixel_size
        work = self.img.astype(np.float32)
        for t in range(work.shape[0]):
            bf  = work[t, self.bf_channel].astype(np.float64)
            bg  = gaussian_filter(bf, sigma=sigma_px)
            work[t, self.bf_channel] = (bf - bg + float(np.mean(bg))).astype(np.float32)
        return work

    def estimate_fl_offset(self, fl_channel, search_radius_px=8, max_samples=80):
        """
        Estimate a fixed pixel offset between the brightfield-fitted cell
        outlines and ``fl_channel``, for correcting a chromatic/optical
        misalignment between channels (e.g. from a dichroic or a second
        camera). Tries every integer ``(dx, dy)`` shift within
        ``search_radius_px`` and picks the one that, averaged over a sample
        of already-fitted cells, best separates "inside the (shifted) cell
        outline" from "outside it" in the fluorescence signal.

        Call this *after* :meth:`fit_cells` (or :meth:`refit_range`) has
        produced some fitted outlines to compare against. The result is
        **not** applied automatically — store it in
        :attr:`fl_channel_offsets` yourself, then re-run fitting so new
        :class:`~pybud.cell.Cell` objects pick it up (cells already fitted
        keep whatever offset was active when they were fitted).

        Parameters
        ----------
        fl_channel : int
            Channel index to estimate the offset for.
        search_radius_px : int
            Shifts from ``-search_radius_px`` to ``+search_radius_px`` (both
            axes) are tried.
        max_samples : int
            Cap on how many fitted cells to use — evenly sampled if there
            are more, to keep runtime bounded on long movies.

        Returns
        -------
        tuple or None
            ``(dx, dy, best_score, zero_score)`` — the best shift, its mean
            inside-minus-outside contrast score, and that same score at
            zero offset (for gauging how much the shift actually helped).
            ``None`` if there are no fitted, non-interpolated cells to
            compare against.
        """
        if self.img is None:
            return None

        samples = [c for c in self.cells if c.cell_found and not getattr(c, 'interpolated', False)]
        if not samples:
            return None
        if len(samples) > max_samples:
            step = len(samples) / max_samples
            samples = [samples[int(i * step)] for i in range(max_samples)]

        H, W = self.img.shape[2], self.img.shape[3]
        R = int(search_radius_px)

        totals = {}   # (dx, dy) -> [sum_of_contrast, n_samples]
        for cell in samples:
            ellipse = cell.ellipse
            cx, cy  = ellipse.get_x_center(), ellipse.get_y_center()
            pad     = int(ellipse.get_major()) + R + 2

            x0, x1 = max(0, int(cx - pad)), min(W, int(cx + pad) + 1)
            y0, y1 = max(0, int(cy - pad)), min(H, int(cy + pad) + 1)
            if x1 - x0 <= 2 * R or y1 - y0 <= 2 * R:
                continue   # too close to the image edge to search the full range safely

            crop = self.img[cell.frame, fl_channel, y0:y1, x0:x1].astype(np.float64)
            crop_h, crop_w = crop.shape

            for dy in range(-R, R + 1):
                for dx in range(-R, R + 1):
                    # Shifted mask within this cell's local crop (see Ellipse.get_mask):
                    # a candidate (dx, dy) means "sample the fluorescence at the
                    # brightfield position plus this offset".
                    mask = ellipse.get_mask(crop_h, crop_w, offset=(dx - x0, dy - y0))
                    n_in = int(mask.sum())
                    if n_in == 0 or n_in == mask.size:
                        continue
                    contrast = crop[mask].mean() - crop[~mask].mean()
                    key = (dx, dy)
                    if key in totals:
                        totals[key][0] += contrast
                        totals[key][1] += 1
                    else:
                        totals[key] = [contrast, 1]

        if not totals:
            return None

        avg_scores = {k: total / n for k, (total, n) in totals.items()}
        best_dx, best_dy = max(avg_scores, key=avg_scores.get)
        return best_dx, best_dy, avg_scores[(best_dx, best_dy)], avg_scores.get((0, 0), 0.0)

    @staticmethod
    def _ellipse_mask_crop(ellipse, y0, y1, x0, x1):
        """Boolean mask for *ellipse* over the pixel crop ``[y0:y1, x0:x1]``."""
        yg, xg = np.ogrid[y0:y1, x0:x1]
        dx = xg - ellipse.get_x_center()
        dy = yg - ellipse.get_y_center()
        a_rad = np.radians(ellipse.get_angle())
        ca, sa = np.cos(a_rad), np.sin(a_rad)
        xr =  dx * ca + dy * sa
        yr = -dx * sa + dy * ca
        return (xr / ellipse.get_major()) ** 2 + (yr / ellipse.get_minor()) ** 2 <= 1
