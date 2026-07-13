from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image

FrameStatus = Literal["available", "protected", "duplicate"]


@dataclass(frozen=True, slots=True)
class FrameAssessment:
    status: FrameStatus
    mean: float
    variance: float
    entropy: float
    near_black_ratio: float
    perceptual_hash: str


class FrameValidator:
    """Classify captured frames without attempting to bypass protected content."""

    def __init__(
        self,
        *,
        near_black_value: int = 16,
        near_black_ratio_threshold: float = 0.92,
        protected_variance_threshold: float = 16.0,
        protected_entropy_threshold: float = 3.0,
        duplicate_distance_threshold: int = 4,
        hash_size: int = 8,
    ) -> None:
        if not 0 <= near_black_value <= 255:
            raise ValueError("near_black_value must be between 0 and 255")
        if not 0.0 <= near_black_ratio_threshold <= 1.0:
            raise ValueError("near_black_ratio_threshold must be between 0 and 1")
        if protected_variance_threshold < 0:
            raise ValueError("protected_variance_threshold must be non-negative")
        if protected_entropy_threshold < 0:
            raise ValueError("protected_entropy_threshold must be non-negative")
        if duplicate_distance_threshold < 0:
            raise ValueError("duplicate_distance_threshold must be non-negative")
        if hash_size <= 0:
            raise ValueError("hash_size must be positive")
        self._near_black_value = near_black_value
        self._near_black_ratio_threshold = near_black_ratio_threshold
        self._protected_variance_threshold = protected_variance_threshold
        self._protected_entropy_threshold = protected_entropy_threshold
        self._duplicate_distance_threshold = duplicate_distance_threshold
        self._hash_size = hash_size

    def classify(
        self,
        frame: NDArray[np.generic],
        previous_perceptual_hash: str | None = None,
    ) -> FrameAssessment:
        gray = _to_grayscale(frame)
        grayscale_bytes = np.clip(np.rint(gray), 0, 255).astype(np.uint8)
        mean = float(np.mean(gray))
        variance = float(np.var(gray))
        histogram = np.bincount(grayscale_bytes.ravel(), minlength=256)
        probabilities = histogram[histogram > 0] / grayscale_bytes.size
        entropy = float(-np.sum(probabilities * np.log2(probabilities)))
        near_black_ratio = float(np.mean(gray <= self._near_black_value))
        perceptual_hash = _difference_hash(grayscale_bytes, self._hash_size)

        protected = (
            near_black_ratio >= self._near_black_ratio_threshold
            and variance <= self._protected_variance_threshold
            and entropy <= self._protected_entropy_threshold
        )
        if protected:
            status: FrameStatus = "protected"
        elif (
            previous_perceptual_hash is not None
            and (
                distance := _hash_distance(
                    perceptual_hash,
                    previous_perceptual_hash,
                )
            )
            is not None
            and distance <= self._duplicate_distance_threshold
        ):
            status = "duplicate"
        else:
            status = "available"

        return FrameAssessment(
            status=status,
            mean=mean,
            variance=variance,
            entropy=entropy,
            near_black_ratio=near_black_ratio,
            perceptual_hash=perceptual_hash,
        )


def _to_grayscale(frame: NDArray[np.generic]) -> NDArray[np.float64]:
    array = np.asarray(frame)
    if array.size == 0:
        raise ValueError("frame must not be empty")
    if array.ndim == 2:
        return array.astype(np.float64)
    if array.ndim != 3 or array.shape[2] not in (3, 4):
        raise ValueError("frame must be grayscale, RGB, or RGBA")
    rgb = array[..., :3].astype(np.float64)
    return rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float64)


def _difference_hash(grayscale: NDArray[np.uint8], hash_size: int) -> str:
    image = Image.fromarray(grayscale)
    resized = np.asarray(
        image.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS),
        dtype=np.uint8,
    )
    bits = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return f"{value:0{(hash_size * hash_size + 3) // 4}x}"


def _hash_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None
