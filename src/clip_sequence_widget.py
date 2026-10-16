"""
Sequence tray widget displaying the list of clips to be stitched.
Supports reordering, slicing clips in two sections, per-section speed adjustments,
preview loading, and deletion.
"""

from __future__ import annotations

import os
import tempfile
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_engine import ClipSegment, generate_thumbnail
from speed_config import SPEED_PRESETS, get_speed_info
from timeline_widget import format_timestamp


class ClipSequenceWidget(QWidget):
    """
    Manages the sequence of video clips and sections ready for export,
    including slicing clips and per-section playback speed changes.
    """
    clip_activated = Signal(str, float, float, float)  # file_path, start_time, end_time, speed
    sequence_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._clips: List[ClipSegment] = []
        self._temp_dir = tempfile.mkdtemp(prefix="fastcut_thumbs_")

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header title and sequence stats
        header_bar = QHBoxLayout()
        lbl_title = QLabel("Export Sequence", self)
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #ecf0f1;")

        self.lbl_stats = QLabel("0 sections | 00:00:00.000", self)
        self.lbl_stats.setStyleSheet("color: #95a5a6; font-size: 12px; font-family: monospace;")

        header_bar.addWidget(lbl_title)
        header_bar.addStretch(1)
        header_bar.addWidget(self.lbl_stats)
        layout.addLayout(header_bar)

        # Table of clips / sections
        self.table = QTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["#", "Thumb", "Source File", "Trim Range", "Speed", "Duration"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 80)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setStyleSheet(
            """
            QTableWidget {
                background-color: #16181d;
                border: 1px solid #282c37;
                border-radius: 4px;
                gridline-color: #20242c;
            }
            QHeaderView::section {
                background-color: #20242c;
                color: #b0b6c4;
                padding: 4px;
                border: 1px solid #282c37;
                font-weight: bold;
            }
            QTableWidget::item:selected {
                background-color: #2980b9;
                color: white;
            }
            """
        )
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table, stretch=1)

        # Sequence actions toolbar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(6)

        self.btn_slice = QPushButton("✂ Slice (S)", self)
        self.btn_slice.setToolTip("Slice active section into two independent parts at playhead (Hotkey: S)")
        self.btn_slice.setStyleSheet("color: #00d2ff; font-weight: bold;")

        self.btn_move_up = QPushButton("Move Up", self)
        self.btn_move_up.setToolTip("Move selected section earlier in sequence")
        self.btn_move_up.clicked.connect(self.move_selected_up)

        self.btn_move_down = QPushButton("Move Down", self)
        self.btn_move_down.setToolTip("Move selected section later in sequence")
        self.btn_move_down.clicked.connect(self.move_selected_down)

        self.btn_remove = QPushButton("Remove", self)
        self.btn_remove.setStyleSheet("color: #e74c3c;")
        self.btn_remove.setToolTip("Remove selected section from sequence")
        self.btn_remove.clicked.connect(self.remove_selected)

        self.btn_clear = QPushButton("Clear All", self)
        self.btn_clear.setToolTip("Clear all sections in sequence")
        self.btn_clear.clicked.connect(self.clear_all)

        btn_bar.addWidget(self.btn_slice)
        btn_bar.addWidget(self.btn_move_up)
        btn_bar.addWidget(self.btn_move_down)
        btn_bar.addWidget(self.btn_remove)
        btn_bar.addStretch(1)
        btn_bar.addWidget(self.btn_clear)

        layout.addLayout(btn_bar)

    def add_clip(self, file_path: str, start_time: float, end_time: float, speed: float = 1.0):
        """Adds a trimmed clip segment to the sequence."""
        if not os.path.exists(file_path):
            return

        clip = ClipSegment(file_path=file_path, start_time=start_time, end_time=end_time, speed=speed)
        self._clips.append(clip)
        self._refresh_table()
        self.sequence_changed.emit()

    def slice_clip_at(self, timestamp: float, file_path: Optional[str] = None) -> bool:
        """
        Slices the clip containing `timestamp` into two independent sections.
        Returns True if sliced successfully.
        """
        if not self._clips:
            return False

        target_idx = -1

        # Check currently selected row first
        cur_row = self.table.currentRow()
        if 0 <= cur_row < len(self._clips):
            c = self._clips[cur_row]
            if (not file_path or c.file_path == file_path) and (c.start_time + 0.05 < timestamp < c.end_time - 0.05):
                target_idx = cur_row

        # Otherwise find first matching clip
        if target_idx == -1:
            for idx, c in enumerate(self._clips):
                if (not file_path or c.file_path == file_path) and (c.start_time + 0.05 < timestamp < c.end_time - 0.05):
                    target_idx = idx
                    break

        if target_idx == -1:
            return False

        orig = self._clips[target_idx]
        section1 = ClipSegment(
            file_path=orig.file_path,
            start_time=orig.start_time,
            end_time=timestamp,
            speed=orig.speed,
        )
        section2 = ClipSegment(
            file_path=orig.file_path,
            start_time=timestamp,
            end_time=orig.end_time,
            speed=orig.speed,
        )

        # Replace original with section1, insert section2 right after
        self._clips[target_idx] = section1
        self._clips.insert(target_idx + 1, section2)

        self._refresh_table()
        self.table.selectRow(target_idx + 1)
        self.sequence_changed.emit()
        return True

    def get_clips(self) -> List[ClipSegment]:
        return list(self._clips)

    def total_duration(self) -> float:
        return sum(c.duration for c in self._clips)

    def move_selected_up(self):
        row = self.table.currentRow()
        if row > 0:
            self._clips[row - 1], self._clips[row] = self._clips[row], self._clips[row - 1]
            self._refresh_table()
            self.table.selectRow(row - 1)
            self.sequence_changed.emit()

    def move_selected_down(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._clips) - 1:
            self._clips[row + 1], self._clips[row] = self._clips[row], self._clips[row + 1]
            self._refresh_table()
            self.table.selectRow(row + 1)
            self.sequence_changed.emit()

    def remove_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._clips):
            self._clips.pop(row)
            self._refresh_table()
            if row < len(self._clips):
                self.table.selectRow(row)
            elif len(self._clips) > 0:
                self.table.selectRow(len(self._clips) - 1)
            self.sequence_changed.emit()

    def clear_all(self):
        self._clips.clear()
        self._refresh_table()
        self.sequence_changed.emit()

    def _refresh_table(self):
        self.table.setRowCount(len(self._clips))
        self.table.setIconSize(QSize(64, 36))

        for row, clip in enumerate(self._clips):
            self.table.setRowHeight(row, 44)

            # 1. Index
            item_idx = QTableWidgetItem(f"{row + 1}")
            item_idx.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, item_idx)

            # 2. Thumbnail
            thumb_path = os.path.join(self._temp_dir, f"thumb_{row}_{int(clip.start_time*1000)}.jpg")
            if not os.path.exists(thumb_path):
                sample_t = clip.start_time + min(0.5, clip.source_duration / 2)
                generate_thumbnail(clip.file_path, sample_t, thumb_path, width=128)

            item_thumb = QTableWidgetItem()
            if os.path.exists(thumb_path):
                pix = QPixmap(thumb_path).scaled(64, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                item_thumb.setIcon(QIcon(pix))
            item_thumb.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, item_thumb)

            # 3. Source Filename
            file_name = os.path.basename(clip.file_path)
            item_name = QTableWidgetItem(file_name)
            item_name.setToolTip(clip.file_path)
            self.table.setItem(row, 2, item_name)

            # 4. Trim Range
            trim_str = f"{format_timestamp(clip.start_time)} → {format_timestamp(clip.end_time)}"
            item_range = QTableWidgetItem(trim_str)
            item_range.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, item_range)

            # 5. Speed Selector with color badge
            combo_speed = QComboBox(self.table)
            for s_val, short_lbl, full_lbl, fg, bg in SPEED_PRESETS:
                combo_speed.addItem(full_lbl, s_val)

            # Select current speed
            cur_speed = clip.speed
            matched_idx = 4  # 1x default
            for i in range(combo_speed.count()):
                if abs(combo_speed.itemData(i) - cur_speed) < 1e-3:
                    matched_idx = i
                    break
            combo_speed.setCurrentIndex(matched_idx)
            self._style_speed_combo(combo_speed, cur_speed)

            # Wire speed change
            combo_speed.currentIndexChanged.connect(
                lambda idx, r=row, cb=combo_speed: self._on_speed_changed_for_row(r, cb)
            )
            self.table.setCellWidget(row, 4, combo_speed)

            # 6. Duration
            dur_str = format_timestamp(clip.duration)
            item_dur = QTableWidgetItem(dur_str)
            item_dur.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 5, item_dur)

        total_sec = self.total_duration()
        self.lbl_stats.setText(f"{len(self._clips)} sections | {format_timestamp(total_sec)}")

    def _style_speed_combo(self, combo: QComboBox, speed: float):
        _, _, _, fg, bg = get_speed_info(speed)
        combo.setStyleSheet(
            f"QComboBox {{ background-color: {bg}; color: {fg}; font-weight: bold; border-radius: 4px; padding: 2px 6px; }}"
        )

    def _on_speed_changed_for_row(self, row: int, combo: QComboBox):
        if 0 <= row < len(self._clips):
            new_speed = combo.currentData()
            self._clips[row].speed = new_speed
            self._style_speed_combo(combo, new_speed)

            # Update duration text in column 5
            dur_str = format_timestamp(self._clips[row].duration)
            self.table.item(row, 5).setText(dur_str)

            # Update stats
            total_sec = self.total_duration()
            self.lbl_stats.setText(f"{len(self._clips)} sections | {format_timestamp(total_sec)}")
            self.sequence_changed.emit()

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if 0 <= row < len(self._clips):
            clip = self._clips[row]
            self.clip_activated.emit(clip.file_path, clip.start_time, clip.end_time, clip.speed)
