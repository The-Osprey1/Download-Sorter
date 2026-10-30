# Security Policy

## 🔒 100% Offline & Local-First Commitment

Download Sorter is built from the ground up on the principle of **strict data privacy**:
* **Zero Telemetry**: No analytics, phone-home beacons, or usage tracking.
* **Zero Network Requests**: The background daemon, rules manager, and utilities perform zero outbound HTTP/HTTPS connections. All classification, extraction, and inspection logic executes strictly on your local machine.
* **Non-Destructive File Movement**: Files are never permanently erased. Reversible moves are indexed in `%LOCALAPPDATA%\download-sorter\history.jsonl`, and aged cleanup is delegated strictly to the Windows Recycle Bin (`SHFileOperationW` with `FOF_ALLOWUNDO`).
* **Zip Slip Defense**: Archive extraction explicitly sanitizes archive members to prevent path traversal (`../`) attacks.

---

## 🛡️ Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability or potential path traversal flaw:

1. **Do not disclose publicly** in public issues or discussions.
2. Please open a Private Security Advisory on GitHub or contact the maintainer directly.
3. Include clear reproduction steps, sample file names or archive structures, and observed behavior.
4. We will acknowledge receipt within 48 hours and work on a patch promptly.
