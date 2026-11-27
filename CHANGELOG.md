# Changelog

All notable changes to **Download Sorter** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [4.0.0-beta.2] - 2026-11-27

A simpler, single-screen way to design your folders, built from feedback that 4.0.0-beta.1 had too many overlapping panels and too much jargon.

### Added
- **Folders screen (`ui/folders/folders_tab.py`).** The first tab is now one screen, in the style of Finder:
  - **Sidebar.** A "Where would a file go?" box, your folder list and a "Handled for you" section. Drag a folder onto another to put it inside; a drop that cannot work is refused while you drag.
  - **Folder page.** Each folder reads as one sentence, "Put [types] [named …] [from …] in this folder.", and you click a blue part to change it. Rename a folder by clicking its name. A note explains when a more specific folder wins, and other rules that send files there are listed with Edit and Remove.
  - **Will land here.** Shows the loose files that would go to the folder and how many files are already inside.
  - **More options.** Turn automatic sorting on or off, skip files with certain words, add an advanced rule (PDF words, renaming, size, unzip), show the folder in File Explorer, find projects inside, or remove the folder.
  - **Folder menu.** Right-click a folder to make a folder inside it, rename, move, combine, find projects, show it in File Explorer, or remove it.
  - **Layout menu (⋯).** Use folders already in Downloads, save or restore a layout, tidy files already inside folders, start over from a ready-made layout, or open the advanced canvas.
  - **New folder menu (+).** Make an empty folder, or add a ready-made set (School, Job hunting, Bills & receipts, Travel, Contracts, 3D printing).
  - **Handled for you.** "Everything else" (an Other folder or leave in Downloads, and optional guessing from past moves), "Duplicates" (keep both, keep the new one or keep the old one; a removed copy goes to the Recycle Bin), and "Waiting" (3D print files held for your slicer, with "File them now").
  - **Still in Downloads.** Lists every loose file and where it would go. Right-click a file to put it in a folder once, or teach the app to always do that. **Tidy** shows the full plan before anything moves.
  - **Messages with Undo.** Every change shows a message with Undo, and folder renames, moves, merges and removals move the files on disk with Undo.
  - **Animations.** Pages crossfade, and new sections fade in.
- **Mistake-proof folder logic (`folder_designer.py`).** All edits go through one module that refuses changes that cannot work and explains why in plain words:
  - names that Windows reserves or that use characters it does not allow;
  - two folders with the same name in one place;
  - moving a folder into itself, or deeper than five levels.
  - A file type lives in one folder at a time, so giving a type to a new folder takes it from the old one and says so.
  - Clearing every part of the sentence makes the folder a manual one instead of deleting it.
  - Renaming or moving a folder updates the rules that point at it.
  - Removing a folder moves its files to the parent folder; nothing is deleted.
- **Rule collision note in New Folder (`ui/flow/folder_builder.py`).** When an existing rule already catches some of the loose files a new folder would match, step 3 shows an amber note naming that rule and where those files go today. Extension-only folders get a **Put this folder ahead of those rules** option; folders with words or websites already go first, and the note says so.

### Changed
- **Tabs are now Folders, Activity and Settings.** The Sorting Flow view is no longer a tab; its dialogs remain reachable from the Folders screen menus. "Trace" from Activity fills the Folders tester.
- **First run opens Folder Setup (`ui/main_window.py`).** New installs go straight into the interactive 3-step Folder Setup Studio instead of the v3 slideshow. Closing it still counts as seen, so automatic sorting is not held back, and the **Guide** button still opens the tour.
- **Taller folder dialogs on roomy screens (`ui/flow/folder_builder.py`, `ui/flow/setup_studio.py`).** New Folder opens at 840px and Folder Setup at 880px when the screen has room, so the website chip row in step 2 no longer needs a scroll. Smaller screens are capped to the usable height.

### Fixed
- Notification bars with a button (such as Undo) no longer log a style sheet error.

## [4.0.0-beta.1] - 2026-11-25

### Added
- **Sorting Flow (v4.0):** Unified 3-tab interface (`Sorting Flow`, `Activity`, `Preferences`) with a live 3-step decision spine (`Hold & group first` → `Your rules` → `Sort the rest by file type`), transparent Bezier wire bridge to the live destination folder tree, inline rule composer, Folder Focus mode, and Teach-by-Example drag-and-drop onto folders.
- **Unified Pipeline (`download_sorter.pipeline`):** Single source of truth for `trace_file()`, `infer_teach_cues()`, and `build_rule_from_cues()` powering the live tester bar, `Activity` row double-click (`Trace in Flow`), and rule authoring.
- **Header Watch Switcher (`Switch ▾`):** Quick-switch between watched folders and add extra folders directly from the header while keeping all v3.6.1 features intact.
- **Guided New Folder dialog (`ui/flow/folder_builder.py`).** A 3-section folder composer: name and location with one-click presets and name checks, clickable file-type, word and website chips (with sample-file drop to fill them in), a **Sort automatically** / **Manual folder** switch, and a plain-English summary with a live preview of matching loose files and an undoable **move them now** option.
- **Folder Setup Studio (`ui/flow/setup_studio.py`).** A 3-step guided setup: start from a scan of your Downloads (games, extracted apps, `_Duplicates` and bundles stay untouched), a blueprint, or your current folders; fix each folder from a checklist with a live filename tester; then review a dry run and apply with Undo.
- **Guided rule composer in Sorting Flow (`ui/flow/spine_panel.py`).** Inline rule editing with format chips, URL and extension clean-up, `+ Folder...` creation, a live plain-English preview and a completeness check.
- **Drag-and-drop flow editing (`ui/flow/`).** Drag rules to reorder or re-route them, drag file-type groups onto folders, and drag folders to nest or promote them, with a live wire preview.
- **Live Downloads alignment (`ui/flow/align_dialog.py`).** A dry-run preview to tidy selected loose files into the current folder layout, with Undo.

### Changed
- **Holding and grouping are opt-in (`config.py`).** New installs no longer hold `.3mf` files, group projects or align existing files until you turn those on.
- **Apple / Windows 11 style redesign and 2-pane Preferences (`ui/main_window.py`, `ui/settings_widgets.py`).**
- **Real-world Downloads hardening (`architecture.py`, `service.py`).** Folder scans follow each folder's dominant file types, protect game, extracted-app and Arduino bundles and curated subfolders, and filter extension IDs and download-server hosts.

### Fixed
- **Crashes in `Qt6Gui.dll` (`0xc0000005`) during painting and drag-and-drop (`ui/flow/bridge_overlay.py`, `spine_panel.py`, `folder_tree_panel.py`).** Painting now checks every widget is still alive and always ends the painter, and drops defer rebuilds until the drag has finished.
- **Toggles showed the wrong state when set quietly (`ui/settings_widgets.py`).**

## [3.6.1] - 2026-11-23

### Added
- **Branch-focused Folder Architecture canvas and cleaner routing (`ui/architecture_canvas.py`).** Opening Folder Architecture now shows Tier 0 (`Downloads`) and Tier 1 folders first, revealing a folder's lower tiers when you click it (with `N sub ▾` badges, a `Collapse ▴` band pill, and a **View: Branch / View: All** toggle). Multi-row lines route cleanly through inter-column channels, and connected cards highlight with parent-accent borders, halos, directional arrowheads, and top/bottom connection ports.
- **Tier 2 subfolder suggestions by File Type or Classification (`architecture.py`, `ui/architecture_canvas.py`).** Adding or renaming a subfolder under any default Tier 1 folder opens a filterable dropdown (**All** | **By File Type** | **By Classification**) alongside free-text custom names, pre-filling extensions, keywords, domains, and exclusions automatically—plus 1-click **+ All File Types** and **+ All Classifications** buttons and matching suggestions in the **New Custom Folder** dialog.
- **Detect Projects in Folder Architecture (`project_detect.py`, `ui/architecture_canvas.py`).** Select any folder card and click **Detect Projects...** to cluster multi-file projects (stripping revision/date/copy/slicer noise, extracting structured course/part codes and repeated phrases, merging spacing/plural variants, and scoring cross-extension bursts), review or edit suggested project names and keywords, and add them as undoable subfolder cards before shifting files.
- **Subfolder merging (`architecture.py`, `service.py`, `ui/architecture_canvas.py`).** Combine two folders via **Merge Into...** or `Shift+Drag` on the canvas to merge rules, reparent or merge matching child subfolders, and move files on disk in a single undoable batch.
- **Subfolder removal confirmation and safe file evacuation (`architecture.py`, `service.py`, `ui/architecture_canvas.py`).** Removing a folder card now opens an **"Are you sure?"** dialog showing its on-disk file count and sample filenames, defaulting to safely moving files to the parent (or another chosen) folder and promoting child subfolders up one tier with full Undo support.
- **Experimental AI Features & Offline Insights (`config.py`, `learning.py`, `service.py`, `chrome_bridge.py`, `ui/main_window.py`).** Added an offline statistical Naive Bayes route learner (`TokenRouteLearner`) and a Settings card for **AI Smart Route Hints**, **AI Auto-Route Unmatched Files** (with a `50%–95%` confidence threshold), **Smart Scan TF-IDF Keywords**, **AI Insights...** (live predictions and 1-click rule creation), and **Reset AI**.
- **Chrome Extension v1.2.0 upgrades (`chrome_extension/`, `chrome_bridge.py`, `service.py`).** Added **Sort Downloads Now**, **Pin site...** rule pinning, clickable destination badges and **Reveal** backed by native `reveal_file`, merged desktop recent moves, smart hints in tab tests, and automatic Native Messaging host manifest validation and self-repair on startup.
- **Visible version badges and subtle startup entrance (`ui/main_window.py`, `ui/motion.py`).** Shows the version in the window title, a header pill, and the footer, and adds a subtle `165ms` opacity entrance animation on launch (respecting reduced-motion settings).

### Changed
- **Major scan, routing, and UI performance pass (`router.py`, `service.py`, `dest.py`, `db.py`, `fs_ops.py`, `learning.py`, `ui/architecture_canvas.py`, `ui/motion.py`).** Switched `preview_architecture_shift()` and `inspect_folder_on_disk()` to `os.scandir()`/`os.walk()`, skipped unused trace formatting during bulk scans (`include_trace=False`), reused `precomputed_plan` on Apply, cached token/path normalization and `TokenRouteLearner`, made `Zone.Identifier`/EXIF/stat reads lazy, reused a persistent lock-guarded SQLite connection in `StateDB`, removed redundant duplicate checks and hashing work in `fs_ops.py`, cached canvas layout/grid geometry, and replaced per-tick stylesheet animations with lightweight overlays (cutting 50k-file architecture scans from ~19s to under 1s with identical routing).
- **`.zip` files sort like normal downloads by default (`config.py`, `router.py`).** Added `zip_peek_enabled` (default `False`) so `.zip` archives follow your User Rules and `Archives` folder instead of being pulled into other categories based on their contents unless explicitly enabled.
- **Cleaner Scan Existing Folders scope and selective import (`architecture.py`, `ui/architecture_canvas.py`).** Reduced default scan depth from `5` to `2` (Tier 1 & Tier 2), skipped extracted/archive/build/cache directories (`extracted`, `_archive`, `node_modules`, `site-packages`, `build`, `dist`, `crashpad`, `ebwebview`, `__macosx`), and made every folder in **Root Map Preview** checkable before merging or replacing.
- **Expanded built-in maker, CAD, firmware, and media extensions (`config.py`, `architecture.py`).** Added `.ino`, `.avif`, `.jfif`, `.3gp`, `.mid`, `.midi`, `.m4r`, `.chitubox`, `.lys`, `.bbsflmt`, `.cnc`, `.mks`, `.lac`, `.lbrn`, `.lbrn2`, `.uf2`, `.m`, `.dmg`, `.pkg`, `.appx`, and `.ics`.

### Fixed
- **Unmanaged project and extracted app subfolders were flattened during architecture shifts (`service.py`).** `preview_architecture_shift()` now descends only into known managed architecture folders and skips `_`-prefixed directories, dot-directories, and project/repository bundles (`.git`, `.gitignore`, `package.json`, `pyproject.toml`, etc.) so extracted software and project trees stay intact.
- **Deleted or unused default Tier 2 subfolders leaked back into compiled rules (`architecture.py`, `config.py`).** `apply_architecture_to_config()` now builds `type_subfolders` strictly from Tier 2 folders present on the canvas and preserves an explicit empty `{}` map across save and reload.
- **Temporary `.tmp` and `.download` files bypassed the Ignore Guard (`router.py`).** Checked `ignore_extensions` before magic-byte sniffing and removed `.tmp` and `.download` from generic sniff extensions so in-flight files are never moved prematurely.
- **Binary `.dat` files starting with `0xFF 0xFF` were misclassified as MP3 (`router.py`).** Tightened raw MP3 frame-header validation to verify MPEG version, Layer III, bitrate, and sample-rate bits.
- **Preset false positives and missed matches (`router.py`, `architecture.py`, `ui/architecture_canvas.py`).** Added `exclude_keywords` across folder nodes, presets, and specificity-ordered rules; added singular/plural and safe compound-suffix matching (e.g., `makerworldlogo.png`, `paper-computer-icons.jpg`); wired Step 2.6 (`Image Metadata Assist`) into `evaluate()` so generic-named screenshots (`IMG_4196.png`) route to `Images\Screenshots` without grabbing 4K logos or banners; tightened `Miniatures & Figurines` (removed bare `mini` and added hardware exclusions), `Bills & Receipts` (removed bare `statement`), `Audio\MP4` (removed `.mp4`), `Source Code Zips` (removed `main`/`master`), and added `.jpg`/`.jpeg` to `Logos & Icons`.
- **Dark-mode dialog styling and spinbox clipping (`ui/theme.py`, `ui/architecture_canvas.py`, `ui/main_window.py`).** Fixed QSS selector precedence and explicit dark styling on folder removal and AI Insights dialogs so backgrounds and `%` spinboxes render cleanly.

## [3.6.0] - 2026-11-22

