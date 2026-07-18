# Overlay Interaction and Question Detection Design

## Goal

Improve the Windows Interview Assistant so the overlay does not block the
underlying interface, every global hotkey is discoverable and configurable,
questions from either audio source can trigger an answer without losing speaker
roles, indirect Russian and English interview prompts are detected, streamed
Markdown is readable, manual screenshots have visible one-shot semantics, and
the model answers like a concise interview candidate.

## Product constraints

- The application remains a Windows-only PyQt6 desktop application.
- `Interviewer` continues to mean system-audio loopback and `You` continues to
  mean the local microphone. Triggering a request must not erase this role data.
- The normal overlay mode is click-through. Mouse interaction is available only
  in an explicit edit mode selected with a configurable global hotkey.
- Capture affinity must be reapplied and verified if changing a window flag
  recreates the native HWND.
- Existing configurations that do not contain a `hotkeys` section remain valid
  and receive documented defaults.
- Tokens and other secrets remain in Windows Credential Manager and never enter
  `config.yaml`, logs, screenshots, source archives, or test fixtures.
- The installer and portable release continue to include the application and
  bundled STT model. LM Studio, the text/vision model, LM Link, and MCP setup
  remain user responsibilities.

## Architecture

The existing event-driven runtime remains authoritative. The change extends the
typed configuration with hotkey bindings, adds one overlay-interaction action to
the event bus, records the source of detected questions, and replaces the
delta-local Markdown formatter with a safe full-buffer renderer. No additional
service process or remote classifier is introduced.

The main flow becomes:

```text
system loopback / microphone
          |
          v
source-preserving STT transcript
          |
          v
RU/EN intent detector + cross-source duplicate suppression
          |
          v
DetectedQuestion(text, kind, trigger_source)
          |
          v
role-labelled recent conversation + optional one-shot screenshot
          |
          v
LM Studio streaming response
          |
          v
safe full-buffer Markdown renderer in LiquidRibbon
```

## Overlay interaction states

`LiquidRibbon` has two independent state axes:

1. Visibility: visible or temporarily hidden.
2. Interaction: passive click-through or edit mode.

The overlay starts visible and passive. Passive mode uses Qt's top-level
transparent-for-input window behavior so mouse clicks, wheel events, and hover
reach the application below the Ribbon. The window remains always-on-top and
capture-excluded.

The configurable `overlay_interaction` hotkey toggles edit mode. Edit mode:

- accepts mouse and keyboard interaction;
- shows a thin `#50DE73` outline and the label `Режим настройки`;
- permits dragging from the header, resizing from all edges, scrolling, and
  text selection;
- returns to passive mode when the same global hotkey is pressed again.

Changing interaction state must preserve visibility, geometry, collapsed state,
answer content, current scroll state, window opacity, and capture affinity. If
Qt recreates the HWND, the existing `WinIdChange` path reapplies affinity before
the edit transition is considered complete.

The separate visibility hotkey hides or shows the Ribbon without pausing audio,
STT, question detection, capture, or inference. Showing the window restores its
previous interaction state. Geometry is persisted after a completed move or
resize and during orderly shutdown. Invalid or off-screen saved geometry falls
back to the existing centered top-screen placement.

## Discoverable and configurable hotkeys

Add a typed `HotkeysConfig` section to `AppConfig`. The defaults are:

| Action key | Settings label | Default |
|---|---|---|
| `force_request` | Отправить текущий контекст разговора | `Ctrl+Shift+Space` |
| `screenshot` | Подготовить снимок для следующего запроса | `Ctrl+Shift+S` |
| `pause` | Пауза или возобновление распознавания | `Ctrl+Shift+P` |
| `overlay_visibility` | Показать или скрыть окно помощника | `Ctrl+Shift+O` |
| `overlay_interaction` | Изменить положение или размер окна | `Ctrl+Shift+I` |
| `forced_web_search` | Включить поиск для следующего запроса | `Ctrl+Shift+W` |
| `clear_answer` | Очистить ответ и историю разговора | `Ctrl+Shift+C` |

