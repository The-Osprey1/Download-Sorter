# Signing and reputation (issue #9)

Antivirus, SmartScreen, and Windows 11 Smart App Control judge a download by who signed it and how many people have run it.
What the build now does, and what still needs a person:

1. **Version details (done).** `build_release.py` writes a Windows version resource from `about.py`,
   so the file's Properties name the publisher, product and version.
2. **Bootloader built from source (in the release workflow).** `.github/workflows/release.yml`
   reinstalls PyInstaller with `PYINSTALLER_COMPILE_BOOTLOADER=1` and `--no-binary=PyInstaller`, so
   the bootloader is compiled on the runner instead of using the prebuilt one antivirus engines
   often flag. This needs Visual C++, which GitHub's `windows-latest` runner has; it has not been
   run on a local machine.
3. **Whole-folder code signing (in the release workflow, off until set up).**
   Windows 11 **Smart App Control** checks every `.exe`, `.dll`, `.pyd`, and PowerShell `.ps1`
   script as it runs or loads. Signing only the setup installer would let the installer run and then
   block `DownloadSorter.exe` (or `DownloadSorter-cli.exe` for the Chrome connector) at launch,
   while unsigned PowerShell uninstall scripts (`uninstall.ps1`, `uninstall_context_menu.ps1`,
   `uninstall_startup.ps1`) can be forced into Constrained Language Mode under App Control—blocking
   the `.NET` path and registry/shortcut calls in `uninstall.ps1` (lines 99–106) when invoked from
   **Installed apps**.

   A file counts as already signed **only** when `Get-AuthenticodeSignature` reports both
   `Status == "Valid"` **and** `SignatureType == "Authenticode"` (an embedded signature). A
   catalog-only signature (`SignatureType == "Catalog"`) validates on the build machine's OS
   catalogs but does not travel with the file to users' PCs.

   During the app stage (`build_release.py --stage app`), `replace_vcpp_runtime_dlls()` checks for
   bundled Microsoft Visual C++ runtime DLLs (`VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`,
   `MSVCP140.dll` in `_internal/`, `_internal/PySide6/`, and `_internal/shiboken6/`) that lack an
   embedded signature and replaces them with Microsoft's embedded-signed copies from Visual Studio's
   redistributable folder (`VC\Redist\MSVC\<version>\x64\Microsoft.VC143.CRT\`, located via
   `vswhere`) when a same-or-newer version is present. Microsoft's DLLs are never sent to SignPath.

   When `SIGNPATH_ORGANIZATION_ID` is set, `.github/workflows/release.yml` runs a two-stage build:
   1. `python build_release.py --stage app` assembles `dist/DownloadSorter/` and swaps in
      embedded-signed Microsoft VC++ runtime DLLs when Visual Studio redistributables are present.
   2. `python pe_signing.py list --json` logs the full signer breakdown for `dist/DownloadSorter/`.
   3. `python pe_signing.py stage dist/DownloadSorter dist/unsigned-app --github-output` stages **only**
      files built from this repository (`OWNED_SIGNABLE_FILES`: `DownloadSorter.exe`,
      `DownloadSorter-cli.exe`, `uninstall.ps1`, `uninstall_context_menu.ps1`,
      `uninstall_startup.ps1`). If any third-party/unexpected file lacks an embedded Authenticode
      signature, `stage` exits with an error listing those files instead of sending them to SignPath.
   4. The staged `DownloadSorter-app-unsigned` artifact is submitted to SignPath
      (`SignPath/github-action-submit-signing-request@v3`), which signs all staged `.exe` and `.ps1`
      files using `packaging/signpath-artifact-config.xml`.
   5. `python pe_signing.py restore dist/signed-app dist/DownloadSorter` writes the signed files back
      into `dist/DownloadSorter/`.
   6. `python build_release.py --stage package` builds the ZIP, setup file, and `checksums.txt`.
   7. The setup file (`DownloadSorter-Setup-v*-windows-x64.exe`) is uploaded, signed by SignPath,
      copied back into `dist/`, and `checksums.txt` is refreshed for both the ZIP and the setup file.
   8. `python verify_release.py --skip-tests --require-signed` verifies that zero `.exe`, `.dll`,
      `.pyd`, or `.ps1` files under `dist/DownloadSorter/` (and the setup file) lack a valid
      embedded Authenticode signature.
   9. The live update check (`"Check that an update replaces a running copy and opens it again"`) runs
      last, verifying the actual signed installer and signed executables.

   When `SIGNPATH_ORGANIZATION_ID` is not set, the workflow runs `python build_release.py` (which also
   runs the VC++ redistributable replacement step if `vswhere` is present, warning and continuing
   without failing if not) and builds the exact same unsigned output as before.

## Setting up SignPath Foundation

To enable signing with SignPath (free for open-source projects through the SignPath Foundation):

1. **Create a SignPath project** linked to this GitHub repository and configure Authors, Reviewers,
   and Approvers with MFA per SignPath Foundation requirements.
2. **Upload the app artifact configuration:** Import `packaging/signpath-artifact-config.xml` into your
   SignPath project (for example, as `app-binaries`) so both `<pe-file-set>` (our `.exe` binaries)
   and `<powershell-file-set>` (our `.ps1` scripts) are signed. Also configure a single-file PE
   artifact configuration (or default PE configuration) for the setup installer
   (`DownloadSorter-Setup-*.exe`).
3. **Add GitHub repository settings:**
   - Variables (required):
     - `SIGNPATH_ORGANIZATION_ID`
     - `SIGNPATH_PROJECT_SLUG`
     - `SIGNPATH_SIGNING_POLICY_SLUG` (used for the setup file, and as the fallback for the app folder)
   - Variables (optional, if your app folder uses a separate signing policy or named artifact configuration):
     - `SIGNPATH_APP_SIGNING_POLICY_SLUG`
     - `SIGNPATH_APP_ARTIFACT_CONFIGURATION_SLUG`
     - `SIGNPATH_SETUP_ARTIFACT_CONFIGURATION_SLUG`
   - Secret:
     - `SIGNPATH_API_TOKEN`
4. **Code Signing Policy notice:** `README.md` already has a **Code signing policy** section with the
   team roles, what gets signed, how releases are built, and the privacy disclosure. Once SignPath
   Foundation approves the project, swap its first paragraph for the attribution line kept in the
   HTML comment just below it ("Free code signing provided by SignPath.io, certificate by SignPath
   Foundation"). Don't add that line before approval.

Until steps 1–3 are done, every release is unsigned. The release workflow then prints a
"Release is not code-signed" warning on the run's summary page, and `release_notes.py` opens the
release notes with a notice that the version is not code-signed and that Smart App Control will
block it. The notice disappears by itself once signing runs (the workflow passes `--signed`).

### Open question: third-party files without embedded upstream signatures

SignPath Foundation's published terms (`https://signpath.org/terms`) state that projects may include
standard **System Libraries** (as defined in GPL v3 section 1) in signed packages, but prohibit using a
SignPath Foundation project certificate to sign third-party / upstream binaries that their publishers
did not sign (because the SignPath Foundation certificate identifies SignPath Foundation as the
publisher of code built from *this* repository).

To enforce that policy automatically, `pe_signing.py stage` refuses to submit any file outside
`OWNED_SIGNABLE_FILES`:
- Microsoft's VC++ runtime DLLs (`MSVCP140.dll`, `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`) are
  replaced during `--stage app` with Microsoft's embedded-signed copies from Visual Studio's
  `Microsoft.VC143.CRT` redistributable directory when available (never re-signed with our certificate).
- **Open question to resolve if a future dependency ships unsigned:** If a future Python package
  ships an unsigned `.dll` or `.pyd` (or if an upstream redistributable copy cannot be replaced),
  `pe_signing.py stage` stops the workflow with an error listing the file. A maintainer must decide
  whether to replace/exclude that dependency, obtain an upstream embedded-signed build, or switch to
  an organization-owned signing certificate.

## Alternative: Microsoft Artifact Signing (Trusted Signing)

If you prefer an organization-owned publisher certificate (~$10/month via Azure Microsoft Artifact
Signing, formerly Trusted Signing)—or need to sign a third-party dependency that SignPath Foundation
does not permit—the two-stage build requires no structural rewrite:

1. Keep `python build_release.py --stage app`, `pe_signing.py stage`, `python build_release.py --stage package`,
   and `verify_release.py --require-signed` unchanged.
2. Replace the two `SignPath/github-action-submit-signing-request@v3` steps in `.github/workflows/release.yml`
   with `azure/artifact-signing-action` (or `Invoke-TrustedSigning`) pointing at `dist/unsigned-app/`
   before `--stage package`, and at `dist/DownloadSorter-Setup-v*-windows-x64.exe` after `--stage package`.
3. Refresh `checksums.txt` and run `verify_release.py --skip-tests --require-signed`.

## Diagnosing Smart App Control blocks and publisher reputation

- **Finding the exact blocked file:** Open **Event Viewer** → **Applications and Services Logs** →
  **Microsoft** → **Windows** → **CodeIntegrity** → **Operational** and check **Event ID 3076**
  (evaluation/audit block warning) and **Event ID 3077** (enforcement block). Each event logs the exact
  `.exe`, `.dll`, `.pyd`, or `.ps1` file path that Smart App Control refused to load.
- **Reputation ramp after signing:** Even after Download Sorter and every bundled binary and script
  carry valid embedded Authenticode signatures, Smart App Control can still block a newly signed binary
  from a new publisher (because Microsoft's Intelligent Security Graph weighs code-signing reputation
  and install volume) until enough users install and run the signed build.
- **Verify uninstall under Smart App Control:** Once signing is enabled, test uninstalling from Windows
  **Installed apps** on a PC with Smart App Control on to confirm `uninstall.ps1` runs with full
  language mode and completes cleanly.

## Other distribution steps

4. **Winget (templates in `packaging/winget/`).** Submit the manifests after a release is published.
5. **False-positive reports.** Norton and Microsoft reports are sent by hand through each vendor's
   portal after a release, with the published setup file.
