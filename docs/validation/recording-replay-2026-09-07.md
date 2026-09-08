# Recorded speech validation — 2026-09-07

Real human speech was replayed through the production streaming STT worker and
question detector. Both tested precision modes finished the 91.167-second English
recording in about 29 seconds on the local RTX 5070 Ti. Quantization reduced peak
GPU allocation; it did not improve throughput in this sample.

## Source and scope

- Recording: ELLLO #47, *Dream Job*, Todd interviewing Jessica, 91.167375 seconds.
- [Source page and published transcript](https://www.manythings.org/elllo/60.html).
  The page attributes the material to ELLLO and states its Creative Commons use.
- [Original MP3](http://www.elllo.org/Audio/A0001/047-Jessica-DreamJob.mp3).
- This is a human conversation about career plans, **not a technical hiring
  interview**. The source MP3 was downloaded to ignored `.cache/recordings` and
  converted with PyAV to 16 kHz mono 16-bit PCM WAV. It is not shipped with the
  repository. YouTube playback was unavailable in this session.
- Both speakers share the recording's single input channel. No speaker diarization
  is performed. This verifies STT and question detection on a recording; it does
  not verify live microphone/loopback separation, live queue drops, overlay output,
  cloud response quality, or end-to-end answer latency.

## Environment and method

- Windows; NVIDIA GeForce RTX 5070 Ti, 16,303 MiB reported VRAM, driver 610.74.
- Model: `large-v3-turbo`, cached CTranslate2 snapshot
  `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` under the former
  `mobiuslabsgmbh/faster-whisper-large-v3-turbo` repository name. The parallel
  `dropbox-dash` cache contained only a README, so it was not used for inference.
- One separate Python process per precision mode; forced English decoding; normal
  streaming worker defaults (0.5 s partial cadence, 0.6 s endpoint silence).
- Offline replay applies backpressure and retains every audio frame. Reported real
  time factor is total elapsed time divided by recording duration, including model
  cold initialization. This is not a live latency percentile.
- GPU memory was sampled with `nvidia-smi` every 250 ms. Peak is total device memory
  used, including other desktop applications. Increment is peak minus pre-run
  baseline, so it is an estimate of application allocation, not an isolated CUDA
  allocator measurement.

```powershell
.venv/Scripts/python.exe scripts/replay_interview.py .cache/recordings/elllo-047-dreamjob.wav --model <local-model-directory> --device cuda --compute-type float16 --language en --include-text --output .cache/recordings/elllo-047-float16.json
.venv/Scripts/python.exe scripts/replay_interview.py .cache/recordings/elllo-047-dreamjob.wav --model <local-model-directory> --device cuda --compute-type int8_float16 --language en --include-text --output .cache/recordings/elllo-047-int8_float16.json
```

## Observed results

| Metric | float16 | int8_float16 |
|---|---:|---:|
| Recording length | 91.167 s | 91.167 s |
| Elapsed time, including cold initialization | 29.187 s | 29.317 s |
| Real time factor | 0.320 | 0.322 |
| Decoder invocations | 105 | 105 |
| Decode duration p95 | 206.95 ms | 245.86 ms |
| Final utterances | 18 | 18 |
| Question-containing utterances detected | 3 | 3 |
| Pre-run total GPU memory | 1,785 MiB | 1,755 MiB |
| Peak total GPU memory | 4,272 MiB | 3,499 MiB |
| Incremental peak GPU memory | 2,487 MiB | 1,744 MiB |
| WER against published edited transcript | 18.25% | 17.49% |

The reference contains 263 words after punctuation removal. WER includes
normalization differences, disfluencies, and edits in the published transcript;
it is not a manually aligned accuracy ground truth. Both precision modes retained
the principal questions. Pauses shorter than 600 ms merged some interviewer
questions with candidate answers, yielding three question-containing utterances
instead of individual speaker turns. Some short fragments were inaccurate.
These results do not establish complete question recall or production acceptance.

The measured incremental allocation was approximately 30% lower with
`int8_float16`. It provides evidence that this STT model's memory needs can fit
inside 8 GB with useful headroom when the LLM runs remotely. The RTX 3060 Ti was
**not measured**: its speed, simultaneous two-channel workload, other applications,
and driver behavior still require testing on that computer. The 5070 Ti timings
must not be represented as 3060 Ti timings.

## Startup speech fixtures

`assets/diagnostics/stt-en.wav` and `stt-ru.wav` contain synthetic English and Russian
speech, respectively, generated from the original test phrases in
`assets/diagnostics/speech-fixtures.json` using Google Translate TTS. They are
4.128 and 5.664 seconds long. They exercise actual decoding on startup, without
downloading audio or contacting TTS again.

Actual CUDA `int8_float16` inference recognized both phrases exactly, with the
expected language forced per fixture. The first inference, including cold model
load, took 14.94 seconds; the Russian inference on the already loaded engine took
0.196 seconds. This demonstrates why the former 8-second cold STT readiness timeout
was insufficient. These two controlled fixtures are smoke checks, not human
interview accuracy evidence.

## Regression checks

78 focused STT engine, worker, deterministic integration, and recording replay
tests passed after adding forced decode language, fair two-source scheduling,
idle-source finalization when no new packets arrive, and lossless WAV replay.
Each new runtime behavior had a failing regression test before implementation.

The recording replay reports live in ignored `.cache/recordings`; they include
transcripts only because `--include-text` was explicitly enabled. Default reports
contain counts, timings, languages, and question types without transcript content.
