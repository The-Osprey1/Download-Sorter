# Changelog

All notable changes to **Download Sorter** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Home says why a file was filed (`safe_ops.py`, `sorter.py`, `config_manager.py`, `onboarding.py`).** Each move in the history now keeps the kind of match, the rule's name and the website the file came from (its name only, never an address). Hover a file in **Recently sorted** to read it: "Filed by your rule "Banking"", "Filed by its file type", "Downloaded from yourbank.com". Files filed by an older version show the path only.
- **The learner hears "no" (`learning.py`, `sort_undo.py`, `refile.py`, `watch.py`, `ribbon_popup.py`).** Putting back a file that a learned rule filed, moving it somewhere else from Home, or dragging it out in Explorer now counts against that rule. After two such files a ribbon asks "Stop filing into Banking?"; **Yes** switches the rule off (it stays on the Rules page), **No** means it won't ask again. Rules you wrote and file types are never judged this way.
- **It can file what no rule could place (`learning.py`, `sorter.py`, `ribbon_popup.py`, `onboarding.py`, `rules_manager.py`).** Off unless you ask for it. When a download would go to the catch-all folder and you have moved 3 similar files into one folder, the ribbon that asks "Put files like this in Bookkeeping?" now also has **Always**: it adds the rule and, from then on, files such downloads without asking. The same switch is in **Settings → Learning** ("File what no rule can place where I put files like it"). A file that a name rule, a folder of yours or a file type places is never moved on a guess, files already in the catch-all folder stay there, and the folder has to exist already. Put one back (Undo on the ribbon, or Home) and it stops guessing that word or website for that folder. Home and the ribbon's tooltip say when a file was filed this way, **Try a filename** shows a **Learned** badge, and "See what it has learned" counts the files it filed and how many you took back. `python learning.py --replay` shows how it would have done on your past moves.
- **A searchable list of every file in your sorted folder (new `file_index.py`, `watch.py`, `main.py`).** Download Sorter keeps a list of the files under the folder it sorts, including ones you put there yourself: name, folder, when and why each was filed, the website it came from, and for print files the model, designer, printer and filament. `DownloadSorter-cli.exe --find statement from:yourbank type:pdf` prints the matches; words match from their start and ignore plurals, `in:` keeps one folder, `from:` one website, `type:` one kind of file. The list is built in the background when sorting starts and once a day, and each filed, moved or deleted file updates it. A Git project is one entry and its files are not listed; `node_modules`, `.venv` and similar folders are skipped. It stays on this PC, in the app's data folder (`file_index.db`). Turn it off with `"search": {"enabled": false}` in the settings file.
- **Recent website suggestions in the Rule Editor (`rules_manager.py`).** The "Only when downloaded from these websites" box in `RuleEditDialog` now autocompletes comma-separated domains from your learned downloads, existing rules, and recent files in Downloads (completing Issue #25).

### Fixed
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
