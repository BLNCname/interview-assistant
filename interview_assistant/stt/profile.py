"""Small, non-secret record of the backend that actually passed local inference."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from interview_assistant import __version__


def profile_path(config_path: Path) -> Path:
    return config_path.with_suffix(".stt-hardware.json")


def defer_setup(config_path: Path) -> None:
    save_json(config_path.with_suffix(".stt-deferred.json"), {"version": __version__})


def setup_deferred(config_path: Path) -> bool:
    try:
        return json.loads(config_path.with_suffix(".stt-deferred.json").read_text("utf-8")).get("version") == __version__
    except (OSError, ValueError, AttributeError, UnicodeError):
        return False


def load_profile(config_path: Path) -> dict:
    try:
        path = profile_path(config_path)
        if path.stat().st_size > 65536:
            return {}
        profile = json.loads(path.read_text("utf-8"))
        if not isinstance(profile, dict) or profile.get("version") != __version__:
            return {}
        if profile.get("status") != "ready" or profile.get("device") not in {"cpu", "cuda", "openvino", "directml"}:
            return {}
        if (profile.get("compute_type") not in {"float16", "float32", "int8_float16", "int8"}
                or type(profile.get("gpu_index")) is not int or profile["gpu_index"] < 0
                or not isinstance(profile.get("stt_model"), str) or not profile["stt_model"].strip()):
            return {}
        if profile["device"] != "cuda" and profile["compute_type"] not in {"float32", "int8"}:
            return {}
        return profile
    except (OSError, ValueError, UnicodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
    try:
        with temporary.open("w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def explicit_fields(raw: dict) -> set[str]:
    audio = raw.get("audio", {})
    if not isinstance(audio, dict):
        return set()
    return set(audio.get("saved_preferences", audio))
