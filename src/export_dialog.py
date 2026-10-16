"""
Export dialog modal allowing users to configure GPU encoder, resolution,
quality presets, constant bitrate with text input, and monitor export progress.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ffmpeg_engine import (
    ClipSegment,
    EncoderOption,
    ExportWorker,
    detect_gpu_encoders,
    get_speed_presets_for_encoder,
    normalize_bitrate_str,
    probe_video,
)
from timeline_widget import format_timestamp


class ExportDialog(QDialog):
    """
    Export settings and live progress monitoring modal with granular quality,
    constant bitrate text input, and encoder speed presets.
    """

    def __init__(self, clips: List[ClipSegment], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Export Video (GPU Accelerated)")
        self.setMinimumWidth(560)
        self.setModal(True)

        self.clips = clips
        self.worker: Optional[ExportWorker] = None
        self.encoders = detect_gpu_encoders()

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Overview box
        total_dur = sum(c.duration for c in self.clips)
        lbl_info = QLabel(
            f"Exporting sequence: <b>{len(self.clips)} clip(s)</b> | Total duration: <b>{format_timestamp(total_dur)}</b>"
        )
        layout.addWidget(lbl_info)

        # Settings Group
        group_settings = QGroupBox("Export Configuration", self)
        form = QFormLayout(group_settings)
        form.setSpacing(10)

        # 1. Output destination
        out_layout = QHBoxLayout()
        self.lbl_output_path = QLabel("Select destination...", self)
        self.lbl_output_path.setStyleSheet("color: #ecf0f1; background: #16181d; padding: 5px; border-radius: 3px;")
        self.btn_browse = QPushButton("Browse...", self)
        self.btn_browse.clicked.connect(self._browse_output)
        out_layout.addWidget(self.lbl_output_path, stretch=1)
        out_layout.addWidget(self.btn_browse)
        form.addRow("Output File:", out_layout)

        # Suggest default destination
        default_dir = os.path.dirname(self.clips[0].file_path) if self.clips else os.path.expanduser("~")
        self.output_file = os.path.join(default_dir, "fastcut_export.mp4")
        self.lbl_output_path.setText(self.output_file)

        # 2. Encoder selection
        self.combo_encoder = QComboBox(self)
        gpu_selected = False
        for idx, enc in enumerate(self.encoders):
            badge = " [GPU]" if enc.is_gpu else " [CPU]"
            self.combo_encoder.addItem(f"{enc.label}{badge}", enc.codec_id)
            if enc.is_gpu and not gpu_selected:
                self.combo_encoder.setCurrentIndex(idx)
                gpu_selected = True

        self.combo_encoder.currentIndexChanged.connect(self._on_encoder_changed)
        form.addRow("Video Encoder:", self.combo_encoder)

        # 3. Rate Control Mode (Quality / CQ vs Constant Bitrate / CBR)
        self.combo_rate_mode = QComboBox(self)
        self.combo_rate_mode.addItem("Target Quality (CQ / CRF Variable Rate)", "cq")
        self.combo_rate_mode.addItem("Constant Bitrate (CBR)", "cbr")
        self.combo_rate_mode.currentIndexChanged.connect(self._on_rate_mode_changed)
        form.addRow("Rate Control:", self.combo_rate_mode)

        # 4. Quality Section (for CQ/CRF mode)
        self.widget_cq_container = QWidget(self)
        cq_layout = QHBoxLayout(self.widget_cq_container)
        cq_layout.setContentsMargins(0, 0, 0, 0)
        cq_layout.setSpacing(8)

        self.combo_cq_preset = QComboBox(self)
        self.combo_cq_preset.addItem("Visually Lossless (CQ 15)", 15)
        self.combo_cq_preset.addItem("Near Visually Lossless (CQ 18)", 18)
        self.combo_cq_preset.addItem("High Quality (CQ 21)", 21)
        self.combo_cq_preset.addItem("Balanced Default (CQ 24)", 24)
        self.combo_cq_preset.addItem("Standard / Good (CQ 27)", 27)
        self.combo_cq_preset.addItem("Economy / Compact (CQ 30)", 30)
        self.combo_cq_preset.addItem("Ultra Compact (CQ 34)", 34)
        self.combo_cq_preset.addItem("Custom Value...", None)
        self.combo_cq_preset.setCurrentIndex(3)  # Balanced (CQ 24)
        self.combo_cq_preset.currentIndexChanged.connect(self._on_cq_preset_changed)

        self.spin_cq = QSpinBox(self)
        self.spin_cq.setRange(0, 51)
        self.spin_cq.setValue(24)
        self.spin_cq.setPrefix("CQ: ")
        self.spin_cq.setFixedWidth(85)
        self.spin_cq.valueChanged.connect(self._on_spin_cq_changed)

        cq_layout.addWidget(self.combo_cq_preset, stretch=1)
        cq_layout.addWidget(self.spin_cq)
        form.addRow("Quality Level:", self.widget_cq_container)

        # 5. Bitrate Section (for CBR mode)
        self.widget_cbr_container = QWidget(self)
        cbr_layout = QHBoxLayout(self.widget_cbr_container)
        cbr_layout.setContentsMargins(0, 0, 0, 0)
        cbr_layout.setSpacing(8)

        self.edit_bitrate = QLineEdit("12M", self)
        self.edit_bitrate.setPlaceholderText("e.g. 15M, 8000k, 25M")
        self.edit_bitrate.setToolTip("Target constant bitrate (e.g. '12M', '8000k')")
        self.edit_bitrate.setFixedWidth(110)

        self.combo_cbr_quick = QComboBox(self)
        self.combo_cbr_quick.addItem("Quick Bitrate Presets...", "")
        self.combo_cbr_quick.addItem("4 Mbps (4M) - Web / 720p", "4M")
        self.combo_cbr_quick.addItem("8 Mbps (8M) - 1080p Standard", "8M")
        self.combo_cbr_quick.addItem("12 Mbps (12M) - 1080p High Quality", "12M")
        self.combo_cbr_quick.addItem("20 Mbps (20M) - 1080p 60fps / 4K Light", "20M")
        self.combo_cbr_quick.addItem("35 Mbps (35M) - 4K High Quality", "35M")
        self.combo_cbr_quick.addItem("50 Mbps (50M) - 4K Master", "50M")
        self.combo_cbr_quick.currentIndexChanged.connect(self._on_cbr_quick_selected)

        cbr_layout.addWidget(self.edit_bitrate)
        cbr_layout.addWidget(self.combo_cbr_quick, stretch=1)
        form.addRow("Constant Bitrate:", self.widget_cbr_container)
        self.widget_cbr_container.setVisible(False)

        # 6. Encoder Speed / Preset (P1-P7 for NVENC, ultrafast-veryslow for x264, etc.)
        self.combo_speed_preset = QComboBox(self)
        self._populate_speed_presets()
        form.addRow("Encoding Speed / Preset:", self.combo_speed_preset)

        # 7. Resolution
        self.combo_resolution = QComboBox(self)
        self.combo_resolution.addItem("Match Source / First Clip", "source")
        self.combo_resolution.addItem("1080p Full HD (1920x1080)", (1920, 1080))
        self.combo_resolution.addItem("4K Ultra HD (3840x2160)", (3840, 2160))
        self.combo_resolution.addItem("720p HD (1280x720)", (1280, 720))
        self.combo_resolution.addItem("1080x1920 (Vertical 9:16)", (1080, 1920))
        form.addRow("Resolution:", self.combo_resolution)

        # 8. Framerate
        self.combo_fps = QComboBox(self)
        self.combo_fps.addItem("Match Source FPS", None)
        self.combo_fps.addItem("60 FPS", 60.0)
        self.combo_fps.addItem("30 FPS", 30.0)
        self.combo_fps.addItem("24 FPS", 24.0)
        form.addRow("Framerate:", self.combo_fps)

        # 9. Audio Bitrate
        self.combo_audio_bitrate = QComboBox(self)
        self.combo_audio_bitrate.addItem("128 kbps (Standard)", "128k")
        self.combo_audio_bitrate.addItem("192 kbps (High Quality Default)", "192k")
        self.combo_audio_bitrate.addItem("256 kbps (Very High Quality)", "256k")
        self.combo_audio_bitrate.addItem("320 kbps (Maximum)", "320k")
        self.combo_audio_bitrate.setCurrentIndex(1)
        form.addRow("Audio Bitrate:", self.combo_audio_bitrate)

        layout.addWidget(group_settings)

        # Progress Group (hidden until export starts)
        self.group_progress = QGroupBox("Export Progress", self)
        prog_layout = QVBoxLayout(self.group_progress)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                text-align: center;
                border: 1px solid #333;
                border-radius: 4px;
                background-color: #1a1a1a;
                height: 22px;
            }
            QProgressBar::chunk {
                background-color: #2ecc71;
            }
            """
        )
        prog_layout.addWidget(self.progress_bar)

        self.lbl_progress_status = QLabel("Ready to export", self)
        self.lbl_progress_status.setStyleSheet("color: #bdc3c7; font-family: monospace; font-size: 11px;")
        prog_layout.addWidget(self.lbl_progress_status)

        self.group_progress.setVisible(False)
        layout.addWidget(self.group_progress)

        # Action Buttons
        btn_box = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        self.btn_start = QPushButton("Start Export", self)
        self.btn_start.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;"
        )
        self.btn_start.clicked.connect(self._start_export)

        btn_box.addStretch(1)
        btn_box.addWidget(self.btn_cancel)
        btn_box.addWidget(self.btn_start)
        layout.addLayout(btn_box)

    def _populate_speed_presets(self):
        """Populates the speed preset dropdown based on the selected encoder."""
        encoder_codec = self.combo_encoder.currentData() or "h264_nvenc"
        presets = get_speed_presets_for_encoder(encoder_codec)
        self.combo_speed_preset.clear()

        default_idx = 0
        for idx, (val, label) in enumerate(presets):
            self.combo_speed_preset.addItem(label, val)
            if val in ("p4", "medium", "balanced"):
                default_idx = idx

        self.combo_speed_preset.setCurrentIndex(default_idx)

    def _on_encoder_changed(self):
        self._populate_speed_presets()

    def _on_rate_mode_changed(self):
        mode = self.combo_rate_mode.currentData()
        if mode == "cq":
            self.widget_cq_container.setVisible(True)
            self.widget_cbr_container.setVisible(False)
        else:
            self.widget_cq_container.setVisible(False)
            self.widget_cbr_container.setVisible(True)

    def _on_cq_preset_changed(self):
        val = self.combo_cq_preset.currentData()
        if val is not None:
            self.spin_cq.blockSignals(True)
            self.spin_cq.setValue(val)
            self.spin_cq.blockSignals(False)

    def _on_spin_cq_changed(self, val: int):
        # Check if matches any preset
        matched = False
        for i in range(self.combo_cq_preset.count()):
            if self.combo_cq_preset.itemData(i) == val:
                self.combo_cq_preset.blockSignals(True)
                self.combo_cq_preset.setCurrentIndex(i)
                self.combo_cq_preset.blockSignals(False)
                matched = True
                break
        if not matched:
            custom_idx = self.combo_cq_preset.count() - 1
            self.combo_cq_preset.blockSignals(True)
            self.combo_cq_preset.setCurrentIndex(custom_idx)
            self.combo_cq_preset.blockSignals(False)

    def _on_cbr_quick_selected(self):
        val = self.combo_cbr_quick.currentData()
        if val:
            self.edit_bitrate.setText(val)

    def _browse_output(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output File",
            self.output_file,
            "MP4 Video (*.mp4);;Matroska Video (*.mkv);;QuickTime (*.mov)",
        )
        if file_path:
            self.output_file = file_path
            self.lbl_output_path.setText(file_path)

    def _start_export(self):
        if not self.clips:
            QMessageBox.warning(self, "No Clips", "Please add at least one clip to export.")
            return

        if not self.output_file:
            QMessageBox.warning(self, "No Destination", "Please choose an output file location.")
            return

        encoder_codec = self.combo_encoder.currentData()
        res_data = self.combo_resolution.currentData()
        target_res = None if res_data == "source" else res_data
        target_fps = self.combo_fps.currentData()
        rate_mode = self.combo_rate_mode.currentData()
        speed_preset = self.combo_speed_preset.currentData()
        cq_val = self.spin_cq.value()
        bitrate_input = normalize_bitrate_str(self.edit_bitrate.text().strip())
        audio_bitrate = self.combo_audio_bitrate.currentData()

        # Update UI state
        self.group_progress.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_progress_status.setText("Initializing FFmpeg encoder...")
        self.btn_start.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.combo_encoder.setEnabled(False)
        self.combo_resolution.setEnabled(False)
        self.combo_fps.setEnabled(False)
        self.combo_rate_mode.setEnabled(False)
        self.combo_cq_preset.setEnabled(False)
        self.spin_cq.setEnabled(False)
        self.edit_bitrate.setEnabled(False)
        self.combo_cbr_quick.setEnabled(False)
        self.combo_speed_preset.setEnabled(False)
        self.combo_audio_bitrate.setEnabled(False)

        # Start worker thread
        self.worker = ExportWorker(
            clips=self.clips,
            output_path=self.output_file,
            encoder=encoder_codec,
            target_res=target_res,
            target_fps=target_fps,
            speed_preset=speed_preset,
            cq_level=cq_val,
            rate_control_mode=rate_mode,
            bitrate_str=bitrate_input,
            audio_bitrate=audio_bitrate,
            parent=self,
        )
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_progress(self, pct: float, cur_time: float, fps: float, speed: str):
        self.progress_bar.setValue(int(pct))
        time_str = format_timestamp(cur_time)
        fps_str = f"{fps:.1f} fps" if fps > 0 else ""
        speed_str = f"Speed: {speed}" if speed else ""
        status_parts = [f"Progress: {pct:.1f}%", f"Encoded: {time_str}"]
        if speed_str:
            status_parts.append(speed_str)
        if fps_str:
            status_parts.append(fps_str)
        self.lbl_progress_status.setText(" | ".join(status_parts))

    def _on_worker_finished(self, success: bool, message: str):
        self.btn_start.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.combo_encoder.setEnabled(True)
        self.combo_resolution.setEnabled(True)
        self.combo_fps.setEnabled(True)
        self.combo_rate_mode.setEnabled(True)
        self.combo_cq_preset.setEnabled(True)
        self.spin_cq.setEnabled(True)
        self.edit_bitrate.setEnabled(True)
        self.combo_cbr_quick.setEnabled(True)
        self.combo_speed_preset.setEnabled(True)
        self.combo_audio_bitrate.setEnabled(True)

        if success:
            self.progress_bar.setValue(100)
            self.lbl_progress_status.setText("Export completed successfully.")
            reply = QMessageBox.information(
                self,
                "Export Complete",
                f"Video exported successfully to:\n{self.output_file}",
                QMessageBox.Open | QMessageBox.Ok,
            )
            if reply == QMessageBox.Open:
                import subprocess
                import sys
                folder = os.path.dirname(self.output_file)
                if sys.platform == "win32":
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    subprocess.run(["open", folder])
                else:
                    subprocess.run(["xdg-open", folder])
            self.accept()
        else:
            self.lbl_progress_status.setText("Export failed or canceled.")
            if "canceled" not in message.lower():
                QMessageBox.critical(self, "Export Error", message)

    def _on_cancel_clicked(self):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Export",
                "An export is currently in progress. Do you want to cancel it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.worker.cancel()
        else:
            self.reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)
        event.accept()
