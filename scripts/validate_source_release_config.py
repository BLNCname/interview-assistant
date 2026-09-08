from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


EXPECTED_CONFIG = {
    "audio": {
        "system_device_id": None,
        "microphone_device_id": None,
        "sample_rate": 16000,
        "language": "auto",
        "stt_model": "large-v3-turbo",
    },
    "lmstudio": {
        "host": "127.0.0.1",
        "port": 1234,
        "text_model": "",
        "vision_model": "",
        "preferred_device_name": "",
    },
    "capture": {
        "mode": "event",
        "persistent_screenshots": False,
        "black_frame_threshold": 0.92,
    },
    "search": {
        "mode": "auto",
        "provider": "firecrawl",
        "timeout_seconds": 5.0,
    },
    "overlay": {"opacity": 0.88, "max_height": 360},
    "hotkeys": {
        "force_request": "ctrl+shift+space",
        "screenshot": "ctrl+shift+s",
        "pause": "ctrl+shift+p",
        "overlay_visibility": "ctrl+shift+o",
        "overlay_interaction": "ctrl+shift+i",
        "forced_web_search": "ctrl+shift+w",
        "clear_answer": "ctrl+shift+c",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        payload = yaml.safe_load(arguments.config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        payload = None
    if payload != EXPECTED_CONFIG:
        print("sanitized source release config failed validation", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
