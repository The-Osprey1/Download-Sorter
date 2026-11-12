# Signing and reputation (issue #9)

Antivirus and SmartScreen judge a download by who published it and how many people have run it.
What the build now does, and what still needs a person:

1. **Version details (done).** `build_release.py` writes a Windows version resource from `about.py`,
   so the file's Properties name the publisher, product and version.
2. **Bootloader built from source (in the release workflow).** `.github/workflows/release.yml`
   reinstalls PyInstaller with `PYINSTALLER_COMPILE_BOOTLOADER=1` and `--no-binary=PyInstaller`, so
   the bootloader is compiled on the runner instead of using the prebuilt one antivirus engines
   often flag. This needs Visual C++, which GitHub's `windows-latest` runner has; it has not been
   run on a local machine.
3. **Code signing (in the release workflow, off until set up).** The workflow signs the setup file
   with SignPath's action (`SignPath/github-action-submit-signing-request@v3`) and refreshes
   `checksums.txt`, but only when these exist in the repository settings:
   - variable `SIGNPATH_ORGANIZATION_ID`
   - variable `SIGNPATH_PROJECT_SLUG`
   - variable `SIGNPATH_SIGNING_POLICY_SLUG`
   - secret `SIGNPATH_API_TOKEN`

   Getting these needs a SignPath account (free for open-source projects through the SignPath
   Foundation) and a signing policy that SignPath approves. Until then the steps are skipped and
   releases build exactly as before. Only the setup file is signed; the programs inside the zip are not.
4. **Winget (templates in `packaging/winget/`).** Submit the manifests after a release is published.
5. **False-positive reports.** Norton and Microsoft reports are sent by hand through each vendor's
   portal after a release, with the published setup file.