### Added
- **Tidy more than Downloads.** Settings > **Also Tidy These Folders** adds folders like your Desktop or Screenshots. Each is sorted into folders inside itself with the same rules; files no rule or file type matches stay where they are. Undo works the same.
- **Pick what you download in the welcome steps.** The second welcome step now asks what you download most and adds rules for the ones you tick: School, Job hunting, Bills & receipts, Travel, Contracts or 3D printing. It used to be about holding 3D-print files for a slicer.
- **Three everyday rule packs:** Bills & receipts (invoices, receipts, bank statements, tax forms, also in German, Spanish, French, Italian, Portuguese, Russian, Japanese and Chinese), Travel (boarding passes, itineraries, bookings) and Contracts (agreements, leases, signed documents). The Custom Rules page offers every pack from one **+ Rule Pack** menu.
- **Text follows Windows' text size.** Settings > Accessibility > Text size now makes Download Sorter's text bigger too (up to 150%). Header buttons fold to icons sooner and table columns widen, so nothing is cut off.
- **A simpler window for new installs.** New installs show Home, Custom Rules and Settings. **Show Advanced Tools** in Settings brings back the Folder Architecture page and **Sort Lower Tiers**. Existing setups keep everything they had.
- **Screen readers can name every button.** Narrator, NVDA and JAWS read icon-only buttons as "button", the × close button as "multiplication sign" and the Settings switches with no label. Every button, switch and text box now has a name taken from its label, tooltip or Settings row.

