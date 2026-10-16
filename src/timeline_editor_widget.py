"""
Multi-track visual timeline editor widget.
Displays rows for each available media source with editable section blocks,
interactive trim handles, speed modification, slicing, and playhead scrubbing.
"""

from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_engine import ClipSegment
from speed_config import SPEED_PRESETS, get_speed_info
from timeline_widget import format_timestamp


@dataclass
class VisualClip:
    clip_id: str
    file_path: str
    source_start: float  # In seconds in source video
    source_end: float    # Out seconds in source video
    speed: float = 1.0   # Speed multiplier

    @property
    def source_duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def timeline_duration(self) -> float:
        return self.source_duration / max(0.001, self.speed)

    def to_clip_segment(self) -> ClipSegment:
        return ClipSegment(
            file_path=self.file_path,
            start_time=self.source_start,
            end_time=self.source_end,
            speed=self.speed,
        )


class TimelineCanvas(QWidget):
    """
    Custom canvas painting the tracks, visual slice blocks, ruler, and playhead.
    """
    seek_requested = Signal(float)       # global timeline timestamp
    clip_selected = Signal(object)      # VisualClip or None
    clip_modified = Signal(object)      # VisualClip
    timeline_changed = Signal()

    HEADER_WIDTH = 150
    RULER_HEIGHT = 28
    ROW_HEIGHT = 56
    HANDLE_WIDTH = 8

    DRAG_NONE = 0
    DRAG_PLAYHEAD = 1
    DRAG_LEFT_HANDLE = 2
    DRAG_RIGHT_HANDLE = 3

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._media_sources: List[str] = []
        self._clips: List[VisualClip] = []
        self._selected_clip_id: Optional[str] = None

        self._pixels_per_second: float = 80.0  # zoom factor
        self._playhead_time: float = 0.0

        self._drag_mode: int = self.DRAG_NONE
        self._drag_clip_id: Optional[str] = None
        self._drag_start_x: float = 0.0
        self._drag_orig_val: float = 0.0

        self._update_geometry()

    # --- Data Management ---

    def set_media_sources(self, sources: List[str]):
        """Sets the list of available media sources (one row per source)."""
        self._media_sources = list(sources)
        self._update_geometry()
        self.update()

    def add_media_source(self, file_path: str):
        file_p = os.path.abspath(file_path)
        if file_p not in self._media_sources:
            self._media_sources.append(file_p)
            self._update_geometry()
            self.update()

    def remove_media_source(self, file_path: str):
        file_p = os.path.abspath(file_path)
        if file_p in self._media_sources:
            self._media_sources.remove(file_p)
            # Remove any clips belonging to this source
            self._clips = [c for c in self._clips if os.path.abspath(c.file_path) != file_p]
            self._update_geometry()
            self.timeline_changed.emit()
            self.update()

    def set_clips(self, clips: List[ClipSegment]):
        """Populates visual timeline clips from ClipSegments."""
        self._clips.clear()
        for c in clips:
            file_p = os.path.abspath(c.file_path)
            if file_p not in self._media_sources:
                self._media_sources.append(file_p)
            vc = VisualClip(
                clip_id=str(uuid.uuid4()),
                file_path=file_p,
                source_start=c.start_time,
                source_end=c.end_time,
                speed=c.speed,
            )
            self._clips.append(vc)

        self._update_geometry()
        self.timeline_changed.emit()
        self.update()

    def add_clip(self, file_path: str, start_time: float, end_time: float, speed: float = 1.0) -> VisualClip:
        file_p = os.path.abspath(file_path)
        if file_p not in self._media_sources:
            self._media_sources.append(file_p)
        vc = VisualClip(
            clip_id=str(uuid.uuid4()),
            file_path=file_p,
            source_start=start_time,
            source_end=end_time,
            speed=speed,
        )
        self._clips.append(vc)
        self._update_geometry()
        self.timeline_changed.emit()
        self.update()
        return vc

    def clear_all(self):
        self._clips.clear()
        self._selected_clip_id = None
        self._update_geometry()
        self.clip_selected.emit(None)
        self.timeline_changed.emit()
        self.update()

    def get_clips(self) -> List[ClipSegment]:
        return [c.to_clip_segment() for c in self._clips]

    def total_timeline_duration(self) -> float:
        return sum(c.timeline_duration for c in self._clips)

    def set_playhead_time(self, t: float):
        self._playhead_time = max(0.0, min(t, max(1.0, self.total_timeline_duration())))
        self.update()

    def set_zoom(self, pixels_per_second: float):
        self._pixels_per_second = max(10.0, min(500.0, pixels_per_second))
        self._update_geometry()
        self.update()

    def get_selected_clip(self) -> Optional[VisualClip]:
        for c in self._clips:
            if c.clip_id == self._selected_clip_id:
                return c
        return None

    # --- Section Operations ---

    def slice_selected_or_active(
        self, playhead_t: Optional[float] = None, file_path: Optional[str] = None
    ) -> bool:
        """Slices the clip at playhead into two sections."""
        t = playhead_t if playhead_t is not None else self._playhead_time

        target_clip = None
        source_cut_t = None

        if file_path:
            norm_path = os.path.abspath(file_path)
            for c in self._clips:
                if os.path.abspath(c.file_path) == norm_path and c.source_start + 0.05 < t < c.source_end - 0.05:
                    target_clip = c
                    source_cut_t = t
                    break

        if not target_clip:
            clip_info = self._get_clip_at_timeline_time(t)
            if not clip_info:
                return False
            clip, start_t, dur, offset = clip_info
            target_clip = clip
            source_cut_t = clip.source_start + offset * clip.speed

        if not (target_clip.source_start + 0.05 < source_cut_t < target_clip.source_end - 0.05):
            return False

        # Split into two visual clips
        idx = self._clips.index(target_clip)
        part1 = VisualClip(
            clip_id=str(uuid.uuid4()),
            file_path=target_clip.file_path,
            source_start=target_clip.source_start,
            source_end=source_cut_t,
            speed=target_clip.speed,
        )
        part2 = VisualClip(
            clip_id=str(uuid.uuid4()),
            file_path=target_clip.file_path,
            source_start=source_cut_t,
            source_end=target_clip.source_end,
            speed=target_clip.speed,
        )

        self._clips[idx] = part1
        self._clips.insert(idx + 1, part2)
        self._selected_clip_id = part2.clip_id

        self._update_geometry()
        self.clip_selected.emit(part2)
        self.timeline_changed.emit()
        self.update()
        return True

    def remove_selected_clip(self) -> bool:
        sel = self.get_selected_clip()
        if not sel:
            return False
        self._clips.remove(sel)
        self._selected_clip_id = None
        self._update_geometry()
        self.clip_selected.emit(None)
        self.timeline_changed.emit()
        self.update()
        return True

    def set_selected_clip_speed(self, speed: float) -> bool:
        sel = self.get_selected_clip()
        if not sel:
            return False
        sel.speed = speed
        self._update_geometry()
        self.clip_modified.emit(sel)
        self.timeline_changed.emit()
        self.update()
        return True

    # --- Layout Computations ---

    def _update_geometry(self):
        total_sec = max(10.0, self.total_timeline_duration())
        track_count = max(1, len(self._media_sources))
        canvas_w = int(self.HEADER_WIDTH + total_sec * self._pixels_per_second + 200)
        canvas_h = self.RULER_HEIGHT + track_count * self.ROW_HEIGHT + 30
        self.setMinimumSize(canvas_w, canvas_h)
        self.resize(canvas_w, canvas_h)

    def _get_row_for_file(self, file_path: str) -> int:
        file_p = os.path.abspath(file_path)
        for idx, src in enumerate(self._media_sources):
            if os.path.abspath(src) == file_p:
                return idx
        return 0

    def _compute_clip_timeline_spans(self) -> List[Tuple[VisualClip, float, float]]:
        """Returns list of (clip, timeline_start_time, timeline_duration)."""
        spans = []
        cur_t = 0.0
        for c in self._clips:
            dur = c.timeline_duration
            spans.append((c, cur_t, dur))
            cur_t += dur
        return spans

    def _get_clip_at_timeline_time(self, t: float) -> Optional[Tuple[VisualClip, float, float, float]]:
        """Returns (clip, start_t, duration, offset_in_clip) for global time t."""
        spans = self._compute_clip_timeline_spans()
        if not spans:
            return None
        for i, (c, start_t, dur) in enumerate(spans):
            is_last = (i == len(spans) - 1)
            if (start_t <= t < start_t + dur) or (is_last and t >= start_t):
                offset = max(0.0, min(dur, t - start_t))
                return c, start_t, dur, offset
        last = spans[-1][0]
        last_dur = last.timeline_duration
        return last, max(0.0, self.total_timeline_duration() - last_dur), last_dur, last_dur

    def _timeline_to_x(self, t: float) -> float:
        return self.HEADER_WIDTH + t * self._pixels_per_second

    def _x_to_timeline(self, x: float) -> float:
        return max(0.0, (x - self.HEADER_WIDTH) / self._pixels_per_second)

    # --- Mouse Events & Hit Testing ---

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return

        x = event.position().x()
        y = event.position().y()

        # Check click on ruler -> scrub playhead
        if y <= self.RULER_HEIGHT:
            self._drag_mode = self.DRAG_PLAYHEAD
            t = self._x_to_timeline(x)
            self.set_playhead_time(t)
            self.seek_requested.emit(t)
            return

        # Check block or handle click
        hit_clip = None
        for c, start_t, dur in self._compute_clip_timeline_spans():
            row_idx = self._get_row_for_file(c.file_path)
            row_y = self.RULER_HEIGHT + row_idx * self.ROW_HEIGHT
            block_x = self._timeline_to_x(start_t)
            block_w = dur * self._pixels_per_second

            rect = QRectF(block_x, row_y + 4, block_w, self.ROW_HEIGHT - 8)
            if rect.contains(event.position()):
                hit_clip = c
                # Check left handle
                if abs(x - block_x) <= self.HANDLE_WIDTH:
                    self._drag_mode = self.DRAG_LEFT_HANDLE
                    self._drag_clip_id = c.clip_id
                    self._drag_start_x = x
                    self._drag_orig_val = c.source_start
                # Check right handle
                elif abs(x - (block_x + block_w)) <= self.HANDLE_WIDTH:
                    self._drag_mode = self.DRAG_RIGHT_HANDLE
                    self._drag_clip_id = c.clip_id
                    self._drag_start_x = x
                    self._drag_orig_val = c.source_end
                else:
                    self._drag_mode = self.DRAG_PLAYHEAD
                    t = self._x_to_timeline(x)
                    self.set_playhead_time(t)
                    self.seek_requested.emit(t)
                break

        if hit_clip:
            self._selected_clip_id = hit_clip.clip_id
            self.clip_selected.emit(hit_clip)
        else:
            self._selected_clip_id = None
            self.clip_selected.emit(None)
            if x >= self.HEADER_WIDTH:
                self._drag_mode = self.DRAG_PLAYHEAD
                t = self._x_to_timeline(x)
                self.set_playhead_time(t)
                self.seek_requested.emit(t)

        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()

        if self._drag_mode == self.DRAG_PLAYHEAD:
            t = self._x_to_timeline(x)
            self.set_playhead_time(t)
            self.seek_requested.emit(t)
            return

        elif self._drag_mode in (self.DRAG_LEFT_HANDLE, self.DRAG_RIGHT_HANDLE):
            clip = self.get_selected_clip()
            if not clip or clip.clip_id != self._drag_clip_id:
                return

            delta_px = x - self._drag_start_x
            delta_timeline_sec = delta_px / self._pixels_per_second
            delta_source_sec = delta_timeline_sec * clip.speed

            if self._drag_mode == self.DRAG_LEFT_HANDLE:
                new_start = max(0.0, min(clip.source_end - 0.1, self._drag_orig_val + delta_source_sec))
                clip.source_start = new_start
            else:
                new_end = max(clip.source_start + 0.1, self._drag_orig_val + delta_source_sec)
                clip.source_end = new_end

            self._update_geometry()
            self.clip_modified.emit(clip)
            self.timeline_changed.emit()
            self.update()
            return

        # Hover cursor
        cursor_set = False
        if x >= self.HEADER_WIDTH:
            for c, start_t, dur in self._compute_clip_timeline_spans():
                row_idx = self._get_row_for_file(c.file_path)
                row_y = self.RULER_HEIGHT + row_idx * self.ROW_HEIGHT
                block_x = self._timeline_to_x(start_t)
                block_w = dur * self._pixels_per_second

                if abs(y - (row_y + self.ROW_HEIGHT / 2)) <= self.ROW_HEIGHT / 2:
                    if abs(x - block_x) <= self.HANDLE_WIDTH or abs(x - (block_x + block_w)) <= self.HANDLE_WIDTH:
                        self.setCursor(QCursor(Qt.SizeHorCursor))
                        cursor_set = True
                        break
        if not cursor_set:
            self.setCursor(QCursor(Qt.ArrowCursor))

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_mode = self.DRAG_NONE
        self._drag_clip_id = None

    def contextMenuEvent(self, event):
        clip = self.get_selected_clip()
        if not clip:
            return

        menu = QMenu(self)

        # Speed submenu
        speed_menu = menu.addMenu("Speed")
        for s_val, short_lbl, full_lbl, fg, bg in SPEED_PRESETS:
            act = speed_menu.addAction(full_lbl)
            act.triggered.connect(lambda chk, s=s_val: self.set_selected_clip_speed(s))
            if abs(clip.speed - s_val) < 1e-3:
                act.setIcon(self.style().standardIcon(getattr(self.style(), "SP_DialogApplyButton", 0)))

        menu.addSeparator()

        act_slice = menu.addAction("Slice at Playhead (S)")
        act_slice.triggered.connect(lambda: self.slice_selected_or_active())

        act_remove = menu.addAction("Remove Section (Del)")
        act_remove.triggered.connect(self.remove_selected_clip)

        menu.exec(event.globalPos())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_S:
            self.slice_selected_or_active()
            event.accept()
            return
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected_clip()
            event.accept()
            return
        super().keyPressEvent(event)

    # --- Painting ---

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Canvas background
        painter.fillRect(0, 0, w, h, QColor(20, 22, 28))

        # 1. Track Header Column
        painter.fillRect(0, 0, self.HEADER_WIDTH, h, QColor(25, 28, 36))
        painter.setPen(QPen(QColor(38, 42, 54), 1))
        painter.drawLine(self.HEADER_WIDTH, 0, self.HEADER_WIDTH, h)

        # 2. Ruler Header
        painter.fillRect(0, 0, w, self.RULER_HEIGHT, QColor(16, 18, 23))
        painter.setPen(QPen(QColor(38, 42, 54), 1))
        painter.drawLine(0, self.RULER_HEIGHT, w, self.RULER_HEIGHT)

        # Draw Ruler markings
        self._paint_ruler(painter, w)

        # 3. Track rows
        track_count = max(1, len(self._media_sources))
        for r_idx in range(track_count):
            row_y = self.RULER_HEIGHT + r_idx * self.ROW_HEIGHT
            # Alternating subtle row color
            bg_color = QColor(23, 25, 32) if r_idx % 2 == 0 else QColor(20, 22, 28)
            painter.fillRect(self.HEADER_WIDTH, row_y, w - self.HEADER_WIDTH, self.ROW_HEIGHT, bg_color)
            painter.setPen(QPen(QColor(33, 37, 47), 1))
            painter.drawLine(0, row_y + self.ROW_HEIGHT, w, row_y + self.ROW_HEIGHT)

            # Paint track header card on the left
            if r_idx < len(self._media_sources):
                file_path = self._media_sources[r_idx]
                fname = os.path.basename(file_path)
            else:
                fname = "Empty Track"

            header_rect = QRectF(8, row_y + 6, self.HEADER_WIDTH - 16, self.ROW_HEIGHT - 12)
            painter.setPen(QColor(140, 148, 166))
            font = QFont("Sans-Serif", 8, QFont.Bold)
            painter.setFont(font)
            painter.drawText(header_rect, Qt.AlignLeft | Qt.AlignTop, f"Track {r_idx + 1}")

            font_name = QFont("Sans-Serif", 9)
            painter.setFont(font_name)
            painter.setPen(QColor(220, 224, 235))
            painter.drawText(
                QRectF(8, row_y + 24, self.HEADER_WIDTH - 16, self.ROW_HEIGHT - 26),
                Qt.AlignLeft | Qt.AlignTop | Qt.TextSingleLine,
                fname,
            )

        # 4. Paint Visual Blocks
        self._paint_clips(painter)

        # 5. Playhead
        self._paint_playhead(painter, h)

    def _paint_ruler(self, painter: QPainter, width: int):
        font = QFont("Sans-Serif", 8)
        painter.setFont(font)
        painter.setPen(QColor(120, 128, 145))

        step_sec = 1.0 if self._pixels_per_second > 60 else (5.0 if self._pixels_per_second > 20 else 10.0)
        max_time = self._x_to_timeline(width)

        t = 0.0
        while t <= max_time:
            x = self._timeline_to_x(t)
            painter.drawLine(int(x), self.RULER_HEIGHT - 6, int(x), self.RULER_HEIGHT)
            time_str = format_timestamp(t).split(".")[0]
            painter.drawText(QRectF(x - 25, 2, 50, 14), Qt.AlignCenter, time_str)
            t += step_sec

    def _paint_clips(self, painter: QPainter):
        for c, start_t, dur in self._compute_clip_timeline_spans():
            row_idx = self._get_row_for_file(c.file_path)
            row_y = self.RULER_HEIGHT + row_idx * self.ROW_HEIGHT
            block_x = self._timeline_to_x(start_t)
            block_w = max(4.0, dur * self._pixels_per_second)

            rect = QRectF(block_x, row_y + 4, block_w, self.ROW_HEIGHT - 8)
            is_selected = (c.clip_id == self._selected_clip_id)

            # Colors from speed
            _, short_lbl, _, fg_col, bg_col = get_speed_info(c.speed)
            base_color = QColor(bg_col)
            border_color = QColor(0, 210, 255) if is_selected else QColor(fg_col)

            # Block body
            painter.setBrush(QBrush(base_color))
            painter.setPen(QPen(border_color, 2 if is_selected else 1))
            painter.drawRoundedRect(rect, 5, 5)

            # Header & Speed badge
            if block_w > 40:
                font = QFont("Sans-Serif", 8, QFont.Bold)
                painter.setFont(font)
                painter.setPen(QColor(fg_col))

                # Speed badge pill
                badge_w = 44.0
                badge_rect = QRectF(rect.right() - badge_w - 4, rect.top() + 4, badge_w, 16)
                painter.setBrush(QBrush(QColor(bg_col).lighter(140)))
                painter.setPen(QPen(QColor(fg_col), 1))
                painter.drawRoundedRect(badge_rect, 3, 3)
                painter.drawText(badge_rect, Qt.AlignCenter, short_lbl)

                # Clip label
                label_rect = QRectF(rect.left() + 8, rect.top() + 4, max(1.0, rect.width() - badge_w - 14), 16)
                fname = os.path.basename(c.file_path)
                painter.setPen(QColor(240, 243, 250))
                painter.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, fname)

                # Time span label
                dur_str = f"{format_timestamp(dur).split('.')[0]} ({c.source_start:.1f}s-{c.source_end:.1f}s)"
                time_rect = QRectF(rect.left() + 8, rect.bottom() - 18, rect.width() - 16, 14)
                painter.setFont(QFont("Sans-Serif", 7))
                painter.setPen(QColor(180, 186, 200))
                painter.drawText(time_rect, Qt.AlignLeft | Qt.AlignVCenter, dur_str)

            # Edge handles for trimming
            if is_selected:
                painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
                painter.setPen(Qt.NoPen)
                painter.drawRect(QRectF(rect.left(), rect.top() + 4, 3, rect.height() - 8))
                painter.drawRect(QRectF(rect.right() - 3, rect.top() + 4, 3, rect.height() - 8))

    def _paint_playhead(self, painter: QPainter, height: int):
        x = self._timeline_to_x(self._playhead_time)
        color = QColor(0, 220, 255)

        # Scrubber vertical line
        painter.setPen(QPen(color, 2))
        painter.drawLine(int(x), 0, int(x), height)

        # Ruler pointer triangle
        tri = QPolygonF([
            QPointF(x - 6, 0),
            QPointF(x + 6, 0),
            QPointF(x + 6, self.RULER_HEIGHT - 6),
            QPointF(x, self.RULER_HEIGHT),
            QPointF(x - 6, self.RULER_HEIGHT - 6),
        ])
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawPolygon(tri)


