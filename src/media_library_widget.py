"""
Media Library sidebar widget displaying loaded video sources.
Supports drag-and-drop file import, thumbnail generation, source selection, and quick actions.
"""

from __future__ import annotations

import os
import tempfile
from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_engine import VideoInfo, generate_thumbnail, probe_video
from timeline_widget import format_timestamp


class MediaLibraryWidget(QWidget):
    """
    Left sidebar managing imported video media assets.
    """
    source_activated = Signal(str)  # file_path
    source_added = Signal(str)  # file_path
    source_removed = Signal(str)  # file_path
    add_to_sequence_requested = Signal(str)  # file_path

    SUPPORTED_EXTENSIONS = {
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv", ".m2ts"
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

        self._sources: Dict[str, VideoInfo] = {}
        self._temp_dir = tempfile.mkdtemp(prefix="fastcut_media_")

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        lbl_title = QLabel("Media Library", self)
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #ecf0f1;")

        self.btn_import = QPushButton("+ Import", self)
        self.btn_import.setToolTip("Import video files (or drag & drop)")
        self.btn_import.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px;"
        )
        self.btn_import.clicked.connect(self._browse_import)

        header.addWidget(lbl_title)
        header.addStretch(1)
        header.addWidget(self.btn_import)
        layout.addLayout(header)

        # List of media assets
        self.list_widget = QListWidget(self)
        self.list_widget.setIconSize(QSize(56, 34))
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background-color: #14171d;
                border: 1px solid #282c37;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 6px;
                border-bottom: 1px solid #1f232b;
            }
            QListWidget::item:selected {
                background-color: #242b38;
                border-left: 3px solid #3b82f6;
                color: #ffffff;
            }
            """
        )
        self.list_widget.itemSelectionChanged.connect(self._on_item_selected)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, stretch=1)

        # Selected source details card
        self.details_card = QFrame(self)
        self.details_card.setFrameShape(QFrame.StyledPanel)
        self.details_card.setStyleSheet(
            "background-color: #1a1e27; border: 1px solid #2e3442; border-radius: 4px; padding: 6px;"
        )
        card_layout = QVBoxLayout(self.details_card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(4)

        self.lbl_selected_title = QLabel("No source selected", self)
        self.lbl_selected_title.setStyleSheet("font-weight: bold; color: #ecf0f1; font-size: 11px;")
        self.lbl_selected_info = QLabel("Import or drag videos above", self)
        self.lbl_selected_info.setStyleSheet("color: #9aa1b2; font-size: 10px; font-family: monospace;")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_add_entire = QPushButton("+ Add to Sequence", self)
        self.btn_add_entire.setToolTip("Add full video to export sequence")
        self.btn_add_entire.setStyleSheet("background-color: #27ae60; color: white; font-size: 10px; padding: 3px 6px;")
        self.btn_add_entire.clicked.connect(self._on_add_entire_clicked)

        self.btn_delete_source = QPushButton("Remove", self)
        self.btn_delete_source.setStyleSheet("color: #e74c3c; font-size: 10px; padding: 3px 6px;")
        self.btn_delete_source.clicked.connect(self._on_remove_selected_clicked)

        btn_row.addWidget(self.btn_add_entire, stretch=1)
        btn_row.addWidget(self.btn_delete_source)

        card_layout.addWidget(self.lbl_selected_title)
        card_layout.addWidget(self.lbl_selected_info)
        card_layout.addLayout(btn_row)

        self.details_card.setVisible(False)
        layout.addWidget(self.details_card)

    def add_source(self, file_path: str, select: bool = True) -> bool:
        """Adds a video file to the media library."""
        if not os.path.isfile(file_path):
            return False

        file_path = os.path.abspath(file_path)
        if file_path in self._sources:
            if select:
                self.select_source(file_path)
            return True

        try:
            info = probe_video(file_path)
        except Exception:
            return False

        self._sources[file_path] = info

        # Generate thumbnail
        thumb_path = os.path.join(self._temp_dir, f"media_thumb_{len(self._sources)}.jpg")
        generate_thumbnail(file_path, min(1.0, info.duration / 2), thumb_path, width=96)

        item = QListWidgetItem()
        name = os.path.basename(file_path)
        dur_str = format_timestamp(info.duration).split(".")[0]
        item.setText(f"{name}\n{dur_str} | {info.width}x{info.height} @ {info.fps:.0f}fps")
        item.setData(Qt.UserRole, file_path)

        if os.path.exists(thumb_path):
            pix = QPixmap(thumb_path).scaled(56, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item.setIcon(QIcon(pix))

        self.list_widget.addItem(item)

        self.source_added.emit(file_path)

        if select:
            self.list_widget.setCurrentItem(item)

        return True

    def select_source(self, file_path: str):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == file_path:
                self.list_widget.setCurrentItem(item)
                break

    def get_selected_source(self) -> Optional[str]:
        cur = self.list_widget.currentItem()
        if cur:
            return cur.data(Qt.UserRole)
        return None

    def _browse_import(self):
        ext_filter = "Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.ts *.m4v);;All Files (*.*)"
        files, _ = QFileDialog.getOpenFileNames(self, "Import Media Files", "", ext_filter)
        for f in files:
            self.add_source(f, select=True)

    def _on_item_selected(self):
        path = self.get_selected_source()
        if not path or path not in self._sources:
            self.details_card.setVisible(False)
            return

        info = self._sources[path]
        name = os.path.basename(path)
        self.lbl_selected_title.setText(name)
        dur_str = format_timestamp(info.duration)
        self.lbl_selected_info.setText(
            f"Duration: {dur_str}\n"
            f"Resolution: {info.width}x{info.height} | {info.fps:.1f} fps\n"
            f"Codec: {info.video_codec.upper()} | Audio: {'Yes' if info.has_audio else 'None'}"
        )
        self.details_card.setVisible(True)
        self.source_activated.emit(path)

    def _on_item_double_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            self.source_activated.emit(path)

    def _on_add_entire_clicked(self):
        path = self.get_selected_source()
        if path:
            self.add_to_sequence_requested.emit(path)

    def _on_remove_selected_clicked(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            item = self.list_widget.takeItem(row)
            path = item.data(Qt.UserRole)
            self._sources.pop(path, None)
            if self.list_widget.count() == 0:
                self.details_card.setVisible(False)
            if path:
                self.source_removed.emit(path)

    # --- Drag & Drop ---

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile():
                    ext = os.path.splitext(u.toLocalFile())[1].lower()
                    if ext in self.SUPPORTED_EXTENSIONS:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        imported_any = False
        for u in event.mimeData().urls():
            if u.isLocalFile():
                path = u.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    if self.add_source(path, select=not imported_any):
                        imported_any = True
        if imported_any:
            event.acceptProposedAction()
        else:
            event.ignore()
