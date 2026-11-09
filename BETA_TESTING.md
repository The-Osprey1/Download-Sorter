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

- [ ] The welcome steps made sense, and the folders it offered matched what's in your Downloads folder.
- [ ] If you chose to sort the files already in Downloads, the preview showed sensible numbers before anything moved.
- [ ] **Undo last sort** on Home put everything back where it was.
- [ ] Download a PDF, a picture and a zip from your browser. Each one was filed a few seconds after it finished, and a ribbon showed where it went.
- [ ] Download something large (a few hundred MB). It wasn't moved while it was still downloading.
- [ ] Click a ribbon. File Explorer opened with the file selected.
- [ ] On **Rules**, use **Try a filename** with a name like `March invoice.pdf`.
- [ ] Pause sorting from the tray icon (near the clock, possibly under the **^** arrow), download a file, then resume.

**During the week**

- [ ] Use your PC as normal. Did any file end up somewhere you didn't expect?
- [ ] Move 3 similar files (for example, 3 bank statements) into one of your own folders inside Downloads. Did a ribbon ask "Put files like this in ...?" Was the question clear?
- [ ] Restart your PC. Did Download Sorter start again by itself (if you chose that)?
- [ ] If a new version comes out during the test: did **Download and install** on Home work, and were your rules kept?

**If you have time**

- [ ] **Clean up a folder** on a copy of a messy folder (not the original).
- [ ] 3D printing: download a 3MF from MakerWorld or Printables and open it in your slicer. Was it held in Downloads, and did **File now** work?
- [ ] Uninstall from Windows Settings → Apps. Your sorted files should stay where they are.

## 3. Tell us what you found

- **Something broke or went to the wrong place:** right-click the tray icon and choose **Report a problem...** (also in Settings → About). Your browser opens a bug report with your version and Windows version filled in. Nothing is posted until you click **Submit**.
- **If Download Sorter shows "ran into an unexpected problem":** click **Yes** to open a bug report with the error details filled in. A copy is saved in `%LOCALAPPDATA%\download-sorter\crash-reports`.
- **Everything else** (first impressions, wording, what you liked): use the [beta feedback form](https://github.com/The-Osprey1/Download-Sorter/issues/new?template=beta_feedback.yml). Short answers are fine.
- **No GitHub account?** Send the same details to whoever invited you to the beta.

**Please check what you paste.** Logs and error messages can include file and folder names. Replace anything private with something like `file.pdf` before posting.

Where to find things, if you're asked for them:

| What | Where |
|:---|:---|
| Version | Settings → About |
| Log | Tray icon → **Open log folder** → `sorter.log` |
| Crash reports | The same folder → `crash-reports` |
| Undo history | The same folder → `history.jsonl` |

## Known issues

- **Windows SmartScreen and some antivirus programs warn about the installer** (Norton, for example, may say `WS.Reputation.1`). Download Sorter isn't code-signed yet, and new apps from small developers have no "reputation" with these programs. You can check that your download is the published one with [checksums](README.md#windows-protected-your-pc). Progress on signing: [issue #9](https://github.com/The-Osprey1/Download-Sorter/issues/9).
- **The tray icon may be hidden** under the **^** arrow next to the clock. Drag it onto the taskbar to keep it visible.
- **Files that are open in another program wait.** A file another program has open stays in Downloads until it's closed. That's on purpose.
- **Windows Storage Sense** can be set to delete old files in Downloads, including sorted ones. Settings warns you when it is.

## Stopping the test

- **Pause:** tray icon → **Pause sorting**. Nothing moves until you resume.
- **Put files back:** **Undo last sort** on Home, as many times as needed. Each click undoes one batch.
- **Uninstall:** Windows Settings → Apps → Installed apps → Download Sorter → Uninstall. Your sorted files stay where they are. It asks whether to also delete your rules and history.
