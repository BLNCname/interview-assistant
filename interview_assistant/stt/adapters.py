"""Discover the adapter ordinals used by CUDA, OpenVINO and DirectML on Windows."""

import ctypes as ct
from dataclasses import dataclass
import sys
from uuid import UUID


@dataclass(frozen=True)
class Adapter:
    device: str
    index: int
    name: str
    memory: int = 0
    capability: int = 0


def dxgi_adapters() -> list[tuple[int, int, str, int]]:
    if sys.platform != "win32":
        return []

    class Description(ct.Structure):
        _fields_ = [("name", ct.c_wchar * 128), ("vendor", ct.c_uint), ("device", ct.c_uint),
                    ("subsystem", ct.c_uint), ("revision", ct.c_uint), ("vram", ct.c_size_t),
                    ("ram", ct.c_size_t), ("shared", ct.c_size_t), ("luid", ct.c_byte * 8),
                    ("flags", ct.c_uint)]

    ptr = ct.c_void_p
    def call(obj, index, result, *types):
        table = ct.cast(obj, ct.POINTER(ct.POINTER(ptr))).contents
        return ct.WINFUNCTYPE(result, ptr, *types)(table[index])

    lib = ct.WinDLL("dxgi.dll", winmode=0x800)
    create = lib.CreateDXGIFactory1
    create.argtypes = [ptr, ct.POINTER(ptr)]
    create.restype = ct.c_long
    iid = (ct.c_ubyte * 16).from_buffer_copy(UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    factory = ptr()
    if create(iid, ct.byref(factory)) < 0:
        return []
    found = []
    try:
        for index in range(64):
            adapter = ptr()
            if call(factory, 12, ct.c_long, ct.c_uint, ct.POINTER(ptr))(factory, index, ct.byref(adapter)) < 0:
                break
            try:
                desc = Description()
                if call(adapter, 10, ct.c_long, ct.POINTER(Description))(adapter, ct.byref(desc)) >= 0 and not desc.flags & 2:
                    found.append((index, desc.vendor, desc.name, desc.vram))
            finally:
                call(adapter, 2, ct.c_ulong)(adapter)
    finally:
        call(factory, 2, ct.c_ulong)(factory)
    return found


def cuda_adapters() -> list[Adapter]:
    if sys.platform != "win32":
        return []
    try:
        lib = ct.WinDLL("nvcuda.dll", winmode=0x800)
    except OSError:
        return []
    n = ct.c_int()
    if lib.cuInit(0) or lib.cuDeviceGetCount(ct.byref(n)):
        return []
    found = []
    for i in range(n.value):
        handle, major, minor = ct.c_int(), ct.c_int(), ct.c_int()
        memory, name = ct.c_size_t(), ct.create_string_buffer(256)
        if (lib.cuDeviceGet(ct.byref(handle), i)
                or lib.cuDeviceComputeCapability(ct.byref(major), ct.byref(minor), handle)
                or lib.cuDeviceTotalMem_v2(ct.byref(memory), handle)
                or lib.cuDeviceGetName(name, len(name), handle)):
            continue
        found.append(Adapter("cuda", i, name.value.decode(errors="replace"),
                             memory.value, major.value * 10 + minor.value))
    return found


def intel_devices(core) -> list[str]:
    return [d for d in core.available_devices if d.split(".")[0] == "GPU"
            and "intel" in core.get_property(d, "FULL_DEVICE_NAME").lower()]


def detect_adapters() -> list[Adapter]:
    try:
        dxgi = dxgi_adapters()
    except (OSError, AttributeError):
        dxgi = []
    try:
        found = cuda_adapters()
    except (AttributeError, OSError):
        found = []
    if any(vendor == 0x8086 for _, vendor, _, _ in dxgi):
        try:
            import openvino as ov  # type: ignore[import-untyped]
            core = ov.Core()
            for index, device in enumerate(intel_devices(core)):
                name = str(core.get_property(device, "FULL_DEVICE_NAME"))
                try:
                    memory = int(core.get_property(device, "GPU_DEVICE_TOTAL_MEM_SIZE"))
                except (TypeError, ValueError, RuntimeError):
                    memory = 0
                # Integrated GPUs share system RAM. Prefer a dedicated adapter where available.
                if "igpu" in name.lower():
                    memory = 0
                found.append(Adapter("openvino", index, name, memory))
        except (ImportError, RuntimeError, OSError):
            pass
    for index, vendor, name, memory in dxgi:
        if vendor in {0x1002, 0x1022, 0x8086, 0x10DE}:
            found.append(Adapter("directml", index, name, memory))
    return found


def auto_order(adapters: list[Adapter]) -> list[Adapter]:
    # The pinned Windows CTranslate2 DLL carries PTX starting at compute_86.
    usable = [d for d in adapters if d.device != "cuda" or d.capability >= 86]
    rank = {"cuda": 3, "openvino": 2, "directml": 1}
    # Native vendor backend takes precedence over DirectML for the same adapter.
    native = {d.name.casefold() for d in usable if d.device == "cuda"}
    has_intel = any(d.device == "openvino" for d in usable)
    def priority(d: Adapter):
        secondary = d.device == "directml" and (d.name.casefold() in native or
                                                 (has_intel and "intel" in d.name.lower()))
        return (not secondary, d.memory, rank[d.device], -d.index)
    return sorted(usable, key=priority, reverse=True) + [Adapter("cpu", 0, "CPU")]