Settings displays all seven actions in a dedicated `Горячие клавиши` group.
Each row contains the human-readable label, an editable single-chord value, and
a concise explanation. A `Восстановить стандартные` button restores all seven
defaults in the form before saving.

Saving validates every chord through the existing canonical `HotkeyChord`
parser, requires at least one modifier, and rejects duplicates across actions.
An invalid form is not persisted and identifies the conflicting or malformed
rows. A valid form is persisted without secrets and is applied atomically to the
running `HotkeyManager`; restarting the application is not required. If live
application fails, the previous complete binding map remains active and the
Settings window reports the failure.

The `force_request` action keeps its internal compatibility name but its UI copy
describes its actual behavior. It submits the latest final interviewer utterance
together with recent role-labelled transcript entries from both sources. If no
final interviewer utterance exists, it reports that condition and does not send
an ambiguous request.

## Source-preserving question detection

`DetectedQuestion` records `trigger_source: AudioSource | None`. Automatic
detections use their real source. Manual force requests use `None` while their
context is explicitly built from the latest interviewer utterance.

Final STT hypotheses from both `AudioSource.SYSTEM` and
`AudioSource.MICROPHONE` are eligible for automatic detection. Partial
hypotheses never trigger. The existing recent-question cache remains shared
across both sources so the same utterance captured through loopback and the
microphone produces one request.

The intent layer retains the existing direct questions and strong imperatives
and adds bounded Russian and English request phrases, including these families:

- `хотелось бы услышать ваше мнение`, `интересно ваше мнение`,
  `как вы считаете`, `что вы думаете`, `раскройте тему`, `давайте обсудим`,
  `можете подробнее рассказать`;
- `I'd like to hear your opinion`, `I'd like your view`, `what's your view`,
  `what do you think`, `could you elaborate`, `walk me through`,
  `let's discuss`.

Matching is case-insensitive, whitespace-tolerant, and accepts surrounding
polite language. It remains phrase-based rather than matching isolated words
such as `opinion`, `мнение`, `question`, or `вопрос`, preventing ordinary
narration from becoming a trigger. RU/EN negative fixtures cover statements,
quoted documentation, candidate answers, and incomplete fragments.

When the trigger comes from `Interviewer`, the context has a required
`Latest interviewer request` item. When it comes from `You`, the trigger is
labelled `Candidate clarification trigger`; the latest available interviewer
utterance is added as a separate required item, followed by recent conversation
history. The system prompt instructs the model to answer the current interview
topic for the candidate, not to respond conversationally to the candidate's
clarification. If no interviewer utterance exists, the microphone question is
still answerable but is explicitly labelled as originating from the candidate.

## Manual screenshot lifecycle

The configurable screenshot hotkey keeps one-shot semantics and makes the state
visible:

1. Emit `Снимок создаётся` when capture begins.
2. On a usable frame, retain only that temporary image and show
   `Снимок готов для следующего запроса`.
3. Attach it to the next automatic or manual request and show
   `Снимок добавлен в запрос` when the outbound multimodal request owns it.
4. Clear the pending reference immediately after consumption so it cannot be
   attached to a second request.

A newer manual screenshot replaces the older pending screenshot using the
existing bounded temporary-file policy. Capture failure or a protected/invalid
frame leaves no pending screenshot and produces a concise visible error.
Closing the application cancels capture work and removes temporary images as it
does today.

## Safe streaming Markdown

The current renderer parses each delta independently, so delimiters split across
two LM Studio deltas remain visible. Replace this with rendering from the full
accumulated `answer_text` after every accepted delta. The expected answer size is
small enough for deterministic full-buffer rendering, including the explicitly
allowed longer technical answers.

Supported presentation includes:

- headings;
- bold and italic text;
- ordered and unordered lists;
- inline code and fenced code blocks;
- block quotes.

Raw HTML is treated as inert text or omitted by the Markdown engine. External
resource loading is denied, image syntax cannot load local or remote content,
and link activation remains disabled. A safe browser/document subclass owns the
resource-blocking boundary so a future prompt or renderer change cannot silently
enable network or filesystem reads.

