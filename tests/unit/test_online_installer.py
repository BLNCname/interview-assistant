"""Exercise the native Inno preflight on an isolated desktop and tiny payloads."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
import zipfile

import pytest


ROOT = Path(__file__).parents[2]
INCLUDE = ROOT / "packaging/online_downloads.iss"
LEGACY_CLEANUP = ROOT / "packaging/online_legacy_cleanup.iss"
LEGACY_DLLS = (
    "dbgcore.dll", "dbghelp.dll", "MSVCP140_1.dll", "MSVCP140_2.dll",
    "Qt6Core.dll", "Qt6Gui.dll", "Qt6Network.dll", "Qt6OpenGL.dll",
    "Qt6Positioning.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll", "Qt6QmlModels.dll",
    "Qt6QmlWorkerScript.dll", "Qt6Quick.dll", "Qt6WebChannel.dll", "Qt6WebEngineCore.dll",
)


def _isolated_desktop() -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    buffer = ctypes.create_unicode_buffer(512)
    needed = wintypes.DWORD()
    handle = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    if not user32.GetUserObjectInformationW(
        handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
    ):
        return False
    return buffer.value.startswith("CodexSandboxDesktop-")


@pytest.fixture
def compiler():
    path = Path(os.environ.get("INNO_ISCC", ROOT / "build/tooling/inno-6.7.2/ISCC.exe"))
    if os.name != "nt" or not path.is_file():
        pytest.skip("Native Inno harness requires the official Windows Inno compiler")
    if not _isolated_desktop():
        pytest.skip("Native Inno harness refuses the user's interactive desktop")
    return path


@contextmanager
def _server():
    state = {"responses": {}, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            if callback := state.get("on_request"):
                callback(self.path)
            response = state["responses"].get(self.path, (503, b"fixture unavailable"))
            self.send_response(response[0])
            self.send_header("Content-Length", str(len(response[1])))
            self.end_headers()
            try:
                if state.get("slow"):
                    state["started"].set()
                    for offset in range(0, len(response[1]), 1024):
                        self.wfile.write(response[1][offset:offset + 1024])
                        self.wfile.flush()
                        time.sleep(0.01)
                else:
                    self.wfile.write(response[1])
            except (BrokenPipeError, ConnectionResetError):
                pass  # A cancelled fixture closes its HTTP connection.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _fixture(
    tmp_path, compiler, url, *, member="vendor/runtime.dll", member_hash=None,
    wheel_first=False, expanded_size=None, legacy=False,
):
    model = b"fixture model, not actual STT weights"
    library = b"fixture native dependency, never executed"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vendor/runtime.dll", library)
        archive.writestr("unused.txt", b"must not be installed")
    wheel = stream.getvalue()
    artifacts = [
        dict(Id="model", Kind="file", Url=url + "/model", SHA256=_digest(model),
             DisplayName="Fixture model", CacheName=_digest(model) + ".bin", Size=len(model),
             ExpandedSize=len(model)),
        dict(Id="wheel", Kind="wheel", Url=url + "/wheel", SHA256=_digest(wheel),
             DisplayName="Fixture dependency", CacheName=_digest(wheel) + ".zip", Size=len(wheel),
             ExpandedSize=len(library) + len(b"must not be installed")),
    ]
    if expanded_size is not None:
        artifacts[1]["ExpandedSize"] = expanded_size
    files = [
        dict(RelativePath="model.bin", MemberPath="", SHA256=_digest(model),
             ArtifactIndex=0, Size=len(model)),
        dict(RelativePath="runtime.dll", MemberPath=member, SHA256=member_hash or _digest(library),
             ArtifactIndex=1, Size=len(library)),
    ]
    if wheel_first:
        artifacts.reverse()
        for file in files:
            file["ArtifactIndex"] = 1 - file["ArtifactIndex"]
    data = ["procedure LoadOnlinePayload;", "begin", "  SetArrayLength(Artifacts, 2);",
            "  SetArrayLength(PayloadFiles, 2);"]
    for name, records in (("Artifacts", artifacts), ("PayloadFiles", files)):
        for index, record in enumerate(records):
            for key, value in record.items():
                literal = str(value) if isinstance(value, int) else _quoted(value)
                data.append(f"  {name}[{index}].{key} := {literal};")
    data.append("end;")
    data_path = tmp_path / "data.iss"
    data_path.write_text("\n".join(data), encoding="utf-8")
    cache = tmp_path / "cache"
    install = tmp_path / "installed"
    install.mkdir()
    sentinel = install / "previous-version.txt"
    sentinel.write_bytes(b"previous installed data")
    legacy_paths = [install / "_internal" / name for name in LEGACY_DLLS] if legacy else []
    if legacy:
        (install / "_internal" / "PySide6").mkdir(parents=True)
        for path in legacy_paths:
            path.write_bytes(b"obsolete legacy DLL")
        (install / "_internal" / "unrelated.dll").write_bytes(b"preserve unrelated DLL")
        (install / "_internal" / "PySide6" / "Qt6Core.dll").write_bytes(b"preserve new layout")
    legacy_include = f'#include "{LEGACY_CLEANUP}"' if legacy else ""
    source = tmp_path / "fixture.iss"
    app_name = "Online fixture " + uuid.uuid4().hex
    source.write_text(
        f'''#define PayloadDataPath "{data_path}"
#define OnlineCacheDirectory "{cache}"
[Setup]
AppId=OnlineFixture-{uuid.uuid4().hex}
AppName={app_name}
AppVersion=1
DefaultDirName={install}
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir={tmp_path}
OutputBaseFilename=fixture-setup
Compression=none
ArchiveExtraction=full
SetupLogging=yes
{legacy_include}
[Files]
Source: "{{code:GetPreparedFile|0}}"; DestDir: "{{app}}"; DestName: "model.bin"; Flags: external ignoreversion; ExternalSize: {len(model)}; Hash: "{_digest(model)}"
Source: "{{code:GetPreparedFile|1}}"; DestDir: "{{app}}"; DestName: "runtime.dll"; Flags: external ignoreversion; ExternalSize: {len(library)}; Hash: "{_digest(library)}"
[Code]
#include "{INCLUDE}"
''', encoding="utf-8",
    )
    result = subprocess.run([str(compiler), "/Q", str(source)], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return dict(setup=tmp_path / "fixture-setup.exe", cache=cache, install=install,
                sentinel=sentinel, model=model, library=library, wheel=wheel, artifacts=artifacts,
                app_name=app_name, legacy_paths=legacy_paths)


def _stop_fixture_download(app_name):
    assert _isolated_desktop()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    posted = []
    fixture_processes = set()

    @callback_type
    def cancel_button(window, _param):
        title = ctypes.create_unicode_buffer(512)
        user32.SendMessageW(window, 0x000D, len(title), ctypes.addressof(title))  # WM_GETTEXT
        if title.value.replace("&", "") == "Stop download":
            posted.append(bool(user32.PostMessageW(window, 0x00F5, 0, 0)))  # BM_CLICK
        return True

    @callback_type
    def identify(window, _param):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, title, len(title))
        if app_name in title.value:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
            fixture_processes.add(process_id.value)
        return True

    @callback_type
    def visit(window, _param):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if process_id.value in fixture_processes:
            user32.EnumChildWindows(window, cancel_button, 0)
        return True

    user32.EnumWindows(identify, 0)
    user32.EnumWindows(visit, 0)
    return any(posted)


def _run(fixture, *, cancel_after=None):
    assert _isolated_desktop(), "Refusing to start a native fixture on the interactive desktop"
    log = fixture["setup"].parent / ("setup-" + uuid.uuid4().hex + ".log")
    temporary = ROOT / ".cache" / "inno-temp" / uuid.uuid4().hex[:8]
    temporary.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update(TEMP=str(temporary), TMP=str(temporary))
    process = subprocess.Popen(
        [str(fixture["setup"]), "/SILENT" if cancel_after is not None else "/VERYSILENT",
         "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
         "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/LOG=" + str(log)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
    )
    try:
        if cancel_after is not None:
            assert cancel_after.wait(timeout=20), "Fixture download did not start"
            assert _stop_fixture_download(fixture["app_name"]), "Fixture Stop download was not found"
        process.communicate(timeout=90)
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise
    return process.returncode, log.read_text(encoding="utf-8-sig")


def test_native_online_failure_is_bounded_and_preserves_existing_install(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url)
        code, log = _run(fixture)
        assert code == 7, log
        assert state["requests"] == ["/model"] * 3
        assert fixture["sentinel"].read_bytes() == b"previous installed data"
        assert sorted(path.name for path in fixture["install"].iterdir()) == ["previous-version.txt"]


def test_native_online_success_uses_verified_cache_when_offline(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url)
        state["responses"] = {"/model": (200, fixture["model"]), "/wheel": (200, fixture["wheel"])}
        code, log = _run(fixture)
        assert code == 0, log
        assert (fixture["install"] / "model.bin").read_bytes() == fixture["model"]
        assert (fixture["install"] / "runtime.dll").read_bytes() == fixture["library"]
        assert not (fixture["install"] / "unused.txt").exists()
        assert state["requests"] == ["/model", "/wheel"]
        state["responses"].clear()
        state["requests"].clear()
        code, log = _run(fixture)
        assert code == 0, log
        assert state["requests"] == []


def test_native_online_corrupt_cache_is_not_installed_or_kept(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url)
        fixture["cache"].mkdir()
        corrupt = fixture["cache"] / fixture["artifacts"][0]["CacheName"]
        corrupt.write_bytes(b"corrupt cached bytes")
        state["responses"]["/model"] = (200, b"also corrupt downloaded bytes")
        code, log = _run(fixture)
        assert code == 7, log
        assert not corrupt.exists()
        assert not list(fixture["cache"].glob("*.tmp"))
        assert fixture["sentinel"].read_bytes() == b"previous installed data"
        assert not (fixture["install"] / "model.bin").exists()


def test_native_online_keeps_completed_download_before_next_failure(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url)
        state["responses"]["/model"] = (200, fixture["model"])
        code, log = _run(fixture)
        assert code == 7, log
        assert state["requests"] == ["/model", "/wheel", "/wheel", "/wheel"]
        cached_model = fixture["cache"] / fixture["artifacts"][0]["CacheName"]
        assert cached_model.read_bytes() == fixture["model"]
        state["requests"].clear()
        state["responses"] = {"/wheel": (200, fixture["wheel"])}
        code, log = _run(fixture)
        assert code == 0, log
        assert state["requests"] == ["/wheel"]


def test_native_online_rechecks_archive_after_later_download(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url, wheel_first=True)
        fixture["cache"].mkdir()
        archive = fixture["cache"] / fixture["artifacts"][0]["CacheName"]
        archive.write_bytes(fixture["wheel"])
        state["responses"]["/model"] = (200, fixture["model"])

        def replace_cached_archive(path):
            if path == "/model":
                archive.write_bytes(b"changed after the initial cache check")

        state["on_request"] = replace_cached_archive
        code, log = _run(fixture)
        assert code == 7, log
        assert "Cached archive changed before extraction" in log
        assert state["requests"] == ["/model"]
        assert fixture["sentinel"].read_bytes() == b"previous installed data"
        assert not (fixture["install"] / "model.bin").exists()


def test_native_online_rejects_cache_junction_before_read_or_write(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url)
        target = tmp_path / "unrelated-cache"
        target.mkdir()
        sentinel = target / "untouched.txt"
        sentinel.write_bytes(b"unrelated data")
        command = (
            "New-Item -ItemType Junction -Path " + _quoted(fixture["cache"])
            + " -Target " + _quoted(target) + " -ErrorAction Stop | Out-Null"
        )
        linked = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
        code, log = _run(fixture)
        assert code == 7, log
        assert "symbolic links or junctions" in log
        assert state["requests"] == []
        assert sentinel.read_bytes() == b"unrelated data"
        assert sorted(path.name for path in target.iterdir()) == ["untouched.txt"]


def test_native_online_preflights_expanded_disk_space_before_download(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url, expanded_size=2**60)
        code, log = _run(fixture)
        assert code == 7, log
        assert "Not enough free disk space" in log
        assert state["requests"] == []
        assert fixture["sentinel"].read_bytes() == b"previous installed data"


def test_native_online_cancel_preserves_install_and_no_partial_cache(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url)
        state.update(slow=True, started=threading.Event())
        state["responses"]["/model"] = (200, b"fixture bytes" * 65536)
        code, log = _run(fixture, cancel_after=state["started"])
        assert code != 0, log
        assert "Download cancelled" in log
        assert state["requests"] == ["/model"]
        assert not list(fixture["cache"].iterdir())
        assert fixture["sentinel"].read_bytes() == b"previous installed data"
        assert not (fixture["install"] / "model.bin").exists()


@pytest.mark.parametrize("outcome", ["success", "http_failure", "cancel"])
def test_native_online_removes_exact_legacy_dlls_only_after_preparation(
    tmp_path, compiler, outcome,
):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url, legacy=True)
        cancellation = None
        if outcome == "success":
            state["responses"] = {"/model": (200, fixture["model"]), "/wheel": (200, fixture["wheel"])}
        elif outcome == "cancel":
            state.update(slow=True, started=threading.Event())
            state["responses"]["/model"] = (200, b"fixture bytes" * 65536)
            cancellation = state["started"]
        code, log = _run(fixture, cancel_after=cancellation)
        assert code == (0 if outcome == "success" else 7), log
        for path in fixture["legacy_paths"]:
            if outcome == "success":
                assert not path.exists(), path
            else:
                assert path.read_bytes() == b"obsolete legacy DLL", path
        assert (fixture["install"] / "_internal" / "unrelated.dll").read_bytes() == b"preserve unrelated DLL"
        assert (fixture["install"] / "_internal" / "PySide6" / "Qt6Core.dll").read_bytes() == b"preserve new layout"
        assert fixture["sentinel"].read_bytes() == b"previous installed data"


def test_native_online_rejects_legacy_directory_junction_before_cleanup(tmp_path, compiler):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url, legacy=True)
        internal = fixture["install"] / "_internal"
        redirected = tmp_path / "unrelated-legacy-files"
        assert internal.resolve().is_relative_to(ROOT)
        assert redirected.resolve().is_relative_to(ROOT)
        internal.rename(redirected)
        linked = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path " + _quoted(internal)
             + " -Target " + _quoted(redirected) + " -ErrorAction Stop | Out-Null"],
            capture_output=True, text=True, timeout=30,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
        code, log = _run(fixture)
        assert code == 7, log
        assert "symbolic links or junctions" in log
        assert state["requests"] == []
        for name in LEGACY_DLLS:
            assert (redirected / name).read_bytes() == b"obsolete legacy DLL"


@pytest.mark.parametrize("member,bad_hash", [("../outside.dll", None),
                                           ("vendor/runtime.dll", "0" * 64)])
def test_native_online_rejects_unsafe_or_corrupt_selected_member(
    tmp_path, compiler, member, bad_hash,
):
    with _server() as (url, state):
        fixture = _fixture(tmp_path, compiler, url, member=member, member_hash=bad_hash)
        state["responses"] = {"/model": (200, fixture["model"]), "/wheel": (200, fixture["wheel"])}
        code, log = _run(fixture)
        assert code == 7, log
        assert fixture["sentinel"].read_bytes() == b"previous installed data"
        assert not (fixture["install"] / "model.bin").exists()
