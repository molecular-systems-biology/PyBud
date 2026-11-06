"""
gui/image_viewer.py — the image panel: contrast histogram, the clickable
image label (seed placement, zoom, pan, overlays), and the ImageViewer
widget that wraps them with frame/channel controls, Measure/Auto-Detect/
Re-fit, and FL-offset estimation.
"""
import time
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal, QEvent, QPoint, QPointF
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QPolygon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QScrollArea, QScrollBar,
    QLineEdit, QPushButton, QMessageBox,
    QComboBox, QSpinBox, QDoubleSpinBox, QShortcut,
)

from .state import pybud
from .workers import FitCellsWorker, RefitRangeWorker, AutoDetectWorker, FLOffsetWorker
from .help_dialog import HelpDialog


class ContrastHistogram(QWidget):
    """
    ImageJ-style brightness/contrast widget: a log-scaled histogram of the
    current frame's raw pixel values, with two draggable vertical handles
    marking the display black-point (min, green) and white-point (max, red).
    Dragging a handle emits range_changed(lo, hi); display-only, exactly like
    the min/max spin boxes it's paired with -- never affects measurements.
    """
    range_changed = pyqtSignal(float, float)

    _MAX_SAMPLE_PIXELS = 500_000   # strided subsample above this, for responsiveness on big frames
    _N_BINS = 256

    _HANDLE_GRAB_PX = 8   # generous hit-box around each handle, beyond the 2px line itself
    _HANDLE_TRIANGLE = 6  # size of the always-visible grip triangle at the bottom of each handle

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self.setMinimumWidth(160)
        self.setMouseTracking(True)
        self.setToolTip("Drag the green/red handles (grip triangles at the bottom) to set "
                         "the display black-/white-point")
        self._counts = None
        self._data_min, self._data_max = 0.0, 1.0
        self._lo, self._hi = 0.0, 1.0
        self._dragging = None   # 'lo', 'hi', or None
        self._hover = None      # 'lo', 'hi', or None -- which handle the cursor is near

    def set_data(self, frame_data, lo, hi):
        """Rebuild the histogram for a newly displayed frame. `lo`/`hi` are the
        current display range (handle positions), independent of the data itself."""
        sample = frame_data
        if sample.size > self._MAX_SAMPLE_PIXELS:
            step = max(1, int(np.ceil((sample.size / self._MAX_SAMPLE_PIXELS) ** 0.5)))
            sample = sample[::step, ::step]

        data_min, data_max = float(sample.min()), float(sample.max())
        if data_max <= data_min:
            data_max = data_min + 1.0
        self._counts = np.histogram(sample, bins=self._N_BINS, range=(data_min, data_max))[0]
        self._data_min, self._data_max = data_min, data_max
        self._lo, self._hi = lo, hi
        self.update()

    def set_range(self, lo, hi):
        """Reposition the handles without recomputing the histogram (e.g. after
        the spin boxes change, or Auto re-stretches)."""
        self._lo, self._hi = lo, hi
        self.update()

    def _value_to_x(self, value):
        span = max(self._data_max - self._data_min, 1e-9)
        return int((value - self._data_min) / span * max(self.width() - 1, 1))

    def _x_to_value(self, x):
        span = max(self._data_max - self._data_min, 1e-9)
        frac = max(0.0, min(1.0, x / max(self.width() - 1, 1)))
        return self._data_min + frac * span

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        if self._counts is not None and self._counts.max() > 0:
            w, h, n = self.width(), self.height(), len(self._counts)
            log_counts = np.log1p(self._counts.astype(np.float64))
            max_log = log_counts.max()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(150, 150, 150))
            for i, c in enumerate(log_counts):
                bar_h = int(c / max_log * (h - 2))
                if bar_h <= 0:
                    continue
                x0 = int(i / n * w)
                x1 = int((i + 1) / n * w)
                painter.drawRect(x0, h - bar_h, max(x1 - x0, 1), bar_h)

        self._draw_handle(painter, self._value_to_x(self._lo), QColor(0, 200, 0), self._hover == 'lo')
        self._draw_handle(painter, self._value_to_x(self._hi), QColor(255, 60, 60), self._hover == 'hi')

    def _draw_handle(self, painter, x, color, is_hot):
        """A vertical line plus an always-visible grip triangle at the bottom,
        so the handle reads as draggable rather than just a plot annotation.
        Widens/brightens slightly on hover or while being dragged."""
        painter.setPen(QPen(color, 3 if is_hot else 2))
        painter.drawLine(x, 0, x, self.height())
        t = self._HANDLE_TRIANGLE + (2 if is_hot else 0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygon([
            QPoint(x - t, self.height()),
            QPoint(x + t, self.height()),
            QPoint(x, self.height() - t),
        ]))

    def _handle_at(self, x):
        """Which handle (if any) is within the grab tolerance of pixel x,
        picking the nearer one when both are in range; always returns the
        nearer handle outside that tolerance too, so a click anywhere still
        drags the closest one."""
        lo_x, hi_x = self._value_to_x(self._lo), self._value_to_x(self._hi)
        return 'lo' if abs(x - lo_x) <= abs(x - hi_x) else 'hi'

    def mousePressEvent(self, event):
        x = event.pos().x()
        self._dragging = self._handle_at(x)
        self._drag_to(x)

    def mouseMoveEvent(self, event):
        x = event.pos().x()
        if self._dragging is not None:
            self._drag_to(x)
        else:
            lo_x, hi_x = self._value_to_x(self._lo), self._value_to_x(self._hi)
            near_lo = abs(x - lo_x) <= self._HANDLE_GRAB_PX
            near_hi = abs(x - hi_x) <= self._HANDLE_GRAB_PX
            hover = ('lo' if abs(x - lo_x) <= abs(x - hi_x) else 'hi') if (near_lo or near_hi) else None
            if hover != self._hover:
                self._hover = hover
                self.setCursor(Qt.SizeHorCursor if hover else Qt.ArrowCursor)
                self.update()

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def _drag_to(self, x):
        value = self._x_to_value(x)
        if self._dragging == 'lo':
            self._lo = min(value, self._hi - 1e-6)
        else:
            self._hi = max(value, self._lo + 1e-6)
        self.update()
        self.range_changed.emit(self._lo, self._hi)