During streaming, the viewport follows the newest content only when it was
already at the bottom. If the user scrolls upward in edit mode, rerendering keeps
their approximate reading position. Plain `answer_text` remains the exact model
output for context history and diagnostics; only display rendering removes
Markdown delimiters.

## Readability

The graphite visual direction remains. The answer pane receives a darker,
denser background, near-white text, a 15 px default body size, and clearer
spacing between paragraphs, lists, and code blocks. Headings use a compact
hierarchy rather than oversized document typography. Code uses the existing
Cascadia Mono/Consolas fallback and a distinct dark block background.

Contrast tests evaluate the final composite at the minimum permitted window
opacity against both black and white desktop backgrounds. Primary answer text
must meet WCAG AA `4.5:1`. Secondary status text retains the same requirement.
The opacity control remains user-configurable within the current documented
range and capture-compatible whole-window opacity strategy.

## Interview-style system prompt

The packaged system prompt explicitly asks the model to speak as a prepared
candidate, not as a chat assistant. For ordinary conceptual or behavioral
questions it must:

- answer directly without preamble;
- use 3–6 short sentences or at most five concise bullets;
- normally remain at or below 120 words;
- avoid generic AI phrases, repeated conclusions, and unnecessary headings;
- answer in the language of the current interview exchange.

The 120-word limit does not apply when the interviewer requests code, an
algorithm, debugging, architecture/system design, a detailed technical
derivation, or another task that cannot be answered correctly in abbreviated
form. Those answers must be complete and may include code, assumptions,
trade-offs, complexity, and step-by-step reasoning where appropriate. Even long
answers remain focused on what a candidate would say or present during an
interview.

For a microphone clarification trigger, the prompt distinguishes the trigger
from the interviewer's topic and instructs the model to prepare the candidate's
substantive answer to that topic.

## Error handling and compatibility

- Missing `hotkeys` configuration uses defaults and is written only after an
  explicit successful Settings save.
- Invalid hotkey edits never partially update the running listener.
- Interaction-mode transition failure restores the previous mode and displays
  an overlay notification.
- Capture-affinity failure remains visible and blocks any claim that the Ribbon
  is excluded from capture.
- Markdown failures fall back to escaped plain text without losing the answer.
- A failed manual screenshot never reuses a stale earlier image.
- Existing readiness behavior remains: warning-only reports can start, required
  failures cannot.

## Testing and release acceptance

Implementation follows test-first development. Required automated coverage:

- microphone and system questions both trigger while retaining their source;
- cross-source duplicates produce one request;
- the agreed indirect RU/EN phrases trigger and declarative negative fixtures do
  not;
- candidate clarification prompts contain separate candidate and interviewer
  labels;
- forced context submission remains based on the latest interviewer utterance;
- every hotkey is visible, editable, persistable, and uniquely validated;
- live hotkey replacement is atomic and restart-free;
- passive mode has top-level click-through behavior and edit mode restores input;
- geometry, content, visibility, collapsed state, and affinity survive mode
  transitions;
- move/resize geometry is persisted;
- split-delta Markdown renders headings, emphasis, lists, and code correctly;
- raw HTML, images, external resources, and link activation remain blocked;
- answer and secondary text meet contrast requirements;
- screenshot state messages and one-shot consumption are correct;
- packaged and recovery prompts contain the agreed concise-answer rule and the
  technical-answer exception.

Before release, run the complete test suite and Ruff, rebuild the portable
onedir application and installer, run frozen diagnostics, exercise real bundled
CUDA STT inference, and smoke-test installer install/diagnostics/uninstall. The
root handoff artifacts and `SHA256SUMS.txt` are replaced only after all checks
pass. Subjective usability and real microphone/YouTube behavior remain final
acceptance checks for the user on the target setup.

## Non-goals

- Speaker diarization beyond the two physical audio sources.
- An LLM call solely to decide whether an utterance is a question.
- Automatic transmission of every microphone utterance.
- Persistent screenshot history.
- Enabling HTML, external media, or clickable links in model output.
- Redesigning the graphite Ribbon or replacing the existing application
  architecture.
