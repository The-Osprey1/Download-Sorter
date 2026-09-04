# Download Sorter Architecture & Processing Flowchart

This document details the decision lifecycle of files processed by the Download Sorter background daemon and manual execution scripts.

## Processing Flowchart

```mermaid
flowchart TD
    Start(["New File Lands in Downloads"]) --> Checks{"Is File Ready?<br>• Not temporary (.crdownload, .part)<br>• Settle timer elapsed<br>• File not locked"}

    Checks -- "No" --> Wait["Wait / Ignore until download finishes"]
    Checks -- "Yes" --> Route{"Classify File by Rules"}

    subgraph Classification ["Rule Hierarchy"]
        Route -->|"1. Active Project Active?"| R_Active["Route to Project Folder"]
        Route -->|"2. Keyword Match?"| R_Keyword["Route to Keyword Folder<br>(e.g. Documents/School)"]
        Route -->|"3. G-Code Sniffer?"| R_Sniffer["Inspect toolpath headers<br>(Haas CNC vs 3D Print)"]
        Route -->|"4. Extension Match?"| R_Ext["Route by Extension<br>(Images, Audio, PDFs, Archives...)"]
        Route -->|"5. No Match"| R_Default["Default Folder (Other)"]
    end

    R_Active --> Dedup{"Exact duplicate<br>already in destination?"}
    R_Keyword --> Dedup
    R_Sniffer --> Dedup
    R_Ext --> Dedup
    R_Default --> Dedup

    Dedup -- "Yes" --> DeleteDupe["Delete redundant download<br>(Intelligent Deduplication)"]
    Dedup -- "No" --> ZipCheck{"Is it a .zip archive?"}

    ZipCheck -- "3D Model Archive & 3D Unpack ON" --> Unzip3D["Extract 3D files to 3D Models/<br>Move original .zip to Archives/"]
    ZipCheck -- "General .zip & Auto-Unzip ON" --> UnzipGen["Extract to Extracted/archive_name/<br>Strip redundant wrapper folder<br>Move original .zip to Archives/"]
    ZipCheck -- "No / Normal File" --> Move["Move file to target folder<br>(Auto-rename if name collides)"]

    Unzip3D --> Notify["Log to History (for Undo)<br>Show Top-Right Ribbon Notification"]
    UnzipGen --> Notify
    Move --> Notify
    DeleteDupe --> Done(["Done"])
    Notify --> Done(["Done"])
```

## Step-by-Step Breakdown

1. **Pre-Flight Checks (`is_file_ready_to_move`)**:
   - **Incomplete Browser Downloads**: Files ending with `.crdownload`, `.part`, `.partial`, `.tmp`, `.download`, or `.opdownload` are ignored until the browser finalizes the download.
   - **Settle Timer (`settle_seconds`)**: Verifies that the file's modification timestamp hasn't changed within the settle window (default 1.5s).
   - **Lock Detection**: Uses Windows `CreateFileW` with exclusive share flags to ensure the writing application (browser/slicer/CAD) has completely closed its file handle.
   - **Cloud Placeholder Defense**: Detects unhydrated OneDrive cloud placeholders (`FILE_ATTRIBUTE_RECALL_ON_OPEN` / `FILE_ATTRIBUTE_OFFLINE`) to prevent moving files still owned by the cloud provider.

2. **Classification Hierarchy (`evaluate_routing`)**:
   - **Active Project Mode**: If a project timer is active (set via HUD or tray), any CAD, 3D model, or toolpath file is routed straight to the project directory.
   - **Keyword Rules**: Filename tokens are matched first (e.g. `syllabus`, `homework`, `me3210` $\rightarrow$ `Documents\School`).
   - **G-Code Machine Sniffer (`gcode_peek.py`)**: Analyzes internal G-code headers and comments to route Haas CNC files to `Code\Haas` and 3D printer g-code to `Code\Gcode`.
   - **Extension Rules**: Standard mapping for general formats (`.stl`, `.step`, `.pdf`, `.docx`, `.png`, `.mp4`, `.zip`, `.exe`, etc.).
   - **Fallback**: Uncategorized files fall into `Other`.

3. **Intelligent Deduplication**:
   - Compares the file size and SHA-256 chunk hash against files with matching names in the destination folder.
   - If an exact duplicate exists, the newly downloaded copy is pruned to avoid `filename (1).ext` clutter.

4. **Archive Handling & Auto-Unzipping**:
   - **3D Archives (`unzip_3d.py`)**: MakerWorld / Printables archives containing 3D models are unpacked into `3D Models\<archive_name>\` or flattened if single-model. The archive moves to `Archives\`.
   - **General Archives (`unzip_archive.py`)**: Non-3D `.zip` archives are extracted into `Extracted\<archive_name>\`. Redundant root folders are smartly stripped, and the original archive moves to `Archives\`.
   - **Zip Slip Defense**: Path traversal characters (`../`) are rejected to secure the host filesystem.

5. **History & User Feedback**:
   - Every file move or extraction is appended to `%LOCALAPPDATA%\download-sorter\history.jsonl` with batch IDs.
   - Runs can be undone at any time using `sort_undo.py`.
   - An interactive non-blocking ribbon popup (`ribbon_popup.py`) surfaces in the top-right corner of the primary display with one-click click-to-reveal in Windows File Explorer.
