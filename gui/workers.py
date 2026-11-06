"""
gui/workers.py — QThread workers that run a PyBud operation off the GUI
thread: tracking (Measure/Re-fit/Auto-Detect), FL offset estimation, and
TIF loading. Keeping these off the GUI thread is what keeps the window
responsive during long-running operations.
"""
import numpy as np
import tifffile as tiff
from PyQt5.QtCore import QThread, pyqtSignal

from .state import pybud


class _TrackingWorker(QThread):
    """
    Common shape for every worker below: emit ``frame_processed`` as frames
    complete, report any exception, and always emit ``finished`` exactly once.
    Subclasses only need to implement :meth:`_track`.
    """
    finished        = pyqtSignal()
    frame_processed = pyqtSignal(int)
    progress        = pyqtSignal(int, int)   # (frames_done, frames_total) across all seeds combined
    _error_label    = "tracking"   # subclasses override for a clearer error message

    def run(self):
        try:
            self._track()
        except Exception as e:
            print(f"Error during {self._error_label}: {e}")
        finally:
            self.finished.emit()

    def _track(self):
        raise NotImplementedError

    def _on_frame(self, frame_number):
        self.frame_processed.emit(frame_number)

    def _on_progress(self, done, total):
        self.progress.emit(done, total)

    def stop(self):
        pybud.stop()


class FitCellsWorker(_TrackingWorker):
    _error_label = "fit_cells"

    def _track(self):
        pybud.fit_cells(self._on_frame, self._on_progress)


class RefitRangeWorker(_TrackingWorker):
    _error_label = "refit_range"

    def __init__(self, start_frame, end_frame, cell_ids=None):
        super().__init__()
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.cell_ids = cell_ids

    def _track(self):
        pybud.refit_range(self.start_frame, self.end_frame, self._on_frame, self.cell_ids, self._on_progress)


class AutoDetectWorker(_TrackingWorker):
    """Runs AutoDetect.detect() + pybud.fit_cells(); detection logic lives in pybud.autodetect."""
    _error_label  = "auto-detect"
    status_update = pyqtSignal(str)

    def _track(self):
        if pybud.img is None:
            return

        try:
            from pybud import AutoDetect
            # Trigger the optional import early so we can show a friendly error
            from scipy.ndimage import gaussian_filter   # noqa: F401
            from skimage.feature import canny           # noqa: F401
        except ImportError:
            self.status_update.emit(
                "Auto-detect requires scikit-image. Install with: pip install scikit-image"
            )
            return

        pybud._should_run = True
        pybud.clear()

        self.status_update.emit("Phase 1/2 — detecting cells …")
        AutoDetect().detect(pybud, frame_callback=self.frame_processed.emit)

        n_seeds = sum(len(v) for v in pybud.selections.values())
        self.status_update.emit(f"Phase 2/2 — tracking {n_seeds} seed(s) …")
        pybud.fit_cells(self._on_frame, self._on_progress)


class FLOffsetWorker(QThread):
    """
    Runs PyBud.estimate_fl_offset() off the GUI thread — a one-shot analysis
    over already-fitted cells, not a frame-by-frame tracking run, so it
    doesn't need _TrackingWorker's frame_processed/callback shape.
    """
    finished = pyqtSignal()

    def __init__(self, fl_channel):
        super().__init__()
        self.fl_channel = fl_channel
        self.result = None   # (dx, dy, best_score, zero_score) or None — set before `finished`

    def run(self):
        try:
            self.result = pybud.estimate_fl_offset(self.fl_channel)
        except Exception as e:
            print(f"Error during estimate_fl_offset: {e}")
        finally:
            self.finished.emit()


class ImageLoadWorker(QThread):
    """
    Reads a (possibly large) TIF stack off the GUI thread, reporting real
    per-page progress instead of leaving the UI frozen with no feedback.
    Pages are read one at a time and reshaped into the file's series shape
    (standard layout for ImageJ/OME hyperstacks); if that reshape doesn't
    line up for some unusual file layout, falls back to plain tiff.imread()
    (correct either way — just without per-page progress in that case).
    """
    progress = pyqtSignal(int, int)   # (pages_read, total_pages); total<=0 means indeterminate
    finished = pyqtSignal(object, dict, str)   # (tif_data or None, metadata dict, error message or "")

    def __init__(self, image_path, read_meta_fn):
        super().__init__()
        self.image_path = image_path
        self._read_meta_fn = read_meta_fn

    def run(self):
        try:
            meta = self._read_meta_fn(self.image_path)
        except Exception:
            meta = {}
        try:
            tif_data = self._read_with_progress()
        except Exception as e:
            self.finished.emit(None, meta, str(e))
            return
        self.finished.emit(tif_data, meta, "")

    def _read_with_progress(self):
        with tiff.TiffFile(self.image_path) as tf:
            pages = tf.pages
            n = len(pages)
            series_shape = tf.series[0].shape if tf.series else None
            if series_shape and n > 0:
                try:
                    first = pages[0].asarray()
                    out = np.empty((n,) + first.shape, dtype=first.dtype)
                    out[0] = first
                    self.progress.emit(1, n)
                    for i in range(1, n):
                        out[i] = pages[i].asarray()
                        self.progress.emit(i + 1, n)
                    return out.reshape(series_shape)
                except Exception:
                    pass   # unusual layout -- fall through to the plain read below
        self.progress.emit(0, 0)   # indeterminate: no per-page progress available
        return tiff.imread(self.image_path)
