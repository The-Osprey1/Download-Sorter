# Publishing to winget

Winget makes the installer easy to install (`winget install DevynFidel.DownloadSorter`) and helps build download reputation over time.

The three multi-file manifests in `manifests/` (`DevynFidel.DownloadSorter.yaml`, `DevynFidel.DownloadSorter.installer.yaml`, and `DevynFidel.DownloadSorter.locale.en-US.yaml`) match the single-file setup `.exe` built by `installer_builder.py` (`InstallerType: exe`, `/S`, `/SILENT`, `/D="<INSTALLPATH>"`).

## Updating for a new release

1. After the GitHub release finishes publishing, read the setup installer's SHA-256 from `checksums.txt` (or run `(Get-FileHash .\DownloadSorter-Setup-vX.Y.Z-windows-x64.exe -Algorithm SHA256).Hash`).
2. Update `PackageVersion` in all three `.yaml` files under `manifests/`, `InstallerUrl` and `InstallerSha256` (uppercase) in `DevynFidel.DownloadSorter.installer.yaml`, and `ReleaseNotesUrl` in `DevynFidel.DownloadSorter.locale.en-US.yaml`.
3. Validate locally:
   ```powershell
   winget validate --manifest packaging\winget\manifests
   ```
4. Copy the three `.yaml` files from `manifests/` to `manifests/d/DevynFidel/DownloadSorter/<VERSION>/` in a fork of `microsoft/winget-pkgs` and open a pull request (or use `wingetcreate update DevynFidel.DownloadSorter`).
