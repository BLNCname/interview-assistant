from __future__ import annotations

import base64
from pathlib import Path


_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def build_chat_payload(
    instance_id: str,
    prompt: str,
    image_path: Path | None = None,
) -> dict[str, object]:
    """Build one stateless native LM Studio chat request.

    Images cross the HTTP boundary as an official ``data_url`` value. Local
    capture paths are deliberately never serialized.
    """

    model = instance_id.strip()
    text = prompt.strip()
    if not model:
        raise ValueError("LM Studio model instance id must not be empty")
    if not text:
        raise ValueError("LM Studio prompt must not be empty")

    request_input: object = text
    if image_path is not None:
        mime_type = _IMAGE_MIME_TYPES.get(image_path.suffix.casefold())
        if mime_type is None:
            raise ValueError("LM Studio images must be JPEG, PNG, or WebP")
        if image_path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("LM Studio image is too large")
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        request_input = [
            {"type": "text", "content": text},
            {
                "type": "image",
                "data_url": f"data:{mime_type};base64,{encoded}",
            },
        ]

    return {
        "model": model,
        "input": request_input,
        "store": False,
    }
