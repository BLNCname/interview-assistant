from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray


class AudioSource(StrEnum):
    SYSTEM = "interviewer"
    MICROPHONE = "you"


@dataclass(frozen=True, slots=True)
class AudioFrame:
    source: AudioSource
    timestamp: float
    samples: NDArray[np.float32]
    sample_rate: int = 16_000
