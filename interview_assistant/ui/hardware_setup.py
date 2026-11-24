"""Run native compilation outside the Qt thread and show the current install step."""

import os
from pathlib import Path
import subprocess

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from interview_assistant.stt.automatic import executable_args
from interview_assistant.stt.profile import defer_setup, load_profile


def prepare_dialog(config: Path, parent: QWidget | None = None, *, model: str | None = None) -> dict:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Prepare speech recognition")
    layout = QVBoxLayout(dialog)
    label = QLabel("Detecting your graphics adapter and preparing the speech model…")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(370)
    layout.addWidget(label)
    button = QPushButton("Skip for now")
    layout.addWidget(button)
    process = QProcess(dialog)
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    args = executable_args("--prepare-stt", "--config", str(config))
    if model is not None:
        args.extend(("--force-auto", "--model", model))

    def progress():
        while process.canReadLine():
            line = bytes(process.readLine()).decode("utf-8", errors="replace").strip()
            if line:
                label.setText(line)

    def finished(code, status):
        progress()
        if not code and status == QProcess.ExitStatus.NormalExit:
            dialog.accept()
        else:
            label.setText("Preparation failed. Check your connection or graphics driver and retry in Settings → Audio.")
            button.setText("Open settings")

    def failed(error):
        if error == QProcess.ProcessError.FailedToStart:
            finished(1, QProcess.ExitStatus.CrashExit)

    process.readyReadStandardOutput.connect(progress)
    process.finished.connect(finished)
    process.errorOccurred.connect(failed)
    button.clicked.connect(dialog.reject)
    QTimer.singleShot(0, lambda: process.start(args[0], args[1:]))
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if process.state() != QProcess.ProcessState.NotRunning:
        pid = process.processId()
        if os.name == "nt" and pid:
            # The preparation helper can own a separate native CUDA compiler/test process.
            try:
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                                "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=6,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        process.kill()
        process.waitForFinished(2000)
    if not accepted:
        if model is None:
            defer_setup(config)
        return {}
    return load_profile(config)
