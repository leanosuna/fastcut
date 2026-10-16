"""
Playback and export speed configurations, color ranges, and FFmpeg filter utilities.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# (speed_factor, short_label, full_label, fg_color, bg_color)
SPEED_PRESETS: List[Tuple[float, str, str, str, str]] = [
    (0.1,  "0.1x",  "0.1x Ultra Slow", "#b388ff", "#311b92"),  # Deep Purple
    (0.25, "0.25x", "0.25x Slow Mo",   "#80d8ff", "#01579b"),  # Blue
    (0.5,  "0.5x",  "0.5x Half Speed", "#84ffff", "#006064"),  # Cyan
    (0.75, "0.75x", "0.75x Moderate",  "#a7ffeb", "#004d40"),  # Teal
    (1.0,  "1x",    "1x Normal",       "#b9f6ca", "#1b5e20"),  # Green
    (2.0,  "2x",    "2x Fast",         "#ffe57f", "#ff6f00"),  # Amber / Gold
    (3.0,  "3x",    "3x Very Fast",    "#ffd180", "#e65100"),  # Orange
    (5.0,  "5x",    "5x Ultra Fast",   "#ff8a80", "#b71c1c"),  # Red
]


def get_speed_info(speed: float) -> Tuple[float, str, str, str, str]:
    """Returns (speed, short_label, full_label, fg_color, bg_color) matching the speed."""
    for s, short_lbl, full_lbl, fg, bg in SPEED_PRESETS:
        if abs(speed - s) < 1e-3:
            return s, short_lbl, full_lbl, fg, bg
    # Custom fallback
    return speed, f"{speed:g}x", f"{speed:g}x Custom", "#ffffff", "#374151"


def build_atempo_filter(speed: float) -> str:
    """
    Builds a chain of FFmpeg atempo filters to achieve any target speed.
    FFmpeg atempo filter accepts values strictly between 0.5 and 2.0.
    """
    if abs(speed - 1.0) < 1e-4:
        return ""

    filters = []
    rem = speed

    if rem > 1.0:
        while rem > 2.0:
            filters.append("atempo=2.0")
            rem /= 2.0
        if abs(rem - 1.0) > 1e-4:
            filters.append(f"atempo={rem:.4f}")
    else:
        while rem < 0.5:
            filters.append("atempo=0.5")
            rem /= 0.5
        if abs(rem - 1.0) > 1e-4:
            filters.append(f"atempo={rem:.4f}")

    return ",".join(filters)