### Changed
- **New installs start with file-type sorting only.** Every new install used to get the School, Career and 3D printing rules. **Return to Defaults** uses the same starting point. People upgrading from 2.x still get the three original packs.
- **Ribbons wait out full-screen apps.** The top-right ribbon no longer slides over a game, a full-screen video, a presentation or the lock screen. The files are still sorted and listed on Home with **Undo**. (Windows' own Do Not Disturb switch isn't reported to apps this way, so it isn't covered yet.)
- **The log stays small.** `sorter_v3.log` starts a new file at 2 MB and keeps two older ones (about 6 MB in all) instead of growing for as long as the app is installed. Unexpected errors are written to it with the details a fix needs.

### Fixed
- **Recent File Insert lost its Open window on a second monitor.** Dragging the Open or Upload window to another monitor put the pill on the wrong screen, or between screens, whenever Windows scaling wasn't 100%. The pill now works out which monitor the window is on and uses that monitor's scaling, and the file checklist under it follows too.
- **Rules didn't work for file names in many languages.** A rule word in Russian, Greek, Arabic, Chinese or Japanese never matched, and an accented word lost its accented letters, so "über" matched `ber_report.pdf`. Words now work in any language, accents on Latin letters are optional (`resume` matches `Résumé.pdf`), and words in languages written without spaces are found inside longer names (`請求書` in `2026年4月請求書.pdf`). Renaming with `{stem_slug}` keeps non-English letters instead of turning the name into `file`.
- **The wrong folder was watched if Downloads had been moved.** Download Sorter always watched `%USERPROFILE%\Downloads`. If you moved Downloads to another drive, or OneDrive moved it, your real downloads were never sorted. It now asks Windows where Downloads is, and Settings shows that folder.
- **The window could read gigabytes on every refresh.** Each refresh paired every file in `_Duplicates` with its original by reading both in full, so one 4 GB duplicate meant 8 GB of reading after every sort. The answer is now remembered until either file changes.

## [3.5.1] - 2026-11-19

### Fixed
- **Keyword shortcuts filed unrelated files (`router.py`).** The 3.5.0 shortcuts that let "hw" match "homework" also let "review" match `PCB_rev3.zip`, "solution" match `sol_system_map.png`, "calculus" match `calc_mortgage.xlsx` and "fluid mechanics" match `fluid_mechanical_pump.pdf`. A shortcut now only connects an abbreviation with its own full word, and the abbreviations that usually mean something else ("sol", "calc", "mech", and "rev" as review) are gone. "rev" still means revision.
- **Zipped folders were taken for Office files (`router.py`).** A zip holding a folder named `Word`, `PPT` or `XL` was filed as a Word, PowerPoint or Excel file. A zip now counts as an Office or 3MF file only when it has the parts those files always contain.
- **Zips went to folders you never made (`router.py`).** Sorting a zip by what is inside it used fixed folder names ("Documents", "Images", "Code", "3D Models", "Code/Gcode") ahead of your own file-type folders. It now uses your folder for the main file type inside, and when you have none, the zip goes where your zips normally go.
- **A word inside a zip skipped the rest of the rule (`router.py`).** When a rule's keyword named a file inside a zip, the rule's file types, exclude words, websites, size and age limits and ALL/ANY setting were ignored. They are all checked now, and exclude words also apply to the zip's own name.
- **A failed unzip could delete a folder it did not make (`fs_ops.py`).** If a folder with the zip's name appeared just before unzipping finished, the cleanup after the failure removed it. Only folders the unzip itself made are removed now.
- **Hidden `.staging` folders were left behind (`fs_ops.py`, `db.py`).** Unzipping now works inside `Downloads\.staging`, which startup cleanup checks, and every `.staging` folder is removed once it is empty. A copy left in a destination's `.staging` folder by a move to another drive that was cut short is removed when Download Sorter next starts.

## [3.5.0] - 2026-11-18

### Added
- **3-Point Sparse-Hash Deduplication Index (`db.py`, `fs_ops.py`).** Added `file_hashes` in `StateDB` with size-gated 3-point sparse hashing (head/mid/tail 64 KB + size) and full SHA-256 confirmation to catch renamed duplicates and quarantine them to `_Duplicates/<YYYY-MM>`.
- **Crash-Safe `.staging` Extraction & Cross-Volume Moves (`fs_ops.py`, `service.py`).** Staged ZIP extractions and cross-volume moves in `.staging\<batch_id>\` with `..` / `..\\` / symlink Zip Slip guards, OneDrive cloud-placeholder checks before opening files, and `cleanup_orphaned_staging()` on startup.
- **Pre-Routing ZIP Peek, MIME/Container Disambiguation & Image Metadata (`router.py`).** Added `peek_zip_contents()`, `sniff_magic_mime()` / ZIP container disambiguation (`.3mf`, `.docx`, `.xlsx`, `.pptx`), copy-suffix/delimiter/alias token normalization (`normalize_filename_tokens()`), and `inspect_image_metadata()` (Pillow EXIF/resolution classification).
- **Duplicate Download Resolution (`Replace Old`, `Delete New & Locate Old`, `Keep Both`) (`fs_ops.py`, `service.py`, `ui/toasts.py`, `ui/main_window.py`).** Duplicate downloads and browser-numbered copies (e.g. `report (1).pdf`) matched to sorted originals inside `Downloads` now offer one-click **Replace Old**, **Delete & Locate Old** (moves the new copy to the Recycle Bin and opens File Explorer highlighting the existing file), and **Keep Both** actions directly in both the top-right Side Ribbon notification and the **Manage Duplicate Files** dialog (`Original Copy` column).
- **Focused Orthogonal Bus Routing & Parent Pills in Folder Architecture (`ui/architecture_canvas.py`).** Decluttered the Folder Architecture diagram with a single vertical Tier 0 trunk, bottom-row placement for Tier 1 parent folders, rounded orthogonal bus routing through a widened gutter (`Lines: Focused` by default with a `Lines: All` toggle pill), and color-tinted `↳ Parent` badges on Tier 2+ cards.
- **Hover Tooltips Replacing Inline Subtitle Clutter (`ui/architecture_canvas.py`, `ui/main_window.py`, `ui/settings_widgets.py`).** Hid cluttered inline muted subtitle explanations across Settings, cards, toolbars, dialogs, and canvas node cards, surfacing clear descriptions on hover for canvas nodes and non-trivial buttons.

### Fixed
- **Scan Folders Button Now Runs Live With Progress & Feedback (`architecture.py`, `ui/architecture_canvas.py`, `ui/main_window.py`).** Fixed a `TypeError` in `ArchitectureTab._on_scan_folders` caused by passing the status title positionally to `_run_background()`, and added live progress HUD reporting, `"Scanning..."` busy button feedback, new-vs-existing folder counts, and completion banners.
- **Apply & Shift Files replaced your Custom Rules (`architecture.py`).** Applying the Folder Architecture rebuilt the rule list from the folder map, so every rule you made, added from a starter pack or carried over from 2.x was renamed after its folder and lost its regex, rename template, exclude words, size and age limits, PDF content words, ALL/ANY setting and auto-unzip. Your own rules are now kept exactly as they are, in their order, ahead of the rules the folder map makes. Words or websites you add on a folder card become a rule for just those words.
- **An empty folder card caught every file (`architecture.py`).** A Tier 3 folder with no file types, words or websites became a rule with no conditions, so every download went into it. An empty folder now stays a plain folder.
- **Keywords matched inside other words (`router.py`).** A keyword of four or more letters matched anywhere in a name: "resume" filed `presumed_dead.epub`, "school" filed `preschool_menu.pdf`, "bank" filed `Bankruptcy_notice.pdf`. A keyword now has to line up with whole words. `Resume2026`, `IME3311` and `lecture_notes` still match "resume", "ime 3311" and "lecturenotes", and the typo allowance needs the first letter to agree.
- **Blank Search, Detox and Scan Folders buttons (`ui/motion.py`).** Their icons were never drawn, so in a normal-sized window they showed as empty grey boxes.
- **Rule suggestions were never shown (`ui/main_window.py`).** Download Sorter counted files you moved by hand into the same folder but never offered the rule. **Custom Rules** now shows "You moved 3 files from chase.com to 'Finance/Bank'. Make that a rule?" with **Make a Rule...** (opens the rule filled in, for you to check) and **Not Now**. A rule you accept goes last, after your own. In a window under 700 px tall the bar folds into a **Suggestion** button.
- **Reading a PDF's text could use hundreds of MB of memory (`router.py`).** A small compressed stream inside a PDF was inflated in full before the first 64 KB were read. Only those 64 KB are inflated now.

## [3.4.0] - 2026-11-17

### Added
- **Auto-Scan Folder Architecture & Snapshots (`architecture.py`, `ui/architecture_canvas.py`).** Added **Scan Folders** (`scan_folder_architecture()`, `merge_architectures()`) to walk your existing `Downloads` tree, infer extensions, distinctive keywords, `Zone.Identifier` domains, and visual kinds, with a **Merge Missing** / **Replace Map** preview dialog. Added **Snapshots** (`save_architecture_snapshot()`, `list_architecture_snapshots()`, `load_architecture_snapshot()`, `delete_architecture_snapshot()`) to save and restore Folder Architecture layouts.
- **Upgraded Sorting & Borrowed Power-User Rule Features (`router.py`, `dest.py`, `config.py`, `ui/main_window.py`).** Added pure-Python magic-byte type sniffing (`sniff_magic_extension()`), CamelCase + unordered token + fuzzy keyword matching, `rule_specificity_score()` / `sort_rules_by_specificity()` (**Smart Order**), EXIF `{exif_year}` / `{exif_month}` photo date tokens, **Exclude Words** (`exclude_keywords`), **Regex Pattern & Named Capture Groups** (`regex_pattern`), **`ALL` vs `ANY` Condition Logic** (`match_mode`), **File Size & Age Bounds** (`min_size_mb`, `max_size_mb`, `min_age_days`), and **Safe Rename Templates** (`rename_template` + `TokenChipBar`).
- **Cohesive `QPainter` Emblems & Instant Hover/Click Feedback (`ui/emblems.py`, `ui/motion.py`, `ui/theme.py`, `ui/main_window.py`).** Added `FolderScanRadarWidget`, `SnapshotEmblemWidget`, `DetoxEmblemWidget`, styled dark-mode `QToolTip`s, upgraded `InteractiveRibbonBtn` hover rims + press pulses, and `install_global_button_feedback()` for immediate footer hover tips and `✓` click feedback on every button.

## [3.2.0] - 2026-11-16

### Added
- **Chrome Extension v1.1.0 Upgrades (`chrome_extension/`, `chrome_bridge.py`, `tray_app.py`).** Active-tab URL/title fallback routing for CDN downloads, dynamic right-click **"Download Sorter: Save & Sort to"** context menu, MIME extension repair (`.jfif`, `.bin`, `.tmp`, extensionless), Chrome-side duplicate guard (`check_duplicate` + `DUP` badge), **Test Site** (`test_tab`), **Move to...** re-routing (`reroute_file`), **Always `<host>`** 1-click rule pinning (`create_rule`), **Pause/Resume** (`toggle_pause` + `set_paused` IPC), **Undo Last**, **Reveal**, and macOS dark-mode popup animations refined with Google AI Studio.
- **Redesigned 5-Card Settings Tab (`ui/settings_widgets.py`, `ui/theme.py`, `ui/main_window.py`).** Grouped settings into 5 scannable macOS dark-mode cards (**Core Routing & Watch Folder**, **Archives & 3D Print Workflows**, **Notifications & Quick Switcher**, **Chrome Extension**, **System, Updates & Backup**) with `MacToggle` switches and `DependentSubGroup` progressive disclosure.
- **Global Automatic `.zip` Extraction & Zip-Bomb Verification (`config.py`, `fs_ops.py`, `service.py`).** Added `auto_unzip_all`, `zip_keep_original`, and per-rule `auto_unzip` backed by `ZipBombError` and real-time 64 KB streaming verification (max 100:1 compression ratio above 1 MB, max 250 MB/file, max 500 MB total, symlink & zip-slip blocking, no recursive nested zip extraction, and atomic rollback on failure).
- **Dynamic Destination Tokens (`dest.py`, `router.py`).** Added `{year}`, `{month}`, `{day}`, `{host}`, and `{ext}` tokens expanded safely inside the watched folder.
- **Content-Aware PDF Sniffing (`router.py`, `config.py`).** Per-rule `content_keywords` matching via `extract_pdf_text_preview()` for uncompressed and `FlateDecode` PDF streams.
- **Downloads Detox (`fs_ops.py`, `service.py`, `ui/main_window.py`).** `DetoxDialog` to review and recycle installers older than 30 days and prune empty folders.
- **Global Quick Search Palette (`Ctrl+Shift+Space`) & Rule Learning (`ui/quick_search_palette.py`, `learning.py`).** Keyboard-first search across `StateDB` history and `Downloads` files, plus manual re-route pattern tracking.
- **Desktop UI & Motion Polish (`ui/theme.py`, `ui/motion.py`).** Specular card gradients, `attach_press_feedback()`, `flash_routed_widget()`, `flash_table_row()`, `crossfade_stacked_widget()`, and `spring_pop_in()`.

### Fixed
- **Bambu Studio "File is missing" on `.3mf` downloads (`chrome_bridge.py`, `fs_ops.py`).** Chrome-routed `.3mf` downloads now bypass the hold and save straight into `3D Models\...` before being opened in Bambu Studio, and `sync_slicer_recent_projects()` updates `BambuStudio.conf` / `OrcaSlicer.conf` `recent_projects` when a `.3mf` is moved on disk.

## [3.1.0] - 2026-11-16

### Added
- **Interactive 4-Step Chrome Extension Picture Guide (`ui/chrome_walkthrough.py`, `ui/assets/chrome_guide/`, `ui/main_window.py`).** Added a **Setup Guide** button (`?`) and clickable walkthrough link in **Settings → Chrome Extension** (plus a **Chrome Guide** shortcut on Step 3 of the welcome walkthrough) with arrow-annotated screenshots for Developer mode, **Load unpacked**, both Windows **Select Folder** path methods, one-click `chrome://extensions` and extension-folder copy buttons, **Reveal in File Explorer**, and live Native Messaging host status / repair.

### Changed
- **Smaller Windows package and release signing readiness (`DownloadSorter.spec`, `build_release.py`, `pe_signing.py`, `verify_release.py`, `packaging/`).** Dropped unused OpenSSL binaries (`libcrypto-3-x64.dll`, `libssl-3-x64.dll`, `qopensslbackend.dll`) that Qt hooks had picked up from runner `PATH` (reducing the Windows distribution archive by ~2.65 MB while keeping Python's own OpenSSL for updates), added a release publisher allowlist and optional whole-folder SignPath signing pipeline with Microsoft VC++ CRT refresh and PowerShell uninstaller script signing, and documented Windows 11 Smart App Control guidance (`README.md`, `BETA_TESTING.md`, `packaging/SIGNING.md`).
- **Release notes say when a version isn't code-signed (`release_notes.py`, `.github/workflows/release.yml`).** Until code signing is set up, each release's notes open with a warning that Windows 11 Smart App Control will block it, with a link to what you can do.

## [3.0.3] - 2026-11-15

### Added
- **Chrome Extension Connector & Native Messaging Bridge (`chrome_extension/`, `chrome_bridge.py`, `tray_app.py`).** Manifest V3 Chrome extension routes downloads straight into Download Sorter's sorted subfolders via `chrome.downloads.onDeterminingFilename` + Native Messaging, keeping Chrome's download bubble draggable/clickable (never marked "Removed") while updating `StateDB`, Quick Switcher (`Ctrl+G`), Side Ribbons, and Recent Activity in real time.
- **Animated Processing HUD (`SortingProgressHUD`) & Working Sorty Mascot (`ui/motion.py`, `ui/workers.py`, `service.py`).** Sorting, lower-tier cleanup, moving held `.3MF` files, Folder Architecture shifts, and Undo now expand an inline dark-mode progress HUD with an indeterminate scanning shuttle sweep, determinate gradient bar + specular highlight catch-light, middle-elided active filename, step & percentage pills, completion transition, and a live working/scanning Sorty folder mascot animation.
- **Native Duplicate File Manager (`DuplicateManagerDialog`, `SorterService.list_duplicate_files`, `delete_duplicate_files`).** Added a **`Duplicates (N)`** button in the Downloads Folder Preview header showing how many files and bytes sit in `_Duplicates\`, with one-click **`Keep Newest Copy`** / **`Select All`** controls and Recycle Bin cleanup confined strictly to `_Duplicates\`.
- **Direct Selection & Deletion in Downloads Folder Preview (`ui/main_window.py`, `fs_ops.py`).** Multi-select files in the Downloads preview table to reveal a contextual **`Delete (N)`** button, press **`Del`**, or right-click to reveal in Explorer or move selected unlocked files directly to the Windows Recycle Bin.

## [3.0.2] - 2026-11-15

### Added
- **Sort Lower Tiers button (`MainWindow.btn_sort_tiers`)**: Added a **Sort Lower Tiers** button in the top toolbar (`SorterService.sort_managed_tiers_now()`) to re-sort misplaced files already inside managed Tier 1 / Tier 2+ folders into their proper subfolders with full 1-click Undo support.

## [3.0.1] - 2026-11-15

### Added
- **Check for Updates Now (`ui/main_window.py`).** Settings -> Updates has a button that asks GitHub straight away, even when the daily check is off, and says "You're on the latest version" when there is nothing newer.

### Fixed
- **Rules carried over from 2.x that named your profile folder filed into a folder called `${USERPROFILE}` (new `dest.py`, `config.py`, `router.py`).** A 2.x rule pointing at `%USERPROFILE%\Documents\Banking` became a folder literally named `${USERPROFILE}\Documents\Banking` inside Downloads. Every rule, file-type folder and the catch-all folder is now turned into a folder inside Downloads when the settings load: a path in your profile keeps the part below it (`Documents\Banking`), a path inside Downloads keeps the part below that, any other full path keeps its last folder name, and anything containing `..` or characters Windows doesn't allow goes to `Other`. Rules already affected are corrected the next time Download Sorter starts; files already filed into a `${USERPROFILE}` folder stay where they are and can be put back with Undo.
- **A rule could move files out of Downloads (`fs_ops.py`, `dest.py`).** A destination such as `..\..\Desktop` or `C:\Somewhere` was followed as written. Besides the clean-up above, the move itself now refuses any target outside the watched folder.
- **The Quick Switcher hotkey in Settings did nothing (`ui/quick_switcher_pill.py`, `quick_switcher.py`, `ui/tray_app.py`, `cli.py`).** The pill always listened for Ctrl+G. It now uses the hotkeys from Settings, and its button shows the one you chose. A hotkey needs Ctrl or Alt plus a letter, number or F-key, because the pill watches the keyboard while you type a file name; anything else is refused with a message and the previous hotkey is kept.
- **Settings showed the watched folder as `${USERPROFILE}\Downloads` (`ui/main_window.py`, `config.py`).** It now shows the real path (`C:\Users\you\Downloads`) and still saves it in the portable form, so a setup file works on another PC. The carry-over summary shows the real path too.
- **The window could not be made smaller than about 1310 x 745, so it didn't fit a 1080p screen at 150% scaling or half of a snapped screen (`ui/main_window.py`, `ui/architecture_canvas.py`, `ui/motion.py`).** It now goes down to 900 x 560:
  - In a narrower window the header's Undo, Open Folder and Guide buttons, and below 1000 px Auto-Sort and Move Held, show only their icons (the words are in their tooltips), the status badge is hidden, and the folder path is shortened in the middle.
  - Settings scrolls, and its Notifications and Quick Switcher rows are split over two lines.
  - Folder Architecture's toolbar shortens its hint line, the Selected Folder panel scrolls, and Auto-Create Basic and Undo show only their icons below 950 px.
  - On Home, the "How Download Sorter Works" card folds away in a window under 740 px tall, so the file lists keep their room, and the preview gets more of the height than Recent Activity.
  - On Custom Rules, the tester's answer is on its own line under the boxes (shortened to fit, full text on hover), and its step-by-step list folds away in a window under 700 px tall.

### Changed
- **Repository housekeeping (no change to the app).** The 2.x program files that version 3 does not use (the separate tray, watcher, rules-window and learning scripts and their tests) are no longer in the source tree; the 2.x releases and tags keep them. The package metadata (`pyproject.toml`) now lists only the v3 program, and the beta-testing guide, security notes, contributing guide, flowchart and issue templates describe version 3 instead of 2.x. Nothing in the installed app, the installer or the release files changes.

## [3.0.0] - 2026-11-14

Download Sorter 3.0 replaces the collection of separate scripts with one program (`DownloadSorter.exe`, plus `DownloadSorter-cli.exe` for the command line). It is a rewrite, and smaller than 2.8: read **Upgrading from 2.x** before installing over a 2.x copy.

### Upgrading from 2.x
- **Your rules come with you.** The first time version 3 starts it copies your 2.x word rules, watched folder and .3mf hold setting into its own settings folder (`%LOCALAPPDATA%\download-sorter-v3`). Your 2.x settings are left untouched, and **Carry Over v2.8 / Setup...** does the import again whenever you want. Other 2.x settings start from the version 3 defaults.
- **Nothing is sorted until you have seen the welcome steps.** A new install, or the first start after upgrading, shows three short steps; automatic sorting begins when you finish or skip them.
- **Not in 3.0:** the Ctrl+Shift+Space search window, learning from your moves, project folders and the Projects page, Clean up a folder (remodel), unzipping downloads, the G-code and MakerWorld checks, the Desktop sweep, old-installer cleanup, pictures by month, and rules that read inside PDFs. If you rely on one of these, stay on 2.8 for now.

### Added
- **One window, four tabs (`src/download_sorter/ui/`).** Home, Folder Architecture, Custom Rules and Settings, with the tray icon, the watcher and the window all in a single process.
- **Folder Architecture canvas (`architecture.py`, `ui/architecture_canvas.py`).** Lay your folders out top to bottom in tiers, start from **Auto-Create Basic** or 40+ premade folders, add your own folders with rules, then **Apply & Shift Files**. **Undo** and **Return to Defaults** put things back.
- **Side ribbons you control (`ui/toasts.py`).** Set how long a ribbon stays (1-30 seconds), hover to pause it, close one with ✕ or all of them with **Close Ribbons**.
- **Recent File Insert (`quick_switcher.py`, `ui/quick_switcher_pill.py`).** When a program asks you to pick a file to open or upload, a small pill offers the file(s) Download Sorter just filed. Works for one file or a batch; `--dialog <hwnd>` does the same from a command line.
- **A .3mf stays put while your slicer is open (`fs_ops.py`, `service.py`).** Bambu Studio, OrcaSlicer, PrusaSlicer and Cura read a .3mf into memory and let go of the file, so it looked free to move while you were still working on it. When a held .3mf's days run out, it now stays in Downloads for as long as one of those programs is running, up to 14 days. **Move Held .3MF** still files it straight away.
- **It tells you when a new version is out (`update_check.py`, `updater.py`, `ui/main_window.py`).** Once a day the app asks GitHub which version is newest. When there is one, the window offers **Download and install**: the setup file is downloaded, checked against the release's published checksum, and installed; a file that does not match is deleted and never run. Switch it off in **Settings -> Updates**.
- **Sorty.** Click the mascot for tips. There is something to find if you click often enough.

### Fixed
- **Changing the watched folder could freeze the window for 3 seconds (`service.py`).** Stopping the old folder watcher waited on it while holding a lock the watcher itself needed. The watcher is now stopped after the lock is released.
- **The window no longer stalls on a large Downloads folder (`ui/workers.py`, `ui/main_window.py`, `ui/architecture_canvas.py`).** Reading the folder, each file's download source, the history database, and sorting, shifting and undoing files all happen in the background; the window only repaints when the result arrives.
- **Sorting or undoing from the command line, the tray, or a ribbon's Undo button no longer freezes the window (`ipc.py`, `ui/tray_app.py`).** `--sort-now`, `--file-held` and `--undo` sent to the running app are done in the background and answered when the files have actually been moved; the ribbon's **Undo** uses the same background undo as the window.
- **A file that is open in another program no longer fills the log (`service.py`, `fs_ops.py`).** A download that could not be moved was retried every couple of seconds, with a warning each time. It is now retried after a pause that doubles each time (up to a minute), tried again promptly when the file changes, and logged once. The check for "is this file open elsewhere" also never reported a lock on 64-bit Windows; it does now, so such files are skipped cleanly instead of failing mid-move.
- **Only one copy can run at a time, even when two start together (`ui/tray_app.py`, `installer_builder.py`).** After an update the installer started the app twice at the same moment, and both copies began watching Downloads. The installer now starts it once, and a second copy that starts while the first is still coming up hands over to it and exits. The installer's "open Download Sorter when finished" step also used an option the app no longer has (`--welcome`); it now uses `--home`.
- **A crash or power cut mid-sort can no longer lose track of a file (`db.py`, `fs_ops.py`).** Each move is written to the history as pending before the file is touched and marked complete once it has arrived. On the next start, anything left pending is checked against what is actually on disk and settled, so **Undo** keeps working.
- **Undo never overwrites a newer file (`fs_ops.py`).** If a new download has taken the original name, the file being put back is saved beside it as `name (Restored).ext`. A file that is open in another program is skipped and reported instead of stopping the whole undo, and a file you have since deleted is dropped from the history instead of causing an error.
- **Cloud-only files are not downloaded just to compare them (`fs_ops.py`).** A OneDrive or iCloud placeholder is no longer read in full to check whether it duplicates another file.
- **Updating over a running copy is more reliable (`installer_builder.py`).** The installer waits for Download Sorter to exit, retries a file that is briefly locked (antivirus, Explorer) up to ten times with growing pauses, and from the fifth try renames the locked file out of the way so the new one can take its place.

### Changed
- **Comparing two large files with the same name is faster (`fs_ops.py`).** Files over 256 KB are compared by size, then by their first and last 64 KB, and only read in full when those match.
- **The history database is tidied as it goes (`db.py`).** Its write-ahead log is folded in after each batch and emptied on exit, so it does not grow for weeks while the tray app runs.
- **The installed package no longer includes the 2.x setup scripts (`build_release.py`, `uninstall.ps1`).** `setup.bat` and the `install_*.ps1` scripts set up the old Python version and did nothing useful for 3.0. Uninstalling now offers to remove the version 3 settings folder as well as a leftover 2.x one.
- **GitHub tests and builds version 3 (`.github/workflows`).** CI and the release build run `tests_v3` and the release preflight, and a release can be published as a pre-release so installed copies are not updated until it is promoted.
- **`verify_release.py` checks the 3.0 release.** It verifies that every version number agrees, that all command-line options exist, that `tests_v3` passes, and that the built programs, ZIP, setup file and checksums are present and run.

## [2.8.0] - 2026-11-13

### Added
- **Skip the 7-day hold: file every held .3mf now (`hold_manager.py`, `tray.py`, `onboarding.py`, `main.py`).** A held .3mf stays where it was saved until its days are up, so a slicer that has it open keeps working. You could already click **File now** on a file's ribbon; now you can do them all at once. The tray menu has **File held 3MF files now** (shown while something is held), **Settings -> 3D printing** has a **File held .3mf files now** button with a count, and `DownloadSorter.exe --file-held` does the same from a command line (`--include-kept` also files the ones you marked Keep here). It is one batch, so **Undo last sort** puts them all back, a file that is open in another program keeps its hold and is reported, and files marked Keep here are left alone. To stop holding altogether, untick **Leave new .3mf files where they were saved** in the same section.

### Fixed
- **The held count no longer includes files that are gone (`hold_manager.py`).** The tray's "N 3MF files held in Downloads" line counted entries for files that had since been moved or deleted, so it could show a number higher than what was actually sitting there.

## [2.7.2] - 2026-11-13

### Fixed
- **`--status` no longer crashes (`status.py`).** The status command failed on its first line with `UnboundLocalError: cannot access local variable 'load_config'`, because a second import of the same name further down made it local to the whole function. It now uses the one loaded at the top.

## [2.7.1] - 2026-11-13

### Fixed
- **Ribbons are no longer placed off the edge of the screen on a scaled display (`ribbon_popup.py`).** On a display set to 125% or more, the "file moved" and "Put files like this in ...?" ribbons opened past the right edge of the screen, so nothing showed. The ribbon took the monitor's size in physical pixels but placed itself in the pixels Qt uses, which differ whenever scaling is not 100%. It now takes the work area from the matching Qt screen.

### Changed
- **README screenshots show 2.7.** `home.png` is the current Home page, and two new ones show the Rules page (with "Try a filename") and the new search window. `tools/render_screens.py` has a `shelf` group for the search window.

## [2.7.0] - 2026-11-13

### Added
- **Home says why a file was filed (`safe_ops.py`, `sorter.py`, `config_manager.py`, `onboarding.py`).** Each move in the history now keeps the kind of match, the rule's name and the website the file came from (its name only, never an address). Hover a file in **Recently sorted** to read it: "Filed by your rule "Banking"", "Filed by its file type", "Downloaded from yourbank.com". Files filed by an older version show the path only.
- **The learner hears "no" (`learning.py`, `sort_undo.py`, `refile.py`, `watch.py`, `ribbon_popup.py`).** Putting back a file that a learned rule filed, moving it somewhere else from Home, or dragging it out in Explorer now counts against that rule. After two such files a ribbon asks "Stop filing into Banking?"; **Yes** switches the rule off (it stays on the Rules page), **No** means it won't ask again. Rules you wrote and file types are never judged this way.
- **It can file what no rule could place (`learning.py`, `sorter.py`, `ribbon_popup.py`, `onboarding.py`, `rules_manager.py`).** Off unless you ask for it. When a download would go to the catch-all folder and you have moved 3 similar files into one folder, the ribbon that asks "Put files like this in Bookkeeping?" now also has **Always**: it adds the rule and, from then on, files such downloads without asking. The same switch is in **Settings → Learning** ("File what no rule can place where I put files like it"). A file that a name rule, a folder of yours or a file type places is never moved on a guess, files already in the catch-all folder stay there, and the folder has to exist already. Put one back (Undo on the ribbon, or Home) and it stops guessing that word or website for that folder. Home and the ribbon's tooltip say when a file was filed this way, **Try a filename** shows a **Learned** badge, and "See what it has learned" counts the files it filed and how many you took back. `python learning.py --replay` shows how it would have done on your past moves.
- **A searchable list of every file in your sorted folder (new `file_index.py`, `watch.py`, `main.py`).** Download Sorter keeps a list of the files under the folder it sorts, including ones you put there yourself: name, folder, when and why each was filed, the website it came from, and for print files the model, designer, printer and filament. `DownloadSorter-cli.exe --find statement from:yourbank type:pdf` prints the matches; words match from their start and ignore plurals, `in:` keeps one folder, `from:` one website, `type:` one kind of file. The list is built in the background when sorting starts and once a day, and each filed, moved or deleted file updates it. A Git project is one entry and its files are not listed; `node_modules`, `.venv` and similar folders are skipped. It stays on this PC, in the app's data folder (`file_index.db`). Turn it off with `"search": {"enabled": false}` in the settings file.
- **Ctrl+Shift+Space finds any file in your sorted folder (`shelf.py`, `tray.py`, `tray_qt.py`).** The recent-files shelf now has a search box. Left empty it lists the newest filed files, as before. Type a few words and it searches every file under the sorted folder by name, folder, website and print details: "statement oct", "pla", `from:yourbank`, `type:stl`, `in:banking`. Each row shows the folder, how long ago, the website and the print details. **Enter** shows the file in its folder, **Ctrl+Enter** opens it, **Ctrl+C** copies it, the arrow keys move through the list while you type, and a row can still be dragged out. The **...** button (or a right-click) has **Move to...**, **Put back where it was** and **Send to project** for the projects in your hub; every one of those moves can be undone. The tray now shows the shelf itself and keeps one window, so the shortcut no longer starts a new program each time.
- **Recent website suggestions in the Rule Editor (`rules_manager.py`).** The "Only when downloaded from these websites" box in `RuleEditDialog` now autocompletes comma-separated domains from your learned downloads, existing rules, and recent files in Downloads (completing Issue #25).

### Fixed
- **The shelf window is dark like the rest of the app (`shelf.py`).** It had the system's light grey behind light text, and no highlight on the chosen row.
- **Clean up a folder keeps non-installer files in renamed category folders (`clean_old.py`, `status.py`, `space_report.py`).** When setup points **Installers** or **Archives** at one of your own folders (such as `Programs`), daily clean-up, the reclaimable-space estimate, and Leftovers cleanup now filter by that rule's file extensions instead of recycling or counting unrelated personal files (`README.txt`, `manual.pdf`) by age alone.
- **Consolidate protects personal subfolders inside categories (`consolidate.py`).** User-created and MakerWorld subfolders inside `Documents`, `3D Models`, `Images`, `Audio`, `Videos`, and `Archives` (such as `Documents\Tax Records` or `3D Models\Flanged Mace`) are pruned at category level and left untouched; only recognized legacy or type subfolders are flattened, and `--dry-run` no longer creates destination folders on disk.
- **Missing module registration in packaging and release checks (`DownloadSorter.spec`, `pyproject.toml`, `verify_release.py`).** Added `consolidate` (`main.py --consolidate`) and `toggle_pause` to the PyInstaller bundle list, synced `pyproject.toml`, and included all runtime modules in `verify_release.py`.
- **Settings no longer scrolls sideways in a 900 px window.** Long checkbox labels (such as the folder options under **Folders** and **Experimental**) now wrap cleanly onto two lines instead of pushing cards past the right edge of the viewport. Fixed issue #54 and removed the expected-failure marks from the Settings layout fit tests.
- **"Look for projects" works in the installed app.** The Projects page was shipped without the part that does the looking (`project_scan.py`), so the button ended in "No module named 'project_scan'". The module is now in the app and in the release checks. A folder Windows won't let the app read (for example `Documents\My Music`) is left out instead of stopping the whole search with "Access is denied".
- **Home's buttons are no longer cut off in a narrow window.** At the smallest window size (900 px wide) the four buttons under the status line did not fit side by side, and "Open Downloads" lost its last letters. The row now wraps: "Open Downloads" moves to a second row when there is no room, and sits at the right edge as before when there is.
- **Clean up a folder fits the smallest window.** The "Leftovers" line wraps instead of pushing the page wider than the window. After a scan, a 560 px tall window could not hold everything and the folder row was squeezed until "Browse..." and "Preview changes" lost the bottom of their words; the part above Apply now scrolls in a short window instead. In a taller window nothing changes.

### Changed
- **Bigger page titles and a clearer sidebar selection.** Page titles (`Home`, `Rules`, `Clean up a folder`, `Projects`, `Settings`) now use a 24 px heading while body and section text keep their existing sizes. The active sidebar item shows a mint bar alongside semibold text and a tinted frame, distinct from hover and keyboard focus (and visible without relying on color alone). Closes issue #53.
- **The layout checks use the window's real smallest size.** They allowed pages up to 1000 px wide while the window can be 900 px, so a page that needed 957 px passed. The limit is now 900x560, the same as the window, and new checks look at where each button actually ends up (inside its box, wide enough for its words) at 900x560, 900x640 and 1040x760, in the on, paused, off and files-waiting states.
- `tools/render_screens.py --min` renders at 900x560 (it used 900x640); `--compact` renders the old 900x640.

---

## [2.6.1] - 2026-11-12

### Added
* **Manual "Check for updates" button (`onboarding.py`, `main.py`, `update_check.py`)**: **Settings → About** now has a **Check for updates** button that queries GitHub immediately (even when the daily automatic check is turned off), shows whether a newer version is available or you're up to date, and links directly to **View update on Home** (or the release page when running from source). Clicking Refresh on **Home** also checks for updates, and `--check-update` works from the command line.

### Fixed
* **Background tray re-checks daily while running (`tray.py`)**: the tray icon now re-checks for updates every 24 hours while running instead of only once at startup.

## [2.6.0] - 2026-11-12

### Added
* **The welcome step asks what you mostly download (`onboarding.py`, `rules_manager.py`)**: pick **Everyday files**, **3D printing** or **Software** at the top of the first step. Everyday files starts with the 3D Models and CAD folders unticked and hides the Projects page and the 3D printing tools; 3D printing ticks those folders and shows both; Software shows the Projects page. When your Downloads folder already holds 3D files, 3D printing is picked for you. You can still tick any folder by hand, and **Settings** has a **Show the Projects page** box. Nothing changes for an install that is already set up.
* **Settings shows the options that needed the settings file (`onboarding.py`)**: a new **Folder to sort** section shows the folder being sorted and lets you change it or go back to your Downloads folder. A whole drive, your profile folder and the Windows and program folders are refused, and a folder that is missing (an unplugged drive) is said on the page. **Folders** has boxes for your own type-folder names (`.pdf = PDFs`) and for folders of yours that should not collect files. A new **3D printing** section has MakerWorld folders by designer and the `.3mf` hold with its number of days. The Desktop sweep's number of days can be changed.
* **Projects hub (new `projects_hub.py`, `projects_page.py`, RFC #1)**: a new **Projects** page lists project folders with their kind, state and Git status. **Add to hub** puts a junction to the project in `%USERPROFILE%\Projects Hub\<kind>`, so all projects can be opened from one place while staying where they are; **Remove from hub** removes only the junction. **Zip a copy** archives without touching the project.
* **Route a download to a project (`ribbon_popup.py`, RFC #1)**: when you have projects in the hub, the moved-file ribbon has a **Route** button with a menu of them; picking one moves the file into that project (Undo still works).

### Changed
* **Signing and a home-built bootloader in the release workflow (`.github/workflows/release.yml`, `packaging/`, issue #9)**: the release build compiles PyInstaller's bootloader on the runner instead of using the prebuilt one antivirus engines flag, and signs the setup file with SignPath (`SignPath/github-action-submit-signing-request@v3`) once the SignPath variables and secret are set; until then signing is skipped. Winget manifest templates and the signing setup steps are in `packaging/`. The SignPath account, the winget submission and the Norton and Microsoft reports still need the maintainer.
* **One settings loader (`sorter.py`, `config_manager.py`, RFC #2)**: the watcher and the window now load settings the same way. Before, a damaged settings file made the window fall back to its backup but the watcher to the built-in defaults, so it sorted without your rules.
* **Tray icon on Qt (new `tray_qt.py`, `tray.py`, RFC #2)**: the tray uses PySide6's system tray, like the rest of the app, and pystray is no longer a dependency. The menu and its behaviour are unchanged. The ribbon and the quick switcher still use Tk.

### Fixed
* **Running the tests no longer shows a "Location is not available" box (`tests/conftest.py`)**: one test opens a real Windows file dialog, and the temporary home folder each test gets had no Desktop folder for it to start in.
* **The Settings page no longer scrolls sideways (`onboarding.py`)**: one long checkbox made the page wider than the window, so the right edge of every section was cut off. Its note is now on a line of its own.
* **Dragging a file out now works in every app (`batch_handoff.py`, issue #28)**: the drag object from the quick switcher and the recent-files shelf carried only the shell's own item list, which Explorer reads but browsers, chat apps and slicers do not. It now also carries the standard file list (CF_HDROP).

## [2.5.0] - 2026-11-12

### Added
* **Make a rule from "Try a filename" (`rules_manager.py`)**: when no name rule decided the file name you typed (it went by file type, or to the catch-all folder), **Make a rule for this...** opens the rule editor filled in from the name. The new rule goes first and the name is checked again at once, so you see it win.
* **One folder per MakerWorld model (`sorter.py`, new `kits.py`, `unzip_3d.py`, issue #27)**: a model goes to `3D Models\MakerWorld\<Model>` (add the designer level with `"makerworld_by_designer": true`). Its STL, STEP and guide PDF files go to the same folder, found from the model page they were downloaded from, and a part pack (`.zip`) from MakerWorld keeps its sub-folders, build guide and photos together there. A folder that already holds the model is reused, ignoring case and punctuation. Name rules still win. Turn the kit grouping off with `"makerworld_kits": false`.
* **Bambu Studio's Recently Opened list follows sorted files (opt-in, new `bambu_recent.py`, `watch.py`, issue #27)**: off by default; asks once when a `.3mf` moves while Bambu Studio is installed (or turn on under Settings → Experimental). When enabled, once Bambu Studio is closed its `recent_projects` entries in `BambuStudio.conf` are updated and the `# MD5 checksum` footer recomputed. A backup (`BambuStudio.conf.download-sorter.bak`) is kept and can be restored from Settings.
* **Open in Bambu (`ribbon_popup.py`, issue #27)**: a sorted `.3mf` toast has an **Open in Bambu ›** button when Bambu Studio is installed, so Bambu records the sorted path from the start.
* **Re-saved print files join their model's folder (`kits.py`, `watch.py`, issue #27)**: the MakerWorld model id (DesignModelId) is remembered when a kit is filed, so a re-saved multi-plate `.3mf` with a different title goes into the same folder.
* **Unplaced files are offered to the folder the learner recognises (`learning.py`, `watch.py`, issue #25)**: a file that lands in the default folder gets the same "Put files like this in X?" question, when the learner has seen enough of X. A question already waiting is not repeated.
* **PDFs sorted by their first page (new `pdf_content.py`, issue #25)**: bank statements, tax forms, shipping labels and manuals go to `Documents\Statements`, `Tax`, `Shipping` and `Manuals`. This needs PDF text reading on (Settings) and pypdf.
* **Camera photos and screenshots kept apart (new `photo_kind.py`, issue #25)**: screenshots (by name) go to `Images\Screenshots`; photos with a camera make or model in their EXIF data go to `Images\Camera` (by month when that option is on). Enabled for new installs; existing installs keep their current layout until turned on under Settings → Experimental.
* **A zip with one file is filed where that file belongs (`unzip_archive.py`, issue #25)**: a zip holding one PDF, font or 3D model is not left in its own folder under Extracted. Code and CAD projects with several files stay together.
* **Print details are searchable (new `prints.py`, `gcode_peek.py`, `onboarding.py`, issue #25)**: the printer, filament, plate count and print time of each filed `.3mf` or G-code file are recorded, and the Home filter finds files by them (for example "PLA" or "X1C").
* **Recent files shelf and shortcut (new `shelf.py`, `hotkey.py`, `batch_handoff.py`, issue #25)**: Ctrl+Shift+Space (configurable under Settings → Experimental) opens the last 15 filed files. With a slicer or CAD app open, 3D files are listed first. Double-click shows a file in Explorer; Ctrl+C / **Copy file** puts the file on the clipboard; dragging a row drops the real file.
* **Rule checks when you add a rule (`rules_manager.py`, new `rule_check.py`, issue #25)**: a rule that an earlier rule always catches is flagged as one that will never run. If matching files are already in Downloads, the message offers **Move them**.
* **Leftover space is reported and can be cleaned (`space_report.py`, `status.py`, `rules_manager.py`, issue #25)**: duplicates and quarantine sizes are shown in status output and on the Clean up page, with **Review leftovers...** to send each kind to the Recycle Bin after confirmation.
* **Desktop sweep, opt-in (new `desktop_sweep.py`, issue #25)**: off by default (Settings → Experimental, or `"desktop_sweep": {"enabled": true, "days": 7}`); loose files on the Desktop untouched for that long move into Downloads, where the normal sorter files them. Shortcuts are left alone.
* **Shareable setup export (new `setup_export.py`, issue #25)**: `python setup_export.py setup.json` writes your rules and folder setup with your profile paths replaced by `%USERPROFILE%`, and without the learning history, active project or move history.
* **The .exe has publisher and version details (new `version_resource.py`, `build_release.py`, `DownloadSorter.spec`, issue #9)**: the build writes a Windows version resource from `about.py` (company, description, version, copyright), so the file's Properties show who published it.
* **Shared zip rules (new `zip_members.py`, RFC #2)**: the macOS-metadata and path-safety checks used by `unzip_3d.py` now come from one module. Each tool keeps its own list of extra junk names. The wider simplification in the RFC (one GUI toolkit, one config authority) is not included.
* **Winget and signing notes (`packaging/`, issue #9)**: winget manifest templates and the steps for publishing them, and notes on signing with SignPath. Signing and the winget submission itself are not done (they need outside accounts).

### Changed
* **The Rules page shows where a rule came from, and which one won (`rules_manager.py`, `ui_widgets.py`)**: a rule made for you carries a small tag in the Name column, **Learned** (you said Yes to a suggestion) or **From setup** (made from a folder you already had), until you edit it and it becomes your own. Folders get a folder icon, **Unsaved changes** appears next to Save, and each tab's rule count is in its tooltip. Typing a name in **Try a filename** now selects the rule that decided it, on its own tab, and flashes its row. In a short window the examples and details under it fold away so the table keeps its room (the button at the right of its title brings them back, and your choice is remembered). The "Moves to" and "File types" columns are narrower so the words column keeps its width in a narrow window, and the hint above the table shortens instead of spilling out. **Alt+Up** and **Alt+Down** move the selected rule in the list.
* **The rule editor says what is missing (`rules_manager.py`)**: instead of a pop-up, the problem shows in red under the box, and **Save rule** stays off until the rule could match something, with "Still needed: a name, a folder, a word to look for" beside it. A name rule needs a word to look for (or a website): a rule with only a file type, or nothing, never matches anything, and the message says so. File types are tidied as you type (".PDF, docx; *.xlsx" becomes ".pdf, .docx, .xlsx", shown under the box), the folder box suggests the folders already inside Downloads, and **Enter** saves a finished rule.
* **Clean up a folder is easier to read (`rules_manager.py`, `ui_widgets.py`)**: after a scan, four tiles show the numbers (looked at, would move, left alone, duplicates), and **Would move** follows the choice you make on each folder. Each folder's choice is a row of buttons (**Organize by file type**, **Keep intact**, **Skip for now**) instead of three plain radio buttons, and the reason a folder is left alone is a clearly visible tag that shortens, with the full text on hover, instead of pushing the card wider. "Found 1 duplicate files (0.0 MB)" now reads "Found 1 duplicate file (under 1 KB); it'd be set aside for 30 days, not deleted". Before a scan, the page says what it will show. A finished clean-up, an undo and a folder that doesn't exist are reported in the message bar instead of pop-ups, and the clean-up message has a **Where did my files go?** button.
* **The rule library marks what you already have (`rules_manager.py`)**: a set whose rules are all in your list shows **Already added** and can't be ticked (**Select all** skips it), and a set with some of them shows "1 of 4 already added". **Add selected** stays off until you tick a set, instead of a pop-up saying "Nothing selected". Set names such as "Office, Invoices & Finance" now show the "&" (it was being swallowed as a keyboard-shortcut mark).
* **No more 7-day hold on loose `.3mf` files (`sorter.py`, `config.json`, issue #27)**: a `.3mf` is filed straight away. The hold is still there for anyone who turns it on in `hold.enabled`.
* **3D files stay in the quick switcher for 12 hours (`batch_handoff.py`, issue #27)**: `.3mf`, `.stl`, `.step` and `.f3d` files stay offered for Ctrl+O much longer than the 5-minute window used for other downloads.

### Fixed
* **Review fixes from issue #24 (`config.json`, `sorter.py`, `hold_manager.py`, `remodel.py`, `consolidate.py`, `clean_old.py`, `tray.py`, `status.py`, `onboarding.py`)**:
  * New installs get the current folder layout: `config.json` had CAD files under 3D Models and no MakerWorld subfolder; it now matches the built-in defaults, including the type and own-folder settings.
  * One slicer list for the 7-day hold (now also Cura and ChiTuBox), shared by `sorter.py` and `hold_manager.py`.
  * `_Remodel Quarantine` batches older than the quarantine window (30 days by default) go to the Recycle Bin once a day, not kept forever.
  * A duplicate that is moved to `_Duplicates` is reported as a duplicate, not as the file it matched.
  * The Clean Up HTML receipt escapes file names, so `<`, `>` and `&` no longer break its table.
  * Consolidate clears the hold and Undo marks through the real helpers; its messages say "two-level folders" instead of "flat categories".
  * Old-file clean-up looks inside the type folders and measures age from when the file arrived (the earlier of creation and modified time), not from the last edit.
  * Rules and Clean Up open as their own windows from the tray; they are no longer stopped after five minutes.
  * Home reuses one history reader, so it reads only new lines on each refresh.
  * The Rule Editor has an "Only when downloaded from these websites" box for name rules, so a website can be added or changed by hand.
  * Status checks find the packaged `DownloadSorter.exe`, use the configured clean-up folders and age, and show "auto-unpack 3D zips" as off by default, as the app does.
* **MakerWorld and Bambu `.3mf` files are recognised again (`gcode_peek.py`, issue #27)**: the MakerWorld ids, title, designer and Bambu Studio version are read from the model's own header, so current downloads are no longer filed under `3D Models\3MF`. The folder is named after the model's Title (the file name if it has none), and the print profile ("0.2mm layer, 3 walls") is kept as metadata only. Large multi-plate `.3mf` files are no longer rejected by the size limit: the model's mesh is not decompressed. The zip-bomb guard still applies to every other file in the archive.
* **The learner reads its files less (`learning.py`, issue #24)**: the learned model is re-read only when `learning.json` changes, and the log is counted in memory rather than re-read on each sorted file.

## [2.4.0] - 2026-11-11

### Changed
* **Two-level folders are the default (`sorter.py`, Settings)**: inside a main folder, each kind of file now gets its own folder, so `Documents\PDF` holds only PDFs, `3D Models\STL` only STL files, `CAD\Fusion 360` only Fusion files. Rare kinds of file, and the small Installers and Archives folders, stay one level deep. A rule that already points deeper (`Documents\Invoices & Receipts`) or outside Downloads is used exactly as written. This applies to everyone, including settings saved by older versions. Turn it off in **Settings → Folders**, or rename a folder with `"type_folders": {"names": {".pdf": "PDFs"}}` in the settings file.
* **Files already in a main folder are moved into the folder for their kind**: the first time Download Sorter starts with this version, files sitting directly in `Documents`, `3D Models` and the other main folders move into their type folder. This is one batch of its own with its own ribbon, so a single **Undo last sort** puts them all back, and files Undo put back stay put. Files you moved somewhere yourself, files from before a "don't organize existing files" choice, and files in your own folders are not touched. Nothing moves while sorting is paused.
* **A real window, and messages that stay out of the way (`rules_manager.py`, `ui_widgets.py`)**: the main window can now be minimized and maximized, has the Download Sorter icon, remembers its size and position (in `window.ini` next to your settings), and can be a little shorter (560 px instead of 640, for a 1080p laptop at 150% scaling). **Esc** used to close it without asking about rule changes you hadn't saved; it now asks, like the close button. Rules added, imported, exported or reset are reported in a message bar at the top instead of a pop-up box. The sidebar shows the sorter's state the same way Home does, and clicking it opens Home; a dot appears on **Rules** while changes are unsaved, and on **Home** when an update is waiting and you're on another page. The version and credit no longer wrap onto three lines.
* **Home reads at a glance (`onboarding.py`)**: the status card is tinted green while sorting is on and amber while paused, and says when the last file was filed. Each file in **Recently sorted** has an icon for its kind, a time in plain words ("3 min ago", with the full date on hover), and a filter box above the list; **Show more** loads up to 100 files. Files that were moved or deleted since are dimmed. A file that was moved on again (by you, from the new menu) is listed once, where it is now. When nothing has been filed yet, the page says what to do next.
* **Easier to read (`ui_style.py`)**: the small grey caption text now uses the same muted color as other secondary text (6:1 contrast against the background instead of 3.7:1; 4.5:1 is the usual minimum), table rows light up under the pointer, and progress bars share one look instead of separate copies.
* **Nothing is taken out of a folder you made (`sorter.py`, `watch.py`)**: a file saved, unzipped or dragged into your own folder inside Downloads stays there. Before, a recognised file one level deep in such a folder was treated as misplaced and moved to its category, which pulled apart archives extracted into Downloads. Only Download Sorter's own folders (main folders, type folders and rule folders) are tidied.
* **Consolidate (`--consolidate`) files into the two-level layout** instead of flattening everything into the main folders.
* **The tray menu puts what you do most together (`tray.py`)**: **Sort Downloads now**, **Undo last sort** and **Pause sorting** now sit right under **Open Download Sorter**, and the "filed today" counts moved down next to the new **Recently sorted**. Nothing was removed or renamed.
* **Text instead of emoji on the 3MF hold ribbon (`ribbon_popup.py`)**: the buttons read **File now** and **Keep here**, not "📁 File now" and "📌 Keep here". The icons in the rest of the app are line drawings, not emoji.
* **Ribbons no longer cut the folder name at 24 characters (`ribbon_popup.py`)**: "Saved to <folder>" is measured with the ribbon's own font and fitted to the room the buttons leave. A long path first collapses its leading folders ("Saved to ...\Invoices & Receipts"), then drops "Saved to", and only a single folder name that still doesn't fit is shortened, in the middle. The row fits the 360 px ribbon at every display scale from 100% to 200%.

### Added
* **Fix one misfiled file from Home (new `refile.py`, `onboarding.py`)**: the **...** menu at the end of each row in **Recently sorted** has **Put back where it was** (undoes only that file), **Move to...** (files it in a folder you pick, never replacing a file, and tells the learner you chose that folder, so it may ask "Put files like this in ...?") and **Always file like this...** (opens the rule editor filled in from the file; the new rule goes first, so it wins, with an **Open Rules** button on the message). Every move is journaled, so **Undo** still works. Right-click a row for the same menu; double-click it to show the file in Explorer.
* **Building blocks for a tidier look (`ui_style.py`, `ui_icons.py`, new `ui_categories.py` and `ui_widgets.py`)**: badges, tinted cards, on/off switches, button-style choices, a step indicator, file-type tiles and a "..." row menu, plus 13 file-type icons and a few new line icons. The pages will use them in the coming releases, so nothing on screen changes yet apart from the item under Changed. The ribbon and the window now share one file-type table (`ui_categories.py`), so a PDF is the same color in both.
* **A screenshot tool for design review (new `tools/render_screens.py`)**: draws every page offscreen with made-up data (it never reads your settings, history or folders) and saves PNG files, at any window size and display scale. Two new tests (`tests/test_layout_fit.py`) fail when a page needs more room than its window promises (900×640 for the main window, 720×640 for the welcome steps).
* **A folder you make collects files named after it (`sorter.py`)**: create `3D Models\Flanged Mace` or `Downloads\Flanged Mace`, and new downloads with that name in theirs (`flanged_mace_v1.f3d`, `Flanged Mace v2.step`, `FlangedMace-v3.stl`) go there, every version kept. A folder inside 3D Models or CAD takes 3D and CAD files of any kind, so one project keeps its `.f3d`, `.step` and `.stl` together; a folder at the top of Downloads takes any kind of file. The longest matching folder name wins, name rules still win over folders, and folders with general names (`New folder`, `Old`, `Parts`, `2024`) never collect files. Turn it off in **Settings → Folders**, or stop one folder with `"my_folders": {"skip": ["3D Models\\Old"]}` or a `.download-sorter-keep` file inside it. The Rules page's **Try a filename** shows a "Your folder" badge when a folder matches.
* **Undo one file from its ribbon (`ribbon_popup.py`, `sort_undo.py`)**: the ribbon that appears when a file is filed has an **Undo** button next to **Jump to File**. It puts back that file only, not the whole batch, and a short "Put back" ribbon says where it went. Not offered on a 3MF file that is still held, since that file has not been filed. From the command line it is `--undo --file <path>` (also `-f`; add `--dry-run` to preview). It matches the file by where it is now, wins over `--all`, `--batch` and `--count`, and `--list` still only lists.
* **A "Recently sorted" menu in the tray (`tray.py`)**: the last 5 files filed, newest first with the folder each went to. Click one and File Explorer opens with that file selected. A file that has been moved or deleted since stays in the list but greyed out. The history is read once per change, not each time Windows asks for the menu.
* **Phone photos, WEBP and TIFF pictures and voice memos get their own folders (`sorter.py`)**: `.heic` and `.heif` go to `Images\HEIC`, `.webp` to `Images\WEBP`, `.tif` and `.tiff` to `Images\TIFF`, and `.m4a` to `Audio\M4A`, the way JPGs and MP3s already did. Before, these kinds had no folder of their own, so they piled up loose in `Images` and `Audio`. The first time Download Sorter starts with this version, the ones already sitting there move into the new folders, as one batch that **Undo last sort** puts back. A name of your own in `"type_folders": {"names": {...}}` still wins, and an empty name keeps a kind in the main folder. Kinds without a folder, such as `.bmp`, `.ico`, `.ogg` and `.avi`, still stay one level deep.

### Fixed
* **Show in folder on Home selects the file when its path has a space (`onboarding.py`)**: the path was joined onto `/select,` as one argument, so Windows quoted the whole thing and Explorer ignored the request. The button, the **...** menu entry and double-clicking a row now give Explorer the path as its own argument.
* **Starting while paused no longer sorts (`watch.py`)**: the sort that runs when Download Sorter starts used to ignore Pause. It now waits until sorting is resumed.
* **An active project no longer empties the sorted folders (`sorter.py`)**: with a project turned on, the tidy-up of the main folders treated every 3D and CAD file already filed as belonging to the project and moved it there. A project now only collects new downloads.
* **A file that shows up at the last moment is never replaced (`safe_ops.py`)**: Download Sorter picks a free name for a file and then moves it. If another program (or a second Download Sorter) took that name in between, the move could silently replace that file. Now the move stops without touching anything, and the file is tried again a moment later under a fresh name. Undo is the same: it skips that file (the log says why), and undoing again files it under a fresh name. Moves to another drive are copied under a temporary name first and renamed into place, so a half-copied file never has the real name.
* **A command that failed is no longer started again (`main.py`)**: when `--undo`, `--sort`, `--consolidate` or one of the window commands hit an internal error of one kind (`TypeError`), it was run a second time, and for `--undo` that could undo a second batch. It now stops, and the error is reported like any other unexpected error.
* **"Keep here" was cut off on the 3MF hold ribbon (`ribbon_popup.py`)**: the long text "Holding in Downloads (Bambu Studio)" was laid out first and took the room, leaving **Keep here** a 1 px sliver at 100% display scale (13 of 121 px at 150%). The buttons are laid out first now, and the text shortens to "Holding in Downloads" to make room.
* **Running the tests no longer disturbs the Download Sorter you have installed (`tests/conftest.py`, `installer_builder.py`)**: on a PC with the app installed, a full test run closed the tray that was open (it sent the same quit signal the installer sends), switched your "start with Windows" and right-click entries to ones that pointed at the source folder, replaced your "Installed apps" entry with a test copy (so Uninstall in Windows Settings stopped working), and left empty `XYZFiles` and `Finance\Taxes` folders in your Downloads folder (a test that built its own settings, with no Downloads folder in them, ran the welcome wizard against the real one). Each test now has a quit signal of its own and a home folder of its own, so the Downloads it falls back to is an empty temporary one, and the registry entries the app writes go to a scratch area that is deleted after the test. The setup file has a new `/NO-REGISTRY` option, used by the automated install tests, that copies the files without adding the "Installed apps" entry.
* **3MF files that had sat in Downloads for days were held for another week, and with more than 15 the oldest were never filed (`hold_manager.py`, `sorter.py`)**: the hold counted its days from when Download Sorter first looked at a file, not from when the file arrived. A sweep over old 3MFs (at start-up, or after a reinstall that forgot the holds) gave every one a fresh week. A file that is already older than the hold is now filed at once, and a download is held for what is left of its week (the newer of its modified and created times counts, since some browsers stamp a download with the server's old date). The limit of 15 held files was worse: over it, the oldest hold was *deleted*, which left the file in place with nothing to ever release it, and the sweeps then held and dropped them in turn. Now the file held longest is filed (once no slicer or program has it open) and the newest downloads keep their hold. Set `"hold": {"max_held": N}` to change the limit. A dry run (`--sort --dry-run`) now says what the real run would do.
* **A watch folder that is missing is no longer swapped for your Downloads folder (`sorter.py`, `watch.py`, `tray.py`)**: when the folder you chose in Settings was not there (a drive that is not plugged in, or a folder that was deleted or misspelled), Download Sorter quietly sorted your Windows Downloads folder instead, with your rules. Only the default Downloads folder follows Windows to a new place when you have moved it (Properties, Location); a folder you named never turns into another one. Download Sorter waits for it instead: nothing is sorted, released from a 3MF hold or cleaned up while it is away, the log and the tray tooltip say "Watch folder not found", and watching and the start-up sort pick up again within about 30 seconds of the folder coming back, with no restart. The records of 3MFs held on that drive are kept. The folder is not made for you, since it may be a drive that is plugged in under another letter. **Sort Downloads now** and **Open Downloads** in the tray say that the folder was not found, and a sort that cannot reach the folder moves nothing and logs why, instead of failing with an error.
* **A watch folder that goes away while Download Sorter is running is noticed (`watch.py`, `hold_manager.py`)**: unplug the drive your watch folder is on, or delete the folder, and Download Sorter went on saying it was running while it watched nothing, because Windows stops the watch of a folder that disappears without a word. It also forgot the 3MFs held on that drive (a hold whose file is gone is dropped, and a file on a drive that is not there looks gone), **Keep here** choices included, and when the drive came back it did not watch the folder again until you restarted. Now it notices within about 30 seconds and waits the way it does for a folder that is missing at start: the tray says "Watch folder not found" and nothing is sorted. The holds of files inside the folder are kept while it is away. When the folder is back it is watched again and what arrived in the meantime is sorted. A drive that was gone for only a moment is picked up the same way.
* **A held 3MF is announced once, not every time it is looked at (`hold_manager.py`, `sorter.py`)**: the "Holding in Downloads" notice appeared whenever Download Sorter found a 3MF that stays put, and that included one it was already holding. So every new 3MF download showed it twice (a second event for the same file, a few seconds later), and every start of Download Sorter showed it again for every file that was still held: five at once with five holds, up to fifteen. The notice now appears when a hold is new, or when the file has changed since (a new download of it), and not when the file is found again unchanged or when you chose **Keep here**.
* **The tray menu follows your latest settings (`tray.py`, `sorter.py`)**: the tray read your settings once, when it started with Windows, and kept them for as long as it ran. After you changed the folder in Settings, **Sort Downloads now** and **Open Downloads** in the tray still used the old folder until you restarted Download Sorter. They now read the settings as they are saved when you click. (Sorting in the background already followed your changes.) If the settings file cannot be read at that moment, for instance while it is being written, the tray keeps the settings it has instead of falling back to the defaults.

### Removed
* The leftover folder name `xyzfiles` from the list of folders "Clean up a folder" treats as already organized (`remodel.py`). `3D Models` was already on that list.

---

## [2.3.0] - 2026-11-09

### Added
* **Setup starts from your own folders (new `folder_scan.py`, `onboarding.py`)**: the first welcome step no longer asks which of 4 profiles you are. It looks at the folders already in your Downloads folder and offers to keep filing into them, with a tick box each:
  * a folder like `Music` or `Pictures` takes over that kind of file;
  * a folder like `Invoices`, `Banking` or `IME 4030` gets a rule from its name and from words that keep coming back in its files' names ("chase", "lecture"). Words that also show up elsewhere in Downloads are left out, so "statement" won't pull in your personal statement;
  * folders with no clear pattern are shown but can't be ticked; Download Sorter learns them from your moves.
  When Downloads has no folders of its own, the step shows a box for each kind of file plus optional extra folders (receipts, school, career, taxes and bank). The files already in your folders also teach the learner where things go. In Settings, the profile list is now "Your folders and starter rules", with **Just my folders** first and **Look at my folders again**.
* **Learning is on, and asks before adding a rule (`learning.py`, `ribbon_popup.py`, `watch.py`)**: after you've moved 3 similar files into one folder (same word in the name, or same website), a ribbon asks "Put files like this in Banking?". **Yes** adds a rule, named "Banking (learned)", that you can see and change on the Rules page; **No** means it won't ask about that again. It only asks when the word or website points to that folder at least 4 times out of 5, and never when a rule already files it there. It still never moves a file on its own guess. Learning is switched on for everyone once; you can turn it off in Settings.
* **Rules can check the website a file came from (`sorter.py`)**: a learned rule like "“statement” files from yourbank.com" only matches statements downloaded from that site. The Rules page shows it as "(downloaded from yourbank.com)".

* **Ideas from similar tools, rebuilt for Download Sorter** (Hazel, File Juggler, organize, AI File Sorter, a browser add-on that sorts by website):
  * **Course websites (`folder_scan.py`)**: an optional starter folder files anything downloaded from Canvas, Blackboard, Brightspace, Gradescope, Piazza, Schoology or MoodleCloud into `Documents\School` (Google Classroom files only when downloaded from the Classroom page itself, since most come from Google Drive), whatever the file is called. Offered in the welcome steps.
  * **Storage Sense warning (`config_manager.py`, Settings)**: if Windows Storage Sense is set to delete files in Downloads after a number of days, Settings says so and links to the setting, since that would delete sorted files too.
  * **Old installers and zips to the Recycle Bin (`clean_old.py`, `watch.py`, Settings)**: once a day, if turned on, files older than 30 days in Installers and Archives go to the Recycle Bin, with a ribbon saying how much space it freed.
  * **A preview before the first sort (`onboarding.py`)**: finishing the welcome steps with "Sort the files already in my Downloads folder" first shows how many files would go to which folder, with **Sort them** or **Not now**.
  * **Pictures by month (`sorter.py`, Settings)**: optionally, pictures go into `Images\2026-11`-style folders by the date the photo was taken (or the file's date). Clean-up keeps those folders.
  * **Words at the top of a PDF (`sorter.py`, Settings; new dependency `pypdf`)**: optionally, a rule's words are also looked for at the top of a PDF's first page, so `document (3).pdf` that says "INVOICE" still goes to Invoices. Large (over 10 MB), locked or unreadable PDFs are skipped.
  * **Tidy names and dates (`sorter.py`, Settings)**: optionally, `My_File (1).pdf` is filed as `My File.pdf`, and/or the date goes in front (`2026-11-09 Invoice.pdf`). Code, CAD, 3D and installer files keep their names. Undo puts the old name back.

* **Ready for beta testers**:
  * **Unexpected errors are no longer silent (new `problem_report.py`, `main.py`, `tray.py`)**: the installed app has no console, so an error used to close it without a trace. Now every unexpected error is written to `sorter.log` and saved in `%LOCALAPPDATA%\download-sorter\crash-reports` (the newest 20 are kept). In the tray and windows, a message says what happened and offers to open a bug report with the details filled in. Nothing is sent unless you post it.
  * **Report a problem (`tray.py`, Settings)**: a new tray menu item, and the link in Settings → About, open a GitHub bug report with your Download Sorter and Windows versions already filled in.
  * **Beta testing guide (new `BETA_TESTING.md`)**: what to install, a checklist of things to try, known issues, and how to send feedback. Linked from the top of the README and from GitHub's "New issue" page.
  * **Issue forms (`.github/ISSUE_TEMPLATE`)**: the bug report asks for the installer, portable zip or source, has a field the app fills in, and reminds people that logs contain file names. A new **Beta feedback** form asks about first impressions.

### Changed
* **Easier to use (`onboarding.py`, `rules_manager.py`, `tray.py`, `ui_style.py`)**:
  * When sorting is off or paused, **Start sorting** / **Resume sorting** is the green button on Home, since that's what you most likely want.
  * Closing the window with rule changes you haven't saved asks **Save changes**, **Don't save** or **Cancel**, instead of losing them.
  * Keyboard shortcuts: **Ctrl+1** to **Ctrl+4** switch pages, **Ctrl+S** saves rules, **Ctrl+Z** on Home undoes the last sort, **F5** refreshes Home. Buttons show them in their tooltips.
  * Rules: an empty list no longer leaves a gap under the tabs, and hovering a cut-off cell shows its full text.
  * Settings shows **Just my folders** for anyone set up from their own folders, instead of "Everyday".
  * The tray reads "1 file filed today" instead of "1 file(s)", and only mentions held 3MF files when there are some.

### Fixed
* **Saving rules undid other settings (`rules_manager.py`)**: the Rules page saved the whole settings file as it was when the window opened, so it could turn an option you had just changed in Settings back off, or remove a rule you had just added from a learning ribbon. It now changes only the rules in the current file, and shows rules added elsewhere.
* **Settings could remove a rule just learned (`onboarding.py`, `config_manager.py`)**: each Settings option now changes only itself in the current settings file.
* **Nothing private in bug reports (`problem_report.py`)**: a bug report opened from a crash showed the error message as its title, which can contain a file's full path and your Windows user name. The title now names only the kind of error, and the report hides your user name (`%USERPROFILE%`) and file names (`<file>.pdf`). Crash reports saved on your PC keep the full details.
* **No question during an update (`rules_manager.py`, `onboarding.py`)**: closing for an update with unsaved rule changes now saves them instead of asking while the installer waits.
* **Daily clean-up and renamed folders (`clean_old.py`)**: it now follows the Installers and Archives rules, so it also cleans up when setup pointed them at one of your own folders.
* **Plural words (`learning.py`)**: "taxes", "boxes" and "classes" were read as "taxe", "boxe" and "classe".

## [2.2.2] - 2026-11-09

### Added
* **One ribbon for big batches (`ribbon_popup.py`, `watch.py`, `sort_undo.py`)**: when lots of files land at once (a zip unpacking, a browser's "download all"), the first 3 still get their own ribbon. After that, one ribbon counts the rest while they arrive ("Filed 37 files · Images 25 · Documents 12") and closes a few seconds after the last one. Its **Undo** puts the whole batch back and **Open** shows the folder. `--undo --batch` can now be given several times.
* **Report a problem in Settings (`onboarding.py`)**: Added a direct link in the Settings About section to report an issue or suggest rules on GitHub.

### Changed
* **Differentiate 3D Print Meshes and CAD models (`sorter.py`)**: 3D print mesh and printer formats (`.stl`, `.3mf`, `.obj`, `.blend`, `.3ds`, `.fbx`, `.glb`, `.gltf`, `.gcode`) sort to `3D Models`, while mechanical/engineering CAD files (`.step`, `.stp`, `.iges`, `.igs`, `.f3d`, `.f3z`, `.ipt`, `.iam`, `.sldprt`, `.sldasm`, `.slddrw`, `.dwg`, `.dxf`) sort to `CAD`.

### Removed
* **Old one-off scripts**: `restore_from_log.py` (rebuilt moves from logs written before undo history existed, before 2.0) and `clean_test_history.py` (removed test records from history, no longer needed since tests use their own folders), plus `sorter.record_history`, which only the first one used.
* **Repeated tests**: the `--version` check for every script ran in four test files, starting about 20 processes each time. It now runs once, in `tests/test_attribution.py`, with the strictest check of the four. The stray `test_ribbon_popup.py` moved into `tests/`.

### Fixed
* **Protect library folders in Remodel (`remodel.py`)**: Cleanup and remodel scans now recognize library folders (`3D Printing`, `CAD`, `Games`, `Finance`, `Extracted`, `timelapse`, etc.) as protected categories so their subfolder hierarchies are never moved into `Other`.
* **Ribbons didn't appear in the installed app (`ribbon_popup.py`)**: since 2.1.0 the installed app started each "File sorted" and 3MF hold ribbon in a way it didn't understand ("Unknown command"), so they never showed. They now start through the app's own `--ribbon` command. Summary ribbons were not affected.
* **"Couldn't download the update" (`updater.py`)**: GitHub's download server sometimes answers "504 Gateway Timeout" or drops the connection, and the in-app update gave up on the first try. It now tries up to 3 times, a few seconds apart, says what went wrong (for example "GitHub answered with error 504") if it still can't, and reuses an installer it already downloaded and checked instead of downloading it again.

## [2.2.1] - 2026-11-09

### Fixed
* **Consolidate only empties Download Sorter's own old folders (`consolidate.py`)**: `--consolidate` no longer empties top-level folders named `Finance`, `XYZFiles` or `DownloadSorter-v2...` in Downloads. Those names were left over from testing. It now only touches folders older versions of Download Sorter made (`Spreadsheets`, `Code\Gcode` and `Code\Haas`), whatever their capitals are. To have it empty one of your own old folders too, add the folder's name to `LEGACY_FOLDERS_TO_CONSOLIDATE`.

## [2.2.0] - 2026-11-08

### Changed
* **Simpler folders (`sorter.py`, `config.json`)**: new installs file downloads into one flat folder per kind of file, covering 121 file types (up from 75):
  * `Documents`: PDFs, Word, text, spreadsheets, presentations, e-books and more (before: `Documents\PDFs`, `Documents\Word`, `Documents\Text` and a separate `Spreadsheets`).
  * `3D Models`: STL, 3MF, OBJ, STEP, CAD and G-code files, including Haas CNC and 3D printer programs found by the detectors (before: sub-folders such as `3D Models\STL`, plus `Code\Gcode` and `Code\Haas`).
  * `Images`, `Videos`, `Audio`, `Archives`, `Installers` and `Code` cover more file types, and anything else still goes to `Other`.
  * **Already using Download Sorter?** Your saved rules are kept, so files keep going where they went before. To switch to the new layout, open **Rules**, then **More**, then **Reset to defaults** and **Save changes**. This replaces all your rules, so pick your profile again in **Settings** afterwards to get its name rules back. Then, to move files you already sorted into the old sub-folders, run `DownloadSorter.exe --consolidate` (see below).

### Added
* **Move already-sorted files into the new folders (new `consolidate.py`; `DownloadSorter.exe --consolidate`, also `--flatten`)**: files sitting in sub-folders of `Documents`, `3D Models`, `Images`, `Videos`, `Audio` and `Archives` (for example `Documents\PDFs` or `3D Models\STL`), in the old `Spreadsheets` folder, and in `Code\Gcode` and `Code\Haas`, are moved to where your current rules send them. Loose files in Downloads are filed too, including held 3MF files and files you undid (`--no-held` leaves those alone). Note that this also empties sub-folders you made yourself inside those folders. Exact duplicates are set aside in `_Duplicates`, emptied folders are removed (`--keep-empty-dirs` keeps them), and every move is recorded, so `DownloadSorter.exe --undo` puts everything back. Try `--dry-run` first to see what would move.
* **Updates install from inside the app (new `updater.py`, `update_check.py`, Home)**: when a newer version is out, Home offers **Download and install**. It downloads the installer with a progress bar and Cancel, checks its size and SHA-256 against the release's `checksums.txt` (a mismatch is deleted, never run), then **Restart and install** hands over to the installer, which closes Download Sorter, updates it and opens it again. Only this project's GitHub release downloads are accepted. Running from source, or a release without those files, keeps the "Download" link to the release page.
* **`DownloadSorter.exe --quit` (new `quit_signal.py`, `main.py`, `tray.py`, `rules_manager.py`)**: asks a running Download Sorter (background sorter and windows) to close cleanly, without switching sorting off.

### Fixed
* **Shortcuts during an update (`installer_builder.py`)**: an in-app update now keeps the Desktop and Start menu shortcuts you have (and refreshes them), and doesn't add ones you deleted. A new `/NO-SHORTCUTS` option installs without any shortcuts (used by the automated install tests).
* **Files written a moment ago (`safe_ops.py`)**: with no settle time set, a file whose timestamp was a fraction of a millisecond ahead of the clock was refused as "not settled". A settle time of 0 now means "don't wait".
* **Installing over a running Download Sorter (`installer_builder.py`)**: the installer never closed the running app, so Windows refused to replace its files and an update stopped halfway, leaving a mix of old and new files. The installer now asks the app to close (`--quit`), waits up to 10 seconds, closes what is left, and retries files that are still locked. If it still can't, it says so instead of leaving a broken install. `/UPDATE` (used by the in-app updater) keeps the install folder and shortcuts and opens the app again afterwards.
* **Messages replacing each other (`ui_motion.py`)**: a message shown right after clicking another message's button could disappear when the old one finished fading out.

## [2.1.0] - 2026-11-08

### Changed
* **Home shows what the sorter is doing (`onboarding.py`, new `ui_motion.py`)**: turning sorting on used to change one word and nothing else. Now:
  * The status line says which folder is being watched, and its dot gently pulses while sorting is on.
  * Files the background sorter files appear in "Recently sorted" within a couple of seconds, without clicking Refresh. New rows glow briefly, the "files organized today" count counts up, and a short message says what was filed ("Filed report.pdf in PDFs.") with an Undo button.
  * Files that were already in Downloads before sorting started are counted ("7 files are waiting in Downloads"), and the main button becomes "Sort 7 files now".
  * "Sort Downloads now" runs in the background, so the window no longer freezes. It ends with a summary ("Sorted 7 files into 4 folders: Images (3), PDFs (2), ...") and an Undo button. If there was nothing to do, it says so.
  * Starting the sorter shows "Starting..." until it is running, then confirms. If it doesn't start within 15 seconds, it says so.
  * Pausing, resuming and undoing each say what happens next.
* **Smoother window (`rules_manager.py`, `ui_motion.py`)**: pages fade in when you switch between them, and messages slide in and fade out. All motion is skipped when Windows' "Show animations in Windows" setting is off.
* **Tray messages (`tray.py`)**: pausing and resuming from the tray now say what happens next.
* **Zip files are kept as they are unless you choose otherwise (`sorter.py`, `onboarding.py`, `config.json`)**: the welcome steps now ask "Unzip downloaded .zip files" (off by default), and the shipped default is off. Before, every zip was unpacked into Extracted automatically. The switch is still in Settings.
* **A README for regular people (`README.md`, `docs/images/`)**: it now starts with what the app does, a screenshot, how to install, what happens on first run, what to click on the "Windows protected your PC" screen and how to check a download, privacy, undo, updates, uninstalling and how to report a problem. The technical details moved under "For power users and developers". `SECURITY.md` and `CONTRIBUTING.md` now describe the one optional network request instead of promising none.
* **Tray menu matches the window (`tray.py`)**: "Undo last sort", "Rules..." and "Clean up a folder..." replace "Undo last batch", "Configure Rules & Customize..." and "Remodel Messy Folders...".
* **Release notes come from this changelog (`release_notes.py`, `.github/workflows/release.yml`)**: each GitHub release starts with short install steps, followed by that version's changelog section, instead of a list of pull request titles.
* **No personal paths in the app (`rules_manager.py`)**: the "Try a filename" box no longer looks for sample programs in a folder on the developer's PC.

### Added
* **"A newer version is available" (new `update_check.py`, Home, tray, Settings)**: once a day at most, Download Sorter asks GitHub which version is newest. When there is a newer one, Home shows "Download Sorter X is available" with a Download button, and the tray mentions it once. Only the app name and version are sent. Failures are silent. Turn it off in Settings, under "Check for new versions once a day".
* **Tray tooltip stays within Windows' 127-character limit (`tray.py`)**: a long tooltip would raise an error in the tray library. The credit line is dropped first when space runs out.
* `sorter.preview_existing`: lists what "Sort Downloads now" would move, without moving anything or writing to the log.

## [2.0.2] - 2026-11-04

### Added
* **Learning from how you organize (preview, stage 1, off by default) (`learning.py`, Settings)**: when you move a sorted file into another folder inside Downloads, Download Sorter notes the words in its name, its type and the website it came from (site name only, read from the "downloaded from" note Windows keeps). It only keeps a practice log of what it would have suggested, and shows nothing yet. Settings has "See what it has learned" (how often its guesses were right) and "Forget everything". It learns only from your own moves, never from its own.

### Fixed
* **Your moves are respected (`watch.py`, `sorter.py`)**: a file you moved into a folder inside Downloads (for example `Downloads\Banking`) was treated as a misplaced download and moved straight back out, and files you put in a different category folder were moved back by the next startup sweep. Files you move yourself now stay where you put them.
* **Files reached through another spelling of Downloads are sorted (`sorter.py`)**: `move_file` compared folder paths as written, so a file reached through a short 8.3 name, a linked folder or a mapped drive was treated as "not in Downloads" and skipped: never sorted and never put on a 3MF hold. Folders are now compared by identity (`same_folder`).
* **Nothing is sorted before the welcome wizard is finished (`tray.py`, `watch.py`, `onboarding.py`)**: on first run the tray used to sort Downloads (and Documents) while the wizard was still open, using the default rules. The watcher now waits until the wizard is saved, then sorts with the chosen profile. New downloads that arrive meanwhile wait too. If the wizard is closed early, the tray shows "Waiting for you to finish setup" and a **Finish setup...** menu item.
* **"Organize existing files" is honored (`onboarding.py`, `sorter.py`, `watch.py`)**: unticking it records the moment setup finished (`skip_files_before`); automatic sweeps, now and after every restart, leave files that were already there alone. "Sort Downloads now" still sorts everything. When ticked and no background sorter is running, the wizard sorts as one batch so a single Undo puts everything back.
* **The sorter starts after setup (`rules_manager.py`)**: when the installer opens the wizard on its own, finishing it now starts the background sorter instead of waiting for the next sign-in.
* **Profile recommendation reads whole words (`config_manager.py`)**: "teams", "amount", "template", "decade" and "syntax" no longer count as 3D-printing or tax files, and camera names such as `IMG_1234.jpg` are no longer read as course codes.
* **Auto-Unpack ZIPs switch works (`onboarding.py`)**: it saved a setting nothing read; it now changes `watch.auto_unzip`, which the sorter uses.
* **Uninstall removes everything it should (`uninstall.ps1`, `installer.iss`)**: the startup entry the wizard creates is removed; for the single-file setup, the shortcuts, the Installed Apps entry and the program folder are removed too; the Inno Setup uninstaller now runs the same cleanup.
* **`setup.bat`**: a message containing `(winget)` broke the Python-install branch, and a failed virtual-environment setup was not detected. `update.bat` had the same error-check problem.
* **Remodel "Organize" moves every file (`remodel.py`)**: since 2.0.1 only the last file of each folder was moved, the rest were left behind, and a skipped file (an Office lock file or a partial download) could be moved under another file's name. A folder whose files were all in subfolders was not organized at all.
* **Duplicates are set aside, not deleted (`sorter.py`)**: an exact duplicate of a file already filed is moved to `Downloads\_Duplicates\YYYY-MM\` through the journal, so Undo can bring it back. "Keep here" and the grace-period hold are checked first, so a kept or held file is never touched.
* **Dry runs no longer change anything (`sorter.py`)**: previews from the CLI, Rules Manager or Documents/reclassify sweeps no longer register or reset a hold or show a notification.
* **Extraction undo restores the zip (`unzip_archive.py`, `unzip_3d.py`, `safe_ops.py`)**: the zip's move to `Archives` is now an undoable move and the extraction itself is a separate `extract` record. Old records that would have renamed an extracted folder to `name.zip` are refused with an explanation.
* **Archive safety limits (`unzip_archive.py`, `unzip_3d.py`)**: archives are checked before anything is decompressed (entries, total size, per-file expansion ratio, free disk space), output is capped while writing, and general extraction goes into a hidden staging folder that is only published when complete. Limits can be changed with `archive_limits` in the config. A rejected zip is filed in `Archives` as a normal file.
* **Hold releases keep their state on failure (`hold_manager.py`, `watch.py`)**: a hold is dropped only after its file has moved. A failed release or "File now" keeps the hold and retries an hour later.
* **Live reload moves folder watches (`watch.py`)**: changing the Downloads folder or Documents sources in the config now re-subscribes the watcher and sweeps the new folders, without a restart.
* **Undo now sticks (`sorter.py`, `safe_ops.py`, `watch.py`)**: a file Undo puts back is left where it is until it is edited or replaced; previously the watcher sorted it straight back out.
* **Packaging (`pyproject.toml`)**: `main` is now a packaged module, so the `download-sorter` and `download-sorter-app` commands work after `pip install`.

### Changed
* **A cleaner window (`rules_manager.py`, `onboarding.py`, new `ui_style.py` and `ui_icons.py`)**: a sidebar with Home, Rules, Clean up a folder and Settings replaces the crowded header and the two-button switcher. One shared style and one set of line icons replace about 100 inline styles and the emoji used as icons. Wording throughout is plainer ("Sorting is on", "Name rules", "Try a filename"). Each screen has a single main button, and Save changes is only enabled when there is something to save.
* **Fixed along the way**: "&" in tab and button labels showed as an underscore ("Home _Activity"); the Home screen said "Idle" and "Watching Downloads" at the same time; "Start Sorter" only cleared the pause flag instead of starting the sorter; "Sort Downloads now" wasn't undoable as one batch; Project mode showed for every profile when no profile was saved; the "try a filename" box had overlapping, bordered labels; the Clean up page had an unstyled white list area; `onboarding` was missing from the packaged modules in `pyproject.toml`.
* **Generic defaults (`sorter.py`, `config.json`)**: the built-in keyword rules (one student's courses and school) are removed; out of the box files are sorted by type, and the wizard adds a starter set for the chosen profile.
* **Documents is only scanned for the Maker profile (`sorter.py`, `config_manager.py`)**: moving 3D/CAD files out of Documents could break Blender or SolidWorks projects kept there, so it is now off by default and turned on only by the **3D Printing, CAD & CNC Maker** profile.
* **Clean up a folder keeps named folders together (`remodel.py`, `rules_manager.py`)**: folders such as `Wedding 2019`, `Taxes 2024` or a game folder now default to **Keep intact**; only folders whose name says nobody sorted them yet ("New folder", "Misc", "Old Downloads", "Mixed stuff") default to **Organize by file type**. You can still choose Organize for any folder, and each card says why it was suggested.
* **Profile starter rules are tighter (`config_manager.py`)**: single words that caught the wrong files are replaced by phrases ("statement" by "bank_statement", "billing_statement"; "tax" by "tax_return", "tax_form"; "reading" by "reading_list"), so `Personal_Statement_UCLA.pdf`, `Tax Policy Essay.docx` and `Reading Festival tickets.pdf` stay out of Invoices, Taxes and School. Each profile now holds what its description says: Everyday is file types plus receipts, manuals and travel; Student and Office add their own folders on top; 3D printing has no name rules of its own, so the Haas detector and the `3D Models\STL` folder are no longer overridden ("haas" sent Haas programs to `Code\Gcode`, and `gear.stl` went to `3D Models\CAD`).
* **Switching profile removes the old profile's rules (`config_manager.py`)**: rules a profile added are dropped when you pick another profile, unless you added your own words to them. Your own rules are always kept.
* **Multi-word keywords match any separator (`sorter.py`)**: "bank_statement" now matches `Bank Statement.pdf`, `bank-statement.pdf` and `BankStatement.pdf`, not only names that use an underscore.
* **One kind of setup file (`installer_builder.py`, `build_release.py`)**: the release used to switch to Inno Setup whenever it was installed on the build machine, and the two installers take different silent options. The single-file installer is now always built (`python build_release.py --inno` still builds the Inno Setup version), and it also accepts `/VERYSILENT`, `/SILENT` and `/DIR=`.
* **Release builds are tested before tagging (`.github/workflows/release.yml`)**: pull requests that change the release files now build the installer and zip on Windows (without publishing), and a tag that does not match the app version is refused. The v2.0.0 and v2.0.1 release builds had stopped at failing tests, so no 2.x installer was ever published.
* **`requirements.txt`** no longer installs `pytest`; test tools are in the `dev` extra (`pip install -e .[dev]`).

---

## [2.0.1] - 2026-11-04

### Fixed
* **Nested Repository & VCS Preservation (`remodel.py`)**:
  * Remodel now walks subdirectories with directory pruning to detect any nested Git, SVN, Mercurial, virtualenv, or Node.js repositories at arbitrary depth (e.g. `Stuff\inner-repo`).
  * Nested repositories are kept completely whole and recorded as protected, preventing code files (e.g. `src\app.py`) from ever being scattered into `Code\` or quarantined as duplicates.
* **Single-Instance Mutex Precedes Crash Recovery**:
  * In `tray.py`, `sort_once.py`, `watch.py`, and `main.py`, the single-instance mutex check now strictly precedes write-ahead journal recovery.
  * Secondary launches or parallel CLI runs can no longer mistakenly flag a slow in-progress move from an active instance.
* **Accurate Remodel Undo Reporting**:
  * `remodel.py` and Rules Manager now explicitly report when some files could not be restored, displaying the exact count of files safely preserved in quarantine rather than reporting blanket success.
* **Windows CMD Syntax Compatibility (`setup.bat`)**:
  * Replaced `(.venv)` with `[.venv]` inside `if (...)` blocks to avoid premature block termination by `cmd.exe`.
  * Added non-interactive ping delay fallback for automated installations.

---

## [2.0.0] - 2026-11-03

### Added
* **Full Single-Source Attribution**:
  * Unified author credit (`Made by Devyn Fidel (2026)`) defined exclusively in `about.py`.
  * Wired into all 18 CLI entry points (`--version`), setup and install scripts (`setup.bat`, `update.bat`, `install_context_menu.ps1`, `install_startup.ps1`), GUI footer, system tray menu/tooltips, generated receipts, and packaging metadata.
* **3-Path Batch File Handoff Quick Switcher (`batch_handoff.py`, `quick_switcher.py`)**:
  * Coalesces incoming download bursts arriving within 25 seconds into an atomic batch.
  * Floating multi-button switcher pill offering:
    * `? Latest` (`Ctrl+G`): Direct injection of the most recent file into Windows File Open/Upload dialogs.
    * `? All N` (`Ctrl+Shift+G`): Simultaneous multi-file dialog injection via hidden `.batch_stage` NTFS hardlinks without copying disk blocks.
    * `? Copy` (`Ctrl+Shift+C`): Native `CF_HDROP` clipboard file-list copying for instant `Ctrl+V` pasting.
  * Native Windows OLE Drag-and-Drop from the pill into CAD, slicers, or file managers.
  * Interactive hover checklist allowing users to untick unwanted files before committing handoff.
* **3MF Grace-Period Hold & MakerWorld Overhaul (`hold_manager.py`, `ribbon_popup.py`, `gcode_peek.py`)**:
  * 7-day idle hold preserving incoming `.3mf` files in `Downloads\` without premature sorting, keeping Bambu Studio shortcuts, recent projects, and `Ctrl+S` incremental saves intact.
  * Active slicer lock detection extending holds up to 14 days while Bambu Studio, OrcaSlicer, or PrusaSlicer are open.
  * Desktop Ribbon Toast preview displaying extracted plate thumbnails up to 1024x1024 with interactive **File now** and **Keep here** actions.
  * Hardened `defusedxml` parser guarding against entity expansion and directory traversal attacks.
  * Empirical MakerWorld project routing to `3D Models\MakerWorld\<Author>\<Model>\<Plate>.3mf`.
  * Dynamic System Tray held items badge displaying count and time remaining.
* **Staged Machine Remodel Engine (`remodel.py`, `rules_manager.py`)**:
  * Safe file system remodeling for chaotic PCs and legacy folders via CLI (`download-sorter-remodel`) and Rules Manager 3rd tab.
  * Deep protections for engineering and simulation data: SOLIDCast (`.fsimodel9`, `.afs`, `.gdproj`, `FLOWCast.set`), MAGMASOFT (`.magma`, simulation trees), KiCad, Arduino, CAD assemblies, developer environments, Git repos, active folders (<14d), and `.download-sorter-keep`.
  * 3-stage deduplication (size -> 64KB fast hash -> full SHA-256 + NTFS inode verification).
  * 30-day quarantine (`_Remodel Quarantine\remodel_<id>\`) and searchable `Where did my files go.html` receipt.
  * 1-Click Undo reversing all remodel operations via write-ahead journal with quarantine file preservation.
* **Write-Ahead Journaling & Automatic Crash Recovery (`safe_ops.py`)**:
  * 2-phase move operations logging `planned` and `done` states with disk `fsync`.
  * Startup crash recovery reconciling orphaned operations across all daemon and CLI entry points.
* **Visual Design Token Compliance (`suite_theme.py`)**:
  * Strict alignment across all UI surfaces, tables, pills, dialogs, and HTML receipts with Devyn Personal Suite design tokens.
  * Zero forbidden off-palette hex codes.

---

## [1.0.0] - 2026-10-30

### Added
* **PySide6 Visual Rules Manager & Live Simulator GUI**:
  * Real-time drag/reorder precedence hierarchy.
  * Instant Live Sandbox evaluating arbitrary test filenames with winning rule, match trigger, and target destination preview.
  * Temporary Active Project Catch-All mode for CAD/3D/CNC files with auto-expiration.
  * Curated **Rule Presets Library** covering Office/Finance, Student Coursework, 3D Printing/Maker, Software Dev, and Creative Media.
  * Standalone JSON Rule **Export** and **Import** (Merge or Replace).
  * Factory defaults restore with confirmation safeguards.
* **Low-Resource Background Daemon & System Tray Companion (`tray.py`)**:
  * Windows single-instance mutex preventing accidental multi-process collisions.
  * Real-time daily file move counter.
  * Instant context menu: Pause/Resume, Sort Downloads Now, Undo Recent Batch, Configure Rules, and Open Downloads/Logs.
* **Per-Monitor High-DPI Desktop Ribbon Notifications (`ribbon_popup.py`)**:
  * Anti-aliased 60fps toast notifications with category-specific color accents (Mint for 3D/CAD, Blue for Documents, Red for CNC/G-Code).
  * 1-Click File Explorer reveal.
  * Dynamic vertical stacking for simultaneous batch downloads.
  * Multi-monitor edge clamping and auto-dismiss on file interaction.
* **Recency-Gated Quick Switcher (`quick_switcher.py`)**:
  * Automatic Common Item Dialog sniffer detecting active File Open/Upload dialogs in browsers, Slack, Discord, slicers, and CAD within 5 minutes of downloading.
  * Floating interactive pill with 1-click "Select" or `Ctrl+G` file path injection.
* **Reversible Undo Engine (`sort_undo.py`)**:
  * Full rollback of sorting batches using append-only `%LOCALAPPDATA%\download-sorter\history.jsonl` audit log.
* **Safe Janitor Cleanup (`clean_old.py`)**:
  * Non-destructive recycling of aging installers and archives directly to the Windows Recycle Bin.
* **3D Printing & CNC Intelligence (`gcode_peek.py`, `unzip_3d.py`)**:
  * Smart content sniffer distinguishing Haas CNC machine programs (`G90 G54`) from slicer layers (`.3mf`, `.stl`, `.gcode`, `.bgcode`).
  * Embedded thumbnail extraction for `.3mf` and slicer files.
  * Flat unpacking of nested 3D model archives from Thingiverse, MakerWorld, and Printables with Zip Slip path traversal defense.
* **Zero-Python Standalone Windows Executable Distribution**:
  * PyInstaller packaging script (`build_release.py`) and spec file (`DownloadSorter.spec`) compiling the entire application into a self-contained Windows x64 distribution (`DownloadSorter.exe`).
  * Automated GitHub Actions release pipeline (`.github/workflows/release.yml`) generating checksum-verified release ZIP archives on version tags.
  * Smart `setup.bat` with automatic Python launcher detection (`py -3`, `python`, `python3`) and automated `winget` installation fallback.
  * Complete clean uninstallation suite (`uninstall.bat` and `uninstall.ps1`).
