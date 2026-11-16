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
   Windows 11 **Smart App Control** checks every `.exe`, `.dll`, and `.pyd` as it loads and blocks
   any unsigned binary that Microsoft does not yet widely trust. Signing only the setup installer
   would let the installer run and then block `DownloadSorter.exe` (or `DownloadSorter-cli.exe`
   for the Chrome connector) at launch.

   When `SIGNPATH_ORGANIZATION_ID` is set, `.github/workflows/release.yml` runs a two-stage build:
   1. `python build_release.py --stage app` assembles `dist/DownloadSorter/`.
   2. `python pe_signing.py stage dist/DownloadSorter dist/unsigned-app --github-output` inspects every
      `.exe`, `.dll`, and `.pyd` with PowerShell's `Get-AuthenticodeSignature` and stages only the
      unsigned files (leaving files already validly signed by Microsoft, Python Software Foundation,
      The Qt Company, etc. untouched).
   3. The staged `DownloadSorter-app-unsigned` artifact is submitted to SignPath (`SignPath/github-action-submit-signing-request@v3`),
      which signs all staged `.exe`, `.dll`, and `.pyd` files using `packaging/signpath-artifact-config.xml`.
   4. `python pe_signing.py restore dist/signed-app dist/DownloadSorter` writes the signed binaries back
      into `dist/DownloadSorter/`.
   5. `python build_release.py --stage package` builds the ZIP, setup file, and `checksums.txt`.
   6. The setup file (`DownloadSorter-Setup-v*-windows-x64.exe`) is uploaded, signed by SignPath,
      copied back into `dist/`, and `checksums.txt` is refreshed for both the ZIP and the setup file.
   7. `python verify_release.py --skip-tests --require-signed` verifies that zero unsigned `.exe`, `.dll`,
      or `.pyd` files remain in `dist/DownloadSorter/` and that the setup file carries a valid signature.
   8. The live update check (`"Check that an update replaces a running copy and opens it again"`) runs
      last, verifying the actual signed installer and signed executables.

   When `SIGNPATH_ORGANIZATION_ID` is not set, the workflow runs `python build_release.py` and builds
   the exact same unsigned output as before.

## Setting up SignPath Foundation

To enable signing with SignPath (free for open-source projects through the SignPath Foundation):

1. **Create a SignPath project** linked to this GitHub repository and configure Authors, Reviewers,
   and Approvers with MFA per SignPath Foundation requirements.
2. **Upload the app artifact configuration:** Import `packaging/signpath-artifact-config.xml` into your
   SignPath project (for example, as `app-binaries`). Also configure a single-file PE artifact
   configuration (or default PE configuration) for the setup installer (`DownloadSorter-Setup-*.exe`).
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
4. **Add SignPath's required Code Signing Policy notice** to the project's public documentation/homepage
   (acknowledging free code signing provided by SignPath.io and the certificate by SignPath Foundation,
   along with team roles and privacy disclosures).

### Open question: third-party files without upstream signatures

SignPath Foundation's published terms (`https://signpath.org/terms`) state that projects may include
standard **System Libraries** (as defined in GPL v3 section 1) in signed packages, but prohibit using a
SignPath Foundation project certificate to directly sign third-party / upstream binaries that their
publishers did not sign (because the SignPath Foundation certificate identifies SignPath Foundation as
the publisher of code built from *this* repository).

In the current v3.0.3 build, inspection with `pe_signing.py` shows that 117 of the 119 PE files under
`DownloadSorter/` (Microsoft CRT/API-sets, Python Software Foundation `.dll`/`.pyd`, The Qt Company
PySide6 `.dll`/`.pyd`, Oracle) are already validly signed by their upstream publishers; only
`DownloadSorter.exe` and `DownloadSorter-cli.exe` (compiled from this repository) are unsigned.

- **Open question to resolve before enabling SignPath:** If a future dependency wheel (for example, a
  compiled C extension in a future package) ships an unsigned `.dll` or `.pyd`, confirm with SignPath
  Foundation during onboarding whether signing that file under the project's policy is permitted, or
  whether those dependencies must be replaced/excluded or signed with an organization-owned certificate
  instead.

## Alternative: Microsoft Artifact Signing (Trusted Signing)

If SignPath Foundation's third-party policy blocks a needed dependency—or if you prefer an
organization-owned publisher certificate (~$10/month for an individual/organization profile via Azure
Microsoft Artifact Signing, formerly Trusted Signing)—the two-stage build requires no structural rewrite:

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
  `.exe`, `.dll`, or `.pyd` file path that Smart App Control refused to load.
- **Reputation ramp after signing:** Even after Download Sorter and every bundled binary carry valid
  Authenticode signatures, Smart App Control can still block a newly signed binary from a new publisher
  (because Microsoft's Intelligent Security Graph weighs code-signing reputation and install volume)
  until enough users install and run the signed build.

## Other distribution steps

4. **Winget (templates in `packaging/winget/`).** Submit the manifests after a release is published.
5. **False-positive reports.** Norton and Microsoft reports are sent by hand through each vendor's
   portal after a release, with the published setup file.
