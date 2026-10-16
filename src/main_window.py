"""
Main application window for FastCut.
Integrates Media library sidebar, player with fullscreen overlay, timeline seeker,
sequence tray with per-section speed, drag-and-drop, menu bar, and GPU status.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from clip_sequence_widget import ClipSequenceWidget
from export_dialog import ExportDialog
from ffmpeg_engine import ClipSegment, VideoInfo, detect_gpu_encoders, probe_video
from media_library_widget import MediaLibraryWidget
from player_widget import PlayerWidget
from timeline_editor_widget import TimelineEditorWidget
from timeline_widget import format_timestamp


class MainWindow(QMainWindow):
    """
    Main editor window with Media sidebar, player, sequence tray, and GPU export.
    """

    SUPPORTED_EXTENSIONS = {
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv", ".m2ts"
    }

    def __init__(self, initial_file: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("FastCut - Cross-Platform Video Editor")
        self.resize(1240, 820)
        self.setAcceptDrops(True)

        self._is_fullscreen: bool = False
        self.encoders = detect_gpu_encoders()

        self._init_ui()
        self._setup_menus()
        self._setup_statusbar()

        if initial_file and os.path.isfile(initial_file):
            self.load_video(initial_file)

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Main horizontal splitter (Left: Media Library, Right: Player + Sequence)
        self.main_hsplitter = QSplitter(Qt.Horizontal, central_widget)
        self.main_hsplitter.setHandleWidth(6)

        # 1. Left Panel: Media Library
        self.media_library = MediaLibraryWidget(self)
        self.media_library.source_activated.connect(self._on_source_activated)
        self.media_library.source_added.connect(self._on_source_added)
        self.media_library.source_removed.connect(self._on_source_removed)
        self.media_library.add_to_sequence_requested.connect(self._on_add_entire_source_to_sequence)
        self.main_hsplitter.addWidget(self.media_library)

        # 2. Right Container
        self.right_container = QWidget(self)
        right_layout = QVBoxLayout(self.right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Vertical Splitter: Player (top) + Timeline Editor (bottom)
        self.splitter = QSplitter(Qt.Vertical, self.right_container)
        self.splitter.setHandleWidth(6)

        # Player Widget
        self.player_widget = PlayerWidget(self)
        self.player_widget.video_loaded.connect(self._on_video_loaded)
        self.player_widget.add_clip_requested.connect(self._on_add_clip_requested)
        self.player_widget.slice_requested.connect(self._on_slice_requested)
        self.player_widget.fullscreen_toggle_requested.connect(self.toggle_fullscreen)
        self.player_widget.speed_changed.connect(self._on_player_speed_changed)

        # Multi-Track Timeline Editor Widget
        self.timeline_editor = TimelineEditorWidget(self)
        self.sequence_widget = self.timeline_editor  # alias for backwards compatibility
        self.player_widget.set_timeline_callbacks(
            self.timeline_editor.get_clip_at_time,
            self.timeline_editor.total_timeline_duration,
        )
        self.timeline_editor.seek_requested.connect(self._on_timeline_seek_requested)
        self.player_widget.timeline_tick.connect(self.timeline_editor.set_playhead_time)
        self.timeline_editor.timeline_changed.connect(self._on_timeline_changed)
        self.timeline_editor.clip_selected.connect(self._on_timeline_clip_selected)
        self.timeline_editor.btn_slice.clicked.connect(self._on_slice_requested)

        self.splitter.addWidget(self.player_widget)
        self.splitter.addWidget(self.timeline_editor)

        # Splitter sizing: 60% player, 40% timeline
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        right_layout.addWidget(self.splitter)
        self.main_hsplitter.addWidget(self.right_container)

        # Proportions: 22% media library, 78% editor
        self.main_hsplitter.setStretchFactor(0, 1)
        self.main_hsplitter.setStretchFactor(1, 4)

        main_layout.addWidget(self.main_hsplitter)

    def _setup_menus(self):
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        action_open = QAction("&Open Video...", self)
        action_open.setShortcut(QKeySequence.Open)
        action_open.triggered.connect(self.action_open_video)
        file_menu.addAction(action_open)

        action_export = QAction("&Export Video...", self)
        action_export.setShortcut(QKeySequence("Ctrl+E"))
        action_export.triggered.connect(self.action_export)
        file_menu.addAction(action_export)

        file_menu.addSeparator()

        action_exit = QAction("E&xit", self)
        action_exit.setShortcut(QKeySequence.Quit)
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        # Edit Menu
        edit_menu = menubar.addMenu("&Edit")

        action_slice = QAction("&Slice Clip at Playhead", self)
        action_slice.setShortcut(QKeySequence(Qt.Key_S))
        action_slice.triggered.connect(self._on_slice_requested)
        edit_menu.addAction(action_slice)

        action_mark_in = QAction("Mark &In", self)
        action_mark_in.setShortcut(QKeySequence(Qt.Key_I))
        action_mark_in.triggered.connect(self.player_widget.mark_in_at_current)
        edit_menu.addAction(action_mark_in)

        action_mark_out = QAction("Mark &Out", self)
        action_mark_out.setShortcut(QKeySequence(Qt.Key_O))
        action_mark_out.triggered.connect(self.player_widget.mark_out_at_current)
        edit_menu.addAction(action_mark_out)

        action_reset_trim = QAction("&Reset Trim Range", self)
        action_reset_trim.setShortcut(QKeySequence("Ctrl+R"))
        action_reset_trim.triggered.connect(self.player_widget.timeline.reset_trim)
        edit_menu.addAction(action_reset_trim)

        edit_menu.addSeparator()

        action_add_seq = QAction("&Add Trim to Sequence", self)
        action_add_seq.setShortcut(QKeySequence(Qt.Key_Return))
        action_add_seq.triggered.connect(self.player_widget._on_add_to_sequence_clicked)
        edit_menu.addAction(action_add_seq)

        action_clear_seq = QAction("&Clear Sequence", self)
        action_clear_seq.triggered.connect(self.sequence_widget.clear_all)
        edit_menu.addAction(action_clear_seq)

        # View Menu
        view_menu = menubar.addMenu("&View")

        action_fullscreen = QAction("&Toggle Fullscreen", self)
        action_fullscreen.setShortcut(QKeySequence("F11"))
        action_fullscreen.triggered.connect(self.toggle_fullscreen)
        view_menu.addAction(action_fullscreen)

        # Help Menu
        help_menu = menubar.addMenu("&Help")

        action_gpu_info = QAction("&GPU Acceleration Info", self)
        action_gpu_info.triggered.connect(self.action_show_gpu_info)
        help_menu.addAction(action_gpu_info)

        action_shortcuts = QAction("&Keyboard Shortcuts", self)
        action_shortcuts.triggered.connect(self.action_show_shortcuts)
        help_menu.addAction(action_shortcuts)

        action_about = QAction("&About FastCut", self)
        action_about.triggered.connect(self.action_about)
        help_menu.addAction(action_about)

        # Toolbar
        self.main_toolbar = QToolBar("Main Toolbar", self)
        self.main_toolbar.setMovable(False)
        self.addToolBar(self.main_toolbar)

        self.main_toolbar.addAction(action_open)
        action_slice_tool = self.main_toolbar.addAction("Slice (S)")
        action_slice_tool.triggered.connect(self._on_slice_requested)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(action_export)

    def _setup_statusbar(self):
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)

        self.lbl_status = QLabel("Ready. Import media or drag and drop videos.", self)
        status_bar.addWidget(self.lbl_status, stretch=1)

        # GPU acceleration status label
        gpu_encoders = [e for e in self.encoders if e.is_gpu]
        if gpu_encoders:
            primary_gpu = gpu_encoders[0]
            gpu_text = f"GPU Acceleration: {primary_gpu.label}"
            self.lbl_gpu = QLabel(gpu_text, self)
            self.lbl_gpu.setStyleSheet("color: #2ecc71; font-weight: bold; padding: 0 8px;")
        else:
            self.lbl_gpu = QLabel("GPU: Not Detected (CPU fallback)", self)
            self.lbl_gpu.setStyleSheet("color: #e67e22; padding: 0 8px;")

        status_bar.addPermanentWidget(self.lbl_gpu)

    # --- Actions ---

    def action_open_video(self):
        ext_filter = "Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.ts *.m4v);;All Files (*.*)"
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video File", "", ext_filter)
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path: str):
        file_path = os.path.abspath(file_path)
        self.media_library.add_source(file_path, select=False)
        self.timeline_editor.add_media_source(file_path)
        self.player_widget.load_video(file_path)

        if len(self.timeline_editor.get_clips()) == 0:
            info = self.player_widget.video_info or probe_video(file_path)
            dur = info.duration if info else 10.0
            self.timeline_editor.add_clip(file_path, 0.0, dur)
            self.player_widget.seek_timeline(0.0)

    def _on_source_activated(self, file_path: str):
        self.player_widget.load_video(file_path)
        self.player_widget.set_preview_mode(PlayerWidget.MODE_SOURCE)
        self.lbl_status.setText(f"Opened source '{os.path.basename(file_path)}' in Source Preview.")

    def _on_source_added(self, file_path: str):
        self.timeline_editor.add_media_source(file_path)
        if len(self.timeline_editor.get_clips()) == 0:
            try:
                info = probe_video(file_path)
                self.timeline_editor.add_clip(file_path, 0.0, info.duration)
                self.player_widget.seek_timeline(0.0)
            except Exception:
                pass

    def _on_source_removed(self, file_path: str):
        self.timeline_editor.canvas.remove_media_source(file_path)
        self.lbl_status.setText(f"Removed source '{os.path.basename(file_path)}' from timeline.")

    def _on_add_entire_source_to_sequence(self, file_path: str):
        if os.path.isfile(file_path):
            info = self.media_library._sources.get(file_path) or probe_video(file_path)
            dur = info.duration if info else 10.0
            self.timeline_editor.add_clip(file_path, 0.0, dur)
            self.lbl_status.setText(f"Added entire '{os.path.basename(file_path)}' to timeline.")

    def _on_slice_requested(self, timestamp: Optional[float] = None):
        """Slices active clip at current playhead timestamp into two selectable sections."""
        if self.player_widget.preview_mode == PlayerWidget.MODE_TIMELINE:
            t = timestamp if timestamp is not None and isinstance(timestamp, (int, float)) else self.player_widget.current_timeline_time
            sliced = self.timeline_editor.slice_clip_at(t)
            if sliced:
                self.lbl_status.setText(f"Sliced timeline clip into two sections at {format_timestamp(t)}.")
            else:
                self.lbl_status.setText("Could not slice at this position. Position the playhead inside a clip.")
        else:
            cur_file = self.player_widget.current_file
            if not cur_file:
                self.lbl_status.setText("Open a video first before slicing.")
                return

            t = timestamp if timestamp is not None and isinstance(timestamp, (int, float)) else self.player_widget.timeline.current_position
            sliced = self.timeline_editor.slice_clip_at(t, cur_file)
            if sliced:
                self.lbl_status.setText(f"Sliced clip into two sections at {format_timestamp(t)}.")
                return

            in_p = self.player_widget.timeline.in_point
            out_p = self.player_widget.timeline.out_point
            if in_p + 0.05 < t < out_p - 0.05:
                cur_speed = getattr(self.player_widget, "_current_speed", 1.0)
                self.timeline_editor.add_clip(cur_file, in_p, t, speed=cur_speed)
                self.timeline_editor.add_clip(cur_file, t, out_p, speed=cur_speed)
                self.lbl_status.setText(f"Sliced video into 2 sections in timeline at {format_timestamp(t)}.")
            else:
                self.lbl_status.setText("Playhead must be between In and Out markers to slice.")

    def action_export(self):
        clips = self.timeline_editor.get_clips()
        if not clips:
            cur_file = self.player_widget.current_file
            if cur_file:
                in_p = self.player_widget.timeline.in_point
                out_p = self.player_widget.timeline.out_point
                cur_speed = getattr(self.player_widget, "_current_speed", 1.0)
                clips = [ClipSegment(file_path=cur_file, start_time=in_p, end_time=out_p, speed=cur_speed)]
            else:
                QMessageBox.information(
                    self,
                    "No Clips to Export",
                    "Please open a video and add at least one clip or section to the export timeline.",
                )
                return

        dialog = ExportDialog(clips, self)
        dialog.exec()

    def action_show_gpu_info(self):
        gpu_list = [f"• {e.label} ({e.codec_id})" for e in self.encoders if e.is_gpu]
        cpu_list = [f"• {e.label} ({e.codec_id})" for e in self.encoders if not e.is_gpu]

        msg = "<h3>Detected Hardware Video Encoders</h3>"
        if gpu_list:
            msg += "<p><b>GPU Hardware Accelerated:</b></p><ul>" + "".join(f"<li>{item}</li>" for item in gpu_list) + "</ul>"
        else:
            msg += "<p><i>No GPU encoders detected. Software encoders will be used.</i></p>"

        if cpu_list:
            msg += "<p><b>Software / CPU Encoders:</b></p><ul>" + "".join(f"<li>{item}</li>" for item in cpu_list) + "</ul>"

        QMessageBox.information(self, "GPU Acceleration Info", msg)

    def action_show_shortcuts(self):
        shortcuts_text = """
        <h3>FastCut Keyboard Shortcuts</h3>
        <table cellpadding="4">
            <tr><td><b>Space</b></td><td>Play / Pause video</td></tr>
            <tr><td><b>S</b></td><td>Slice clip in two sections at playhead</td></tr>
            <tr><td><b>I</b></td><td>Set Mark In at current playhead position</td></tr>
            <tr><td><b>O</b></td><td>Set Mark Out at current playhead position</td></tr>
            <tr><td><b>Left Arrow</b></td><td>Step backward 0.1s</td></tr>
            <tr><td><b>Right Arrow</b></td><td>Step forward 0.1s</td></tr>
            <tr><td><b>Enter</b></td><td>Add current trimmed clip to sequence</td></tr>
            <tr><td><b>Double Click</b></td><td>Toggle fullscreen mode (with auto-hiding overlay)</td></tr>
            <tr><td><b>Esc</b></td><td>Exit fullscreen mode</td></tr>
            <tr><td><b>F11</b></td><td>Toggle fullscreen mode</td></tr>
            <tr><td><b>Ctrl + O</b></td><td>Open video file</td></tr>
            <tr><td><b>Ctrl + E</b></td><td>Export sequence</td></tr>
            <tr><td><b>Ctrl + R</b></td><td>Reset trim range to entire video</td></tr>
        </table>
        """
        QMessageBox.information(self, "Keyboard Shortcuts", shortcuts_text)

    def action_about(self):
        QMessageBox.about(
            self,
            "About FastCut",
            "<b>FastCut</b><br>"
            "Cross-platform simple video editor with GPU acceleration.<br>"
            "Easily trim videos, slice clips into sections, adjust speeds (0.1x to 5x), and stitch exports.",
        )

    # --- Signals ---

    def _on_video_loaded(self, info: VideoInfo):
        name = os.path.basename(info.file_path)
        status = (
            f"Loaded: {name} | {info.width}x{info.height} @ {info.fps:.2f}fps | "
            f"Codec: {info.video_codec.upper()} | Audio: {'Yes' if info.has_audio else 'None'}"
        )
        self.lbl_status.setText(status)

    def _on_add_clip_requested(self, file_path: str, in_p: float, out_p: float):
        cur_speed = getattr(self.player_widget, "_current_speed", 1.0)
        self.timeline_editor.add_clip(file_path, in_p, out_p, speed=cur_speed)
        name = os.path.basename(file_path)
        self.lbl_status.setText(f"Added section of '{name}' to timeline.")

    def _on_timeline_seek_requested(self, t: float):
        if self.player_widget.preview_mode != PlayerWidget.MODE_TIMELINE:
            self.player_widget.set_preview_mode(PlayerWidget.MODE_TIMELINE)
        self.player_widget.seek_timeline(t)

    def _on_timeline_changed(self):
        total_dur = self.timeline_editor.total_timeline_duration()
        if self.player_widget.preview_mode == PlayerWidget.MODE_TIMELINE:
            self.player_widget.overlay.set_duration(total_dur)
            cur_t = self.player_widget.current_timeline_time
            if cur_t > total_dur:
                self.player_widget.seek_timeline(max(0.0, total_dur))

    def _on_timeline_clip_selected(self, clip):
        if clip:
            name = os.path.basename(clip.file_path)
            self.lbl_status.setText(
                f"Selected: '{name}' [{clip.source_start:.2f}s - {clip.source_end:.2f}s] @ {clip.speed}x"
            )

    def _on_player_speed_changed(self, speed: float):
        if self.player_widget.preview_mode == PlayerWidget.MODE_TIMELINE:
            self.timeline_editor.canvas.set_selected_clip_speed(speed)

    def _on_sequence_clip_activated(self, file_path: str, start_t: float, end_t: float, speed: float = 1.0):
        if file_path != self.player_widget.current_file:
            self.load_video(file_path)
        self.player_widget.timeline.set_in_point(start_t)
        self.player_widget.timeline.set_out_point(end_t)
        self.player_widget.seek_to(start_t)
        self.player_widget.set_playback_speed(speed)

    # --- Drag & Drop ---

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for u in urls:
                if u.isLocalFile():
                    ext = os.path.splitext(u.toLocalFile())[1].lower()
                    if ext in self.SUPPORTED_EXTENSIONS:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for u in event.mimeData().urls():
            if u.isLocalFile():
                path = u.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    self.load_video(path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def toggle_fullscreen(self):
        """Toggles video between fullscreen and restored editor view."""
        if getattr(self, "_is_fullscreen", False):
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        self._is_fullscreen = True
        self._saved_hsplitter_sizes = self.main_hsplitter.sizes()
        self._saved_splitter_sizes = self.splitter.sizes()

        self.player_widget.set_fullscreen_mode(True)

        self.media_library.setVisible(False)
        self.timeline_editor.setVisible(False)
        self.player_widget.timeline.setVisible(False)
        self.player_widget.control_bar.setVisible(False)
        self.menuBar().setVisible(False)
        self.main_toolbar.setVisible(False)
        self.statusBar().setVisible(False)

        self.centralWidget().layout().setContentsMargins(0, 0, 0, 0)
        self.showFullScreen()

    def exit_fullscreen(self):
        self._is_fullscreen = False
        self.player_widget.set_fullscreen_mode(False)

        self.showNormal()
        self.media_library.setVisible(True)
        self.timeline_editor.setVisible(True)
        self.player_widget.timeline.setVisible(True)
        self.player_widget.control_bar.setVisible(True)
        self.menuBar().setVisible(True)
        self.main_toolbar.setVisible(True)
        self.statusBar().setVisible(True)

        self.centralWidget().layout().setContentsMargins(8, 8, 8, 8)
        if hasattr(self, "_saved_splitter_sizes") and self._saved_splitter_sizes:
            self.splitter.setSizes(self._saved_splitter_sizes)
        if hasattr(self, "_saved_hsplitter_sizes") and self._saved_hsplitter_sizes:
            self.main_hsplitter.setSizes(self._saved_hsplitter_sizes)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and getattr(self, "_is_fullscreen", False):
            self.exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)
