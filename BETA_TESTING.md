# Beta testing Download Sorter

Thanks for helping test Download Sorter before more people use it. This page covers what to install, what to try, what's already known, and how to tell us what you found. Everything here takes about 15 minutes, plus a week of normal use.

## Before you start

- **You need Windows 10 or 11.**
- **Nothing is deleted.** Download Sorter only moves files, every move can be undone, and exact copies go to a `_Duplicates` folder instead of being removed. Still, it's a beta. If your Downloads folder holds something you can't lose, back it up first.
- **Your files stay on your PC.** The only thing Download Sorter sends over the internet is a once-a-day question to GitHub about which version is newest. See [Privacy](README.md#privacy).

## 1. Install

1. Download `DownloadSorter-Setup-v<version>-windows-x64.exe` from the [latest release](https://github.com/The-Osprey1/Download-Sorter/releases/latest).
2. Run it. Windows will probably show a blue **"Windows protected your PC"** screen: click **More info**, then **Run anyway**. ([Why this happens](README.md#windows-protected-your-pc).)
3. Go through the welcome steps.

## 2. Things to try

Tick these off as you go. You don't have to do them all, and anything that surprises you is worth reporting.

**First day**

- [ ] The three welcome steps made sense. Nothing was sorted until you finished or skipped them.
- [ ] On **Home**, the preview of what is in Downloads showed where each file would go and why, before anything moved.
- [ ] **Undo** on a row (and **Undo Last Batch** from the tray icon) put the file back where it was.
- [ ] Download a PDF, a picture and a zip from your browser. Each one was filed a few seconds after it finished, and a ribbon showed where it went.
- [ ] Download something large (a few hundred MB). It wasn't moved while it was still downloading.
- [ ] Click a ribbon. File Explorer opened with the file selected.
- [ ] Pause sorting from the tray icon (near the clock, possibly under the **^** arrow), download a file, then resume.

**During the week**

- [ ] Use your PC as normal. Did any file end up somewhere you didn't expect?
- [ ] On **Custom Rules**, make a rule for a word in a file name, then download a file with that word. Did it go where you chose?
- [ ] On **Folder Architecture**, try **Auto-Create Basic**, then **Apply & Shift Files**, then **Undo**. Did the folders and files come back as they were?
- [ ] Restart your PC. Did Download Sorter start again by itself (if you chose that in Settings)?
- [ ] When a program asks you to pick a file to open or upload, did the **Recent File Insert** pill offer the file that was just filed?
- [ ] If a new version comes out during the test: did **Download and install** on Home work, and were your rules kept?

**Coming from 2.x**

- [ ] After installing over a 2.x copy, your word rules and watched folder were there. **Carry Over v2.8 / Setup...** in Settings imports them again.

**If you have time**

- [ ] 3D printing: download a 3MF from MakerWorld or Printables. Was it held in Downloads, and did **Move Held .3MF** work? Did it stay put while your slicer was open?
- [ ] Uninstall from Windows Settings → Apps. Your sorted files should stay where they are.

## 3. Tell us what you found

- **Something broke or went to the wrong place:** open a [bug report](https://github.com/The-Osprey1/Download-Sorter/issues/new?template=bug_report.yml). If you can, attach `%LOCALAPPDATA%\download-sorter-v3\sorter_v3.log` after looking it over (it contains file names).
- **Everything else** (first impressions, wording, what you liked): use the [beta feedback form](https://github.com/The-Osprey1/Download-Sorter/issues/new?template=beta_feedback.yml). Short answers are fine.
- **No GitHub account?** Send the same details to whoever invited you to the beta.

**Please check what you paste.** Logs and error messages can include file and folder names. Replace anything private with something like `file.pdf` before posting.

## Where to find things

| What | Where |
|:---|:---|
| Version | `DownloadSorter-cli.exe --version`, or the title of the window |
| Log | `%LOCALAPPDATA%\download-sorter-v3\sorter_v3.log` |
| Settings and rules | `%LOCALAPPDATA%\download-sorter-v3\config.json` |
| Undo history | `%LOCALAPPDATA%\download-sorter-v3\state.db` (use Undo; don't edit this) |

## Known issues

- **Windows SmartScreen and some antivirus programs warn about the installer** (Norton, for example, may say `WS.Reputation.1`). Download Sorter isn't code-signed yet, and new apps from small developers have no "reputation" with these programs. You can check that your download is the published one with [checksums](README.md#windows-protected-your-pc). Progress on signing: [issue #9](https://github.com/The-Osprey1/Download-Sorter/issues/9).
- **The tray icon may be hidden** under the **^** arrow next to the clock. Drag it onto the taskbar to keep it visible.
- **Files that are open in another program wait.** A file another program has open stays in Downloads until it's closed. That's on purpose.
- **Windows Storage Sense** can be set to delete old files in Downloads, including sorted ones. Turn it off for Downloads if you don't want that.

## Stopping the test

- **Pause:** tray icon → **Pause / Resume**. Nothing moves until you resume.
- **Put files back:** **Undo** on Home or **Undo Last Batch** on the tray icon, as many times as needed. Each use undoes one batch.
- **Uninstall:** Windows Settings → Apps → Installed apps → Download Sorter → Uninstall. Your sorted files stay where they are. It asks whether to also delete your rules and history.
