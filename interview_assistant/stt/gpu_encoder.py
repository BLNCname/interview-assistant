"""GPU Whisper encoder with the existing multilingual CTranslate2 CPU decoder."""

from pathlib import Path
from threading import Lock

import ctranslate2  # type: ignore[import-untyped]
import numpy as np
from faster_whisper import WhisperModel  # type: ignore[import-untyped]

from .download import ModelUnavailable


class GPUWhisperModel(WhisperModel):
    def __init__(self, weights: str, encoder: Path, backend: str, precision: str, adapter: int):
        super().__init__(weights, device="cpu", compute_type=precision, local_files_only=True)
        if backend == "openvino":
            import openvino as ov  # type: ignore[import-untyped]

            core = ov.Core()
            devices = [d for d in core.available_devices if d.split(".")[0] == "GPU"
                       and "intel" in core.get_property(d, "FULL_DEVICE_NAME").lower()]
            if adapter >= len(devices):
                raise ModelUnavailable("OpenVINO GPU is unavailable. Check the graphics driver and GPU index in Audio.")
            try:
                model = core.read_model(str(encoder))
                # The stream sends one 30-second mel window per encoder invocation.
                model.reshape([1, self.feature_extractor.mel_filters.shape[0], self.feature_extractor.nb_max_frames])
                compiled = core.compile_model(model, devices[adapter])
            except RuntimeError as error:
                raise ModelUnavailable("OpenVINO could not compile this model on the selected Intel GPU. Check its driver or select a smaller model.") from error
            self._dtype = compiled.input(0).element_type.to_dtype()
            self._encode_batch = lambda batch: compiled(batch, share_outputs=False)[compiled.output(0)]
        else:
            import onnxruntime as ort  # type: ignore[import-untyped]

            if "DmlExecutionProvider" not in ort.get_available_providers():
                raise ModelUnavailable("DirectML runtime is unavailable. Install the Windows build with DirectML support.")
            options = ort.SessionOptions()
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(str(encoder), options, providers=[
                ("DmlExecutionProvider", {"device_id": str(adapter)}), "CPUExecutionProvider",
            ])
            if "DmlExecutionProvider" not in session.get_providers():
                raise ModelUnavailable("DirectML could not open this GPU. Check its driver and GPU index in Audio.")
            session.disable_fallback()
            input_info = session.get_inputs()[0]
            self._dtype = np.dtype("float16" if input_info.type == "tensor(float16)" else "float32")
            self._encode_batch = lambda batch: session.run(["last_hidden_state"], {input_info.name: batch})[0]
        self._encode_lock = Lock()

    def encode(self, features: np.ndarray) -> ctranslate2.StorageView:
        # DirectML permits only one Run on a session at a time.
        batch = np.ascontiguousarray(features[None, ...] if features.ndim == 2 else features,
                                     dtype=self._dtype)
        with self._encode_lock:
            hidden = self._encode_batch(batch)
        return ctranslate2.StorageView.from_array(np.ascontiguousarray(hidden, dtype=np.float32))
