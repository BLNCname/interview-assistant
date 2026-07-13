from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from interview_assistant.capture.frame_validator import FrameAssessment, FrameValidator


def test_uniform_black_frame_is_protected() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    assert FrameValidator().classify(frame).status == "protected"


def test_normal_frame_is_available() -> None:
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)

    assert FrameValidator().classify(frame).status == "available"


def test_assessment_reports_deterministic_grayscale_metrics_and_hash() -> None:
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    first = FrameValidator().classify(frame)
    second = FrameValidator().classify(frame.copy())

    assert first == FrameAssessment(
        status="protected",
        mean=0.0,
        variance=0.0,
        entropy=0.0,
        near_black_ratio=1.0,
        perceptual_hash="0000000000000000",
    )
    assert second.perceptual_hash == first.perceptual_hash


def test_assessment_is_frozen_and_slotted() -> None:
    assessment = FrameValidator().classify(np.zeros((8, 8, 3), dtype=np.uint8))

    assert not hasattr(assessment, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(assessment, "status", "available")


def test_letterboxing_alone_remains_available() -> None:
    rng = np.random.default_rng(11)
    frame = rng.integers(32, 255, (100, 120, 3), dtype=np.uint8)
    frame[:15] = 0
    frame[-15:] = 0

    assessment = FrameValidator().classify(frame)

    assert assessment.near_black_ratio == pytest.approx(0.30)
    assert assessment.status == "available"


def test_large_black_region_with_high_variance_remains_available() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[:5] = 255

    assessment = FrameValidator().classify(frame)

    assert assessment.near_black_ratio == pytest.approx(0.95)
    assert assessment.variance > 16.0
    assert assessment.status == "available"


def test_low_level_capture_noise_is_still_protected() -> None:
    rng = np.random.default_rng(13)
    frame = rng.integers(0, 4, (100, 100, 3), dtype=np.uint8)

    assessment = FrameValidator().classify(frame)

    assert assessment.near_black_ratio == 1.0
    assert assessment.variance < 16.0
    assert assessment.entropy < 3.0
    assert assessment.status == "protected"


def test_perceptually_unchanged_frame_is_duplicate() -> None:
    rng = np.random.default_rng(17)
    frame = rng.integers(32, 220, (120, 160, 3), dtype=np.uint8)
    first = FrameValidator().classify(frame)
    changed = frame.copy()
    changed[40, 80] = np.array([255, 255, 255], dtype=np.uint8)

    second = FrameValidator().classify(changed, first.perceptual_hash)

    assert first.status == "available"
    assert second.perceptual_hash == first.perceptual_hash
    assert second.status == "duplicate"


def test_duplicate_distance_is_configurable() -> None:
    ramp = np.linspace(32, 224, 160, dtype=np.uint8)
    increasing = np.broadcast_to(ramp, (120, 160))
    decreasing = increasing[:, ::-1]
    first_frame = np.repeat(increasing[..., None], 3, axis=2)
    second_frame = np.repeat(decreasing[..., None], 3, axis=2)
    first_hash = FrameValidator().classify(first_frame).perceptual_hash

    strict = FrameValidator(duplicate_distance_threshold=63).classify(
        second_frame,
        first_hash,
    )
    permissive = FrameValidator(duplicate_distance_threshold=64).classify(
        second_frame,
        first_hash,
    )

    assert strict.status == "available"
    assert permissive.status == "duplicate"


def test_protected_frame_wins_over_duplicate_comparison() -> None:
    protected = np.zeros((100, 100, 3), dtype=np.uint8)
    protected_hash = FrameValidator().classify(protected).perceptual_hash

    assessment = FrameValidator().classify(protected, protected_hash)

    assert assessment.status == "protected"
