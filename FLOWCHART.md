# Download Sorter Architecture & Processing Flowchart

_Made by Devyn Fidel (2026)_

How the configuration is loaded and reloaded. For how a file is sorted, see the README.

---

## 1. How a file is sorted

The flowchart of how Download Sorter decides where a file goes, and what it does with it, is in the README: [How Download Sorter decides where a file goes](README.md#how-download-sorter-decides-where-a-file-goes). It is kept in one place so it stays correct.

---

## 2. Where settings and history live

```mermaid
flowchart LR
    subgraph AppDataStorage ["%LOCALAPPDATA%\download-sorter-v3"]
        UserConfig["config.json (rules, folders, settings)"]
        BackupConfig["config.json.bak (previous copy)"]
        State["state.db (move history, held files)"]
        Recent["recent_files.json (Recent File Insert)"]
    end

    Window["Main window (Settings, Custom Rules, Folder Architecture)"]
    Service["Sorter service (watcher + router)"]
    V2["2.x settings (%LOCALAPPDATA%\download-sorter\config.json)"]

    Window -->|"Validate, write a temp file, replace config.json"| UserConfig
    UserConfig -->|"Previous copy kept"| BackupConfig
    Window -->|"Hand the new settings to the running service"| Service
    Service -->|"Write each move as pending, then complete"| State
    Service -->|"Note each filed file"| Recent
    V2 -->|"Carry Over (read only, 2.x files untouched)"| UserConfig
```

### Key safety guarantees
1. **Atomic saves**: settings are validated, written to a temporary file and swapped into place with `os.replace`. If `config.json` is damaged, the `.bak` copy is used.
2. **New settings apply without a restart**: the window passes them to the running service, which swaps them in; the tray and watcher keep running.
3. **Crash-safe history**: each move is recorded as pending before the file is touched and marked complete afterwards. On the next start, anything left pending is checked against what is on disk.
4. **Reversible**: every batch can be undone from Home, from the ribbon, or with `--undo`. Undo never overwrites a newer file (it saves the old one as `name (Restored).ext`).
5. **2.x settings are never changed**: Carry Over only reads them.
