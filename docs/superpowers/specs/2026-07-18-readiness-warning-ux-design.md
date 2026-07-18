# Readiness Warning UX Design

## Context

The production readiness report can legitimately contain warnings when all
blocking checks have passed. Examples include unavailable optional STT fixtures,
LM Link routing that cannot be proven through the documented REST API, MCP
connectivity that cannot be verified without making a tool request, and TTFT
above the preferred threshold.

`ReadinessReport.can_start` already permits this state, and the packaged
application exposes an enabled Start button. The current Settings window still
labels the whole report `Readiness: warning`, while the enabled and disabled
button states are visually similar under the Windows Qt style. This makes a
healthy, startable report look blocked.

## Goals

- Make it explicit when the application is ready despite non-blocking warnings.
- Make an enabled Start button visually distinct using the approved graphite
  color direction.
- Preserve every warning and remediation row for diagnostics.
- Continue blocking startup for required checks whose status is `failed`.

## Non-goals

- Do not hide, downgrade, or fabricate success for readiness checks.
- Do not add a fourth readiness status or change probe semantics.
- Do not auto-start a session after readiness completes.
- Do not change runtime startup, audio, STT, LM Studio, LM Link, or MCP behavior.

## UI Behavior

The Settings window derives a presentation state from the existing immutable
`ReadinessReport`:

- A fully ready report displays `Readiness: ready`.
- A startable report with warnings displays
  `Readiness: ready — N non-blocking warnings`.
- A blocked report displays `Readiness: failed — N blocking checks`.
- While checks are absent or running, Start remains disabled and retains the
  platform's disabled appearance.

When `report.can_start` is true, Start uses a graphite background, white text,
and slightly lighter/darker hover and pressed states. No blue accent is used.
The button also receives a short tooltip explaining whether startup is ready or
which blocking checks must be resolved.

## Data Flow

`SettingsWindow.set_readiness_report()` remains the single update point. It
continues filling the table, then computes warning/blocking counts, updates the
status label and tooltip, and applies the enabled visual state. The controller
and `ReadinessReport.can_start` remain the authority for whether a session may
start.

## Error Handling

Required `failed` checks keep Start disabled. Optional failures and warnings stay
visible and do not become successes. No exception text, API token, device detail,
transcript content, or MCP credential is added to the new presentation.

## Testing and Acceptance

Automated Qt tests cover:

1. A report matching the observed mixture of ready and warning checks enables
   Start, identifies warnings as non-blocking, and applies the graphite state.
2. A required failure disables Start and reports the blocking-check count.
3. Editing readiness-affecting settings clears the report and removes the ready
   presentation.

After the full test suite passes, rebuild the onedir package and use Windows UI
Automation to verify that the packaged Start control reports `IsEnabled=True`
for a warning-only report and that invoking it reveals the Interview Assistant
overlay.
