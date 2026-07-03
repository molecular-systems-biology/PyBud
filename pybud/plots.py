"""
plots.py — matplotlib figure export for PyBud cell tracks.

Kept separate from the GUI so plots can be generated from scripts
without starting a Qt application.
"""

import os
import numpy as np


class Plots:
    """
    Generate and export per-cell time-series figures.

    All methods are static — instantiation is optional.

    Example
    -------
    >>> from pybud import PyBud, Plots
    >>>
    >>> pb = PyBud().load("movie.tif")
    >>> pb.add_selection(0, 150, 200)
    >>> pb.fit_cells()
    >>>
    >>> found = [c for c in pb.cells if c.cell_found]
    >>> Plots.export_cell_plots(found, pb.time_step, pb.time_unit, "out/",
    ...                         img=pb.img, bf_channel=pb.bf_channel,
    ...                         pixel_size=pb.pixel_size)
    """

    @staticmethod
    def export_cell_plots(cells, time_step: float, time_unit: str, out_dir: str,
                          img=None, bf_channel: int = 0,
                          pixel_size: float = 1.0) -> int:
        """
        Generate a figure for each cell track and save as PNG.

        Layout when *img* is provided:

        * **Left** — cropped brightfield snapshot at the first detected frame,
          with the fitted ellipse overlaid in cyan and a scale bar.
        * **Right** — 2×2 time-series panels (X centroid, Y centroid,
          Major semi-axis, Minor semi-axis).

        Without *img* a plain 2×2 grid is used.

        Parameters
        ----------
        cells : list of Cell
            All cell objects (multiple tracks allowed; grouped internally by ID).
        time_step : float
            Time between frames in *time_unit* units.
        time_unit : str
            Label for the time axis (e.g. ``"s"``).
        out_dir : str
            Directory where PNG files are written (must exist).
        img : ndarray (T, C, H, W), optional
            Raw image stack used for the BF snapshot panel.
        bf_channel : int
            Channel index of the brightfield channel in *img*.
        pixel_size : float
            µm per pixel, used for the scale bar.

        Returns
        -------
        int
            Number of PNG files saved.
        """
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec
        plt.switch_backend("Agg")

        from collections import defaultdict
        tracks = defaultdict(list)
        for cell in cells:
            tracks[cell.id].append(cell)

        use_time = (time_step != 1.0)
        x_label  = f"Time ({time_unit})" if use_time else "Frame"

        saved = 0
        for tid, track_cells in sorted(tracks.items()):
            track_cells.sort(key=lambda c: c.frame)

            frames = np.array([c.frame for c in track_cells], dtype=float)
            x_vals = frames * time_step if use_time else frames
            interp = np.array([getattr(c, "interpolated", False) for c in track_cells])

            panels = [
                ("X centroid (µm)",      np.array([c.x_centroid for c in track_cells])),
                ("Y centroid (µm)",      np.array([c.y_centroid for c in track_cells])),
                ("Major semi-axis (µm)", np.array([c.major      for c in track_cells])),
                ("Minor semi-axis (µm)", np.array([c.minor      for c in track_cells])),
            ]

            show_img = img is not None
            if show_img:
                fig = plt.figure(figsize=(14, 6))
                gs  = GridSpec(1, 2, figure=fig,
                               width_ratios=[1, 2.2], wspace=0.3)
                ax_img  = fig.add_subplot(gs[0, 0])
                gs_plot = gs[0, 1].subgridspec(2, 2, hspace=0.55, wspace=0.45)
                plot_axes = [fig.add_subplot(gs_plot[i, j])
                             for i in range(2) for j in range(2)]
            else:
                fig, plot_axes_arr = plt.subplots(2, 2, figsize=(10, 7))
                plot_axes = list(plot_axes_arr.flat)

            fig.suptitle(f"Cell {tid}", fontsize=13)

            for ax, (ylabel, vals) in zip(plot_axes, panels):
                fitted = ~interp
                ax.plot(x_vals[fitted], vals[fitted], "o-",
                        ms=4, lw=1.2, color="steelblue", label="fitted")
                if interp.any():
                    ax.plot(x_vals[interp], vals[interp], "o",
                            ms=5, color="steelblue", mfc="none", mew=1.5,
                            label="interpolated")
                ax.set_xlabel(x_label, fontsize=9)
                ax.set_ylabel(ylabel,  fontsize=9)
                ax.grid(True, alpha=0.3)
                ax.tick_params(labelsize=8)

            if interp.any():
                plot_axes[0].legend(fontsize=8)

            if show_img:
                try:
                    Plots._cell_snapshot(ax_img, track_cells, img, bf_channel, pixel_size)
                except Exception:
                    ax_img.set_title("(image unavailable)", fontsize=8)
                    ax_img.axis("off")

            fig.savefig(os.path.join(out_dir, f"cell_{tid}.png"),
                        dpi=150, bbox_inches="tight")
            plt.close(fig)
            saved += 1

        return saved

    @staticmethod
    def _cell_snapshot(ax, track_cells, img, bf_channel, pixel_size):
        """Cropped BF image at first fitted frame with ellipse overlay and scale bar."""
        from matplotlib.patches import Ellipse as MEllipse

        first = next(
            (c for c in track_cells if not getattr(c, "interpolated", False)),
            track_cells[0],
        )

        t        = first.frame
        ell      = first.ellipse
        cx_px    = ell.get_x_center()
        cy_px    = ell.get_y_center()
        major_px = ell.get_major()

        pad  = int(major_px * 2.2) + 1
        H, W = img.shape[2], img.shape[3]
        x0, x1 = max(0, int(cx_px) - pad), min(W, int(cx_px) + pad)
        y0, y1 = max(0, int(cy_px) - pad), min(H, int(cy_px) + pad)

        crop = img[t, bf_channel, y0:y1, x0:x1].astype(np.float64)
        lo, hi = np.percentile(crop, 1), np.percentile(crop, 99)
        crop_norm = np.clip((crop - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)

        crop_h, crop_w = crop_norm.shape
        ax.imshow(crop_norm, cmap="gray", vmin=0, vmax=255, aspect="equal")
        ax.set_title(f"Frame {t + 1}", fontsize=8)
        ax.axis("off")

        ax.add_patch(MEllipse(
            (cx_px - x0, cy_px - y0),
            width=2 * ell.get_major(),
            height=2 * ell.get_minor(),
            angle=ell.get_angle(),
            fill=False, edgecolor="cyan", linewidth=1.5, zorder=4,
        ))

        Plots._draw_scalebar(ax, crop_h, crop_w, pixel_size)

    @staticmethod
    def _draw_scalebar(ax, crop_h, crop_w, pixel_size):
        """White scale bar with label in the bottom-left of an imshow axis."""
        from matplotlib.patches import Rectangle

        bar_um = Plots._pick_bar_um(crop_w * 0.25, pixel_size)
        bar_px = bar_um / pixel_size

        margin = max(4, int(crop_w * 0.06))
        bar_h  = max(3, int(crop_h * 0.03))
        bar_y  = crop_h - margin - bar_h

        ax.add_patch(Rectangle((margin, bar_y), bar_px, bar_h,
                                color="white", zorder=5))
        ax.text(margin + bar_px / 2, bar_y - 3, f"{bar_um} µm",
                color="white", fontsize=7, ha="center", va="bottom",
                fontweight="bold", zorder=5)

    @staticmethod
    def _pick_bar_um(target_px, pixel_size):
        """Largest round µm value whose pixel width fits within target_px."""
        bar_um = 1
        for candidate in (1, 2, 5, 10, 20, 50, 100, 200, 500):
            if candidate / pixel_size <= target_px:
                bar_um = candidate
            else:
                break
        return bar_um
