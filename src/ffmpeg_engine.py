"""
FFmpeg integration engine for FastCut.
Handles media probing, GPU hardware encoder detection, command generation, and export execution.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal
from speed_config import build_atempo_filter


@dataclass
class VideoInfo:
    file_path: str
    duration: float  # seconds
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: str = ""
    bitrate: int = 0
    size_bytes: int = 0


@dataclass
class ClipSegment:
    file_path: str
    start_time: float  # seconds
    end_time: float  # seconds
    speed: float = 1.0  # speed multiplier (0.1x to 5x)

    @property
    def source_duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    @property
    def duration(self) -> float:
        return self.source_duration / max(0.001, self.speed)


@dataclass
class EncoderOption:
    codec_id: str
    label: str
    is_gpu: bool
    vendor: str  # "nvidia", "amd", "intel", "apple", "cpu"
    codec_family: str  # "h264", "hevc", "av1"


def get_binary_path(name: str) -> str:
    """Finds ffmpeg or ffprobe executable in PATH."""
    import shutil
    path = shutil.which(name)
    if not path:
        # Fallback common locations
        for fallback in [f"/usr/bin/{name}", f"/usr/local/bin/{name}"]:
            if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
                return fallback
        return name
    return path


def detect_gpu_encoders() -> List[EncoderOption]:
    """
    Queries ffmpeg -encoders to find available GPU and CPU video encoders.
    Returns prioritized list with GPU encoders first.
    """
    ffmpeg_bin = get_binary_path("ffmpeg")
    available_encoders: List[EncoderOption] = []

    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        output = result.stdout
    except Exception:
        output = ""

    known_encoders = [
        # NVIDIA NVENC
        ("h264_nvenc", "NVIDIA H.264 (NVENC)", True, "nvidia", "h264"),
        ("hevc_nvenc", "NVIDIA HEVC / H.265 (NVENC)", True, "nvidia", "hevc"),
        ("av1_nvenc", "NVIDIA AV1 (NVENC)", True, "nvidia", "av1"),
        # AMD AMF
        ("h264_amf", "AMD H.264 (AMF)", True, "amd", "h264"),
        ("hevc_amf", "AMD HEVC / H.265 (AMF)", True, "amd", "hevc"),
        ("av1_amf", "AMD AV1 (AMF)", True, "amd", "av1"),
        # Intel QuickSync
        ("h264_qsv", "Intel QuickSync H.264 (QSV)", True, "intel", "h264"),
        ("hevc_qsv", "Intel QuickSync HEVC (QSV)", True, "intel", "hevc"),
        ("av1_qsv", "Intel QuickSync AV1 (QSV)", True, "intel", "av1"),
        # Linux VAAPI
        ("h264_vaapi", "VAAPI H.264 (Hardware)", True, "linux", "h264"),
        ("hevc_vaapi", "VAAPI HEVC (Hardware)", True, "linux", "hevc"),
        # Windows Media Foundation
        ("h264_mf", "Windows Media Foundation H.264", True, "windows", "h264"),
        ("hevc_mf", "Windows Media Foundation HEVC", True, "windows", "hevc"),
        # Apple VideoToolbox
        ("h264_videotoolbox", "Apple VideoToolbox H.264", True, "apple", "h264"),
        ("hevc_videotoolbox", "Apple VideoToolbox HEVC", True, "apple", "hevc"),
        # CPU Fallback
        ("libx264", "Software H.264 (x264 CPU)", False, "cpu", "h264"),
        ("libx265", "Software HEVC (x265 CPU)", False, "cpu", "hevc"),
        ("libsvtav1", "Software AV1 (SVT-AV1 CPU)", False, "cpu", "av1"),
    ]

    for codec_id, label, is_gpu, vendor, family in known_encoders:
        # Check if codec_id is in ffmpeg encoders output
        pattern = rf"\b{re.escape(codec_id)}\b"
        if re.search(pattern, output):
            available_encoders.append(
                EncoderOption(
                    codec_id=codec_id,
                    label=label,
                    is_gpu=is_gpu,
                    vendor=vendor,
                    codec_family=family,
                )
            )

    # If no encoders detected, add standard fallback
    if not available_encoders:
        available_encoders.append(
            EncoderOption("libx264", "Software H.264 (libx264)", False, "cpu", "h264")
        )

    return available_encoders


def probe_video(file_path: str) -> VideoInfo:
    """Uses ffprobe to extract video metadata."""
    ffprobe_bin = get_binary_path("ffprobe")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate:stream=index,codec_name,codec_type,width,height,r_frame_rate",
        "-of",
        "json",
        file_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
    data = json.loads(result.stdout)

    streams = data.get("streams", [])
    format_info = data.get("format", {})

    duration = float(format_info.get("duration", 0.0))
    size_bytes = int(format_info.get("size", 0))
    bitrate = int(format_info.get("bit_rate", 0))

    width = 0
    height = 0
    fps = 30.0
    video_codec = ""
    has_audio = False
    audio_codec = ""

    for s in streams:
        c_type = s.get("codec_type")
        if c_type == "video" and width == 0:
            width = int(s.get("width", 0))
            height = int(s.get("height", 0))
            video_codec = s.get("codec_name", "")
            r_fps = s.get("r_frame_rate", "30/1")
            try:
                if "/" in r_fps:
                    num, den = r_fps.split("/")
                    fps = float(num) / float(den) if float(den) != 0 else 30.0
                else:
                    fps = float(r_fps)
            except Exception:
                fps = 30.0
        elif c_type == "audio":
            has_audio = True
            if not audio_codec:
                audio_codec = s.get("codec_name", "")

    return VideoInfo(
        file_path=file_path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        video_codec=video_codec,
        has_audio=has_audio,
        audio_codec=audio_codec,
        bitrate=bitrate,
        size_bytes=size_bytes,
    )


def generate_thumbnail(file_path: str, timestamp: float, output_path: str, width: int = 160) -> bool:
    """Generates a small thumbnail at the specified timestamp."""
    ffmpeg_bin = get_binary_path("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        file_path,
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-vf",
        f"scale={width}:-1",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=5)
        return os.path.exists(output_path)
    except Exception:
        return False


def normalize_bitrate_str(raw: str) -> str:
    """Normalizes user-supplied bitrate string to FFmpeg format (e.g. '12M', '8000k')."""
    if not raw:
        return "12M"
    raw = raw.strip().lower()
    raw = re.sub(r"bps$", "", raw).strip()
    if raw.isdigit():
        val = int(raw)
        if val <= 500:
            return f"{val}M"
        elif val <= 500000:
            return f"{val}k"
        else:
            return str(val)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([kmg])?$", raw)
    if m:
        num, unit = m.groups()
        unit = unit.upper() if unit else "M"
        return f"{num}{unit}"
    return "12M"


def get_double_bitrate_str(bitrate_str: str) -> str:
    """Computes approximately 2x buffer size string for CBR mode."""
    m = re.match(r"^(\d+(?:\.\d+)?)([KMG]?)$", bitrate_str)
    if m:
        num_str, unit = m.groups()
        num = float(num_str) * 2.0
        if num.is_integer():
            return f"{int(num)}{unit}"
        return f"{num:.1f}{unit}"
    return "24M"


def get_speed_presets_for_encoder(encoder: str) -> List[Tuple[str, str]]:
    """Returns list of (internal_value, display_label) speed presets for the encoder."""
    if "nvenc" in encoder:
        return [
            ("p1", "P1 - Fastest (Lowest Overhead)"),
            ("p2", "P2 - Faster"),
            ("p3", "P3 - Fast"),
            ("p4", "P4 - Medium (Balanced Default)"),
            ("p5", "P5 - Slow (Better Quality)"),
            ("p6", "P6 - Slower (High Quality)"),
            ("p7", "P7 - Slowest (Maximum Quality)"),
        ]
    elif "amf" in encoder:
        return [
            ("speed", "Speed (Fastest)"),
            ("balanced", "Balanced (Default)"),
            ("quality", "Quality (Best)"),
        ]
    elif "qsv" in encoder:
        return [
            ("veryfast", "Very Fast"),
            ("faster", "Faster"),
            ("fast", "Fast"),
            ("medium", "Medium (Default)"),
            ("slow", "Slow"),
            ("slower", "Slower"),
            ("veryslow", "Very Slow (Best)"),
        ]
    elif encoder in ("libx264", "libx265"):
        return [
            ("ultrafast", "Ultrafast"),
            ("superfast", "Superfast"),
            ("veryfast", "Very Fast"),
            ("faster", "Faster"),
            ("fast", "Fast"),
            ("medium", "Medium (Default)"),
            ("slow", "Slow"),
            ("slower", "Slower"),
            ("veryslow", "Very Slow (Best)"),
        ]
    else:
        return [
            ("fast", "Fast"),
            ("medium", "Medium (Default)"),
            ("slow", "Slow"),
        ]


def get_encoder_args(
    encoder: str,
    speed_preset: str = "medium",
    cq_level: int = 22,
    rate_control_mode: str = "cq",
    bitrate_str: str = "12M",
    quality_preset: Optional[str] = None,
) -> List[str]:
    """Generates encoder-specific parameters for CQ/CRF or Constant Bitrate (CBR)."""
    preset = quality_preset if quality_preset is not None else speed_preset
    clean_bitrate = normalize_bitrate_str(bitrate_str)
    bufsize_str = get_double_bitrate_str(clean_bitrate)

    if "nvenc" in encoder:
        preset_map = {"fast": "p2", "medium": "p4", "high": "p6"}
        p = preset_map.get(preset, preset)
        if not (p.startswith("p") and len(p) == 2 and p[1].isdigit()):
            p = "p4"

        if rate_control_mode == "cbr":
            return [
                "-preset", p,
                "-rc", "cbr",
                "-b:v", clean_bitrate,
                "-maxrate", clean_bitrate,
                "-bufsize", bufsize_str,
            ]
        else:
            return ["-preset", p, "-rc", "vbr", "-cq", str(cq_level), "-b:v", "0"]

    elif "amf" in encoder:
        preset_map = {"fast": "speed", "medium": "balanced", "high": "quality"}
        p = preset_map.get(preset, preset)
        if p not in ["speed", "balanced", "quality"]:
            p = "balanced"

        if rate_control_mode == "cbr":
            return ["-quality", p, "-rc", "cbr", "-b:v", clean_bitrate]
        else:
            return ["-quality", p, "-rc", "cqp", "-qp_i", str(cq_level), "-qp_p", str(cq_level)]

    elif "qsv" in encoder:
        preset_map = {"fast": "fast", "medium": "medium", "high": "slow"}
        p = preset_map.get(preset, preset)
        valid_qsv = ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        if p not in valid_qsv:
            p = "medium"

        if rate_control_mode == "cbr":
            return ["-preset", p, "-b:v", clean_bitrate, "-maxrate", clean_bitrate]
        else:
            return ["-preset", p, "-global_quality", str(cq_level)]

    elif "vaapi" in encoder:
        if rate_control_mode == "cbr":
            return ["-b:v", clean_bitrate, "-maxrate", clean_bitrate]
        else:
            return ["-qp", str(cq_level)]

    elif encoder in ("libx264", "libx265"):
        preset_map = {"fast": "faster", "medium": "medium", "high": "slow"}
        p = preset_map.get(preset, preset)
        valid_x = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        if p not in valid_x:
            p = "medium"

        if rate_control_mode == "cbr":
            return [
                "-preset", p,
                "-b:v", clean_bitrate,
                "-minrate", clean_bitrate,
                "-maxrate", clean_bitrate,
                "-bufsize", bufsize_str,
            ]
        else:
            return ["-preset", p, "-crf", str(cq_level)]

    else:
        if rate_control_mode == "cbr":
            return ["-b:v", clean_bitrate]
        else:
            return ["-crf", str(cq_level)]


def build_export_command(
    clips: List[ClipSegment],
    output_path: str,
    encoder: str,
    target_res: Optional[Tuple[int, int]] = None,
    target_fps: Optional[float] = None,
    speed_preset: str = "medium",
    cq_level: int = 22,
    rate_control_mode: str = "cq",
    bitrate_str: str = "12M",
    audio_bitrate: str = "192k",
    quality_preset: Optional[str] = None,
) -> Tuple[List[str], float]:
    """
    Builds the FFmpeg command line to trim and stitch multiple clips.
    Returns (command_list, total_duration).
    """
    if not clips:
        raise ValueError("At least one clip is required for export.")

    ffmpeg_bin = get_binary_path("ffmpeg")
    total_duration = sum(clip.duration for clip in clips)

    # Probe all clips to detect resolution and audio
    clip_infos = [probe_video(c.file_path) for c in clips]

    # Target resolution
    if target_res:
        out_w, out_h = target_res
    else:
        out_w = clip_infos[0].width if clip_infos[0].width > 0 else 1920
        out_h = clip_infos[0].height if clip_infos[0].height > 0 else 1080

    # Ensure even dimensions (required by H.264/HEVC)
    out_w = out_w + (out_w % 2)
    out_h = out_h + (out_h % 2)

    fps = target_fps if target_fps else (clip_infos[0].fps if clip_infos[0].fps > 0 else 30.0)

    preset = quality_preset if quality_preset is not None else speed_preset
    encoder_args = get_encoder_args(
        encoder=encoder,
        speed_preset=preset,
        cq_level=cq_level,
        rate_control_mode=rate_control_mode,
        bitrate_str=bitrate_str,
    )

    # Case 1: Single clip without need for complex concat filter
    if len(clips) == 1:
        clip = clips[0]
        scale_pad = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.2f}"
        )
        if abs(clip.speed - 1.0) > 1e-4:
            pts_factor = 1.0 / clip.speed
            vf_str = f"{scale_pad},setpts={pts_factor:.6f}*PTS,format=yuv420p"
        else:
            vf_str = f"{scale_pad},format=yuv420p"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-nostats",
            "-progress",
            "pipe:1",
            "-ss",
            f"{clip.start_time:.3f}",
            "-to",
            f"{clip.end_time:.3f}",
            "-i",
            clip.file_path,
            "-vf",
            vf_str,
            "-c:v",
            encoder,
            *encoder_args,
        ]

        if clip_infos[0].has_audio:
            if abs(clip.speed - 1.0) > 1e-4:
                atempo_chain = build_atempo_filter(clip.speed)
                cmd.extend(["-af", f"aformat=sample_rates=48000:channel_layouts=stereo,{atempo_chain}"])
            cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000"])
        else:
            # Generate silent audio matching duration
            dur = clip.duration
            cmd.extend([
                "-f", "lavfi", "-i", f"aevalsrc=0:d={dur:.3f}:s=48000",
                "-c:a", "aac", "-b:a", audio_bitrate, "-shortest"
            ])

        cmd.append(output_path)
        return cmd, total_duration

    # Case 2: Multi-clip concatenation with uniform scaling, speed, and audio alignment
    cmd = [ffmpeg_bin, "-y", "-nostats", "-progress", "pipe:1"]

    for clip in clips:
        cmd.extend([
            "-ss",
            f"{clip.start_time:.3f}",
            "-to",
            f"{clip.end_time:.3f}",
            "-i",
            clip.file_path,
        ])

    filter_chains = []
    v_streams = []
    a_streams = []

    for i, (clip, info) in enumerate(zip(clips, clip_infos)):
        scale_pad = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.2f}"
        )
        if abs(clip.speed - 1.0) > 1e-4:
            pts_factor = 1.0 / clip.speed
            v_filter = f"[{i}:v]{scale_pad},setpts={pts_factor:.6f}*PTS,format=yuv420p[v{i}]"
        else:
            v_filter = f"[{i}:v]{scale_pad},format=yuv420p[v{i}]"
        filter_chains.append(v_filter)
        v_streams.append(f"[v{i}]")

        if info.has_audio:
            if abs(clip.speed - 1.0) > 1e-4:
                atempo_chain = build_atempo_filter(clip.speed)
                a_filter = f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,{atempo_chain}[a{i}]"
            else:
                a_filter = f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            filter_chains.append(a_filter)
            a_streams.append(f"[a{i}]")
        else:
            dur = clip.duration
            a_filter = f"aevalsrc=0:d={dur:.3f},aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            filter_chains.append(a_filter)
            a_streams.append(f"[a{i}]")

    # Combine all video and audio streams
    concat_inputs = "".join(f"{v_streams[i]}{a_streams[i]}" for i in range(len(clips)))
    concat_filter = f"{concat_inputs}concat=n={len(clips)}:v=1:a=1[outv][outa]"
    filter_chains.append(concat_filter)

    full_filter = ";\n".join(filter_chains)

    cmd.extend([
        "-filter_complex",
        full_filter,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        encoder,
        *encoder_args,
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        output_path,
    ])

    return cmd, total_duration


class ExportWorker(QThread):
    """
    QThread worker that executes the FFmpeg export command and monitors progress in real time.
    """
    # Signals: (progress_percent, elapsed_time_sec, fps, speed_text)
    progress = Signal(float, float, float, str)
    finished = Signal(bool, str)

    def __init__(
        self,
        clips: List[ClipSegment],
        output_path: str,
        encoder: str,
        target_res: Optional[Tuple[int, int]] = None,
        target_fps: Optional[float] = None,
        speed_preset: str = "medium",
        cq_level: int = 22,
        rate_control_mode: str = "cq",
        bitrate_str: str = "12M",
        audio_bitrate: str = "192k",
        quality_preset: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.clips = clips
        self.output_path = output_path
        self.encoder = encoder
        self.target_res = target_res
        self.target_fps = target_fps
        self.speed_preset = quality_preset if quality_preset is not None else speed_preset
        self.cq_level = cq_level
        self.rate_control_mode = rate_control_mode
        self.bitrate_str = bitrate_str
        self.audio_bitrate = audio_bitrate
        self._process: Optional[subprocess.Popen] = None
        self._canceled = False

    def cancel(self):
        """Cancels running export gracefully."""
        self._canceled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
            except Exception:
                pass

    def run(self):
        try:
            cmd, total_duration = build_export_command(
                clips=self.clips,
                output_path=self.output_path,
                encoder=self.encoder,
                target_res=self.target_res,
                target_fps=self.target_fps,
                speed_preset=self.speed_preset,
                cq_level=self.cq_level,
                rate_control_mode=self.rate_control_mode,
                bitrate_str=self.bitrate_str,
                audio_bitrate=self.audio_bitrate,
            )
        except Exception as e:
            self.finished.emit(False, f"Failed to prepare export command: {str(e)}")
            return

        total_us = total_duration * 1_000_000

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            current_time = 0.0
            current_fps = 0.0
            current_speed = ""

            for line in self._process.stdout:
                if self._canceled:
                    break

                line = line.strip()
                if not line:
                    continue

                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()

                    if key == "out_time_us":
                        try:
                            out_us = int(val)
                            current_time = out_us / 1_000_000
                            pct = min(100.0, max(0.0, (out_us / total_us) * 100)) if total_us > 0 else 0.0
                            self.progress.emit(pct, current_time, current_fps, current_speed)
                        except ValueError:
                            pass
                    elif key == "fps":
                        try:
                            current_fps = float(val)
                        except ValueError:
                            pass
                    elif key == "speed":
                        current_speed = val
                    elif key == "progress" and val == "end":
                        self.progress.emit(100.0, total_duration, current_fps, current_speed)

            self._process.wait()
            _, stderr_content = self._process.communicate()

            if self._canceled:
                # Clean up partial output file
                if os.path.exists(self.output_path):
                    try:
                        os.remove(self.output_path)
                    except Exception:
                        pass
                self.finished.emit(False, "Export was canceled by user.")
            elif self._process.returncode == 0:
                self.finished.emit(True, "Export completed successfully.")
            else:
                self.finished.emit(False, f"Export failed:\n{stderr_content}")

        except Exception as e:
            self.finished.emit(False, f"Export error: {str(e)}")
