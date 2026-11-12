# Signing and reputation (issue #9)

Antivirus and SmartScreen judge a download by who published it and how many people have run it.
Three things help, in order of effort:

1. **Version details (done).** `build_release.py` writes a Windows version resource from `about.py`,
   so the file's Properties name the publisher, product and version.
2. **Winget (templates in `packaging/winget/`).** A published winget package gives the installer a
   known identity. Submit the manifests after each release.
3. **Code signing (not set up).** The open-source route is SignPath (free for open-source projects).
   It needs an account and a signing policy approved by SignPath, so it cannot be done from the code
   alone. Once the account exists, add a step after "Build Standalone Executable & Package" in
   `.github/workflows/release.yml` that uses SignPath's GitHub action on `dist/DownloadSorter.exe` and
   the setup file, gated on a repository secret (for example `if: ${{ secrets.SIGNPATH_API_TOKEN != '' }}`).
   The action's input names come from SignPath's documentation and were not checked for this change.

Not in scope here: building the PyInstaller bootloader from source on the CI runner. That needs a C
toolchain on the runner and is a separate change.

Norton and Microsoft false-positive reports are sent by hand through each vendor's portal after a
release; they are not automated.
