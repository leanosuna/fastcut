"""
Interactive timeline widget with playhead seeker, draggable in/out trim handles,
and time ruler rendering.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget


def format_timestamp(seconds: float) -> str:
    """Formats seconds into HH:MM:SS.mmm or MM:SS.mmm."""
    if seconds < 0:
        seconds = 0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - math.floor(seconds)) * 1000)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"
    return f"{mins:02d}:{secs:02d}.{millis:03d}"


class TimelineWidget(QWidget):
    """
    Custom timeline widget supporting:
    - Playhead scrubbing
    - Visual In and Out trim handles
    - Timecode ruler markings
    - Zoom/drag resilience
    """
    position_changed = Signal(float)       # emitted when playhead is moved by user
    in_point_changed = Signal(float)       # emitted when in-point marker is modified
    out_point_changed = Signal(float)     # emitted when out-point marker is modified
    range_changed = Signal(float, float)   # emitted with (in_point, out_point)

    DRAG_NONE = 0
    DRAG_PLAYHEAD = 1
    DRAG_IN_POINT = 2
    DRAG_OUT_POINT = 3

    HANDLE_WIDTH = 10
    RULER_HEIGHT = 22

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._duration: float = 0.0
        self._position: float = 0.0
        self._in_point: float = 0.0
        self._out_point: float = 0.0

        self._active_drag: int = self.DRAG_NONE
        self._margin: int = 14  # left and right padding in pixels

    # --- Properties and Setters ---

    def set_duration(self, duration: float):
        self._duration = max(0.0, duration)
        self._position = min(self._position, self._duration)
        self._in_point = 0.0
        self._out_point = self._duration
        self.update()

    def set_position(self, position: float):
        self._position = max(0.0, min(position, self._duration))
        self.update()

    def set_in_point(self, timestamp: float):
        self._in_point = max(0.0, min(timestamp, self._out_point))
        self.in_point_changed.emit(self._in_point)
        self.range_changed.emit(self._in_point, self._out_point)
        self.update()

    def set_out_point(self, timestamp: float):
        self._out_point = max(self._in_point, min(timestamp, self._duration))
        self.out_point_changed.emit(self._out_point)
        self.range_changed.emit(self._in_point, self._out_point)
        self.update()

    def reset_trim(self):
        """Resets in/out points to encompass the whole video."""
        self._in_point = 0.0
        self._out_point = self._duration
        self.in_point_changed.emit(self._in_point)
        self.out_point_changed.emit(self._out_point)
        self.range_changed.emit(self._in_point, self._out_point)
        self.update()

    @property
    def in_point(self) -> float:
        return self._in_point

    @property
    def out_point(self) -> float:
        return self._out_point

    @property
    def current_position(self) -> float:
        return self._position

    @property
    def duration(self) -> float:
        return self._duration

    # --- Coordinate conversions ---

    def _usable_width(self) -> float:
        return max(1.0, float(self.width() - 2 * self._margin))

    def _time_to_x(self, t: float) -> float:
        if self._duration <= 0:
            return float(self._margin)
        fraction = max(0.0, min(1.0, t / self._duration))
        return float(self._margin) + fraction * self._usable_width()

    def _x_to_time(self, x: float) -> float:
        if self._duration <= 0:
            return 0.0
        fraction = max(0.0, min(1.0, (x - self._margin) / self._usable_width()))
        return fraction * self._duration

    # --- Hit testing ---

    def _hit_test(self, pos: QPointF) -> int:
        if self._duration <= 0:
            return self.DRAG_NONE

        x = pos.x()
        in_x = self._time_to_x(self._in_point)
        out_x = self._time_to_x(self._out_point)
        play_x = self._time_to_x(self._position)

        # Check handles first
        if abs(x - in_x) <= self.HANDLE_WIDTH:
            return self.DRAG_IN_POINT
        if abs(x - out_x) <= self.HANDLE_WIDTH:
            return self.DRAG_OUT_POINT
        if abs(x - play_x) <= 8:
            return self.DRAG_PLAYHEAD

        return self.DRAG_PLAYHEAD

    # --- Mouse Events ---

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton or self._duration <= 0:
            return

        x = event.position().x()
        in_x = self._time_to_x(self._in_point)
        out_x = self._time_to_x(self._out_point)

        # Handle click priority: in handle, out handle, or playhead/track
        if abs(x - in_x) <= self.HANDLE_WIDTH:
            self._active_drag = self.DRAG_IN_POINT
        elif abs(x - out_x) <= self.HANDLE_WIDTH:
            self._active_drag = self.DRAG_OUT_POINT
        else:
            self._active_drag = self.DRAG_PLAYHEAD
            t = self._x_to_time(x)
            self._position = t
            self.position_changed.emit(t)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._duration <= 0:
            return

        x = event.position().x()
        t = self._x_to_time(x)

        if self._active_drag == self.DRAG_PLAYHEAD:
            self._position = t
            self.position_changed.emit(t)
            self.update()
        elif self._active_drag == self.DRAG_IN_POINT:
            self.set_in_point(min(t, self._out_point))
        elif self._active_drag == self.DRAG_OUT_POINT:
            self.set_out_point(max(t, self._in_point))
        else:
            # Update hover cursor
            in_x = self._time_to_x(self._in_point)
            out_x = self._time_to_x(self._out_point)
            if abs(x - in_x) <= self.HANDLE_WIDTH or abs(x - out_x) <= self.HANDLE_WIDTH:
                self.setCursor(QCursor(Qt.SizeHorCursor))
            else:
                self.setCursor(QCursor(Qt.PointingHandCursor))

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._active_drag = self.DRAG_NONE

    # --- Painting ---

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(0, 0, w, h, QColor(24, 26, 32))

        # Ruler background
        painter.fillRect(0, 0, w, self.RULER_HEIGHT, QColor(18, 20, 24))
        painter.setPen(QPen(QColor(42, 46, 56), 1))
        painter.drawLine(0, self.RULER_HEIGHT, w, self.RULER_HEIGHT)

        # Track area
        track_y = self.RULER_HEIGHT + 6
        track_h = h - track_y - 8
        track_rect = QRectF(self._margin, track_y, self._usable_width(), track_h)

        # Draw empty track
        painter.fillRect(track_rect, QColor(36, 40, 50))

        if self._duration > 0:
            # Active trim range
            in_x = self._time_to_x(self._in_point)
            out_x = self._time_to_x(self._out_point)

            # Dimmed excluded areas
            dim_pen = QPen(Qt.NoPen)
            painter.setPen(dim_pen)

            # Left excluded area
            if in_x > self._margin:
                painter.fillRect(QRectF(self._margin, track_y, in_x - self._margin, track_h), QColor(16, 18, 22, 190))

            # Right excluded area
            if out_x < self._margin + self._usable_width():
                painter.fillRect(QRectF(out_x, track_y, (self._margin + self._usable_width()) - out_x, track_h), QColor(16, 18, 22, 190))

            # Selected trimmed region
            selected_rect = QRectF(in_x, track_y, max(1.0, out_x - in_x), track_h)
            painter.fillRect(selected_rect, QColor(30, 95, 170, 160))

            # Ruler tick marks and labels
            self._draw_ruler(painter)

            # Draw In-Point Handle (Green)
            self._draw_handle(painter, in_x, track_y, track_h, is_in_point=True)

            # Draw Out-Point Handle (Red/Coral)
            self._draw_handle(painter, out_x, track_y, track_h, is_in_point=False)

            # Draw Playhead (Cyan / White)
            play_x = self._time_to_x(self._position)
            self._draw_playhead(painter, play_x, h)
        else:
            # Empty state message
            painter.setPen(QColor(100, 108, 124))
            font = QFont("Sans-Serif", 9)
            painter.setFont(font)
            painter.drawText(QRectF(0, track_y, w, track_h), Qt.AlignCenter, "Open a video to view timeline")

    def _draw_ruler(self, painter: QPainter):
        """Draws time marks along the top ruler."""
        if self._duration <= 0:
            return

        font = QFont("Sans-Serif", 8)
        painter.setFont(font)
        painter.setPen(QColor(120, 130, 148))

        # Determine tick intervals
        usable_w = self._usable_width()
        pixels_per_sec = usable_w / self._duration

        if pixels_per_sec > 100:
            step = 1.0
        elif pixels_per_sec > 40:
            step = 2.0
        elif pixels_per_sec > 15:
            step = 5.0
        elif pixels_per_sec > 5:
            step = 15.0
        elif pixels_per_sec > 1.5:
            step = 30.0
        else:
            step = 60.0

        t = 0.0
        while t <= self._duration:
            x = self._time_to_x(t)
            # Major tick
            painter.drawLine(int(x), self.RULER_HEIGHT - 6, int(x), self.RULER_HEIGHT)
            time_str = format_timestamp(t).split(".")[0]  # Just HH:MM:SS or MM:SS
            painter.drawText(QRectF(x - 30, 2, 60, 14), Qt.AlignCenter, time_str)
            t += step

    def _draw_handle(self, painter: QPainter, x: float, track_y: float, track_h: float, is_in_point: bool):
        color = QColor(46, 204, 113) if is_in_point else QColor(231, 76, 60)
        border_color = QColor(255, 255, 255, 200)

        # Vertical marker line
        painter.setPen(QPen(color, 2))
        painter.drawLine(int(x), int(track_y), int(x), int(track_y + track_h))

        # Handle flag / bracket shape
        handle_w = 8.0
        handle_h = 14.0
        if is_in_point:
            # Bracket pointing right
            poly = QPolygonF([
                QPointF(x, track_y),
                QPointF(x + handle_w, track_y),
                QPointF(x + handle_w, track_y + handle_h),
                QPointF(x, track_y + handle_h),
            ])
        else:
            # Bracket pointing left
            poly = QPolygonF([
                QPointF(x, track_y),
                QPointF(x - handle_w, track_y),
                QPointF(x - handle_w, track_y + handle_h),
                QPointF(x, track_y + handle_h),
            ])

        painter.setBrush(QBrush(color))
        painter.setPen(QPen(border_color, 1))
        painter.drawPolygon(poly)

    def _draw_playhead(self, painter: QPainter, x: float, total_h: float):
        playhead_color = QColor(0, 220, 255)

        # Vertical line through entire widget
        painter.setPen(QPen(playhead_color, 1.5))
        painter.drawLine(int(x), 0, int(x), int(total_h))

        # Top scrubber pin/triangle
        tri_size = 6.0
        pin = QPolygonF([
            QPointF(x - tri_size, 0),
            QPointF(x + tri_size, 0),
            QPointF(x + tri_size, self.RULER_HEIGHT - 6),
            QPointF(x, self.RULER_HEIGHT),
            QPointF(x - tri_size, self.RULER_HEIGHT - 6),
        ])
        painter.setBrush(QBrush(playhead_color))
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawPolygon(pin)
