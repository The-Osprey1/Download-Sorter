# Download Sorter — preview validation

## Deliverables

- Two native PySide6 presentation variants: A — Refined Mint and B — Dark Workspace.
- 26 matched screen/state previews per direction, at six size/scaling combinations: 312 PNG images.
- Offline comparison gallery with screen selection, scale selection, A/B views, full-size image links, and the original repository Home screenshot.
- Source snapshot and isolated preview adapters in `preview-source.zip`.
- Source commit: `513445f4c54b9c89ead87f045fc1add8158a48fd`.

## Executed checks

- All 12 renderer processes exited successfully. Every image is present and decodes correctly.
- All requested window sizes match the recorded actual sizes. Main: 1040 × 760 and 900 × 640. Setup: 760 × 680 and 720 × 640.
- Physical PNG dimensions match Qt scale factors 1.0, 1.5, and 2.0. Dialogs, notifications, component sheets, and the full Settings capture use their own dimensions.
- The two directions contain the same 26 named states in every size/scaling set.
- All 312 image paths referenced by the gallery data resolve locally.
- Existing UI tests: **51 passed** across `test_ui_foundation.py`, `test_ui_workflows.py`, and `test_layout_fit.py`, using `work/run_ui_checks.py` and PySide6 6.8.3. These tests cover the unchanged underlying components and workflows; they do not certify the preview adapters as production code. Pytest emitted two configuration warnings because the optional timeout plugin was not installed.
- Visual inspection covered all screen types at standard size and minimum size at 200% scaling, with full-resolution spot checks for Home, long paths, notifications, and confirmation dialogs.
- Corrected problems found during review: overlapping destination labels in B, notification-host background, crowded onboarding category choices, and excess vertical space in the compact Home layout.
- Existing long Settings descriptions wrap. Long filenames are elided inside table columns; B shortens deep paths while retaining full native tooltips. Those tooltips cannot be opened from a static PNG.
- Selected navigation states, disabled controls, focus rings, status wording, and four notification tones are visible in the captures. Status is communicated with text as well as color.

## Measured palette contrast

WCAG relative-luminance ratios for representative text/surface pairs and the focus outline. This is a palette check, not a complete accessibility audit.

| Role | Foreground | Background | Contrast |
|---|---|---|---|
| A primary text | #EDF3F0 | #151A20 | 15.56:1 |
| A secondary text | #A4B0BE | #151A20 | 7.94:1 |
| B primary text | #EDF3F0 | #151919 | 15.76:1 |
| B secondary text | #AAB8B1 | #151919 | 8.61:1 |
| Primary action text | #0B0D12 | #69E6AD | 12.51:1 |
| Focus outline | #8CB8FF | #1D242C | 7.77:1 |

## Verification limits

- Browser-based gallery verification could not be completed: localhost navigation returned HTTP 403, and browser automation policy rejected `file:` navigation. No alternative browser surface was used to bypass that restriction. The HTML remains a local deliverable that can be opened by the user.
- Gallery markup, JavaScript syntax, and local image paths were checked separately; browser layout, keyboard operation, and event execution remain unverified.
- Offscreen Qt scaling checks do not replace physical Windows multi-monitor DPI testing.
- Screen-reader testing, full keyboard traversal of both proposed layouts, animation, and complete interactive workflow validation were not performed. These are static comparison previews.
- The compact Rules table and folder lists scroll when content exceeds available height. Long table text may be elided; full content remains available through the existing editing controls/tooltips in a future implementation.

## Isolation and reproduction

The original application and generated `suite_theme.py` are unchanged. All adapters live outside the repository. Render state is redirected to disposable workspace folders before importing the application. No actual Downloads files were sorted.

Extract the source archive and follow its README to render the variants. Production rollout, theme switching, and persistence changes are intentionally outside this visual comparison.