class TimelineEditorWidget(QWidget):
    """
    Complete timeline editor container with zoom toolbar, slice/speed actions,
    and horizontal scrollable track canvas.
    """
    seek_requested = Signal(float)       # global timeline seconds
    clip_selected = Signal(object)      # VisualClip
    timeline_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 2)
        toolbar.setSpacing(8)

        lbl_title = QLabel("Timeline", self)
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #ecf0f1;")

        # Actions
        self.btn_slice = QPushButton("✂ Slice (S)", self)
        self.btn_slice.setToolTip("Slice clip at playhead into two sections (Hotkey: S)")
        self.btn_slice.setStyleSheet("color: #00d2ff; font-weight: bold;")
        self.btn_slice.clicked.connect(self._on_slice_clicked)

        self.btn_remove = QPushButton("Remove", self)
        self.btn_remove.setStyleSheet("color: #e74c3c;")
        self.btn_remove.setToolTip("Remove selected section (Del)")
        self.btn_remove.clicked.connect(self._on_remove_clicked)

        # Speed selector
        self.combo_speed = QComboBox(self)
        for s_val, short_lbl, full_lbl, fg, bg in SPEED_PRESETS:
            self.combo_speed.addItem(full_lbl, s_val)
        self.combo_speed.setCurrentIndex(4)  # 1x default
        self.combo_speed.currentIndexChanged.connect(self._on_speed_changed)

        # Zoom slider
        lbl_zoom = QLabel("Zoom:", self)
        lbl_zoom.setStyleSheet("color: #9aa1b2; font-size: 11px;")

        self.slider_zoom = QSlider(Qt.Horizontal, self)
        self.slider_zoom.setRange(20, 200)
        self.slider_zoom.setValue(80)
        self.slider_zoom.setFixedWidth(90)
        self.slider_zoom.valueChanged.connect(self._on_zoom_changed)

        self.lbl_stats = QLabel("00:00:00 total", self)
        self.lbl_stats.setStyleSheet("color: #3498db; font-family: monospace; font-size: 11px;")

        toolbar.addWidget(lbl_title)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.btn_slice)
        toolbar.addWidget(self.btn_remove)
        toolbar.addSpacing(10)
        toolbar.addWidget(QLabel("Speed:", self))
        toolbar.addWidget(self.combo_speed)
        toolbar.addStretch(1)
        toolbar.addWidget(self.lbl_stats)
        toolbar.addSpacing(10)
        toolbar.addWidget(lbl_zoom)
        toolbar.addWidget(self.slider_zoom)

        main_layout.addLayout(toolbar)

        # Scroll Area holding the Canvas
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: 1px solid #282c37; background-color: #14171d; border-radius: 4px; }"
        )

        self.canvas = TimelineCanvas(self.scroll_area)
        self.scroll_area.setWidget(self.canvas)
        main_layout.addWidget(self.scroll_area, stretch=1)

        # Wire Canvas Signals
        self.canvas.seek_requested.connect(self.seek_requested)
        self.canvas.clip_selected.connect(self._on_canvas_clip_selected)
        self.canvas.timeline_changed.connect(self._on_canvas_timeline_changed)

    # --- External Interface ---

    def set_media_sources(self, sources: List[str]):
        self.canvas.set_media_sources(sources)

    def add_media_source(self, source: str):
        self.canvas.add_media_source(source)

    def set_clips(self, clips: List[ClipSegment]):
        self.canvas.set_clips(clips)
        self._update_stats()

    def add_clip(self, file_path: str, start_time: float, end_time: float, speed: float = 1.0) -> VisualClip:
        vc = self.canvas.add_clip(file_path, start_time, end_time, speed)
        self._update_stats()
        return vc

    def clear_all(self):
        self.canvas.clear_all()
        self._update_stats()

    def get_clips(self) -> List[ClipSegment]:
        return self.canvas.get_clips()

    def total_timeline_duration(self) -> float:
        return self.canvas.total_timeline_duration()

    def set_playhead_time(self, t: float):
        self.canvas.set_playhead_time(t)

    def get_clip_at_time(self, t: float) -> Optional[Tuple[ClipSegment, float, float, float]]:
        res = self.canvas._get_clip_at_timeline_time(t)
        if res:
            vc, start_t, dur, offset = res
            return vc.to_clip_segment(), start_t, dur, offset
        return None

    def slice_clip_at(self, playhead_t: Optional[float] = None, file_path: Optional[str] = None) -> bool:
        res = self.canvas.slice_selected_or_active(playhead_t, file_path)
        if res:
            self._update_stats()
        return res

    # --- Internal Slots ---

    def _on_zoom_changed(self, val: int):
        self.canvas.set_zoom(float(val))

    def _on_slice_clicked(self):
        self.canvas.slice_selected_or_active()

    def _on_remove_clicked(self):
        self.canvas.remove_selected_clip()

    def _on_speed_changed(self):
        val = self.combo_speed.currentData()
        if val is not None:
            self.canvas.set_selected_clip_speed(val)

    def _on_canvas_clip_selected(self, clip: Optional[VisualClip]):
        if clip:
            for i in range(self.combo_speed.count()):
                if abs(self.combo_speed.itemData(i) - clip.speed) < 1e-3:
                    self.combo_speed.blockSignals(True)
                    self.combo_speed.setCurrentIndex(i)
                    self.combo_speed.blockSignals(False)
                    break
        self.clip_selected.emit(clip)

    def _on_canvas_timeline_changed(self):
        self._update_stats()
        self.timeline_changed.emit()

    def _update_stats(self):
        dur = self.canvas.total_timeline_duration()
        self.lbl_stats.setText(f"{len(self.canvas._clips)} sections | {format_timestamp(dur).split('.')[0]}")
