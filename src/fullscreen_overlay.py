"""
Floating fullscreen overlay bar with playback transport and seeker.
Revealed on mouse movement in fullscreen mode.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QWidget,
)

from speed_config import get_speed_info
from timeline_widget import format_timestamp


class FullscreenOverlay(QFrame):
    """
    Translucent floating control overlay shown in fullscreen mode on mouse activity.
    """
    play_toggled = Signal()
    seek_requested = Signal(float)  # in seconds
    exit_fullscreen_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("FullscreenOverlay")
        self._duration: float = 0.0
        self._is_dragging: bool = False

        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(54)
        self.setStyleSheet(
            """
            QFrame#FullscreenOverlay {
                background-color: rgba(18, 21, 28, 0.90);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 12px;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #ffffff;
                font-weight: bold;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.18);
            }
            QLabel {
                color: #e0e4ec;
                font-family: monospace;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #00d2ff;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        # Play/Pause
        self.btn_play = QPushButton("Play", self)
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_play.setFixedWidth(75)
        self.btn_play.clicked.connect(self.play_toggled)
        layout.addWidget(self.btn_play)

        # Timecode
        self.lbl_time = QLabel("00:00:00 / 00:00:00", self)
        self.lbl_time.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.lbl_time)

        # Seek Bar Slider
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.valueChanged.connect(self._on_slider_value_changed)
        layout.addWidget(self.slider, stretch=1)

        # Speed Badge
        self.lbl_speed = QLabel("1x", self)
        self.lbl_speed.setStyleSheet(
            "padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; background: #1b5e20; color: #b9f6ca;"
        )
        layout.addWidget(self.lbl_speed)

        # Exit Fullscreen button
        self.btn_exit = QPushButton("Exit", self)
        self.btn_exit.setToolTip("Restore standard view (Esc)")
        self.btn_exit.clicked.connect(self.exit_fullscreen_requested)
        layout.addWidget(self.btn_exit)

    def set_duration(self, duration: float):
        self._duration = max(0.0, duration)
        self.update_position(0.0)

    def update_position(self, pos: float):
        if self._is_dragging:
            return
        if self._duration > 0:
            fraction = max(0.0, min(1.0, pos / self._duration))
            self.slider.blockSignals(True)
            self.slider.setValue(int(fraction * 1000))
            self.slider.blockSignals(False)

        cur_str = format_timestamp(pos).split(".")[0]
        dur_str = format_timestamp(self._duration).split(".")[0]
        self.lbl_time.setText(f"{cur_str} / {dur_str}")

    def update_playback_state(self, is_playing: bool):
        if is_playing:
            self.btn_play.setText("Pause")
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self.btn_play.setText("Play")
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def update_speed(self, speed: float):
        _, short_lbl, _, fg, bg = get_speed_info(speed)
        self.lbl_speed.setText(short_lbl)
        self.lbl_speed.setStyleSheet(
            f"padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; background: {bg}; color: {fg};"
        )

    def _on_slider_pressed(self):
        self._is_dragging = True

    def _on_slider_released(self):
        self._is_dragging = False
        val = self.slider.value()
        target_t = (val / 1000.0) * self._duration
        self.seek_requested.emit(target_t)

    def _on_slider_value_changed(self, val: int):
        if self._is_dragging and self._duration > 0:
            target_t = (val / 1000.0) * self._duration
            cur_str = format_timestamp(target_t).split(".")[0]
            dur_str = format_timestamp(self._duration).split(".")[0]
            self.lbl_time.setText(f"{cur_str} / {dur_str}")
