"""
Unit and integration tests for FastCut video editor backend.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Add src to path
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from ffmpeg_engine import (
    ClipSegment,
    build_export_command,
    detect_gpu_encoders,
    generate_thumbnail,
    probe_video,
)


class TestFastCutEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="fastcut_test_")

        # Generate sample test video 1: 3 seconds 1280x720 with audio
        cls.video1_path = os.path.join(cls.temp_dir, "test1.mp4")
        cmd1 = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            cls.video1_path,
        ]
        subprocess.run(cmd1, capture_output=True, check=True)

        # Generate sample test video 2: 2 seconds 1920x1080 without audio
        cls.video2_path = os.path.join(cls.temp_dir, "test2.mp4")
        cmd2 = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "smptebars=duration=2:size=1920x1080:rate=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            cls.video2_path,
        ]
        subprocess.run(cmd2, capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_gpu_encoder_detection(self):
        encoders = detect_gpu_encoders()
        self.assertTrue(len(encoders) > 0, "Should detect at least one encoder")

        codec_ids = [e.codec_id for e in encoders]
        # On NVIDIA system, nvenc should be present
        if "h264_nvenc" in codec_ids:
            nvenc = next(e for e in encoders if e.codec_id == "h264_nvenc")
            self.assertTrue(nvenc.is_gpu)
            self.assertEqual(nvenc.vendor, "nvidia")

    def test_probe_video(self):
        info1 = probe_video(self.video1_path)
        self.assertEqual(info1.width, 1280)
        self.assertEqual(info1.height, 720)
        self.assertAlmostEqual(info1.duration, 3.0, delta=0.2)
        self.assertTrue(info1.has_audio)

        info2 = probe_video(self.video2_path)
        self.assertEqual(info2.width, 1920)
        self.assertEqual(info2.height, 1080)
        self.assertAlmostEqual(info2.duration, 2.0, delta=0.2)
        self.assertFalse(info2.has_audio)

    def test_generate_thumbnail(self):
        thumb_path = os.path.join(self.temp_dir, "thumb.jpg")
        ok = generate_thumbnail(self.video1_path, 1.0, thumb_path)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(thumb_path))
        self.assertGreater(os.path.getsize(thumb_path), 0)

    def test_build_export_single_clip(self):
        out_path = os.path.join(self.temp_dir, "out_single.mp4")
        clips = [ClipSegment(self.video1_path, 0.5, 2.5)]
        cmd, duration = build_export_command(
            clips=clips,
            output_path=out_path,
            encoder="h264_nvenc",
            quality_preset="medium",
        )
        self.assertAlmostEqual(duration, 2.0, places=2)
        cmd_str = " ".join(cmd)
        self.assertIn("h264_nvenc", cmd_str)
        self.assertIn("-ss 0.500", cmd_str)
        self.assertIn("-to 2.500", cmd_str)
        self.assertIn(out_path, cmd_str)

    def test_build_export_multi_clip_stitch(self):
        out_path = os.path.join(self.temp_dir, "out_stitched.mp4")
        clips = [
            ClipSegment(self.video1_path, 0.0, 1.5),
            ClipSegment(self.video2_path, 0.5, 2.0),
        ]
        cmd, duration = build_export_command(
            clips=clips,
            output_path=out_path,
            encoder="h264_nvenc",
            target_res=(1920, 1080),
        )
        self.assertAlmostEqual(duration, 3.0, places=2)
        cmd_str = " ".join(cmd)
        self.assertIn("-filter_complex", cmd_str)
        self.assertIn("concat=n=2:v=1:a=1", cmd_str)
        self.assertIn("scale=1920:1080", cmd_str)

    def test_actual_nvenc_export_execution(self):
        # Test real GPU encoding with h264_nvenc
        out_path = os.path.join(self.temp_dir, "out_real_nvenc.mp4")
        clips = [
            ClipSegment(self.video1_path, 0.0, 1.5),
            ClipSegment(self.video2_path, 0.0, 1.5),
        ]
        encoders = [e.codec_id for e in detect_gpu_encoders()]
        encoder_to_use = "h264_nvenc" if "h264_nvenc" in encoders else "libx264"

        cmd, _ = build_export_command(
            clips=clips,
            output_path=out_path,
            encoder=encoder_to_use,
            target_res=(1280, 720),
        )

        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"FFmpeg failed with error:\n{res.stderr}")
        self.assertTrue(os.path.exists(out_path))

        # Verify produced video
        out_info = probe_video(out_path)
        self.assertEqual(out_info.width, 1280)
        self.assertEqual(out_info.height, 720)
        self.assertAlmostEqual(out_info.duration, 3.0, delta=0.3)
        self.assertTrue(out_info.has_audio)

    def test_build_export_cbr(self):
        out_path = os.path.join(self.temp_dir, "out_cbr.mp4")
        clips = [ClipSegment(self.video1_path, 0.0, 2.0)]
        cmd, _ = build_export_command(
            clips=clips,
            output_path=out_path,
            encoder="h264_nvenc",
            rate_control_mode="cbr",
            bitrate_str="15M",
            speed_preset="p5",
        )
        cmd_str = " ".join(cmd)
        self.assertIn("-b:v 15M", cmd_str)
        self.assertIn("-preset p5", cmd_str)
        self.assertIn("-rc cbr", cmd_str)

    def test_actual_cbr_export_execution(self):
        out_path = os.path.join(self.temp_dir, "out_cbr_real.mp4")
        clips = [ClipSegment(self.video1_path, 0.0, 1.0)]
        encoders = [e.codec_id for e in detect_gpu_encoders()]
        encoder_to_use = "h264_nvenc" if "h264_nvenc" in encoders else "libx264"

        cmd, _ = build_export_command(
            clips=clips,
            output_path=out_path,
            encoder=encoder_to_use,
            rate_control_mode="cbr",
            bitrate_str="8M",
            speed_preset="p4",
        )
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"FFmpeg CBR failed:\n{res.stderr}")
        self.assertTrue(os.path.exists(out_path))
        info = probe_video(out_path)
        self.assertAlmostEqual(info.duration, 1.0, delta=0.2)

    def test_speed_atempo_filter(self):
        from speed_config import build_atempo_filter
        self.assertEqual(build_atempo_filter(1.0), "")
        self.assertEqual(build_atempo_filter(2.0), "atempo=2.0000")
        self.assertIn("atempo=0.5", build_atempo_filter(0.25))
        self.assertIn("atempo=2.0", build_atempo_filter(5.0))

    def test_actual_speed_export(self):
        # Test exporting with 2x speed (2s source should export to ~1s output)
        out_path = os.path.join(self.temp_dir, "out_speed_2x.mp4")
        clips = [ClipSegment(self.video1_path, 0.0, 2.0, speed=2.0)]
        self.assertAlmostEqual(clips[0].duration, 1.0, places=2)

        encoders = [e.codec_id for e in detect_gpu_encoders()]
        encoder_to_use = "h264_nvenc" if "h264_nvenc" in encoders else "libx264"

        cmd, total_dur = build_export_command(
            clips=clips,
            output_path=out_path,
            encoder=encoder_to_use,
        )
        self.assertAlmostEqual(total_dur, 1.0, places=2)

        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Speed export failed:\n{res.stderr}")
        self.assertTrue(os.path.exists(out_path))

        out_info = probe_video(out_path)
        self.assertAlmostEqual(out_info.duration, 1.0, delta=0.25)

    def test_clip_sequence_slicing(self):
        from clip_sequence_widget import ClipSequenceWidget
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        seq = ClipSequenceWidget()
        seq.add_clip(self.video1_path, 0.0, 3.0, speed=1.0)
        self.assertEqual(len(seq.get_clips()), 1)

        # Slice at 1.2 seconds
        ok = seq.slice_clip_at(1.2, self.video1_path)
        self.assertTrue(ok)
        clips = seq.get_clips()
        self.assertEqual(len(clips), 2)
        self.assertAlmostEqual(clips[0].start_time, 0.0)
        self.assertAlmostEqual(clips[0].end_time, 1.2)
        self.assertAlmostEqual(clips[1].start_time, 1.2)
        self.assertAlmostEqual(clips[1].end_time, 3.0)

        # Change speed of section 1 to 2x and section 2 to 0.5x
        clips[0].speed = 2.0  # 1.2 / 2.0 = 0.6s
        clips[1].speed = 0.5  # 1.8 / 0.5 = 3.6s
        total_dur = sum(c.duration for c in clips)
        self.assertAlmostEqual(total_dur, 4.2, places=2)

    def test_timeline_editor_multitrack_and_slicing(self):
        from PySide6.QtWidgets import QApplication
        from timeline_editor_widget import TimelineEditorWidget

        app = QApplication.instance() or QApplication([])
        timeline = TimelineEditorWidget()

        # Add two media sources (representing 2 distinct tracks)
        timeline.add_media_source(self.video1_path)
        timeline.add_media_source(self.video2_path)
        self.assertEqual(len(timeline.canvas._media_sources), 2)
        self.assertEqual(timeline.canvas._get_row_for_file(self.video1_path), 0)
        self.assertEqual(timeline.canvas._get_row_for_file(self.video2_path), 1)

        # Add section from video 1 (3 seconds)
        clip1 = timeline.add_clip(self.video1_path, 0.0, 3.0, speed=1.0)
        self.assertEqual(len(timeline.get_clips()), 1)
        self.assertAlmostEqual(timeline.total_timeline_duration(), 3.0)

        # Add section from video 2 (2 seconds)
        clip2 = timeline.add_clip(self.video2_path, 0.0, 2.0, speed=1.0)
        self.assertEqual(len(timeline.get_clips()), 2)
        self.assertAlmostEqual(timeline.total_timeline_duration(), 5.0)

        # Slice video 1 at timeline time 1.0s into two selectable sections
        ok = timeline.slice_clip_at(1.0)
        self.assertTrue(ok)
        clips = timeline.get_clips()
        self.assertEqual(len(clips), 3)
        self.assertAlmostEqual(clips[0].start_time, 0.0)
        self.assertAlmostEqual(clips[0].end_time, 1.0)
        self.assertAlmostEqual(clips[1].start_time, 1.0)
        self.assertAlmostEqual(clips[1].end_time, 3.0)
        self.assertAlmostEqual(clips[2].start_time, 0.0)
        self.assertAlmostEqual(clips[2].end_time, 2.0)

        # Modify speed of section 2 to 2x (duration goes from 2.0s to 1.0s)
        # Slicing automatically selects the second section (part2)
        timeline.canvas._selected_clip_id = timeline.canvas._clips[1].clip_id
        timeline.canvas.set_selected_clip_speed(2.0)
        self.assertAlmostEqual(timeline.canvas._clips[1].speed, 2.0)
        # Total duration: 1.0s + (2.0s / 2.0) + 2.0s = 4.0s
        self.assertAlmostEqual(timeline.total_timeline_duration(), 4.0)

        # Query clip at time 1.5s: spans part2 (global range 1.0s to 2.0s, offset 0.5s)
        res = timeline.get_clip_at_time(1.5)
        self.assertIsNotNone(res)
        q_clip, start_t, dur, offset = res
        self.assertEqual(q_clip.file_path, os.path.abspath(self.video1_path))
        self.assertAlmostEqual(start_t, 1.0)
        self.assertAlmostEqual(dur, 1.0)
        self.assertAlmostEqual(offset, 0.5)

        # Remove the third section (video 2)
        timeline.canvas._selected_clip_id = timeline.canvas._clips[2].clip_id
        ok_remove = timeline.canvas.remove_selected_clip()
        self.assertTrue(ok_remove)
        self.assertEqual(len(timeline.get_clips()), 2)
        # Total duration is now 1.0s + 1.0s = 2.0s
        self.assertAlmostEqual(timeline.total_timeline_duration(), 2.0)

    def test_player_widget_timeline_preview_mode(self):
        from PySide6.QtWidgets import QApplication
        from player_widget import PlayerWidget
        from timeline_editor_widget import TimelineEditorWidget

        app = QApplication.instance() or QApplication([])
        player = PlayerWidget()
        timeline = TimelineEditorWidget()

        timeline.add_clip(self.video1_path, 0.0, 2.0, speed=1.0)
        timeline.add_clip(self.video2_path, 0.0, 2.0, speed=2.0)

        player.set_timeline_callbacks(timeline.get_clip_at_time, timeline.total_timeline_duration)

        # Initially in timeline preview mode
        self.assertEqual(player.preview_mode, PlayerWidget.MODE_TIMELINE)

        # Seek to 1.0s (in first clip: video1, speed 1.0)
        player.seek_timeline(1.0)
        self.assertEqual(player.current_file, os.path.abspath(self.video1_path))
        self.assertAlmostEqual(player._current_speed, 1.0)

        # Seek to 2.5s (in second clip: video2, speed 2.0)
        player.seek_timeline(2.5)
        self.assertEqual(player.current_file, os.path.abspath(self.video2_path))
        self.assertAlmostEqual(player._current_speed, 2.0)

        # Toggle to source preview mode
        player.toggle_preview_mode()
        self.assertEqual(player.preview_mode, PlayerWidget.MODE_SOURCE)

        # Toggle back to timeline preview mode
        player.toggle_preview_mode()
        self.assertEqual(player.preview_mode, PlayerWidget.MODE_TIMELINE)

    def test_mainwindow_ui_flow(self):
        from PySide6.QtWidgets import QApplication
        from main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        win = MainWindow()

        # Load video 1
        win.load_video(self.video1_path)
        self.assertEqual(win.media_library.list_widget.count(), 1)
        self.assertEqual(len(win.timeline_editor.get_clips()), 1)
        self.assertEqual(len(win.timeline_editor.canvas._media_sources), 1)

        # Load video 2
        win.media_library.add_source(self.video2_path)
        self.assertEqual(win.media_library.list_widget.count(), 2)
        self.assertEqual(len(win.timeline_editor.canvas._media_sources), 2)

        # Slicing at 1.0s
        win._on_slice_requested(1.0)
        clips = win.timeline_editor.get_clips()
        self.assertEqual(len(clips), 2)

        # Export returns the timeline clips
        export_clips = win.timeline_editor.get_clips()
        self.assertEqual(len(export_clips), 2)


if __name__ == "__main__":
    unittest.main()
