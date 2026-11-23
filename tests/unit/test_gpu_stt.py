from types import SimpleNamespace

import numpy as np
import pytest

from interview_assistant.stt.download import ModelUnavailable
from interview_assistant.stt.gpu_encoder import GPUWhisperModel, WhisperModel


@pytest.fixture
def decoder(monkeypatch):
    def load(model, weights, *, device, compute_type, local_files_only):
        assert (device, compute_type, local_files_only) == ("cpu", "int8", True)
        model.feature_extractor = SimpleNamespace(mel_filters=np.zeros((80, 201)), nb_max_frames=3000)
    monkeypatch.setattr(WhisperModel, "__init__", load)


def test_openvino_compiles_for_intel_adapter_and_encodes_mel_window(monkeypatch, tmp_path, decoder):
    import openvino

    class Compiled:
        def input(self, index):
            return SimpleNamespace(element_type=SimpleNamespace(to_dtype=lambda: np.float16))

        def output(self, index):
            return "hidden"

        def __call__(self, batch, *, share_outputs):
            assert batch.shape == (1, 80, 3000) and batch.dtype == np.float16
            return {"hidden": np.ones((1, 1500, 384), np.float16)}

    class Core:
        available_devices = ["CPU", "GPU.0", "GPU.1"]

        def get_property(self, device, prop):
            return {"GPU.0": "NVIDIA", "GPU.1": "Intel Arc"}[device]

        def read_model(self, _):
            return SimpleNamespace(reshape=lambda shape: shapes.append(shape))

        def compile_model(self, model, device):
            assert device == "GPU.1"
            return Compiled()

    shapes = []
    monkeypatch.setattr(openvino, "Core", Core)
    model = GPUWhisperModel("weights", tmp_path / "encoder", "openvino", "int8", 0)
    assert shapes == [[1, 80, 3000]]
    output = np.asarray(model.encode(np.zeros((80, 3000))))
    assert output.shape == (1, 1500, 384) and output.dtype == np.float32
    with pytest.raises(ModelUnavailable, match="GPU"):
        GPUWhisperModel("weights", tmp_path / "encoder", "openvino", "int8", 1)


def test_directml_cpu_fallback_is_reported_as_unavailable(monkeypatch, tmp_path, decoder):
    import onnxruntime as ort

    monkeypatch.setattr(ort, "get_available_providers", lambda: ["DmlExecutionProvider"])
    def create(path, options, providers):
        assert not options.enable_mem_pattern and options.execution_mode == ort.ExecutionMode.ORT_SEQUENTIAL
        assert providers[0] == ("DmlExecutionProvider", {"device_id": "1"})
        return SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(ort, "InferenceSession", create)
    with pytest.raises(ModelUnavailable, match="DirectML"):
        GPUWhisperModel("weights", tmp_path / "encoder", "directml", "int8", 1)
