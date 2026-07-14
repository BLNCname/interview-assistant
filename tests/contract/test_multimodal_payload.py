from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest


def test_text_payload_is_stateless_and_targets_prepared_instance() -> None:
    from interview_assistant.lmstudio.payload import build_chat_payload

    payload = build_chat_payload("instance:qwen", "Explain the design")

    assert payload == {
        "model": "instance:qwen",
        "input": "Explain the design",
        "store": False,
    }


@pytest.mark.parametrize(
    ("suffix", "mime"),
    [(".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")],
)
def test_multimodal_payload_uses_official_data_url_contract_without_path_leakage(
    tmp_path: Path,
    suffix: str,
    mime: str,
) -> None:
    from interview_assistant.lmstudio.payload import build_chat_payload

    image = tmp_path / f"private-screen-name{suffix}"
    image.write_bytes(b"image-bytes")

    payload = build_chat_payload("instance:qwen-vl", "Inspect this screen", image)

    assert payload == {
        "model": "instance:qwen-vl",
        "input": [
            {"type": "text", "content": "Inspect this screen"},
            {
                "type": "image",
                "data_url": f"data:{mime};base64,{base64.b64encode(b'image-bytes').decode('ascii')}",
            },
        ],
        "store": False,
    }
    serialized = json.dumps(payload)
    assert str(image) not in serialized
    assert image.name not in serialized


def test_multimodal_payload_rejects_unapproved_image_formats(tmp_path: Path) -> None:
    from interview_assistant.lmstudio.payload import build_chat_payload

    image = tmp_path / "screen.bmp"
    image.write_bytes(b"bitmap")

    with pytest.raises(ValueError, match="JPEG, PNG, or WebP"):
        build_chat_payload("instance:qwen-vl", "Inspect", image)


def test_multimodal_payload_rejects_oversized_capture_before_reading_it(
    tmp_path: Path,
) -> None:
    from interview_assistant.lmstudio.payload import MAX_IMAGE_BYTES, build_chat_payload

    image = tmp_path / "oversized.jpg"
    with image.open("wb") as stream:
        stream.truncate(MAX_IMAGE_BYTES + 1)

    with pytest.raises(ValueError, match="too large"):
        build_chat_payload("instance:qwen-vl", "Inspect", image)
