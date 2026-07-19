# Portable Windows release and migration

The per-user installer places program files under
`%LOCALAPPDATA%\Programs\InterviewAssistant`. Installer upgrades replace only the
installer-owned PyInstaller runtime inside that directory. A normal uninstall removes
installer-owned program files only. Both upgrades and uninstalls preserve application data
outside the install directory.

## Moving settings to another PC

The non-secret YAML settings live at
`%LOCALAPPDATA%\InterviewAssistant\InterviewAssistant\config.yaml`. To transfer them, copy
that file to the same path on the new PC while the app is closed.

The LM Studio token is intentionally stored in Windows Credential Manager. It is neither exported nor archived;
enter it again on the new PC. Audio device identifiers are
machine-specific and may need reselection. The remote LM Studio address and model availability
are also machine-specific, so verify them on the destination machine.

## Editable source ZIP

The source ZIP contains the pinned multilingual `large-v3-turbo` RU/EN STT bundle and Git history.
It also contains a sanitized root `config.yaml` with portable defaults, empty model selections,
null audio-device identifiers, loopback LM Studio host, and all seven default hotkeys. This file is
generated from `packaging/source_release_config.yaml`; the machine's live runtime configuration is
never copied into the archive. The ZIP does not contain `.venv`, local caches, credentials, logs,
screenshots, or locally downloaded Hugging Face metadata. After extraction on the target PC,
recreate the development environment:

```powershell
uv sync --extra dev --frozen
```

The extracted standalone clone remains on the captured source branch at the explicitly pinned
source commit, even if release-evidence commits are added later.
It has no `origin` remote, remote-tracking refs, reflogs, transient fetch state, tags, or
unreachable Git objects. Every reachable blob and commit/tag object is scanned for token and
private-key patterns during both archive creation and inspection. Add the intended remote
explicitly before pushing any commits.

## Trust and hardware verification

The application binaries and installer are unsigned unless a real code-signing certificate is
supplied to the release process. Windows may warn before running them. Verify the files against
`SHA256SUMS.txt` produced by the release workflow before installation or extraction.

CUDA acceleration has not been tested on the AMD build machine. It must be verified later on the
target RTX 5070 Ti; no AMD-machine build result should be treated as evidence of working CUDA on
that NVIDIA system.
