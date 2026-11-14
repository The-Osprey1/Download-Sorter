# Security Policy

## 🔒 Local-First Commitment

Download Sorter is built on the principle of **strict data privacy**:
* **Zero Telemetry**: No analytics, phone-home beacons, or usage tracking.
* **One optional network request**: once a day at most, `update_check.py` (in `src/download_sorter`) asks GitHub's public "latest release" address (`api.github.com/repos/The-Osprey1/Download-Sorter/releases/latest`) which version is newest. The request carries only the app name and version (as its User-Agent). Nothing about the user, the computer or any file is sent. Turn it off in Settings ("Check for new versions once a day") or with `"check_for_updates": false` in `config.json`.
* **In-app updates only on request, and checked**: an update is downloaded only when the user clicks "Download and install", and only from `https://github.com/The-Osprey1/Download-Sorter/releases/download/` (`src/download_sorter/updater.py`). Before the installer runs, its size and SHA-256 must match the release's `checksums.txt`; a file that doesn't match is deleted. Because a file downloaded by the app carries no "downloaded from the internet" mark, Windows SmartScreen does not inspect it, and this check takes its place. All classification logic runs strictly on your local machine.
* **Non-Destructive File Movement**: Download Sorter never deletes your files. Each move is written to the history (`%LOCALAPPDATA%\download-sorter-v3\state.db`) before the file is touched, never replaces an existing file, and can be undone.

---

## 🛡️ Supported Versions

| Version | Supported |
| ------- | --------- |
| Latest release | :white_check_mark: |
| Older releases | :x: |

Download Sorter is in beta. Fixes go into the newest version only, and the app offers to update itself (Home → **Download and install**).

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability or potential path traversal flaw:

1. **Do not disclose publicly** in public issues or discussions.
2. Please [open a private security advisory](https://github.com/The-Osprey1/Download-Sorter/security/advisories/new) on GitHub or contact the maintainer directly.
3. Include clear reproduction steps, sample file names or archive structures, and observed behavior.
4. We will acknowledge receipt within 48 hours and work on a patch promptly.
