# FastCut

A lightweight, cross-platform (Linux & Windows) video editor designed for quick trimming, clip slicing, sequence stitching, and GPU-accelerated export.

![FastCut](https://img.shields.io/badge/GPU-NVENC%20%7C%20AMF%20%7C%20QSV-brightgreen)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PySide6](https://img.shields.io/badge/GUI-PySide6%20(Qt6)-success)

## Features

- **Multi-track timeline editor**: Dedicated track row for each imported media source. Visual section blocks with color badges, playhead scrubber, time ruler, and zoom slider.
- **Slice clips (Key S)**: Split clips at playhead position into independent selectable sections. Slice either directly on the timeline or within source media.
- **Timeline and source preview modes**: Toggle between Timeline Preview (plays through sequence cuts, media transitions, and per-section speed changes seamlessly) and Source Preview (individual file inspection and trim marking).
- **Per-section speed control**: Adjust playback and export speeds per section (`0.1x`, `0.25x`, `0.5x`, `0.75x`, `1x`, `2x`, `3x`, `5x`). Exports at the chosen speed via GPU `setpts` and multi-stage `atempo` audio filtering.
- **Color-coded speed badges**: Visual color spectrum identifying section rates at a glance:
  - Slow speeds (0.1x to 0.75x): Purple, Blue, Cyan, Teal
  - Normal speed (1x): Green
  - Fast speeds (2x to 5x): Amber, Orange, Red
- **Interactive trim handles and section deletion**: Drag block edges directly on the timeline to refine In/Out boundaries. Press `Del` or click Remove to omit unwanted slices from playback and export.
- **Media library sidebar**: Left dock organizing all loaded video sources. Supports drag-and-drop import, thumbnail previews, metadata readout, and double-click source activation.
- **Fullscreen mode with auto-hiding overlay**: Double-click the video to expand into fullscreen. Moving the mouse reveals a floating translucent overlay with play/pause, seeker, timecode, and speed badge that automatically hides after 2.5 seconds of inactivity. Double-click or press `Esc` to restore the editor.
- **Hardware-accelerated GPU export**: Auto-detects NVIDIA NVENC (`h264_nvenc`, `hevc_nvenc`, `av1_nvenc`), AMD AMF, and Intel QSV. Falls back to multi-threaded CPU (`libx264`, `libx265`).
- **Customizable rate control and quality**:
  - **Constant Bitrate (CBR)**: Custom text input (e.g. `12M`, `8000k`, `25M`) with quick bitrate presets for 720p, 1080p, and 4K.
  - **Granular CQ/CRF presets**: From Visually Lossless (CQ 15) to Ultra Compact (CQ 34), plus exact numeric spinbox input.
  - **Encoder speed presets**: Granular tuning from P1 (Fastest) to P7 (Slowest / Best) for NVENC, or ultrafast to veryslow for CPU encoders.
  - **Audio bitrate selection**: 128 kbps, 192 kbps, 256 kbps, and 320 kbps.
- **Multi-clip sequence stitching**: Seamlessly stitch multiple trimmed clips and sliced sections into one file. Automatically normalizes resolution, framerate, and audio channels.
- **Live export progress**: Percentage progress bar, encoded timestamp, encoding FPS, and processing speed multiplier with cancellation support.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) and `ffprobe` in PATH (compiled with NVENC/AMF/QSV for GPU support)
- PySide6 (`pip install PySide6`)

## Quick start

### 1. Run directly with Python

```bash
cd /home/nix/dev/fastcut
python main.py [optional_path_to_video.mp4]
```

On Windows:
```cmd
python main.py [optional_path_to_video.mp4]
```

### 2. Standalone packaging (Optional)

To build a standalone executable for Linux or Windows without requiring Python installed:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name FastCut main.py
```

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `S` | Slice clip at playhead into two sections |
| `Del` / `Backspace` | Remove selected section from timeline |
| `Tab` | Toggle between Timeline Preview and Source Preview |
| `Space` | Play / Pause playback |
| `Double Click Video` | Toggle fullscreen mode (with auto-hiding overlay) |
| `Esc` | Exit fullscreen mode |
| `F11` | Toggle fullscreen mode |
| `I` | Set In-point at current playhead position (Source mode) |
| `O` | Set Out-point at current playhead position (Source mode) |
| `Left Arrow` | Step backward 0.1s |
| `Right Arrow` | Step forward 0.1s |
| `Enter` | Add current trimmed clip to timeline |
| `Ctrl + O` | Open video file |
| `Ctrl + E` | Open Export dialog |
| `Ctrl + R` | Reset trim range to entire video |

## Project structure

```
fastcut/
├── main.py                     # Entry point
├── pyproject.toml              # Project metadata & dependencies
├── README.md                   # Documentation
├── src/
│   ├── ffmpeg_engine.py        # Probing, GPU detection, filtergraph builder, export worker
│   ├── speed_config.py         # Speed presets, color palettes, and atempo filter generator
│   ├── media_library_widget.py # Left sidebar managing imported media sources
│   ├── fullscreen_overlay.py   # Floating auto-hiding overlay for fullscreen playback
│   ├── timeline_widget.py      # Custom QPainter timeline seeker with trim handles
│   ├── timeline_editor_widget.py # Multi-track visual timeline with tracks, handles, speeds
│   ├── player_widget.py        # QMediaPlayer wrapper, video canvas, transport controls, preview engine
│   ├── clip_sequence_widget.py # Sequence tray for stitching sections and speed adjustments
│   ├── export_dialog.py        # GPU settings & export progress modal
│   ├── main_window.py          # Main application window & layout
│   └── styles.py               # Dark theme stylesheet
└── tests/
    ├── test_fastcut.py         # Integration & unit test suite
    └── test_e2e_timeline_flow.py # End-to-end headless flow test
```
