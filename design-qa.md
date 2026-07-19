# Liquid Ribbon — Design QA

## Evidence

- Source visual truth: historical `.superpowers/brainstorm/.../overlay-layout-options-v2.html`, variant B.
- Source capture: historical `.superpowers/sdd/task-10-reference-ribbon.png` (`1872×117`).
- Rendered implementation: historical `.superpowers/sdd/task-10-overlay-windows-final.png` (`1888×142`).
- Full-view comparison: historical `.superpowers/sdd/task-10-comparison-native-final.png`.
- Focused comparisons:
  - historical `.superpowers/sdd/task-10-comparison-focused-left-final.png`;
  - historical `.superpowers/sdd/task-10-comparison-focused-answer-final.png`.
- Viewport: Windows desktop at `1920×1080`; Ribbon uses the available width minus the specified 16 px side insets.
- State: dark theme, expanded Ribbon, opacity `0.88`, status/model chips, one Russian question, a three-line streamed answer, and two sources.

The HTML reference was opened successfully in the local browser preview. The implementation is a native PyQt6 window, so it was rendered and captured through the normal Windows Qt platform rather than a browser canvas. Browser console checks are not applicable to the native window; the reference preview did not show an error state. The forced Qt `offscreen` backend was used only for automated layout tests because it exposes no installed system fonts on this machine.

## Findings

No actionable P0, P1, or P2 findings remain.

The final full-view comparison preserves variant B's wide ribbon composition, `105:170` question/answer proportions, 16 px screen inset, 18 px corner radius, 14×12 px inner padding, 14 px body gap, slate glass gradient, and cyan/violet edge treatment. The implementation is 25 px taller than the source capture because it includes the required persistent sources row and a native collapse control. This is an intentional product constraint, not uncontrolled layout drift.

### Required fidelity surfaces

- Fonts and typography: native Windows resolves Segoe UI; the implementation keeps the intended 11–14 px hierarchy, readable line height, semibold question/first answer line, and correct Cyrillic/Latin glyphs. Focused evidence confirms wrapping and optical hierarchy. The reference capture itself contains mojibake, while the implementation renders valid UTF-8 copy.
- Spacing and layout rhythm: header, two-column body, answer inset, sources row, margins, gaps, border, and radius remain aligned with variant B. The post-fix representative footprint returned from `1888×156` to `1888×142` in the captured bold-text state (the non-capturing plain-text measurement is `1888×140`).
- Colors and visual tokens: the slate gradient, translucent chips, green status indicator, cyan/violet edge, and white/blue text match the source direction. At default opacity the palette is visually stable; muted text is minimally lightened from `#94a3b8` to `#9dabbe` to preserve AA contrast.
- Image quality and asset fidelity: the target contains no photographic, illustrative, logo, or decorative raster assets. The collapse affordance uses the platform Qt standard icon as required; no emoji, placeholder art, handcrafted SVG, or CSS-art substitute was introduced.
- Copy and content: Russian and English content renders correctly, source names remain plain text, and unsafe HTML is inert. The sample status differs from the reference's dynamic `Live transcript` state but occupies the same status-chip role and does not alter hierarchy.
- Accessibility and resilience: all small/secondary text reaches at least `4.5:1` at minimum opacity against the worst-case white desktop composite. Long uninterrupted question/source tokens are pixel-elided without widening the Ribbon, while full raw values remain in application state. Keyboard-selectable answer text, scrolling, and the native collapse button remain available.

## Comparison history

1. Initial comparison — blocked:
   - P2: two long URL-like sources expanded an 800 px Ribbon to 7,326 px; a 2,000-character question expanded it to 28,152–35,302 px.
   - P2: minimum-opacity metadata measured only `2.22–3.62:1` instead of WCAG AA `4.5:1`.
   - Fix: added bounded pixel-aware middle elision with preserved raw state, ignored horizontal minimum hints, shared production color constants, and adaptive accessible foreground colors.
   - Post-fix evidence: long-content show/collapse/expand regression remains bounded at 800 px; numeric contrast regression is at least `4.5:1`; full suite passed.
2. Second comparison — blocked:
   - P2: the new eliding labels left cached narrow-width height hints, increasing the normal Windows Ribbon from `1888×140` to `1888×156`.
   - Fix: auto-height now uses each approved `105:170` column width and `heightForWidth()` for question and source labels.
   - Post-fix evidence: final native capture is `1888×142` with bold sample text, the plain-text measurement is `1888×140`, and the long-token, max-height, scrolling, and collapse regressions remain green.
3. Final comparison — passed:
   - Full-view and both focused comparisons show no remaining actionable fidelity, overflow, contrast, asset, typography, copy, or interaction issue.

## Primary interactions tested

- incremental answer streaming, reset, stale-request rejection, and automatic scroll;
- safe complete-delta bold/inline-code formatting with inert HTML and disabled external links;
- collapse/expand, the deterministic fallback move/resize path forced by automated tests, maximum-height scrolling, and geometry persistence/restoration;
- long question/source input before show and through collapse/expand;
- low-opacity contrast, normal short content, app ownership, show, and idempotent close.

## Implementation checklist

- [x] Match variant B composition and liquid-glass visual tokens.
- [x] Preserve correct UTF-8 Russian/English copy and plain-text safety.
- [x] Keep the compact normal footprint while bounding pathological content.
- [x] Verify minimum-opacity contrast numerically.
- [x] Compare final full view and focused typography/layout regions.
- [x] Re-run the focused and complete automated suites after both QA fixes.

## Follow-up polish

No P3 polish is required for handoff. Native Windows `startSystemMove()`/`startSystemResize()`, real multi-monitor DPI behavior, and Teams full-screen exclusion remain system acceptance checks in later tasks, not visual-QA findings for Task 10.

final result: passed
