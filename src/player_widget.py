"""
Video player widget embedding QMediaPlayer and QVideoWidget with transport controls,
Source vs Timeline preview modes, slicing support, playback speed selection,
and an interactive fullscreen overlay.
"""

from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QTime, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_engine import ClipSegment, VideoInfo, probe_video
from fullscreen_overlay import FullscreenOverlay
from speed_config import SPEED_PRESETS, get_speed_info
from timeline_widget import TimelineWidget, format_timestamp


class ClickableVideoWidget(QVideoWidget):
    """QVideoWidget subclass that emits signals on left double click and mouse motion."""
    double_clicked = Signal()
    mouse_moved = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self.mouse_moved.emit()
        super().mouseMoveEvent(event)


class PlayerWidget(QWidget):
    """
    Playback component consisting of video canvas, transport buttons,
    Source vs Timeline Preview modes, timecode readouts, and fullscreen overlay.
    """
    MODE_SOURCE = 0
    MODE_TIMELINE = 1

    video_loaded = Signal(VideoInfo)
    add_clip_requested = Signal(str, float, float)  # file_path, in_point, out_point
    slice_requested = Signal(float)  # timestamp in seconds
    speed_changed = Signal(float)  # speed multiplier
    fullscreen_toggle_requested = Signal()
    preview_mode_changed = Signal(int)  # MODE_SOURCE or MODE_TIMELINE
    timeline_tick = Signal(float)  # global timeline time

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._current_file: str = ""
        self._video_info: Optional[VideoInfo] = None
        self._is_seeking: bool = False
        self._is_fullscreen_mode: bool = False
        self._current_speed: float = 1.0

        # Preview mode tracking
        self._preview_mode: int = self.MODE_TIMELINE
        self._timeline_time: float = 0.0
        self._active_timeline_clip: Optional[ClipSegment] = None
        self._active_clip_start_t: float = 0.0
        self._active_clip_dur: float = 0.0

        # Callbacks for timeline sequence inspection
        self._get_timeline_clip_fn: Optional[Callable[[float], Optional[Tuple[ClipSegment, float, float, float]]]] = None
        self._get_timeline_duration_fn: Optional[Callable[[], float]] = None

        # Setup Qt Multimedia
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_widget = ClickableVideoWidget(self)
        self.player.setVideoOutput(self.video_widget)

        # Style canvas
        self.video_widget.setStyleSheet("background-color: #0b0c0e;")

        # Timeline seeker (for Source mode trim and quick scrubbing)
        self.timeline = TimelineWidget(self)

        # High-res timer for Timeline playback synchronization
        self._timeline_timer = QTimer(self)
        self._timeline_timer.setInterval(33)  # ~30 fps
        self._timeline_timer.timeout.connect(self._on_timeline_timer_tick)

        self._init_ui()
        self._setup_overlay()
        self._connect_signals()
        self.set_preview_mode(self._preview_mode)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        # 1. Video Canvas Frame
        self.video_frame = QFrame(self)
        self.video_frame.setFrameShape(QFrame.StyledPanel)
        self.video_frame.setStyleSheet("background-color: #0d0f12; border: 1px solid #252830; border-radius: 4px;")
        v_frame_layout = QVBoxLayout(self.video_frame)
        v_frame_layout.setContentsMargins(0, 0, 0, 0)
        v_frame_layout.addWidget(self.video_widget)
        main_layout.addWidget(self.video_frame, stretch=1)

        # 2. Source Seeker (visible in source mode or quick trim)
        main_layout.addWidget(self.timeline)

        # 3. Transport Controls & Timecode Bar
        self.control_bar = QWidget(self)
        control_layout = QHBoxLayout(self.control_bar)
        control_layout.setContentsMargins(8, 2, 8, 4)
        control_layout.setSpacing(6)

        # Preview Mode Toggle (Timeline vs Source)
        self.btn_preview_mode = QPushButton("Timeline Preview", self)
        self.btn_preview_mode.setToolTip("Toggle between Timeline Preview (full sequence) and Source Preview (individual file)")
        self.btn_preview_mode.clicked.connect(self.toggle_preview_mode)
        self._update_preview_mode_button_style()

        # Play/Pause
        self.btn_play = QPushButton("Play", self)
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_play.setFixedWidth(75)
        self.btn_play.setToolTip("Play / Pause (Space)")

        # Step controls
        self.btn_prev_frame = QPushButton(self)
        self.btn_prev_frame.setToolTip("Step backward 0.1s (Left Arrow)")
        self.btn_prev_frame.setIcon(self.style().standardIcon(QStyle.SP_MediaSeekBackward))
        self.btn_prev_frame.setFixedWidth(34)

        self.btn_next_frame = QPushButton(self)
        self.btn_next_frame.setToolTip("Step forward 0.1s (Right Arrow)")
        self.btn_next_frame.setIcon(self.style().standardIcon(QStyle.SP_MediaSeekForward))
        self.btn_next_frame.setFixedWidth(34)

        # Slice button (Key S)
        self.btn_slice = QPushButton("✂ Slice", self)
        self.btn_slice.setToolTip("Slice clip into two selectable sections at playhead (Hotkey: S)")
        self.btn_slice.setStyleSheet("color: #00d2ff; font-weight: bold; border-color: #0088cc;")
        self.btn_slice.clicked.connect(self._on_slice_clicked)

        # In / Out trim markers
        self.btn_mark_in = QPushButton("[ In", self)
        self.btn_mark_in.setToolTip("Set start trim point (Hotkey: I)")
        self.btn_mark_in.setStyleSheet("font-weight: bold; color: #2ecc71;")

        self.btn_mark_out = QPushButton("Out ]", self)
        self.btn_mark_out.setToolTip("Set end trim point (Hotkey: O)")
        self.btn_mark_out.setStyleSheet("font-weight: bold; color: #e74c3c;")

        # Playback speed dropdown
        self.combo_speed = QComboBox(self)
        self.combo_speed.setToolTip("Set section playback speed (exports at this speed)")
        for s_val, short_lbl, full_lbl, fg, bg in SPEED_PRESETS:
            self.combo_speed.addItem(full_lbl, s_val)
        self.combo_speed.setCurrentIndex(4)  # 1x default
        self._update_speed_combo_style(1.0)
        self.combo_speed.currentIndexChanged.connect(self._on_speed_combo_changed)

        # Add clip to sequence button
        self.btn_add_to_seq = QPushButton("+ Add Trim", self)
        self.btn_add_to_seq.setToolTip("Add current trimmed range to the export sequence (Hotkey: Enter)")
        self.btn_add_to_seq.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 5px 10px; border-radius: 4px;"
        )

        # Timecode labels
        self.lbl_timecode = QLabel("00:00:00 / 00:00:00", self)
        self.lbl_timecode.setStyleSheet("font-family: monospace; font-size: 11px; color: #d0d4dc;")

        # Volume control
        self.btn_mute = QPushButton(self)
        self.btn_mute.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        self.btn_mute.setFixedWidth(30)

        self.slider_volume = QSlider(Qt.Horizontal, self)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        self.slider_volume.setFixedWidth(70)
        self.audio_output.setVolume(0.8)

        # Assemble layout
        control_layout.addWidget(self.btn_preview_mode)
        control_layout.addSpacing(6)
        control_layout.addWidget(self.btn_play)
        control_layout.addWidget(self.btn_prev_frame)
        control_layout.addWidget(self.btn_next_frame)
        control_layout.addSpacing(6)
        control_layout.addWidget(self.btn_slice)
        control_layout.addSpacing(6)
        control_layout.addWidget(self.btn_mark_in)
        control_layout.addWidget(self.btn_mark_out)
        control_layout.addSpacing(6)
        control_layout.addWidget(self.combo_speed)
        control_layout.addSpacing(6)
        control_layout.addWidget(self.btn_add_to_seq)
        control_layout.addStretch(1)
        control_layout.addWidget(self.lbl_timecode)
        control_layout.addSpacing(8)
        control_layout.addWidget(self.btn_mute)
        control_layout.addWidget(self.slider_volume)

        main_layout.addWidget(self.control_bar)

        # Setup Keyboard Shortcuts
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_I), self, self.mark_in_at_current)
        QShortcut(QKeySequence(Qt.Key_O), self, self.mark_out_at_current)
        QShortcut(QKeySequence(Qt.Key_Left), self, self.step_backward)
        QShortcut(QKeySequence(Qt.Key_Right), self, self.step_forward)
        QShortcut(QKeySequence(Qt.Key_S), self, self._on_slice_clicked)
        QShortcut(QKeySequence(Qt.Key_Return), self, self._on_add_to_sequence_clicked)
        QShortcut(QKeySequence(Qt.Key_Tab), self, self.toggle_preview_mode)

    def _setup_overlay(self):
        self.overlay = FullscreenOverlay(self.video_frame)
        self.overlay.setVisible(False)

        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.timeout.connect(self._hide_overlay)

        self.overlay.play_toggled.connect(self.toggle_play)
        self.overlay.seek_requested.connect(self._on_overlay_seek)
        self.overlay.exit_fullscreen_requested.connect(self.fullscreen_toggle_requested)

        self.video_widget.double_clicked.connect(self.fullscreen_toggle_requested)
        self.video_widget.mouse_moved.connect(self._on_mouse_moved)

        self.video_frame.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched == self.video_frame and event.type() == QEvent.Resize:
            if self._is_fullscreen_mode and self.overlay.isVisible():
                self._position_overlay()
        return super().eventFilter(watched, event)

    def _connect_signals(self):
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)

        self.timeline.position_changed.connect(self._on_source_timeline_seek)
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        self.btn_mute.clicked.connect(self._toggle_mute)

        self.btn_prev_frame.clicked.connect(self.step_backward)
        self.btn_next_frame.clicked.connect(self.step_forward)
        self.btn_mark_in.clicked.connect(self.mark_in_at_current)
        self.btn_mark_out.clicked.connect(self.mark_out_at_current)
        self.btn_add_to_seq.clicked.connect(self._on_add_to_sequence_clicked)

    # --- Mode Management ---

    def set_timeline_callbacks(
        self,
        get_clip_fn: Callable[[float], Optional[Tuple[ClipSegment, float, float, float]]],
        duration_fn: Callable[[], float],
    ):
        self._get_timeline_clip_fn = get_clip_fn
        self._get_timeline_duration_fn = duration_fn

    def toggle_preview_mode(self):
        new_mode = self.MODE_SOURCE if self._preview_mode == self.MODE_TIMELINE else self.MODE_TIMELINE
        self.set_preview_mode(new_mode)

    def set_preview_mode(self, mode: int):
        self._preview_mode = mode
        self._update_preview_mode_button_style()

        if mode == self.MODE_TIMELINE:
            self.timeline.setVisible(False)
            self.btn_mark_in.setVisible(False)
            self.btn_mark_out.setVisible(False)
            self.btn_add_to_seq.setVisible(False)
            dur = self._get_timeline_duration_fn() if self._get_timeline_duration_fn else 1.0
            self.overlay.set_duration(dur)
            self.seek_timeline(self._timeline_time)
        else:
            self.timeline.setVisible(True)
            self.btn_mark_in.setVisible(True)
            self.btn_mark_out.setVisible(True)
            self.btn_add_to_seq.setVisible(True)
            self._timeline_timer.stop()
            if self._video_info:
                self.overlay.set_duration(self._video_info.duration)
                self.seek_to(self.timeline.current_position)

        self.preview_mode_changed.emit(mode)

    def _update_preview_mode_button_style(self):
        if self._preview_mode == self.MODE_TIMELINE:
            self.btn_preview_mode.setText("Timeline Preview")
            self.btn_preview_mode.setStyleSheet(
                "background-color: #1a365d; color: #00d2ff; font-weight: bold; border: 1px solid #00d2ff; border-radius: 4px; padding: 4px 8px;"
            )
        else:
            self.btn_preview_mode.setText("Source Preview")
            self.btn_preview_mode.setStyleSheet(
                "background-color: #262a35; color: #d8dce6; border: 1px solid #363c4c; border-radius: 4px; padding: 4px 8px;"
            )

    @property
    def preview_mode(self) -> int:
        return self._preview_mode

    @property
    def current_timeline_time(self) -> float:
        return self._timeline_time

    # --- Timeline Preview Execution Engine ---

    def seek_timeline(self, global_t: float):
        """Seeks to global timeline time, loading the active section's media source and local frame."""
        total_dur = self._get_timeline_duration_fn() if self._get_timeline_duration_fn else 0.0
        self._timeline_time = max(0.0, min(global_t, max(0.0, total_dur)))

        if not self._get_timeline_clip_fn:
            return

        res = self._get_timeline_clip_fn(self._timeline_time)
        if not res:
            return

        clip, start_t, dur, offset = res
        self._active_timeline_clip = clip
        self._active_clip_start_t = start_t
        self._active_clip_dur = dur

        # Calculate exact source timestamp
        src_seek_t = clip.start_time + offset * clip.speed

        # Switch source file if needed
        if clip.file_path != self._current_file:
            self._current_file = clip.file_path
            self.player.setSource(QUrl.fromLocalFile(clip.file_path))

        # Seek player locally
        self._is_seeking = True
        self.player.setPosition(int(src_seek_t * 1000))
        self.set_playback_speed(clip.speed)
        self._is_seeking = False

        self.overlay.update_position(self._timeline_time)

        cur_str = format_timestamp(self._timeline_time).split(".")[0]
        dur_str = format_timestamp(total_dur).split(".")[0]
        self.lbl_timecode.setText(f"{cur_str} / {dur_str}")

    def _on_timeline_timer_tick(self):
        """Synchronizes global timeline playhead across multiple clips and speeds."""
        if self._preview_mode != self.MODE_TIMELINE:
            self._timeline_timer.stop()
            return

        if self.player.playbackState() != QMediaPlayer.PlayingState:
            return

        total_dur = self._get_timeline_duration_fn() if self._get_timeline_duration_fn else 0.0

        if not self._active_timeline_clip:
            res = self._get_timeline_clip_fn(self._timeline_time) if self._get_timeline_clip_fn else None
            if res:
                self._active_timeline_clip, self._active_clip_start_t, self._active_clip_dur, _ = res
            else:
                self.player.pause()
                self._timeline_timer.stop()
                return

        clip = self._active_timeline_clip
        local_cur_sec = self.player.position() / 1000.0

        # Guard against stale player position before seek finishes
        if local_cur_sec < clip.start_time - 0.2:
            return

        # Calculate global timeline time
        offset = max(0.0, (local_cur_sec - clip.start_time) / max(0.001, clip.speed))
        new_global_t = self._active_clip_start_t + offset

        # Check if current section has finished
        if offset >= self._active_clip_dur - 0.04:
            next_t = self._active_clip_start_t + self._active_clip_dur + 0.01
            if next_t >= total_dur:
                # End of sequence
                self.player.pause()
                self._timeline_timer.stop()
                self.seek_timeline(0.0)
                self.timeline_tick.emit(0.0)
                return
            else:
                # Advance to next clip
                self.seek_timeline(next_t)
                self.player.play()
                self.timeline_tick.emit(next_t)
                return

        self._timeline_time = new_global_t
        self.overlay.update_position(new_global_t)
        self.timeline_tick.emit(new_global_t)

        cur_str = format_timestamp(new_global_t).split(".")[0]
        dur_str = format_timestamp(total_dur).split(".")[0]
        self.lbl_timecode.setText(f"{cur_str} / {dur_str}")

    # --- Transport Actions ---

    def toggle_play(self):
        if self._preview_mode == self.MODE_TIMELINE:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
                self._timeline_timer.stop()
            else:
                total_dur = self._get_timeline_duration_fn() if self._get_timeline_duration_fn else 0.0
                if self._timeline_time >= total_dur - 0.05:
                    self.seek_timeline(0.0)
                self.player.play()
                self._timeline_timer.start()
        else:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
            else:
                if self.player.position() >= self.player.duration():
                    self.jump_to_in()
                self.player.play()

    def step_backward(self, delta_sec: float = 0.1):
        if self._preview_mode == self.MODE_TIMELINE:
            self.seek_timeline(max(0.0, self._timeline_time - delta_sec))
            self.timeline_tick.emit(self._timeline_time)
        else:
            pos_sec = max(0.0, self.timeline.current_position - delta_sec)
            self.seek_to(pos_sec)

    def step_forward(self, delta_sec: float = 0.1):
        if self._preview_mode == self.MODE_TIMELINE:
            total_dur = self._get_timeline_duration_fn() if self._get_timeline_duration_fn else 10.0
            self.seek_timeline(min(total_dur, self._timeline_time + delta_sec))
            self.timeline_tick.emit(self._timeline_time)
        else:
            pos_sec = min(self.timeline.duration, self.timeline.current_position + delta_sec)
            self.seek_to(pos_sec)

    def seek_to(self, seconds: float):
        """Seeks in Source Preview mode."""
        self._is_seeking = True
        self.timeline.set_position(seconds)
        self.overlay.update_position(seconds)
        millis = int(seconds * 1000)
        self.player.setPosition(millis)
        self._update_source_timecode_display(seconds)
        self._is_seeking = False

    def _on_overlay_seek(self, seconds: float):
        if self._preview_mode == self.MODE_TIMELINE:
            self.seek_timeline(seconds)
            self.timeline_tick.emit(seconds)
        else:
            self.seek_to(seconds)

    def _on_source_timeline_seek(self, seconds: float):
        self.seek_to(seconds)

    def mark_in_at_current(self):
        pos = self.timeline.current_position
        self.timeline.set_in_point(pos)

    def mark_out_at_current(self):
        pos = self.timeline.current_position
        self.timeline.set_out_point(pos)

    def jump_to_in(self):
        self.seek_to(self.timeline.in_point)

    def jump_to_out(self):
        self.seek_to(self.timeline.out_point)

    def set_playback_speed(self, speed: float):
        self._current_speed = speed
        self.player.setPlaybackRate(speed)
        self.overlay.update_speed(speed)

        for i in range(self.combo_speed.count()):
            if abs(self.combo_speed.itemData(i) - speed) < 1e-3:
                self.combo_speed.blockSignals(True)
                self.combo_speed.setCurrentIndex(i)
                self._update_speed_combo_style(speed)
                self.combo_speed.blockSignals(False)
                break

    def _update_speed_combo_style(self, speed: float):
        _, _, _, fg, bg = get_speed_info(speed)
        self.combo_speed.setStyleSheet(
            f"QComboBox {{ background-color: {bg}; color: {fg}; font-weight: bold; border-radius: 4px; padding: 2px 8px; }}"
        )

    def _on_speed_combo_changed(self):
        speed = self.combo_speed.currentData()
        if speed is not None:
            self.set_playback_speed(speed)
            self.speed_changed.emit(speed)

    def _on_slice_clicked(self):
        t = self._timeline_time if self._preview_mode == self.MODE_TIMELINE else self.timeline.current_position
        self.slice_requested.emit(t)

    def _on_add_to_sequence_clicked(self):
        if not self._current_file:
            return
        self.add_clip_requested.emit(
            self._current_file,
            self.timeline.in_point,
            self.timeline.out_point,
        )

    # --- Video Loading ---

    def load_video(self, file_path: str):
        if not os.path.isfile(file_path):
            return

        self._current_file = file_path
        try:
            self._video_info = probe_video(file_path)
            duration_sec = self._video_info.duration
        except Exception:
            duration_sec = 0.0

        self.player.setSource(QUrl.fromLocalFile(file_path))
        if duration_sec > 0:
            self.timeline.set_duration(duration_sec)
            if self._preview_mode == self.MODE_SOURCE:
                self.overlay.set_duration(duration_sec)

        self.player.pause()
        self.player.setPosition(0)

        self._update_source_timecode_display(0.0)

        if self._video_info:
            self.video_loaded.emit(self._video_info)

    @property
    def current_file(self) -> str:
        return self._current_file

    @property
    def video_info(self) -> Optional[VideoInfo]:
        return self._video_info

    # --- Slot Handlers ---

    def _on_player_position_changed(self, millis: int):
        if self._preview_mode == self.MODE_SOURCE and not self._is_seeking:
            seconds = millis / 1000.0
            self.timeline.set_position(seconds)
            self.overlay.update_position(seconds)
            self._update_source_timecode_display(seconds)

    def _on_player_duration_changed(self, millis: int):
        if self._preview_mode == self.MODE_SOURCE:
            dur_sec = millis / 1000.0
            if dur_sec > self.timeline.duration:
                self.timeline.set_duration(dur_sec)
                self.overlay.set_duration(dur_sec)
                self._update_source_timecode_display(self.timeline.current_position)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState):
        is_playing = (state == QMediaPlayer.PlayingState)
        self.overlay.update_playback_state(is_playing)
        if is_playing:
            self.btn_play.setText("Pause")
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self.btn_play.setText("Play")
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def _update_source_timecode_display(self, cur_sec: float):
        dur_sec = self.timeline.duration
        cur_str = format_timestamp(cur_sec).split(".")[0]
        dur_str = format_timestamp(dur_sec).split(".")[0]
        self.lbl_timecode.setText(f"{cur_str} / {dur_str}")

    def _on_volume_changed(self, val: int):
        vol = val / 100.0
        self.audio_output.setVolume(vol)
        if val == 0:
            self.btn_mute.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted))
        else:
            self.btn_mute.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))

    def _toggle_mute(self):
        muted = self.audio_output.isMuted()
        self.audio_output.setMuted(not muted)
        if not muted:
            self.btn_mute.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted))
        else:
            self.btn_mute.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))

    # --- Fullscreen Management ---

    def set_fullscreen_mode(self, is_fs: bool):
        self._is_fullscreen_mode = is_fs
        if is_fs:
            self._position_overlay()
            self.overlay.show()
            self.overlay.raise_()
            self._overlay_timer.start(2500)
        else:
            self._overlay_timer.stop()
            self.overlay.hide()
            self.video_widget.setCursor(Qt.ArrowCursor)

    def _position_overlay(self):
        w = min(840, max(460, self.video_frame.width() - 80))
        h = self.overlay.height()
        x = max(0, (self.video_frame.width() - w) // 2)
        y = max(0, self.video_frame.height() - h - 30)
        self.overlay.setGeometry(x, y, w, h)

    def _on_mouse_moved(self):
        if not self._is_fullscreen_mode:
            return
        self.video_widget.setCursor(Qt.ArrowCursor)
        self._position_overlay()
        self.overlay.show()
        self.overlay.raise_()
        self._overlay_timer.start(2500)

    def _hide_overlay(self):
        if not self._is_fullscreen_mode:
            return
        if self.overlay.underMouse():
            self._overlay_timer.start(1500)
            return
        self.overlay.hide()
        self.video_widget.setCursor(Qt.BlankCursor)