class ClickableImageLabel(QLabel):
    _LABEL_POINT_SIZE = 10   # cell-number labels: a bit bigger than the painter's default
    _FL_OFFSET_REFERENCE_COLOR = QColor(80, 160, 255, 70)   # faint: brightfield position, not sampled from

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tif_data = None
        self.frame = 0
        self.scale_factor = 1
        self.highlighted_cell_id = None
        self.display_channel = None   # None → use pybud.bf_channel
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.show_edge_points = False
        self.show_masks = True   # fitted-ellipse outline; toggled off so it doesn't cover edge points
        self.show_scale_bar = True

        self.scroll_area = None          # set by ImageViewer, needed to anchor zoom on the cursor
        self.zoom_changed_callback = None
        self.min_scale_factor = 0.1
        self.max_scale_factor = 10.0

        # Keyboard focus target for ImageViewer's shortcuts (see
        # ImageViewer.__init__) -- those fire from anywhere in the panel, not
        # just when this label specifically has focus. A left-click claims
        # focus (see mousePressEvent/focusInEvent); the border drawn in
        # focusInEvent/focusOutEvent is the only visual cue for which panel
        # the arrow keys currently steer.
        self.setFocusPolicy(Qt.StrongFocus)
        self._just_focused = False   # set in focusInEvent, consumed in mousePressEvent

        # Space+arrow panning (only reachable once focused, like everything
        # else keyboard-driven here) -- held space arms it (cursor becomes an
        # open hand, purely a visual cue) and the arrow keys then pan the
        # view by a fixed step instead of their normal frame/channel-step
        # behaviour (see event()/keyPressEvent below). Space+drag was tried
        # first (three different mechanisms: grabMouse, an app-wide event
        # filter, and QCursor polling) but abandoned: over this user's
        # remote-desktop + trackpad session, movement during a held button
        # is invisible end to end -- no move events, no QCursor.pos()
        # update, and even the position reported by the eventual release is
        # frozen at the press position -- so there is no mouse-drag signal
        # available here at all, only clicks and plain (buttonless) moves.
        self._space_held = False

        # Set by ImageViewer; used by wheelEvent for Alt+scroll channel stepping
        # (wheel events have no QShortcut equivalent, unlike the keyboard shortcuts).
        self.channel_step_callback = None

        # Per-channel display contrast (raw pixel value -> 0..255 stretch).
        # Display-only: never affects measurement, which always reads raw pybud.img.
        # {channel: (lo, hi)}; a channel is auto-stretched (0.5-99.5 percentile) the
        # first time it's shown, then stays fixed until changed via the UI.
        self.display_ranges = {}

        # Set by ImageViewer: called with (raw_frame_2d, lo, hi) every time a new
        # frame/channel is displayed, to keep the contrast histogram in sync.
        self.histogram_changed_callback = None

        # Set by ImageViewer: called with (image_x, image_y, raw_value) while the
        # cursor moves over the image, or (None, None, None) when it leaves.
        self.pixel_hover_callback = None
        self.setMouseTracking(True)

    def set_frame(self, frame):
        self.frame = frame
        self.update_image_display()

    def update_image_display(self):
        if pybud.img is None:
            self.setText("No image")
            return

        if self.frame >= pybud.img.shape[0]:
            self.frame = pybud.img.shape[0] - 1

        pixmap = self._build_scaled_pixmap()
        if pixmap is None:
            return   # unsupported dtype — _build_scaled_pixmap already set the error text

        painter = QPainter(pixmap)
        self._draw_selection_crosses(painter)
        self._draw_fitted_cells(painter)
        self._draw_mother_daughter_lines(painter)
        self._draw_scale_bar(painter, pixmap)
        painter.end()

        self.setPixmap(pixmap)

    def _build_scaled_pixmap(self):
        """Convert the current frame/channel to a QPixmap at the current zoom level, or
        None (after setting an error label) if the array dtype isn't numeric."""
        n_channels = pybud.img.shape[1]
        ch = self.display_channel if (self.display_channel is not None and
                                       self.display_channel < n_channels) else pybud.bf_channel
        if ch >= n_channels:
            ch = 0

        frame = pybud.img[self.frame, ch]
        if not np.issubdtype(frame.dtype, np.number):
            self.setText("Unsupported image format")
            return None

        lo, hi = self.display_range_for_channel(ch, frame)
        if self.histogram_changed_callback is not None:
            self.histogram_changed_callback(frame, lo, hi)
        frame_8bit = np.clip((frame.astype(np.float64) - lo) / max(hi - lo, 1e-9) * 255,
                             0, 255).astype(np.uint8)
        height, width = frame_8bit.shape
        image = QImage(frame_8bit.data, width, height, frame_8bit.strides[0], QImage.Format_Grayscale8)

        pixmap = QPixmap.fromImage(image)
        return pixmap.scaled(
            int(pixmap.width() * self.scale_factor),
            int(pixmap.height() * self.scale_factor),
            Qt.KeepAspectRatio
        )

    def display_range_for_channel(self, ch, frame_data):
        """Raw pixel (lo, hi) stretched to 0..255 for display on channel `ch`. Auto-computed
        (0.5-99.5 percentile) and cached the first time a channel is shown; stays fixed after
        that until overridden (see ImageViewer's contrast controls)."""
        if ch not in self.display_ranges:
            lo = float(np.percentile(frame_data, 0.5))
            hi = float(np.percentile(frame_data, 99.5))
            if hi <= lo:
                lo, hi = float(frame_data.min()), float(frame_data.max())
            if hi <= lo:
                hi = lo + 1.0
            self.display_ranges[ch] = (lo, hi)
        return self.display_ranges[ch]

    @classmethod
    def _draw_outlined_text(cls, painter, x, y, text, color):
        """
        Draw `text` with a solid black outline so cell-number labels stay
        readable over any pixel value underneath, not just a faint 1px shadow.
        """
        font = painter.font()
        font.setPointSize(cls._LABEL_POINT_SIZE)
        painter.setFont(font)

        painter.setPen(QColor(0, 0, 0))
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
            painter.drawText(x + dx, y + dy, text)
        painter.setPen(color)
        painter.drawText(x, y, text)

    def _draw_selection_crosses(self, painter):
        """Green crosses for manual selections, labelled with the cell number they'll
        receive once fitting runs."""
        pen = QPen(QColor(0, 255, 0), 2)
        painter.setPen(pen)
        if self.frame not in pybud.selections:
            return

        pending_ids = pybud.get_pending_ids()
        for sx, sy in pybud.selections[self.frame]:
            cell_id = pending_ids.get((self.frame, sx, sy))
            x = int(sx * self.scale_factor)
            y = int(sy * self.scale_factor)
            painter.setPen(pen)
            painter.drawLine(x - 5, y - 5, x + 5, y + 5)
            painter.drawLine(x - 5, y + 5, x + 5, y - 5)
            if cell_id is not None:
                self._draw_outlined_text(painter, x + 7, y - 7, str(cell_id), QColor(255, 255, 255))

    def _draw_fitted_cells(self, painter):
        """Edge points (optional) and fitted ellipses for every cell found on this frame.

        On a fluorescence channel with a non-zero FL offset, draws two ellipses
        instead of one: the as-sampled outline with the offset applied (full
        strength, same styling as the normal no-offset case, since this is the
        region fluorescence is actually measured from) and, faintly, the
        brightfield-fitted outline where the cell was originally found
        (reference only -- not where this channel is sampled) -- a visual
        check that the correction looks right, instead of just trusting the
        X/Y numbers in Settings.
        """
        pen = QPen(QColor(0, 255, 0), 2)
        n_channels = pybud.img.shape[1]
        ch = self.display_channel if (self.display_channel is not None and
                                       self.display_channel < n_channels) else pybud.bf_channel
        offset = pybud.fl_channel_offsets.get(ch, (0, 0)) if ch != pybud.bf_channel else (0, 0)
        show_offset_overlay = self.show_masks and offset != (0, 0)

        for cell in pybud.cells:
            if cell.frame != self.frame:
                continue

            if self.show_edge_points:
                pen.setColor(QColor(255, 0, 0))
                painter.setPen(pen)
                for x, y in zip(cell.found_x[cell.pixel_found], cell.found_y[cell.pixel_found]):
                    painter.drawPoint(int(x * self.scale_factor), int(y * self.scale_factor))

            if cell.cell_found:
                ellipse = cell.ellipse
                x = ellipse.get_x_center() * self.scale_factor
                y = ellipse.get_y_center() * self.scale_factor
                major = ellipse.get_major() * self.scale_factor
                minor = ellipse.get_minor() * self.scale_factor
                angle = ellipse.get_angle()
                highlighted = (cell.id == self.highlighted_cell_id)

                sampled_x = x + offset[0] * self.scale_factor if show_offset_overlay else x
                sampled_y = y + offset[1] * self.scale_factor if show_offset_overlay else y

                if self.show_masks:
                    if show_offset_overlay:
                        # Faint reference only -- the brightfield fit position, not
                        # where this channel is actually sampled from.
                        self._draw_ellipse_outline(painter, x, y, major, minor, angle,
                                                    self._FL_OFFSET_REFERENCE_COLOR, 1)

                    color = QColor(0, 220, 255, 220) if highlighted else QColor(255, 255, 0, 128)
                    width = 3 if highlighted else 2
                    self._draw_ellipse_outline(painter, sampled_x, sampled_y, major, minor, angle, color, width)

                # Cell number, placed to the right of the as-sampled ellipse (unrotated, so it stays legible)
                label_x = int(sampled_x + major + 4)
                label_y = int(sampled_y + 4)
                self._draw_outlined_text(painter, label_x, label_y, str(cell.id), QColor(255, 255, 255))

    @staticmethod
    def _draw_ellipse_outline(painter, x, y, major, minor, angle, color, width):
        """Draw one fitted-ellipse outline centred at (x, y) (already scaled to
        display pixels), rotated by `angle` degrees."""
        painter.save()
        painter.setPen(QPen(color, width))
        painter.translate(x, y)
        painter.rotate(angle)
        painter.drawEllipse(QPointF(0, 0), major, minor)
        painter.restore()

    def _draw_mother_daughter_lines(self, painter):
        """Orange dashed line from each daughter cell to its mother, if any."""
        mother_ids = getattr(pybud, 'mother_ids', {})
        if not mother_ids:
            return

        frame_pos = {}   # cell_id -> (display_x, display_y)
        for cell in pybud.cells:
            if cell.frame == self.frame and cell.cell_found:
                frame_pos[cell.id] = (
                    cell.ellipse.get_x_center() * self.scale_factor,
                    cell.ellipse.get_y_center() * self.scale_factor,
                )

        pen_link = QPen(QColor(255, 165, 0), 2)
        pen_link.setStyle(Qt.DashLine)
        painter.setPen(pen_link)
        for child_id, mother_id in mother_ids.items():
            if mother_id >= 0 and child_id in frame_pos and mother_id in frame_pos:
                cx, cy = frame_pos[child_id]
                mx, my = frame_pos[mother_id]
                painter.drawLine(int(mx), int(my), int(cx), int(cy))

    def _draw_scale_bar(self, painter, pixmap):
        """White scale bar with a µm label in the bottom-right corner."""
        if not self.show_scale_bar or pybud.pixel_size <= 0:
            return

        # Pick the largest "nice" length that fits in ~15% of image width
        target_px = pixmap.width() * 0.15
        bar_um = 1
        for candidate in (1, 2, 5, 10, 20, 50, 100, 200, 500):
            if candidate / pybud.pixel_size * self.scale_factor <= target_px:
                bar_um = candidate
            else:
                break
        bar_w = int(bar_um / pybud.pixel_size * self.scale_factor)
        bar_h = max(4, int(self.scale_factor * 3))

        margin  = 10
        bar_x   = pixmap.width()  - margin - bar_w
        bar_y   = pixmap.height() - margin - bar_h - 16

        # Solid white bar with thin black border
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRect(bar_x, bar_y, bar_w, bar_h)

        # Label centred below the bar, with a 1-px dark shadow for contrast
        label = f"{bar_um} µm"
        font  = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        fm    = painter.fontMetrics()
        tx    = bar_x + (bar_w - fm.horizontalAdvance(label)) // 2
        ty    = bar_y + bar_h + 12
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(tx + 1, ty + 1, label)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(tx, ty, label)

    def set_zoom(self, new_scale, anchor_pos=None):
        """
        Change the zoom level, optionally keeping the image point under
        ``anchor_pos`` (label-local coordinates, e.g. from a wheel event)
        fixed under the cursor.
        """
        new_scale = max(self.min_scale_factor, min(self.max_scale_factor, new_scale))
        old_scale = self.scale_factor
        if abs(new_scale - old_scale) < 1e-9:
            return

        hbar = vbar = None
        new_hval = new_vval = 0
        if anchor_pos is not None and self.scroll_area is not None:
            ratio = new_scale / old_scale
            hbar = self.scroll_area.horizontalScrollBar()
            vbar = self.scroll_area.verticalScrollBar()
            new_hval = hbar.value() + anchor_pos.x() * (ratio - 1)
            new_vval = vbar.value() + anchor_pos.y() * (ratio - 1)

        self.scale_factor = new_scale
        self.update_image_display()

        if hbar is not None:
            hbar.setValue(int(round(new_hval)))
            vbar.setValue(int(round(new_vval)))

        if self.zoom_changed_callback is not None:
            self.zoom_changed_callback(self.scale_factor)

    def wheelEvent(self, event):
        if pybud.img is None:
            super().wheelEvent(event)
            return

        vertical, horizontal = event.angleDelta().y(), event.angleDelta().x()
        if vertical == 0 and horizontal == 0:
            super().wheelEvent(event)
            return

        if vertical != 0 and (event.modifiers() & Qt.ControlModifier):
            factor = 1.15 if vertical > 0 else 1 / 1.15
            self.set_zoom(self.scale_factor * factor, event.pos())
            event.accept()
        elif horizontal != 0 or (event.modifiers() & Qt.AltModifier):
            # Alt+wheel is commonly delivered by the OS as a horizontal wheel
            # event (e.g. Windows routes it to a "horizontal scroll" tilt
            # action), sometimes without the Alt modifier bit even surviving —
            # so a horizontal delta alone is also treated as "change channel".
            step = horizontal if horizontal != 0 else vertical
            if self.channel_step_callback is not None:
                self.channel_step_callback(1 if step > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def _to_image_xy(self, pos):
        """Convert a label-local pixel position to (image_x, image_y) in the
        original (unscaled) frame, or None if there's no image or the position
        falls outside the displayed pixmap.

        The bounds check and the final clamp both matter: Qt can report a
        mouse position exactly at pixmap.width()/height() (the one-past-the-
        last-pixel edge), which used to map to image index original_width/
        height -- one past the last valid index -- and crash any direct
        pybud.img[...] lookup keyed on it (e.g. the pixel-hover readout).
        """
        if pybud.img is None:
            return None
        pixmap = self.pixmap()
        if pixmap is None:
            return None
        x, y = pos.x(), pos.y()
        if not (0 <= x < pixmap.width() and 0 <= y < pixmap.height()):
            return None
        original_width = pybud.img.shape[3]
        original_height = pybud.img.shape[2]
        image_x = min(int(x * (original_width / pixmap.width())), original_width - 1)
        image_y = min(int(y * (original_height / pixmap.height())), original_height - 1)
        return image_x, image_y

    def event(self, event):
        # Arrow keys are normally plain QShortcuts on the panel (frame/
        # channel step -- see ImageViewer._register_panel_shortcuts), which
        # Qt resolves via ShortcutOverride before an accepted key event would
        # even reach keyPressEvent below. Claiming it here when Space is held
        # is what lets keyPressEvent's pan branch fire instead of the normal
        # shortcut.
        if (event.type() == QEvent.ShortcutOverride and self._space_held
                and event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if self._space_held and event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._pan_by_key(event.key())
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _pan_by_key(self, key):
        if self.scroll_area is None:
            return
        hbar = self.scroll_area.horizontalScrollBar()
        vbar = self.scroll_area.verticalScrollBar()
        step = max(hbar.singleStep(), 20) * 4   # matches Ctrl+arrow (ImageViewer._pan_horizontal/_pan_vertical)
        if key == Qt.Key_Left:
            hbar.setValue(hbar.value() - step)
        elif key == Qt.Key_Right:
            hbar.setValue(hbar.value() + step)
        elif key == Qt.Key_Up:
            vbar.setValue(vbar.value() - step)
        elif key == Qt.Key_Down:
            vbar.setValue(vbar.value() + step)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        if self._just_focused:
            # Qt grants keyboard focus to a clicked widget before delivering
            # the press event, so self.hasFocus() is already True even on
            # the very click that first claims it -- focusInEvent below
            # flags exactly that case (reason == mouse click) so this same
            # click can be swallowed here instead of also toggling a seed.
            # A second, later click (focus already in place, no fresh
            # focusInEvent) falls through to the normal toggle below.
            self._just_focused = False
            return

        coords = self._to_image_xy(event.pos())
        if coords is not None:
            image_x, image_y = coords
            if pybud.contains_selection(self.frame, image_x, image_y):
                pybud.remove_selection(self.frame, image_x, image_y)
            else:
                pybud.add_selection(self.frame, image_x, image_y)

            self.update_image_display()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if event.reason() == Qt.MouseFocusReason:
            self._just_focused = True
        if self.scroll_area is not None:
            self.scroll_area.setStyleSheet("QScrollArea { border: 2px solid #3d8bff; }")

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._just_focused = False
        # A key release delivered after focus has already moved elsewhere
        # isn't guaranteed, so clear the held state here too rather than
        # risk it getting stuck "armed" with the hand cursor showing.
        self._space_held = False
        self.unsetCursor()
        if self.scroll_area is not None:
            self.scroll_area.setStyleSheet("")

    def mouseMoveEvent(self, event):
        if self.pixel_hover_callback is not None:
            coords = self._to_image_xy(event.pos())
            if coords is not None:
                image_x, image_y = coords
                n_channels = pybud.img.shape[1]
                ch = self.display_channel if (self.display_channel is not None and
                                               self.display_channel < n_channels) else pybud.bf_channel
                value = pybud.img[self.frame, ch, image_y, image_x]
                self.pixel_hover_callback(image_x, image_y, value)
            else:
                self.pixel_hover_callback(None, None, None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.pixel_hover_callback is not None:
            self.pixel_hover_callback(None, None, None)
        super().leaveEvent(event)


class ImageViewer(QWidget):
    measurement_started  = pyqtSignal()
    measurements_changed = pyqtSignal()
    status_message       = pyqtSignal(str)
    tracking_started      = pyqtSignal()        # Measure/Auto-Detect/Re-fit specifically (not FL offset)
    progress_updated      = pyqtSignal(int, int)   # (frames_done, frames_total) relayed from the worker

    _FRAME_UPDATE_THROTTLE_S = 0.1   # cap live image redraws during tracking to ~10/sec

    def __init__(self, settings):
        super().__init__()
        self.settings = settings   # used to sync current field values before every fitting run
        self.worker = None
        self._last_frame_update_ts = 0.0

        self.image_label = ClickableImageLabel()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.image_label)
        self.image_label.scroll_area = self.scroll_area
        self.image_label.zoom_changed_callback = self._on_zoom_changed
        self.image_label.channel_step_callback = self._step_channel   # Alt+wheel (see wheelEvent)
        self.image_label.pixel_hover_callback = self._on_pixel_hover
        self._register_panel_shortcuts()

        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.setMinimum(0)
        self.scrollbar.valueChanged.connect(self.update_frame)

        layout = QVBoxLayout()
        layout.addLayout(self._build_top_bar())
        layout.addLayout(self._build_contrast_bar())
        self.image_label.histogram_changed_callback = self._on_frame_histogram
        self._wire_fl_offset_controls()   # widgets live in Settings now, see _build_advanced_section()
        self._wire_display_controls()     # Show Masks / Show Edge Points / Show Scale Bar, likewise
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.scrollbar)
        layout.addLayout(self._build_action_buttons())
        layout.addLayout(self._build_refit_bar())
        self.setLayout(layout)

    def _register_panel_shortcuts(self):
        """
        Plain Left/Right steps frames, plain Up/Down steps channels (Alt+Up/
        Down also steps channels, to match Alt+wheel); Ctrl+arrows (all four)
        scroll the (possibly zoomed) image instead — so panning is always one
        consistent modifier away in any direction, rather than only available
        vertically. +/- zoom in/out. All scoped to this panel
        (WidgetWithChildrenShortcut) so they fire no matter which control
        inside it currently has focus — e.g. after clicking the channel
        dropdown, not just when the image label itself is focused — while text
        fields (here and in Settings) keep their normal editing behaviour.
        """
        for key, handler in (
            (Qt.Key_Left,             lambda: self._step_frame(-1)),
            (Qt.Key_Right,            lambda: self._step_frame(1)),
            (Qt.Key_Up,               lambda: self._step_channel(-1)),
            (Qt.Key_Down,             lambda: self._step_channel(1)),
            (Qt.ALT + Qt.Key_Up,      lambda: self._step_channel(-1)),
            (Qt.ALT + Qt.Key_Down,    lambda: self._step_channel(1)),
            (Qt.CTRL + Qt.Key_Left,   lambda: self._pan_horizontal(-1)),
            (Qt.CTRL + Qt.Key_Right,  lambda: self._pan_horizontal(1)),
            (Qt.CTRL + Qt.Key_Up,     lambda: self._pan_vertical(-1)),
            (Qt.CTRL + Qt.Key_Down,   lambda: self._pan_vertical(1)),
            (Qt.Key_Plus,             lambda: self._zoom_in()),
            (Qt.Key_Equal,            lambda: self._zoom_in()),
            (Qt.Key_Minus,            lambda: self._zoom_out()),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(handler)

    def _build_top_bar(self):
        """Frame counter, channel selector, zoom controls, help button. Show
        Masks/Show Edge Points/Show Scale Bar live in Settings' Advanced
        Settings now -- see _wire_display_controls()."""
        self.frame_number_label = QLabel("")
        self.pixel_value_label = QLabel("")
        self.pixel_value_label.setMinimumWidth(150)
        self.pixel_value_label.setToolTip("Raw pixel value under the cursor (current channel/frame)")

        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setFixedWidth(28)
        self.zoom_out_button.setToolTip("Zoom out (or Ctrl+scroll / the '-' key on the image)")
        self.zoom_out_button.clicked.connect(self._zoom_out)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignCenter)

        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setFixedWidth(28)
        self.zoom_in_button.setToolTip("Zoom in (or Ctrl+scroll / the '+' key on the image)")
        self.zoom_in_button.clicked.connect(self._zoom_in)

        self.zoom_reset_button = QPushButton("Reset Zoom")
        self.zoom_reset_button.clicked.connect(lambda: self.image_label.set_zoom(1.0))

        self.help_button = QPushButton("Help")
        self.help_button.setToolTip("Show a reference for controls, buttons, and settings")
        self.help_button.clicked.connect(self._show_help)

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(130)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        self._channel_label = QLabel("Channel:")

        top_layout = QHBoxLayout()
        top_layout.addWidget(self.frame_number_label)
        top_layout.addWidget(self.pixel_value_label)
        top_layout.addWidget(self._channel_label)
        top_layout.addWidget(self.channel_combo)
        top_layout.addSpacing(16)
        top_layout.addWidget(self.zoom_out_button)
        top_layout.addWidget(self.zoom_label)
        top_layout.addWidget(self.zoom_in_button)
        top_layout.addWidget(self.zoom_reset_button)
        top_layout.addStretch()
        top_layout.addWidget(self.help_button)
        return top_layout

    def _build_contrast_bar(self):
        """Collapsible histogram (with draggable min/max handles) + precise spin
        boxes + Auto button for the current channel's display contrast. Hidden
        by default behind a toggle button -- it's a secondary/occasional
        control, not something that needs to occupy space on screen at all times."""
        self.contrast_histogram = ContrastHistogram()
        self.contrast_histogram.range_changed.connect(self._on_histogram_range_changed)

        self.contrast_min_spin = QDoubleSpinBox()
        self.contrast_min_spin.setRange(-1e7, 1e7)
        self.contrast_min_spin.setDecimals(1)
        self.contrast_min_spin.setFixedWidth(80)
        self.contrast_min_spin.setToolTip("Display black-point: raw pixel values at or below this show as black")
        self.contrast_min_spin.valueChanged.connect(self._on_contrast_changed)

        self.contrast_max_spin = QDoubleSpinBox()
        self.contrast_max_spin.setRange(-1e7, 1e7)
        self.contrast_max_spin.setDecimals(1)
        self.contrast_max_spin.setFixedWidth(80)
        self.contrast_max_spin.setToolTip("Display white-point: raw pixel values at or above this show as white")
        self.contrast_max_spin.valueChanged.connect(self._on_contrast_changed)

        self.contrast_auto_button = QPushButton("Auto")
        self.contrast_auto_button.setToolTip(
            "Re-stretch contrast for the current channel to its 0.5-99.5 percentile "
            "range. Display only — never affects measurements."
        )
        self.contrast_auto_button.clicked.connect(self._auto_contrast)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Contrast — min:"))
        spin_row.addWidget(self.contrast_min_spin)
        spin_row.addWidget(QLabel("max:"))
        spin_row.addWidget(self.contrast_max_spin)
        spin_row.addWidget(self.contrast_auto_button)
        spin_row.addStretch()

        self.contrast_container = QWidget()
        container_layout = QVBoxLayout(self.contrast_container)
        container_layout.setContentsMargins(0, 4, 0, 0)
        container_layout.addWidget(self.contrast_histogram)
        container_layout.addLayout(spin_row)
        self.contrast_container.setVisible(False)

        self.contrast_toggle = QPushButton("▸ Adjust Contrast")
        self.contrast_toggle.setCheckable(True)
        self.contrast_toggle.setChecked(False)
        self.contrast_toggle.setStyleSheet("text-align: left; padding-left: 4px;")
        self.contrast_toggle.toggled.connect(self._toggle_contrast_panel)

        contrast_layout = QVBoxLayout()
        contrast_layout.setContentsMargins(0, 0, 0, 0)
        contrast_layout.addWidget(self.contrast_toggle)
        contrast_layout.addWidget(self.contrast_container)
        return contrast_layout

    def _toggle_contrast_panel(self, checked):
        self.contrast_container.setVisible(checked)
        arrow = "▾" if checked else "▸"
        self.contrast_toggle.setText(f"{arrow} Adjust Contrast")
        if checked:
            # The histogram skips recomputing while hidden (see
            # _on_frame_histogram) -- refresh it now so it isn't stale/empty
            # the moment it's revealed.
            self.image_label.update_image_display()

    def _wire_fl_offset_controls(self):
        """The FL offset X/Y/Auto widgets live in Settings (under Advanced
        Settings -> FL Channel Offset) so switching the displayed channel here
        never shows/hides a row and shifts the image panel underneath it.
        This just wires their Auto buttons to the estimation worker, which
        needs this class's worker-mutual-exclusion and redraw plumbing."""
        self.settings.fl1_offset_auto_button.clicked.connect(lambda: self.estimate_fl_offset(1))
        self.settings.fl2_offset_auto_button.clicked.connect(lambda: self.estimate_fl_offset(2))

    def _wire_display_controls(self):
        """Show Masks / Show Edge Points / Show Scale Bar checkboxes live in
        Settings (under Advanced Settings -> Display) to keep the image
        panel's own toolbar uncluttered; this wires them to the image
        label's actual display state."""
        self.settings.show_masks_checkbox.stateChanged.connect(self._on_show_masks_changed)
        self.settings.edge_points_checkbox.stateChanged.connect(self.show_edge_points)
        self.settings.show_scale_bar_checkbox.stateChanged.connect(self._on_show_scale_bar_changed)

    def _on_show_scale_bar_changed(self, state):
        self.image_label.show_scale_bar = (state == Qt.Checked)
        self.image_label.update_image_display()

    def _build_action_buttons(self):
        """Auto-Detect / Measure / Stop row."""
        self.auto_detect_frame_button = QPushButton("Auto-Detect This Frame")
        self.auto_detect_frame_button.clicked.connect(self.auto_detect_current_frame)

        self.auto_detect_button = QPushButton("Auto-Detect + Measure (Whole Movie)")
        self.auto_detect_button.clicked.connect(self.auto_detect_measure)

        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self.measure)

        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self.stop)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.auto_detect_frame_button)
        button_layout.addWidget(self.auto_detect_button)
        button_layout.addWidget(self.measure_button)
        button_layout.addWidget(stop_button)
        return button_layout

    def _build_refit_bar(self):
        """From/To/cell(s) fields + Re-fit Frame Range button."""
        self.refit_from_spin = QSpinBox()
        self.refit_from_spin.setMinimum(1)
        self.refit_to_spin = QSpinBox()
        self.refit_to_spin.setMinimum(1)
        self.refit_cells_line = QLineEdit()
        self.refit_cells_line.setPlaceholderText("all cells")
        self.refit_cells_line.setFixedWidth(90)
        self.refit_cells_line.setToolTip(
            "Comma-separated cell IDs to re-fit, e.g. 1,3 (see the Cell column "
            "in the results table, or the numbers next to each ellipse). Every "
            "other cell's existing results in the range are left untouched. "
            "Leave blank to re-fit every cell active in the range."
        )
        self.refit_button = QPushButton("Re-fit Frame Range")
        self.refit_button.setToolTip(
            "Re-run tracking for frames [From, To] only, using the current "
            "settings. Existing tracks are continued from just before 'From'; "
            "new crosses placed inside the range start new tracks. Only the "
            "cell(s) listed (or every active cell, if left blank) are "
            "re-fit — all other frames/cells are left untouched."
        )
        self.refit_button.clicked.connect(self.refit_range)

        refit_layout = QHBoxLayout()
        refit_layout.addWidget(QLabel("Re-fit frames:"))
        refit_layout.addWidget(self.refit_from_spin)
        refit_layout.addWidget(QLabel("to"))
        refit_layout.addWidget(self.refit_to_spin)
        refit_layout.addWidget(QLabel("cell(s):"))
        refit_layout.addWidget(self.refit_cells_line)
        refit_layout.addWidget(self.refit_button)
        refit_layout.addStretch()
        return refit_layout

    def update(self):
        """Full reinitialize — call when a brand-new image has just been loaded
        (resets the frame, channel selection, and scrollbar/spin-box ranges)."""
        if pybud.img is not None:
            self.scrollbar.setMaximum(pybud.img.shape[0] - 1)
            n_frames = pybud.img.shape[0]
            for spin in (self.refit_from_spin, self.refit_to_spin):
                spin.setMaximum(n_frames)
            self._rebuild_channel_combo()
            self.update_frame(0)
            self._refresh_contrast_controls()
            self.settings._refresh_fl_offset_rows()
            self.image_label.setFocus()   # so arrow-key frame/channel stepping works immediately

    def refresh(self):
        """
        Lightweight redraw after a settings tweak on the SAME image (including
        the automatic settings sync before Measure/Auto-Detect/Re-fit) — keeps
        the current frame, channel, and zoom intact instead of resetting them.
        """
        if pybud.img is None:
            return
        self._rebuild_channel_combo()
        self.settings._refresh_fl_offset_rows()   # e.g. picks up fl_channel_offsets from Import Settings
        self.image_label.update_image_display()

    def reset_contrast(self):
        """Clear all per-channel contrast overrides — called when a new file is loaded,
        since a previous channel's display range won't make sense for different data."""
        self.image_label.display_ranges.clear()

    def _current_channel(self):
        return self.image_label.display_channel if self.image_label.display_channel is not None else pybud.bf_channel

    def _refresh_contrast_controls(self):
        """Sync the Min/Max spin boxes to the current channel's (auto or overridden) display range."""
        if pybud.img is None:
            return
        ch = self._current_channel()
        frame_idx = min(self.image_label.frame, pybud.img.shape[0] - 1)
        lo, hi = self.image_label.display_range_for_channel(ch, pybud.img[frame_idx, ch])
        self.contrast_min_spin.blockSignals(True)
        self.contrast_max_spin.blockSignals(True)
        self.contrast_min_spin.setValue(lo)
        self.contrast_max_spin.setValue(hi)
        self.contrast_min_spin.blockSignals(False)
        self.contrast_max_spin.blockSignals(False)

    def _on_contrast_changed(self, _value):
        self.image_label.display_ranges[self._current_channel()] = (
            self.contrast_min_spin.value(), self.contrast_max_spin.value()
        )
        self.image_label.update_image_display()

    def _on_histogram_range_changed(self, lo, hi):
        """Dragging a histogram handle -- mirror into the spin boxes (for the
        precise numeric readout) and apply immediately."""
        self.contrast_min_spin.blockSignals(True)
        self.contrast_max_spin.blockSignals(True)
        self.contrast_min_spin.setValue(lo)
        self.contrast_max_spin.setValue(hi)
        self.contrast_min_spin.blockSignals(False)
        self.contrast_max_spin.blockSignals(False)
        self.image_label.display_ranges[self._current_channel()] = (lo, hi)
        self.image_label.update_image_display()

    def _on_frame_histogram(self, frame_data, lo, hi):
        if self.contrast_container.isVisible():   # skip the work while the panel is collapsed
            self.contrast_histogram.set_data(frame_data, lo, hi)

    def _on_pixel_hover(self, x, y, value):
        if value is None:
            self.pixel_value_label.setText("")
        else:
            self.pixel_value_label.setText(f"Pixel ({x}, {y}): {value}")

    def _auto_contrast(self):
        self.image_label.display_ranges.pop(self._current_channel(), None)
        self._refresh_contrast_controls()
        self.image_label.update_image_display()

    def estimate_fl_offset(self, which):
        """
        Auto-estimate the pixel offset for FL channel 1 or 2 (`which`) from
        already-fitted cells, store it, and sync Settings' spin boxes. Runs in
        a worker thread since it can take a few seconds. Does not retroactively
        change already-fitted cells — run Measure/Re-fit again to apply it.
        """
        if self.worker is not None or pybud.img is None:
            return
        line_edit = self.settings.fluorescent_channel1_line if which == 1 else self.settings.fluorescent_channel2_line
        ch = self.settings._parse_channel_field(line_edit)
        if ch < 0:
            return
        if not any(c.cell_found and not getattr(c, 'interpolated', False) for c in pybud.cells):
            QMessageBox.information(
                self, "No Measurements Yet",
                "Run Measure first so there are fitted cell outlines to "
                "compare against this channel's signal."
            )
            return

        self._set_buttons_enabled(False)
        self.worker = FLOffsetWorker(ch)
        self.worker.finished.connect(self._on_fl_offset_estimated)
        self.worker.start()
        self.measurement_started.emit()
        self.status_message.emit("Estimating channel offset …")

    def _on_fl_offset_estimated(self):
        worker = self.worker
        self.worker = None
        self._set_buttons_enabled(True)

        result = worker.result
        if result is None:
            self.status_message.emit(
                "Couldn't estimate an offset — not enough fitted cells to compare against."
            )
            return

        dx, dy, best_score, zero_score = result
        pybud.fl_channel_offsets[worker.fl_channel] = (dx, dy)
        self.settings._refresh_fl_offset_rows()
        self.image_label.update_image_display()   # redraw so the offset overlay reflects the new value
        self.status_message.emit(
            f"Estimated offset: ({dx}, {dy}) px (signal contrast {best_score:.0f} vs "
            f"{zero_score:.0f} uncorrected). Click Measure (or Re-fit Frame Range) "
            f"again to apply it."
        )

    def _on_zoom_changed(self, scale_factor):
        self.zoom_label.setText(f"{round(scale_factor * 100)}%")

    def _step_frame(self, delta):
        if pybud.img is None:
            return
        new_frame = max(0, min(pybud.img.shape[0] - 1, self.image_label.frame + delta))
        self.update_frame(new_frame)

    def _step_channel(self, delta):
        n = self.channel_combo.count()
        if n <= 1:
            return
        self.channel_combo.setCurrentIndex((self.channel_combo.currentIndex() + delta) % n)

    def _pan_vertical(self, direction):
        """Scroll the (possibly zoomed) image view up/down. direction: -1 up, +1 down."""
        vbar = self.scroll_area.verticalScrollBar()
        step = max(vbar.singleStep(), 20) * 4
        vbar.setValue(vbar.value() + direction * step)

    def _pan_horizontal(self, direction):
        """Scroll the (possibly zoomed) image view left/right. direction: -1 left, +1 right."""
        hbar = self.scroll_area.horizontalScrollBar()
        step = max(hbar.singleStep(), 20) * 4
        hbar.setValue(hbar.value() + direction * step)

    def _zoom_in(self):
        self.image_label.set_zoom(self.image_label.scale_factor * 1.25)

    def _zoom_out(self):
        self.image_label.set_zoom(self.image_label.scale_factor / 1.25)

    def _show_help(self):
        HelpDialog(self).exec_()

    def _rebuild_channel_combo(self):
        """Rebuild the channel list to cover every channel in the loaded image,
        not just whichever ones happen to be assigned as bf_channel/fl_channels
        for measurement -- e.g. extra brightfield slices at other focus depths,
        or an extra fluorescence channel beyond the two Settings exposes, are
        otherwise invisible even though the image data has them. This only
        changes what's available to *look at* (display_channel); it has no
        effect on which channels bf_channel/fl_channels actually measure.
        Preserves the currently displayed channel if it's still valid, else
        falls back to Brightfield (or the first channel, if that's missing too
        -- shouldn't normally happen)."""
        previous = self.image_label.display_channel

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        n_ch = pybud.img.shape[1] if pybud.img is not None else 0
        fl_labels = {ch: f"FL Channel {i + 1}" for i, ch in enumerate(pybud.fl_channels)}
        for ch in range(n_ch):
            if ch == pybud.bf_channel:
                label = "Brightfield"
            elif ch in fl_labels:
                label = fl_labels[ch]
            else:
                label = f"Channel {ch}"
            self.channel_combo.addItem(label, ch)

        idx = self.channel_combo.findData(previous) if previous is not None else -1
        if idx < 0:
            idx = self.channel_combo.findData(pybud.bf_channel)
        if idx < 0:
            idx = 0
        self.channel_combo.setCurrentIndex(idx)
        self.image_label.display_channel = self.channel_combo.itemData(idx)
        self.channel_combo.blockSignals(False)

        # Hide the combo (and its label) when there is nothing to switch to
        has_choice = self.channel_combo.count() > 1
        self.channel_combo.setVisible(has_choice)
        self._channel_label.setVisible(has_choice)

    def _on_channel_changed(self, _idx):
        ch = self.channel_combo.currentData()
        if ch is not None:
            self.image_label.display_channel = ch
            self._refresh_contrast_controls()
            self.image_label.update_image_display()

    def update_frame(self, frame=0):
        self.image_label.set_frame(frame)
        if pybud.img is not None:
            self.frame_number_label.setText(f"Frame: {frame + 1}/{pybud.img.shape[0]}")
        else:
            self.frame_number_label.setText(f"Frame: {frame + 1}")
        self.scrollbar.setValue(frame)

    def select_cell(self, frame, cell_id, x, y):
        self.image_label.highlighted_cell_id = cell_id
        self.update_frame(frame)
        self._center_view_on(x, y)

    def _center_view_on(self, image_x, image_y):
        """
        Scroll the view so image-space point (image_x, image_y) is centred in
        the viewport. A no-op (clamped by QScrollBar) when the whole image
        already fits on screen, e.g. at 100% zoom.
        """
        display_x = image_x * self.image_label.scale_factor
        display_y = image_y * self.image_label.scale_factor
        viewport = self.scroll_area.viewport()
        self.scroll_area.horizontalScrollBar().setValue(int(display_x - viewport.width() / 2))
        self.scroll_area.verticalScrollBar().setValue(int(display_y - viewport.height() / 2))

    def _ready_to_run(self):
        """
        Common pre-flight check for Measure/Auto-Detect/Re-fit: no other run
        already in progress, an image loaded, and the current settings-panel
        values synced into pybud. Returns False (and leaves any invalid-field
        warning dialog on screen) if it's not safe to start.
        """
        if self.worker is not None or pybud.img is None:
            return False
        return self.settings.adjust_settings()

    def auto_detect_measure(self):
        if not self._ready_to_run():
            return
        worker = AutoDetectWorker()
        worker.status_update.connect(self._set_status)
        self._start_worker(worker)

    def auto_detect_current_frame(self):
        """
        Run Hough circle detection on just the displayed frame and place a
        seed cross for each new candidate (skipping any that already have a
        seed nearby). Fast, synchronous, and doesn't track/measure anything —
        click Measure afterwards to track the placed seeds forward. For
        finding cells that first appear partway through the movie, use
        Auto-Detect + Measure (Whole Movie) instead.
        """
        if not self._ready_to_run():
            return

        try:
            from pybud import AutoDetect
            from scipy.ndimage import gaussian_filter   # noqa: F401 — early import check
            from skimage.feature import canny           # noqa: F401
        except ImportError:
            self.status_message.emit(
                "Auto-detect requires scikit-image. Install with: pip install scikit-image"
            )
            return

        frame = self.image_label.frame
        min_r_px = pybud.min_detect_radius_um / pybud.pixel_size
        max_r_px = pybud.max_detect_radius_um / pybud.pixel_size

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            candidates = AutoDetect.detect_frame(
                pybud.img[frame, pybud.bf_channel], min_r_px, max_r_px,
                pybud.n_cells_max, pybud.hough_threshold,
            )
        finally:
            QApplication.restoreOverrideCursor()

        added = 0
        for cx, cy in candidates:
            if not pybud.contains_selection(frame, float(cx), float(cy)):
                pybud.add_selection(frame, float(cx), float(cy))
                added += 1

        self.image_label.update_image_display()
        skipped = len(candidates) - added
        skipped_note = f" ({skipped} already had a seed)" if skipped else ""
        self.status_message.emit(
            f"Auto-detected {added} new cell(s) on frame {frame + 1}{skipped_note}. "
            f"Click Measure to track them."
        )

    def measure(self):
        if not self._ready_to_run():
            return
        self._start_worker(FitCellsWorker())

    def refit_range(self):
        if not self._ready_to_run():
            return
        start = self.refit_from_spin.value() - 1
        end = self.refit_to_spin.value() - 1
        if start > end:
            QMessageBox.warning(self, "Invalid Range",
                                "'From' frame must not be greater than 'To' frame.")
            return

        cell_ids = None
        text = self.refit_cells_line.text().strip()
        if text:
            try:
                cell_ids = {int(tok) for tok in text.replace(',', ' ').split()}
            except ValueError:
                QMessageBox.warning(self, "Invalid Cell List",
                                    "Enter cell IDs as numbers separated by commas "
                                    "(e.g. 1,3), or leave blank to re-fit every cell.")
                return

        self._start_worker(RefitRangeWorker(start, end, cell_ids))
        scope = f"cell(s) {', '.join(str(c) for c in sorted(cell_ids))}" if cell_ids else "all cells"
        self.status_message.emit(f"Re-fitting frames {start + 1}–{end + 1} ({scope}) …")

    def _start_worker(self, worker):
        """Wire up and launch a tracking worker started by Measure/Auto-Detect/Re-fit."""
        self._set_buttons_enabled(False)
        self.worker = worker
        self.worker.finished.connect(self.on_fit_cells_finished)
        self._last_frame_update_ts = 0.0
        self.worker.frame_processed.connect(self._on_tracking_frame)
        self.worker.progress.connect(self.progress_updated.emit)
        self.worker.start()
        self.measurement_started.emit()
        self.tracking_started.emit()

    def _on_tracking_frame(self, frame_number):
        """Connected to the worker's frame_processed signal instead of
        update_frame directly. With many seeds tracking in parallel this can
        fire far faster than any human needs to see a redraw -- each one does
        a full image repaint, and letting every single callback trigger one
        was flooding the GUI thread's event queue and making the whole
        window feel frozen during large multi-seed runs. The progress bar
        (progress_updated) still reports every frame accurately regardless;
        only the heavier live image redraw is throttled."""
        now = time.monotonic()
        if now - self._last_frame_update_ts >= self._FRAME_UPDATE_THROTTLE_S:
            self._last_frame_update_ts = now
            self.update_frame(frame_number)

    def _set_buttons_enabled(self, enabled: bool):
        self.measure_button.setEnabled(enabled)
        self.auto_detect_button.setEnabled(enabled)
        self.auto_detect_frame_button.setEnabled(enabled)
        self.refit_button.setEnabled(enabled)
        self.refit_from_spin.setEnabled(enabled)
        self.refit_to_spin.setEnabled(enabled)
        self.refit_cells_line.setEnabled(enabled)
        self.settings.fl1_offset_auto_button.setEnabled(enabled)
        self.settings.fl2_offset_auto_button.setEnabled(enabled)
        # Also frozen during a run: fl_channel_offsets is read live by Cell
        # construction as each frame is processed, so changing it mid-run
        # could give different cells in the same run different offsets.
        self.settings.fl1_offset_x_spin.setEnabled(enabled)
        self.settings.fl1_offset_y_spin.setEnabled(enabled)
        self.settings.fl2_offset_x_spin.setEnabled(enabled)
        self.settings.fl2_offset_y_spin.setEnabled(enabled)

    def _set_status(self, msg: str):
        self.status_message.emit(msg)

    def show_edge_points(self, state):
        self.image_label.show_edge_points = (state == Qt.Checked)
        self.image_label.update_image_display()

    def _on_show_masks_changed(self, state):
        self.image_label.show_masks = (state == Qt.Checked)
        self.image_label.update_image_display()

    def on_fit_cells_finished(self):
        self.measurements_changed.emit()
        if pybud.lost_cells:
            parts = [f"Cell {cid} lost at frame {frame + 1} ({reason})"
                     for cid, frame, reason in pybud.lost_cells]
            self.status_message.emit("Finished — " + "; ".join(parts))
        else:
            self.status_message.emit("")
        self.worker = None
        self._set_buttons_enabled(True)

    def stop(self):
        if self.worker is not None:
            self.worker.stop()

