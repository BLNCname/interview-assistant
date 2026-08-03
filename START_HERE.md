# Interview Assistant — Instructor Quick Start

This folder is the instructor handoff for an educational cybersecurity coursework project. Use
the application only with informed participant consent.

## Install and run

1. Verify the installer before running it:

   ```powershell
   $expected = (Get-Content .\SHA256SUMS.txt).Split()[0]
   $actual = (Get-FileHash .\InterviewAssistant-Setup-0.1.1-win64.exe -Algorithm SHA256).Hash
   if ($actual -ne $expected) { throw "Installer checksum mismatch" }
   ```

2. Run `InterviewAssistant-Setup-0.1.1-win64.exe`.
3. Windows SmartScreen may warn that the publisher is unknown because the academic installer is
   not commercially signed. Continue only after the checksum matches.
4. Install LM Studio 0.4 or newer separately, start its server on `127.0.0.1:1234`, and load a
   compatible model. LM Link is optional and is needed only for inference on another computer.
5. Start Interview Assistant, select the loopback device, microphone, and model, enter an LM Studio
   token if authentication is enabled, and select **Run checks** before **Start**.

The installer includes the application, dependencies, CUDA/cuDNN user-space runtime, and pinned
STT model. It does not include the NVIDIA driver, LM Studio, LLM weights, MCP configuration, API
tokens, or machine-specific settings.

## Review or build the source

Read [`README.md`](README.md) for complete system requirements, source setup, model download,
tests, diagnostics, portable build, installer build, privacy boundaries, and troubleshooting.

The reproducible source environment starts with:

```powershell
uv lock --check
uv sync --extra dev --extra cuda --frozen
.\.venv\Scripts\python.exe -m pytest -q
```

`uv.lock` is the canonical dependency resolution. The ready-to-use installer is intentionally
visible in the repository root but is not stored in Git history.
