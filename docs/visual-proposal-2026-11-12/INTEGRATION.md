# Review and integration brief for Claude Code

## Requested review

Compare A (Refined Mint) and B (Dark Workspace), assess them against the current application, and recommend A, B, or a clearly described combination. Neither direction has been selected by the owner. Treat this as review material for potential integration, not authorization to merge a redesign automatically.

## Read before implementation

1. Read this proposal's README, screenshots, and validation report. The HTML gallery runs locally; GitHub does not execute it.
2. Inspect current `main` and any applicable local instructions, design reports, shared token sources, and open UI work. Previews use historical commit `513445f4c54b9c89ead87f045fc1add8158a48fd`; this branch starts at `5dafdb2191caad5d4a5d75ba366b0dc8fabe905b`. Main now includes #45's Tk-to-Qt migration; #46 contains Projects hub/tray changes. Preserve all newer features and navigation entries.
3. Extract `preview-source.zip` separately if you want to inspect or reproduce the visual adapters. Do not copy its historical application snapshot over current main. Its README describes `work/render_matrix.py` and `work/build_gallery.py`. Use `work/validate_outputs.py` for image/manifest checks.
4. The adapters in `work/preview_variants.py` are a static rendering experiment. They monkeypatch styles and rearrange private widget fields once before capture. They are not a reusable production theme system or a runtime responsive implementation.

## Concrete design changes to evaluate

| Area | A | B |
|---|---|---|
| Shared styling | 24 px titles, 16 px sections, 14 px body, 12 px captions; calmer borders; consistent spacing | Same legibility baseline, more neutral dark surfaces and tighter radii |
| Navigation | Familiar sidebar with stronger selected state | Narrower rail with text labels; preserve newer navigation pages |
| Home | Refined status card and aligned activity table | Status strip, activity workspace, side context/actions; stacked context at narrow widths |
| Rules | Existing table then filename testing | Filename testing above the ordered table |
| Cleanup | Clear scope, results, and apply actions | Add visible choose/review/apply sequence |
| Settings | Grouping and long-label wrapping | Add an in-page section index |
| Onboarding | Readable controls and scrolling step 1 when needed | Add progress bar and compact mascot area |
| Dialogs/notices | Consistent typography, controls, focus and status roles | Match the workspace surfaces |

## Implementation constraints

- Reuse the existing PySide6 components and semantic styles. Integrate selected changes into current owning components rather than shipping the preview monkeypatches.
- Keep generated `suite_theme.py` generated. Locate its owning source before changing shared tokens; otherwise apply app-level presentation roles.
- Preserve sorting/rule precedence, undo, pause/resume, update flow, keyboard shortcuts, saved settings, and all file-operation safeguards.
- Preserve source/destination information and full-path access when eliding text. Do not make a static label replacement that loses selection, context-menu behavior, or accessibility information.
- A production version of B must actually reflow on window resize, not just choose a layout once at startup. Recompute the table and context layout without losing focus or data.
- Long setting labels must remain clickable/keyboard-operable with their controls. The preview's separate wrapping labels are visual scaffolding; a production implementation must retain label activation and accessible names.
- Keep dynamic loading, error, disabled, selected, paused/stopped, and unsaved states coherent. Do not use color as the sole status cue.
- Do not add a permanent theme picker, change configuration schemas, replace the app framework, or remove newer screens merely to match these previews.

## Suggested implementation sequence after a direction is selected

1. Port shared typography, spacing, contrast, and control-state improvements to current components.
2. Implement the chosen shell/Home composition, including resize behavior and dynamic status changes.
3. Apply the chosen Rules, Cleanup, Settings, onboarding, and dialog treatments. Include current Projects/new Qt surfaces when applicable.
4. Render before/after images on current main-derived code using isolated state. Run the relevant UI checks, then the full suite required by CONTRIBUTING before opening an implementation PR. Explain any environment-only failures rather than suppressing them.

## Acceptance checks

- Standard/minimum sizes: main 1040 × 760 and 900 × 640; onboarding 760 × 680 and 720 × 640. Verify at Qt scales 1, 1.5, and 2, plus actual Windows DPI behavior before release.
- Long filenames, deep paths, long setting descriptions, dense/sparse/empty lists, all onboarding steps, matching/nonmatching filenames, unsaved rules, and running/paused/stopped sorting.
- No overlapping controls or compressed checkbox rows; allow scrolling where necessary. Primary actions and navigation remain reachable.
- Keyboard traversal, visible focus, control labels, dialog focus restoration, screen-reader semantics, and reduced motion. Existing underlying UI tests alone do not establish these properties for a new layout.
- Record final before/after screenshots and test results. Keep the first implementation narrowly visual and reviewable.

## Known preview limits

The mockups are static Qt renders. They are not exhaustive interaction tests. B trades destination-column width for activity height at the standard size; the compact version stacks its context above the list. Some dense table values are elided. Onboarding step 1 now scrolls to prevent compressed category controls. Browser execution of the comparison gallery and screen-reader behavior were not verified.
