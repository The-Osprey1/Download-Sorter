# Download Sorter Architecture & Processing Flowchart

_Made by Devyn Fidel (2026)_

How the configuration is loaded and reloaded. For how a file is sorted, see the README.

---

## 1. How a file is sorted

The four-step routing order of how Download Sorter decides where a file goes, and what it does before moving anything, is in the README: [How a file's folder is chosen](README.md#how-a-files-folder-is-chosen). It is kept in one place so it stays accurate.

---

## 2. Where settings and history live

```mermaid
flowchart LR
    subgraph AppDataStorage ["%LOCALAPPDATA%\download-sorter-v3"]
        UserConfig["config.json (rules, folders, settings)"]
        BackupConfig["config.json.bak (previous copy)"]
        State["state.db (move history, sparse-hash dedup)"]
        Learned["learned_routes.json (local route hints)"]
        Recent["recent_files.json (Recent File Insert)"]
        Logs["sorter_v3.log (2 MB rotating log)"]
    end

    Window["Main window (Home, Folder Architecture, Custom Rules, Settings)"]
    Service["Sorter service (watcher + router)"]
    Chrome["Chrome Extension (Native Messaging)"]
    V2["2.x settings (%LOCALAPPDATA%\download-sorter\config.json)"]

    Window -->|"Validate, write a temp file, replace config.json"| UserConfig
    UserConfig -->|"Previous copy kept"| BackupConfig
    Window -->|"Hand the new settings to the running service"| Service
    Chrome <-->|"Local stdio IPC via DownloadSorter-cli.exe"| Service
    Service -->|"Write each move as pending, then complete"| State
    Service -->|"Update offline pattern tallies"| Learned
    Service -->|"Note each filed file"| Recent
    Service -->|"Record diagnostics"| Logs
    V2 -->|"Carry Over (read only, 2.x files untouched)"| UserConfig
```

### Key safety guarantees
1. **Atomic saves**: settings are validated, written to a temporary file and swapped into place with `os.replace`. If `config.json` is damaged, the `.bak` copy is used.
2. **New settings apply without a restart**: the window passes them to the running service, which swaps them in; the tray and watcher keep running.
3. **Crash-safe history**: each move is recorded as pending before the file is touched and marked complete afterwards. On the next start, anything left pending is checked against what is on disk.
4. **Reversible**: every batch can be undone from Home, from the ribbon, or with `--undo`. Undo never overwrites a newer file (it saves the old one as `name (Restored).ext`).
5. **2.x settings are never changed**: Carry Over only reads them.
