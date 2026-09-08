# Single-file online installer

The user downloads one `Setup.exe` from the private GitHub release. The installer
downloads the pinned public runtime dependencies and the STT model, verifies
them, and installs the same frozen application that passed release validation.
No manual `.bin` files, Git installation, Python installation, LM Studio setup,
or developer toolchain is required.

## Distribution decision

The repository remains private. A GitHub browser session does not authenticate
an installer's HTTP requests. Therefore the small private application core is
embedded in Setup, built from the release's Git revision. Public dependency
artifacts are fetched directly from PyPI and the pinned Hugging Face model
revision. No GitHub token, service key, or mutable branch is embedded. This is
the hybrid bootstrapper pattern: embedded product payload plus downloaded
prerequisites. Literal private `git pull` would add an unnecessary GitHub login
and a source build to every installation.

## Requirements

- Windows 11 x64, per-user installation, existing AppId retained.
- Inno Setup 6.7.2 or later; native HTTPS, download progress and SHA-256 checking.
- No custom executable downloader, no installer-wide stronger compression;
  use `Compression=none` for the online Setup payload.
- The build consumes the reviewed frozen distribution and full inventory.
  Every external file is mapped to an exact upstream archive member and digest.
  Files that cannot be proven identical remain embedded.
- PyPI wheel URLs, sizes and SHA-256 come from `uv.lock`; source installations
  use the existing lock. Model URLs use `stt_model_manifest.json`'s full revision.
- Download, verify and extract all prerequisites before changing installed files.
  A download failure or cancellation must leave an existing installation intact.
- A corrupt download cannot be used or silently skipped. Completed verified
  downloads may be cached for retries. Cache hits are reverified.
- Silent installation must fail with a nonzero status on preparation failure.
  Interactive installation must show a useful error and permit retry/cancel.
- Installed paths and hashes must match the tested portable inventory exactly,
  apart from Inno's own uninstall records. No arbitrary wheel installer runs.
- Retain the prior published release until the new installer passes live setup,
  installed diagnostics, inventory verification, and uninstall checks.
- API keys continue to be entered in the application's existing Settings UI.
- Detect NVIDIA name, total/free VRAM, compute capability and driver through a
  bounded local nvidia-smi probe. Explain that STT uses the same pinned
  CTranslate2 runtime across compatible GPUs, with no PyTorch compilation.
  Present conservative CUDA/CPU advice without overwriting existing settings.
- Remove only the 16 obsolete DLL paths identified by comparing the historical
  0.1.1 inventory against 0.1.3. Reject linked installation paths before
  preparation; preserve these files on download failure or cancellation.

## Verification

Unit tests cover mapping failures, path traversal, upstream lock/hash mismatch,
and complete partitioning of embedded versus downloaded files. A native Inno
harness exercises failure and retry before installation. The release installer
is tested with actual public downloads in an isolated Windows desktop, followed
by inventory/hash validation, frozen diagnostics, repeat installation, and
uninstall. Hardware and live model results from earlier releases are historical
evidence, not claimed as new measurements.

## Primary references

- https://jrsoftware.org/ishelp/topic_isxfunc_downloadtemporaryfile.htm
- https://jrsoftware.org/ishelp/topic_isxfunc_createdownloadpage.htm
- https://jrsoftware.org/ishelp/topic_isxfunc_extractarchive.htm
- https://jrsoftware.org/ishelp/topic_scriptevents.htm
- https://github.com/jrsoftware/issrc/blob/main/Examples/CodeDownloadFiles.iss
- https://docs.github.com/en/rest/releases/assets#get-a-release-asset
