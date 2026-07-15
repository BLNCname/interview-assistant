# Hardware and Teams acceptance protocol

This protocol produces observed evidence for `scripts/benchmark_session.py`. It is not
an automated claim that CUDA, LM Link, Teams capture exclusion, or protected-content
handling works on a particular machine. Run it only with every participant's explicit
consent. Use `PASS`, `FAIL`, and `NOT_RUN` literally: missing or incomplete evidence is
`NOT_RUN`, while an observed mismatch is `FAIL`.

The JSONL input contains metadata, timestamps, counters, and booleans only. Never put
transcript text, prompts, answers, audio samples, screenshots, image bytes, credentials,
or tokens into it. Store recordings and screenshots outside the repository and refer to
them with an opaque `evidence_ref`.

`session_id` and `request_id` are short ASCII tokens containing only letters, digits,
dot, underscore, colon, or hyphen. `evidence_ref` is the same kind of opaque token with an
optional path separator. None of these identifiers may contain spaces or descriptive
content. Hardware/software labels are separate fields and may contain spaces. Duplicate
JSON object keys are invalid; the recorder rejects the entire input line rather than
letting a later value replace an earlier security observation.

## 1. Prepare one dated session

1. Create a fresh evidence directory outside the repository and restrict access to the
   consenting test participants.
2. Record UTC date/time, application commit/version, Windows build, NVIDIA driver and
   CUDA versions, RTX GPU, faster-whisper/STT model, LM Studio version, selected LLM/VLM,
   LM Link target/device, Teams version, monitor count, resolution, and scaling in the
   [acceptance template](../docs/validation/acceptance-template.md).
3. Confirm that the main PC runs the complete application and RTX STT worker, while
   Strix Halo performs only LM Studio inference. Do not enable an undeclared LLM fallback
   on the RTX PC.
4. Confirm consent in the template and write one `session_metadata` event. Use an opaque
   evidence reference rather than a transcript or recording name containing spoken text.
5. Start from a clean application session. Keep `InterviewAssistant.exe` visible in Task
   Manager; process masquerading is not part of acceptance.

Example metadata event (replace every value with the observed version):

```json
{"event":"session_metadata","observed_at_utc":"2026-07-15T12:00:00+00:00","consent_confirmed":true,"app_version":"0.1.0+commit","windows_version":"Windows-11-build","teams_version":"observed-version","lm_studio_version":"observed-version","stt_model":"large-v3-turbo","llm_model":"selected-model","main_gpu":"RTX-5070-Ti","inference_device":"Strix-Halo","display_count":1,"display_scale_percent":100,"evidence_ref":"evidence/session-001"}
```

Use a monotonic clock for `timestamp_ms`. Values only need to share the same origin within
the session.

## 2. RU/EN streaming STT benchmark on RTX 5070 Ti

Use at least 20 distinct Russian and 20 distinct English technical utterances. The
speaker must consent to recording and evaluation. Keep the two sources separate: run a
balanced set through the selected microphone and, if system-audio behavior is also being
accepted, repeat it as a separately labelled set. Do not copy utterance text into JSONL.

For every utterance, keep the reference and recognized text only in the controlled WER
worksheet outside JSONL. Emit:

1. `audio_start` when speech begins;
2. `first_partial` when the first usable partial is published;
3. `speech_end` when the VAD/pause begins;
4. `final_transcript` when the final result is published, with only `reference_words` and
   Levenshtein `word_errors` counters.

A complete sample must satisfy `audio_start <= speech_end <= final_transcript` and
`audio_start <= first_partial <= final_transcript`. The first partial may occur before or
after `speech_end`. An incoherent sample is not counted toward the required 20 and cannot
unlock `PASS`.

Keep one opaque `request_id` across all four events and label `language` as `ru` or `en`.
The harness calculates p50/p95 first-partial latency, p50/p95 finalization after pause,
and aggregate WER. A language result is eligible for `PASS`/`FAIL` only after 20 complete,
consented samples with dated RTX/STT metadata. Thresholds are:

- p95 first partial: at most 1000 ms;
- p95 finalization after pause: at most 800 ms.

