# Publishing to winget (issue #9)

Winget makes the installer a known, signed-by-reputation download, which helps SmartScreen.

1. Build the release (`build_release.py`), which writes the setup `.exe` and the version resource.
2. Compute the setup file's hash: `certutil -hashfile DownloadSorter-Setup-vX.Y.Z-windows-x64.exe SHA256`.
3. Copy the two YAML files here, replace every `<placeholder>`, and check them with `winget validate`.
4. Open a pull request against `microsoft/winget-pkgs` with the files under `manifests/d/DevynFidel/DownloadSorter/<VERSION>/`.

The templates are not submitted automatically; that is a step for the maintainer.
