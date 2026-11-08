# Security Policy

## 🔒 Local-First Commitment

Download Sorter is built on the principle of **strict data privacy**:
* **Zero Telemetry**: No analytics, phone-home beacons, or usage tracking.
* **One optional network request**: once a day at most, `update_check.py` asks GitHub's public "latest release" address (`api.github.com/repos/The-Osprey1/Download-Sorter/releases/latest`) which version is newest. The request carries only the app name and version (as its User-Agent). Nothing about the user, the computer or any file is sent. Turn it off in Settings ("Check for new versions once a day") or with `"update_check": {"enabled": false}` in the config. All classification, extraction, and inspection logic runs strictly on your local machine.
* **Non-Destructive File Movement**: Files are never permanently erased. Reversible moves are indexed in `%LOCALAPPDATA%\download-sorter\history.jsonl`, and aged cleanup is delegated strictly to the Windows Recycle Bin (`SHFileOperationW` with `FOF_ALLOWUNDO`).
* **Zip Slip Defense**: Archive extraction explicitly sanitizes archive members to prevent path traversal (`../`) attacks.

---

## 🛡️ Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.1.x   | :white_check_mark: |
| < 2.1   | :x:                |

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability or potential path traversal flaw:

1. **Do not disclose publicly** in public issues or discussions.
2. Please open a Private Security Advisory on GitHub or contact the maintainer directly.
3. Include clear reproduction steps, sample file names or archive structures, and observed behavior.
4. We will acknowledge receipt within 48 hours and work on a patch promptly.