If either threshold is missed, mark the observed run `FAIL`. Record changed VAD/window/
beam settings, create a new session ID, and repeat the complete set; never overwrite the
failed run. WER is reported as evidence and must not be omitted, but this plan does not
invent a WER pass threshold.

## 3. LM Studio, LM Link, load, and recovery

1. Verify from LM Studio/LM Link diagnostics that the selected instance is on Strix Halo.
   Record `same_model_key_selected`. If text and vision select the same key, exactly one
   loaded instance must serve both. If they select distinct keys, this controlled warm-up
   expects two loaded instances and `shared_text_vision_instance:false`; the distinct-model
   path must not be failed merely because sharing is inapplicable.
2. For a normal request emit `request_submitted`, `prompt_processing_end`,
   `first_answer_token`, and `chat_end` with output-token count only. Repeat enough normal
   requests to make p50/p95 TTFT and tokens/s representative. `output_tokens` must be at
   least one. Because the first token already exists at `first_answer_token`, decode rate is
   `(output_tokens - 1) / (chat_end - first_answer_token)`; therefore a one-token response
   has no tokens/s sample and cannot by itself unlock LM acceptance.
3. Emit `model_load_completed` with measured duration, load attempts, selected-device
   confirmation, loaded-instance count, and shared-instance observation.
4. During a controlled active request, manually unload the selected model in LM Studio.
   Queue at least two newer test questions while recovery is active. Do not record their
   text in JSONL.
5. Observe that the assistant performs exactly one reload operation, uses one to three
   attempts, returns to the preferred Strix Halo device, and replays only the newest queued
   request with Recovery Context. Older queued requests must not stream answers. Record
   `rtx_llm_fallback_used:false`; preferred-device confirmation alone does not prove that
   no fallback ran during the outage.
6. Emit one `recovery_completed` event. Measure total recovery duration, but do not apply
   an arbitrary upper limit to loading a large model. A duration is evidence, not a pass
   threshold.
7. Repeat once after an LM Link disconnect/reconnect. Keep this as a separate session if
   its topology or software version differs.

Any duplicate instance, wrong device, more than one successful reload operation,
more than three attempts, stale replay, or automatic RTX LLM fallback is `FAIL`.

## 4. Microsoft Teams entire-screen acceptance

Use two independently controlled endpoints and a consented meeting. Endpoint A is the
main RTX PC; Endpoint B is the observer/recording reviewer.

1. On Endpoint A select **Share > Screen** for the entire primary display, not an app
   window. Start a Teams recording with participant consent.
2. On Endpoint B verify that the whole desktop is visible. Record Windows, Teams, display,
   HDR, resolution, scaling, and monitor topology.
3. On Endpoint A show, move, resize, collapse, expand, and change the opacity of Liquid
   Ribbon. Stream a multi-token answer and trigger both automatic and hotkey event capture.
4. On Endpoint B observe the live share throughout. The Ribbon must be absent while the
   ordinary desktop remains visible. Verify the shared content does not freeze or become
   globally black and record `desktop_visible_and_live:true` only after that observation.
5. Stop sharing and recording. Review the saved recording from Endpoint B or another
   consented reviewer. Verify the Ribbon is absent for all interactions and streamed
   output.
6. Save the readiness display-affinity diagnostics and an opaque reference to the live
   observation/recording. Emit `teams_acceptance` only after every boolean was explicitly
   observed.

If the Ribbon appears live or in the recording, the result is `FAIL`; do not claim that it
is invisible. `WDA_EXCLUDEFROMCAPTURE` is best effort and can be incompatible with a
Windows/Teams capture path. Record that incompatibility and its diagnostics. The process
remaining visible in Task Manager is expected.

## 5. Event capture, protected content, and notifications

1. With consent, trigger an event screenshot on ordinary content. Observe capture duration
   and whether Ribbon is excluded. Emit `capture_completed`; do not store pixels in JSONL.
2. Open an application/source that legitimately returns a protected or black capture frame
   under its normal policy. Do not weaken DRM, DLP, application policy, or Windows capture
   protection and do not switch to an alternate path to defeat it.
