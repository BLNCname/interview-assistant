# Release verification — 2026-08-02

## Scope and pinned source

- Hotkey/question implementation commit: `bf09ca0724fd2becd0716c32ed74e5e10dd8addb`.
- Rebuilt portable inventory commit and source-archive pin:
  `5a05e39c240596da08f841a1d84585e9247bdfe3`.
- The source archive inspector confirmed that its standalone Git `HEAD` is the
  pinned commit above. Release evidence added after archive creation is not part
  of that pinned source snapshot.

## Functional verification

- TDD RED: seven Settings failures covered row-labelled invalid hotkeys,
  restart-free apply, manager rollback, persistence rollback, and disk reload;
  the controller integration initially received zero live updates. The detector
  RED set captured all four missing phrase families and the topic-less punctuation
  boundary.
- Focused GREEN: 177 tests passed across config, hotkeys, Settings, controller,
  runtime lifecycle, and question detection; scoped Ruff passed.
- Full source gate: 811 tests passed in 36.59 seconds; repository-wide Ruff
  passed.
- Hotkey-only saves now apply a complete validated seven-action map to the live
  manager, persist it atomically, retain the existing session/readiness graph,
  and restore the previous live map if manager apply or persistence fails.
  Non-hotkey changes retain the readiness/runtime rebuild path.
- RU/EN topical detection covers `интересно ваше мнение`, `давайте обсудим`,
  `I'd like your view`, and `let's discuss` only as anchored cues with an
  immediate lexical topic.

## Packaged and installer evidence

- Portable build: exit 0 in 205.2 seconds.
- Bundled CUDA/STT probe: `status=ok`, `device=cuda`, `model_source=bundled`,
  model load 6.375467 seconds, real-time factor 0.333362.
- Packaged diagnostics: `status=ok`, `frozen=true`, CUDA device/runtime ready,
  missing CUDA DLL count 0.
- Frozen GUI smoke: alive after 15 seconds; Settings visible; seven key-sequence
  editors and seven labels; Ribbon passive and capture-excluded; normal close
  exit 0.
- Inno Setup build: exit 0 in 957.8 seconds.
- Isolated installer smoke: install 0, exact runtime/model inventory hashes
  validated, frozen diagnostics 0, uninstall 0, install tree removed.
- Root portable runtime: exact inventory match for 4,092 files and
  4,180,693,770 bytes; root frozen diagnostics `status=ok`, `frozen=true`, CUDA
  ready.

## Source archive evidence

- Status: `ok`.
- Size: 1,495,506,423 bytes.
- Top level: `InterviewAssistant-source-0.1.0`.
- 222 ZIP entries; 145 tracked files verified; 177 regular files scanned;
  514 reachable Git objects scanned; six manifest-listed STT files verified.
- Sanitized release configuration, complete archive contents, history privacy,
  forbidden paths, secret patterns, and model hashes passed inspection.

## Root handoff SHA-256

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `InterviewAssistant.exe` | 12,491,178 | `8203577BDDA8554342F79A34854C7404AD416BC46EFECC927D6B9AC2D59A2FCF` |
| `InterviewAssistant-Setup-0.1.0-win64.exe` | 2,439,087,289 | `1FF2929C390880B911D92E3AC452E908A7DEEF9AE89CC43D9EFF43647F151E64` |
| `InterviewAssistant-source-0.1.0.zip` | 1,495,506,423 | `9434F3EB4A28A635D7179D4C8FACCAB2BC93FD8E33FFA23ACD360C61763C1185` |

`SHA256SUMS.txt` contains these three values and an independent recalculation
verified all three lines.

## Remaining acceptance boundary

Automated source, package, CUDA, GUI, installer, privacy, and archive gates pass.
The separate instructor/Teams observed acceptance remains a target-machine
exercise; this report does not promote any unobserved hardware scenario to PASS.
