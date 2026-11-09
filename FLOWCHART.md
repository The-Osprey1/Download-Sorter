# Download Sorter Architecture & Processing Flowchart

_Made by Devyn Fidel (2026)_

How the configuration is loaded and reloaded. For how a file is sorted, see the README.

---

## 1. How a file is sorted

The flowchart of how Download Sorter decides where a file goes, and what it does with it, is in the README: [How Download Sorter decides where a file goes](README.md#how-download-sorter-decides-where-a-file-goes). It is kept in one place so it stays correct.

---

## 2. Configuration & Zero-Restart Hot-Reload Flowchart

```mermaid
flowchart LR
    subgraph RepoStorage ["Git Repository (Pristine Code)"]
        Template["config.json (Default Template)"]
        UpdateScript["update.bat (One-Click Updater)"]
    end

    subgraph AppDataStorage ["User AppData (%LOCALAPPDATA%/download-sorter)"]
        UserConfig["config.json (User Custom Rules)"]
        BackupConfig["config.json.bak (Safe Backup)"]
        History["history.jsonl (Move History)"]
        ActiveProj["active_project.json (Active Sprint)"]
    end

    subgraph DaemonRuntime ["Background Sorter Daemon"]
        Watcher["watch.py (Filesystem Observer)"]
        Reloader["ConfigReloader (Hot Reloader)"]
        Engine["sorter.py (Routing Engine)"]
    end

    subgraph GUI ["Visual Rules Manager (rules_manager.py)"]
        UI["Rules Editor & Sandbox GUI"]
    end

    Template -->|"First Launch Auto-Seed"| UserConfig
    UI -->|"Atomic 5-Stage Save"| UserConfig
    UserConfig -->|"Copy prior state"| BackupConfig
    UserConfig -->|"File mtime change"| Watcher
    Watcher --> Reloader
    Reloader -->|"Zero-Restart In-Memory Hot Swap"| Engine
    UpdateScript -->|"Safe Git Pull (Never Touches UserConfig)"| RepoStorage
```

### Key Safety Guarantees:
1. **Isolated User Config**: User custom rules live in `%LOCALAPPDATA%\download-sorter\config.json`. Pulling or updating repository code never conflicts with or overwrites user rules.
2. **5-Stage Atomic Persistence**: Rule updates are validated, written to a unique temporary file, flushed with `os.fsync`, backed up to `.bak`, and atomically replaced with Windows lock retries.
3. **Zero-Restart Live Reloading**: Modifying rules in `rules_manager.py` instantly propagates to the running daemon without restarting the tray app or losing state.
4. **Reversible History**: Every batch is recorded with UUIDs in `history.jsonl` and can be fully undone with `sort_undo.py`.