3. Trigger capture and verify the assistant reports `Protected content`, drops the image
   before the VLM request, and continues audio and explicit manual-text paths.
4. Watch both applications and Windows for a screenshot/capture notification. Record
   whether one appeared. The observation can be true or false; it is not a promise that a
   third party will never notify.
5. Do not suppress, intercept, hide, patch, or bypass any third-party notification. Record
   `notification_suppression_attempted:false` and `protection_bypass_attempted:false`, then
   emit `protected_content_acceptance` only after every observation was made.

A protected image reaching the VLM, missing `Protected content` status, broken audio/manual
fallback, any bypass attempt, or any notification-suppression attempt is `FAIL`.

## 6. Shutdown and report generation

Stop the app normally and observe release of audio streams, CUDA/STT worker, LM client,
capture worker, MCP subprocesses, temporary files, and global hotkeys. Emit
`shutdown_completed` with duration and `resources_released`.

First validate the recorder without hardware claims on any development machine:

```powershell
.venv\Scripts\python scripts\benchmark_session.py `
  --mode self-test `
  --report docs\validation\self-test.jsonl
```

The self-test must show `harness_self_test: PASS` and all hardware/system criteria plus
`overall_acceptance` as `NOT_RUN`.

Then convert the observed event stream into the acceptance report:

```powershell
.venv\Scripts\python scripts\benchmark_session.py `
  --mode event-input `
  --events-input C:\secure-evidence\session-events.jsonl `
  --report docs\validation\latest.jsonl `
  --session-id rtx-teams-YYYYMMDD-NN
```

Review every criterion record. `overall_acceptance` can be `PASS` only when all required
observed criteria have complete evidence and pass. Preserve failed and incomplete reports;
do not replace them with self-test output.

`capture_pipeline` is evaluated independently from `teams_full_screen`: the first consumes
the sanitized `overlay_excluded` observation from actual capture output, while the second
consumes the live/recording observations from the second Teams participant. Therefore a
Teams observation cannot make overall acceptance pass when local event-capture evidence is
missing or failed.

## Appendix: accepted event schema

Every JSONL line is one object with `event` plus only the listed fields. Required fields
are shown below; `timestamp_ms` is optional for duration/observation events. The recorder
rejects unknown fields and content-like fields before writing a report.

| Event | Required metadata/timing fields |
|---|---|
| `session_metadata` | `observed_at_utc`, `consent_confirmed`, `evidence_ref`; add dated version/device labels needed by each criterion |
| `audio_start`, `speech_end`, `first_partial` | `timestamp_ms`, `request_id`, `language`, `source` |
| `final_transcript` | fields above plus `reference_words`, `word_errors` |
| `request_submitted`, `prompt_processing_end`, `first_answer_token` | `timestamp_ms`, `request_id` |
| `chat_end` | `timestamp_ms`, `request_id`, `output_tokens` |
| `capture_completed` | `duration_ms`, `capture_reason` (`automatic`/`hotkey`), `overlay_excluded` |
| `model_load_completed` | `duration_ms`, `load_attempts`, `preferred_device_confirmed`, `loaded_instances`, `same_model_key_selected`, `shared_text_vision_instance` |
| `recovery_completed` | `duration_ms`, `request_id`, `unload_during_request`, `reload_count`, `reload_attempts`, `preferred_device_confirmed`, `latest_only_replayed`, `rtx_llm_fallback_used` |
| `teams_acceptance` | all explicit live/recording/interaction booleans from section 4 plus `consent_confirmed` and `evidence_ref` |
| `protected_content_acceptance` | all explicit protected-frame/fallback/notification booleans from section 5 plus `consent_confirmed` and `evidence_ref` |
| `shutdown_completed` | `duration_ms`, `resources_released`, `evidence_ref` |

Use `ru` or `en` for language and `microphone` or `system` for source. IDs and evidence
references are short opaque ASCII labels. The report itself contains a sanitized copy of
these events, calculated metrics, and criteria; it never contains the source utterances or
pixels.
