"""Single-file Windows setup executable builder and icon generator for Download Sorter.

Provides:
- generate_app_icon: Generates multi-layer Windows .ico via Pillow matching the app's theme.
- find_csc_compiler: Locates the native Windows C# compiler csc.exe.
- find_iscc_compiler: Locates Inno Setup ISCC.exe if installed.
- build_csharp_installer: Compiles a standalone dark-themed WinForms setup executable embedding payload.zip.
- build_setup_exe: High-level orchestrator producing dist/DownloadSorter-Setup-v{VERSION}-windows-x64.exe.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageDraw

import about
from suite_theme import ACCENTS, COLORS

REPO_ROOT = Path(__file__).resolve().parent

# Color palette derived from canonical suite_theme tokens
PALETTE_MINT_RGB = (105, 230, 173)     # ACCENTS["downloadSorter"] = #69E6AD
PALETTE_SLATE_RGB = (23, 27, 36)       # COLORS["surfaceRaised"] = #171B24
PALETTE_CANVAS_RGB = (11, 13, 18)      # COLORS["canvas"] = #0B0D12
PALETTE_TEXT_RGB = (237, 241, 248)     # COLORS["text"] = #EDF1F8
PALETTE_MUTED_RGB = (139, 148, 167)    # COLORS["textMuted"] = #8B94A7
PALETTE_FAINT_RGB = (101, 112, 132)    # COLORS["textFaint"] = #657084
PALETTE_BORDER_RGB = (42, 48, 59)      # COLORS["border"] = #2A303B
PALETTE_CRITICAL_RGB = (250, 113, 131) # COLORS["critical"] = #FA7183

ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def generate_app_icon(output_path: Path | str | None = None) -> Path:
    """Generate multi-layer Windows .ico icon matching suite theme mint & slate palette.

    Produces layers: 16x16, 32x32, 48x48, 64x64, 128x128, 256x256.
    Uses Pillow Image & ImageDraw.
    """
    out = Path(output_path) if output_path is not None else (REPO_ROOT / "DownloadSorter.ico")
    out.parent.mkdir(parents=True, exist_ok=True)

    base = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)

    slate = PALETTE_SLATE_RGB + (255,)
    mint = PALETTE_MINT_RGB + (255,)

    # Folder tab
    draw.rounded_rectangle((24, 44, 112, 88), radius=12, fill=mint)

    # Folder body
    draw.rounded_rectangle((24, 72, 232, 216), radius=24, fill=slate, outline=mint, width=8)

    # Downward-pointing arrow inside folder body
    # Arrow stem
    draw.rectangle((112, 96, 144, 144), fill=mint)
    # Arrow head
    draw.polygon([(128, 188), (84, 140), (172, 140)], fill=mint)

    # Save multi-layer .ico
    base.save(out, format="ICO", sizes=ICO_SIZES)
    return out


def find_csc_compiler() -> Path | None:
    """Discover native Windows C# compiler csc.exe."""
    candidates = [
        Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c

    # System PATH lookup
    csc_which = shutil.which("csc.exe") or shutil.which("csc")
    if csc_which:
        p = Path(csc_which)
        if p.exists():
            return p

    return None


def find_iscc_compiler() -> Path | None:
    """Check for Inno Setup compiler ISCC.exe in PATH and standard locations."""
    iscc_which = shutil.which("ISCC.exe") or shutil.which("iscc")
    if iscc_which:
        p = Path(iscc_which)
        if p.exists():
            return p

    standard_dirs = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 5" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in standard_dirs:
        if candidate.exists():
            return candidate

    return None


def _create_payload_zip(dist_dir: Path, zip_path: Path) -> Path:
    """Create payload.zip archive from directory."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in dist_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(dist_dir)
                zf.write(file, arcname)
    return zip_path


def _generate_csharp_source(version: str, author: str, credit: str, repo_url: str, app_name: str) -> str:
    """Generate C# WinForms single-file installer source code."""
    # Build C# source using string replacement to avoid f-string escaping conflicts
    source_template = r"""using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

namespace DownloadSorterInstaller
{
    internal static class Program
    {
        private const string AppName = "__APP_NAME__";
        private const string AppVersion = "__APP_VERSION__";
        private const string PublisherName = "__PUBLISHER_NAME__";
        private const string RepoUrl = "__REPO_URL__";
        private const string CreditText = "__CREDIT_TEXT__";

        [STAThread]
        private static int Main(string[] args)
        {
            bool silent = false;
            bool update = false;
            bool noShortcuts = false;
            string customDir = null;

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i].Trim();
                // /S and /D= are the documented options; /SILENT, /VERYSILENT and
                // /DIR= are accepted too, for people used to Inno Setup installers.
                if (string.Equals(a, "/S", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "-S", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "/silent", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "/verysilent", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "--silent", StringComparison.OrdinalIgnoreCase))
                {
                    silent = true;
                }
                else if (string.Equals(a, "/UPDATE", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(a, "--update", StringComparison.OrdinalIgnoreCase))
                {
                    // Started by the app's own updater: keep the user's shortcuts as
                    // they are, and open Download Sorter again afterwards.
                    update = true;
                    silent = true;
                }
                else if (string.Equals(a, "/NO-SHORTCUTS", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(a, "--no-shortcuts", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(a, "/noshortcuts", StringComparison.OrdinalIgnoreCase))
                {
                    noShortcuts = true;
                }
                else if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase) ||
                         a.StartsWith("-D=", StringComparison.OrdinalIgnoreCase) ||
                         a.StartsWith("/dir=", StringComparison.OrdinalIgnoreCase))
                {
                    int eqIndex = a.IndexOf('=');
                    customDir = a.Substring(eqIndex + 1).Trim().Trim('"');
                }
                else if ((string.Equals(a, "/D", StringComparison.OrdinalIgnoreCase) ||
                          string.Equals(a, "-D", StringComparison.OrdinalIgnoreCase)) && i + 1 < args.Length)
                {
                    customDir = args[++i].Trim().Trim('"');
                }
            }

            string targetDir = !string.IsNullOrEmpty(customDir)
                ? Path.GetFullPath(customDir)
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "DownloadSorter");

            if (silent)
            {
                try
                {
                    StopRunningApp(targetDir);
                    if (update)
                    {
                        string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                        bool hasDesktop = !noShortcuts && File.Exists(Path.Combine(desktopDir, "Download Sorter.lnk"));
                        string smPrograms = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs");
                        bool hasStartMenu = !noShortcuts && File.Exists(Path.Combine(smPrograms, "Download Sorter.lnk"));
                        PerformInstall(targetDir, hasDesktop, hasStartMenu);
                        Relaunch(targetDir);
                    }
                    else
                    {
                        PerformInstall(targetDir, !noShortcuts, !noShortcuts);
                    }
                    return 0;
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(ex.ToString());
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var form = new InstallerForm(targetDir);
            Application.Run(form);
            return form.ExitCode;
        }

        // ---- Closing a running copy before its files are replaced ----------
        // Windows will not overwrite a program that is running, and the background
        // sorter (tray) almost always is. Without this an update failed halfway and
        // left a mix of old and new files.

        public static void StopRunningApp(string targetDir)
        {
            string exe = Path.Combine(targetDir, "DownloadSorter.exe");
            if (FindRunning(targetDir).Count == 0)
            {
                return;
            }

            // 1. Ask nicely: the app closes the same way as its tray "Quit" item.
            if (File.Exists(exe))
            {
                try
                {
                    var psi = new ProcessStartInfo(exe, "--quit");
                    psi.UseShellExecute = false;
                    psi.CreateNoWindow = true;
                    using (Process asker = Process.Start(psi))
                    {
                        if (asker != null)
                        {
                            asker.WaitForExit(15000);
                        }
                    }
                }
                catch { }
            }

            // 2. Give it up to 10 seconds to finish what it is doing.
            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            while (DateTime.UtcNow < deadline)
            {
                if (FindRunning(targetDir).Count == 0)
                {
                    return;
                }
                System.Threading.Thread.Sleep(250);
            }

            // 3. Close whatever is still running from this folder.
            foreach (Process left in FindRunning(targetDir))
            {
                try
                {
                    left.Kill();
                    left.WaitForExit(5000);
                }
                catch { }
            }
        }

        private static List<Process> FindRunning(string targetDir)
        {
            var found = new List<Process>();
            string root = Path.GetFullPath(targetDir).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            int self = Process.GetCurrentProcess().Id;
            foreach (Process proc in Process.GetProcesses())
            {
                if (proc.Id == self)
                {
                    continue;
                }
                try
                {
                    if (!proc.ProcessName.StartsWith("DownloadSorter", StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                    string path = proc.MainModule.FileName;
                    if (path != null && path.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                    {
                        found.Add(proc);
                    }
                }
                catch { }
            }
            return found;
        }

        private static FileStream CreateWithRetry(string destPath)
        {
            // A file can stay locked for a moment after its program exits
            // (antivirus scanning it, Windows releasing the image).
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    return new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None);
                }
                catch (IOException)
                {
                    if (attempt >= 20)
                    {
                        throw new IOException(
                            "Download Sorter is still running, so its files could not be replaced. " +
                            "Right-click its icon near the clock, choose Quit, and run this installer again.");
                    }
                    System.Threading.Thread.Sleep(500);
                }
                catch (UnauthorizedAccessException)
                {
                    if (attempt >= 20)
                    {
                        throw;
                    }
                    System.Threading.Thread.Sleep(500);
                }
            }
        }

        private static void Relaunch(string targetDir)
        {
            string exe = Path.Combine(targetDir, "DownloadSorter.exe");
            if (!File.Exists(exe))
            {
                return;
            }
            try { Process.Start(exe); } catch { }            // the background sorter
            try { Process.Start(exe, "--home"); } catch { }  // and the window, so the user sees it is back
        }

        public static void PerformInstall(string targetDir, bool createDesktop, bool createStartMenu)
        {
            if (!Directory.Exists(targetDir))
            {
                Directory.CreateDirectory(targetDir);
            }

            var asm = Assembly.GetExecutingAssembly();
            Stream s = null;
            foreach (string n in asm.GetManifestResourceNames())
            {
                if (n.EndsWith("payload.zip", StringComparison.OrdinalIgnoreCase))
                {
                    s = asm.GetManifestResourceStream(n);
                    break;
                }
            }
            if (s == null)
            {
                s = asm.GetManifestResourceStream("payload.zip");
            }
            if (s == null)
            {
                throw new FileNotFoundException("Embedded payload.zip resource not found.");
            }

            string tempZip = Path.Combine(Path.GetTempPath(), "DS_Payload_" + Guid.NewGuid().ToString("N") + ".zip");
            try
            {
                using (var fs = new FileStream(tempZip, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    byte[] buf = new byte[65536];
                    int r;
                    while ((r = s.Read(buf, 0, buf.Length)) > 0)
                    {
                        fs.Write(buf, 0, r);
                    }
                }

                using (var zip = ZipFile.OpenRead(tempZip))
                {
                    foreach (var entry in zip.Entries)
                    {
                        if (string.IsNullOrEmpty(entry.Name))
                        {
                            continue;
                        }
                        string relPath = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string destPath = Path.Combine(targetDir, relPath);
                        string destDir = Path.GetDirectoryName(destPath);
                        if (!string.IsNullOrEmpty(destDir) && !Directory.Exists(destDir))
                        {
                            Directory.CreateDirectory(destDir);
                        }
                        using (var es = entry.Open())
                        using (var df = CreateWithRetry(destPath))
                        {
                            byte[] b = new byte[65536];
                            int read;
                            while ((read = es.Read(b, 0, b.Length)) > 0)
                            {
                                df.Write(b, 0, read);
                            }
                        }
                    }
                }
            }
            finally
            {
                try { if (File.Exists(tempZip)) File.Delete(tempZip); } catch { }
            }

            CreateShortcuts(targetDir, createDesktop, createStartMenu);
            RegisterUninstall(targetDir);
        }

        private static void CreateShortcuts(string targetDir, bool desktop, bool startMenu)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                dynamic shell = Activator.CreateInstance(shellType);
                string exeTarget = Path.Combine(targetDir, "DownloadSorter.exe");

                if (desktop)
                {
                    try
                    {
                        string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                        dynamic sc = shell.CreateShortcut(Path.Combine(desktopDir, "Download Sorter.lnk"));
                        sc.TargetPath = exeTarget;
                        sc.WorkingDirectory = targetDir;
                        if (File.Exists(exeTarget)) sc.IconLocation = exeTarget + ",0";
                        sc.Description = "Download Sorter - Local automated file organizer";
                        sc.Save();
                    }
                    catch { }
                }

                if (startMenu)
                {
                    try
                    {
                        string smPrograms = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs");
                        if (Directory.Exists(smPrograms))
                        {
                            dynamic sc = shell.CreateShortcut(Path.Combine(smPrograms, "Download Sorter.lnk"));
                            sc.TargetPath = exeTarget;
                            sc.WorkingDirectory = targetDir;
                            if (File.Exists(exeTarget)) sc.IconLocation = exeTarget + ",0";
                            sc.Description = "Download Sorter - Local automated file organizer";
                            sc.Save();
                        }
                    }
                    catch { }
                }
            }
            catch { }
        }

        private static void RegisterUninstall(string targetDir)
        {
            try
            {
                string keyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\DownloadSorter";
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(keyPath))
                {
                    if (key != null)
                    {
                        key.SetValue("DisplayName", AppName);
                        key.SetValue("DisplayVersion", AppVersion);
                        key.SetValue("Publisher", PublisherName);
                        key.SetValue("InstallLocation", targetDir);
                        string exePath = Path.Combine(targetDir, "DownloadSorter.exe");
                        key.SetValue("DisplayIcon", exePath + ",0");
                        string uninstPs1 = Path.Combine(targetDir, "uninstall.ps1");
                        key.SetValue("UninstallString", "powershell.exe -ExecutionPolicy Bypass -File \"" + uninstPs1 + "\"");
                        key.SetValue("QuietUninstallString", "powershell.exe -ExecutionPolicy Bypass -File \"" + uninstPs1 + "\" -Silent");
                        key.SetValue("URLInfoAbout", RepoUrl);
                        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                        try
                        {
                            long totalBytes = 0;
                            if (Directory.Exists(targetDir))
                            {
                                foreach (string f in Directory.GetFiles(targetDir, "*.*", SearchOption.AllDirectories))
                                {
                                    totalBytes += new FileInfo(f).Length;
                                }
                            }
                            key.SetValue("EstimatedSize", (int)(totalBytes / 1024), RegistryValueKind.DWord);
                        }
                        catch { }
                    }
                }
            }
            catch { }
        }

        internal class InstallerForm : Form
        {
            private readonly string _targetDir;
            public int ExitCode { get; private set; }

            private Label _lblStatus;
            private ProgressBar _progressBar;
            private CheckBox _chkDesktop;
            private CheckBox _chkStartMenu;
            private CheckBox _chkLaunch;
            private Button _btnInstall;
            private Button _btnCancel;
            private bool _isFinished;

            public InstallerForm(string targetDir)
            {
                _targetDir = targetDir;
                ExitCode = 0;
                InitializeComponents();
            }

            private void InitializeComponents()
            {
                this.Text = "Download Sorter Setup (v" + AppVersion + ")";
                this.ClientSize = new Size(500, 420);
                this.StartPosition = FormStartPosition.CenterScreen;
                this.FormBorderStyle = FormBorderStyle.FixedDialog;
                this.MaximizeBox = false;
                this.MinimizeBox = false;
                this.BackColor = Color.FromArgb(11, 13, 18);
                this.ForeColor = Color.FromArgb(237, 241, 248);
                this.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);

                var headerPanel = new Panel
                {
                    Location = new Point(0, 0),
                    Size = new Size(500, 85),
                    BackColor = Color.FromArgb(23, 27, 36)
                };

                var lblTitle = new Label
                {
                    Text = "Download Sorter Setup",
                    Font = new Font("Segoe UI", 14f, FontStyle.Bold),
                    ForeColor = Color.FromArgb(237, 241, 248),
                    Location = new Point(24, 16),
                    AutoSize = true
                };

                var lblSub = new Label
                {
                    Text = "Automated local-first file organizer for Windows",
                    Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                    ForeColor = Color.FromArgb(139, 148, 167),
                    Location = new Point(25, 48),
                    AutoSize = true
                };

                headerPanel.Controls.Add(lblTitle);
                headerPanel.Controls.Add(lblSub);
                this.Controls.Add(headerPanel);

                var lblDestHeader = new Label
                {
                    Text = "Installation Target Folder:",
                    Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                    ForeColor = Color.FromArgb(237, 241, 248),
                    Location = new Point(24, 105),
                    AutoSize = true
                };
                this.Controls.Add(lblDestHeader);

                var lblDestPath = new Label
                {
                    Text = _targetDir,
                    Font = new Font("Segoe UI", 8.5f, FontStyle.Regular),
                    ForeColor = Color.FromArgb(139, 148, 167),
                    Location = new Point(24, 128),
                    Size = new Size(450, 36)
                };
                this.Controls.Add(lblDestPath);

                _chkDesktop = new CheckBox
                {
                    Text = "Create Desktop shortcut",
                    Checked = true,
                    Location = new Point(24, 175),
                    AutoSize = true,
                    ForeColor = Color.FromArgb(237, 241, 248)
                };
                this.Controls.Add(_chkDesktop);

                _chkStartMenu = new CheckBox
                {
                    Text = "Create Start Menu shortcut",
                    Checked = true,
                    Location = new Point(24, 205),
                    AutoSize = true,
                    ForeColor = Color.FromArgb(237, 241, 248)
                };
                this.Controls.Add(_chkStartMenu);

                _chkLaunch = new CheckBox
                {
                    Text = "Launch Download Sorter and open Welcome Wizard",
                    Checked = true,
                    Location = new Point(24, 235),
                    AutoSize = true,
                    ForeColor = Color.FromArgb(237, 241, 248)
                };
                this.Controls.Add(_chkLaunch);

                var lblCredit = new Label
                {
                    Text = CreditText,
                    Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                    ForeColor = Color.FromArgb(101, 112, 132),
                    Location = new Point(24, 275),
                    AutoSize = true
                };
                this.Controls.Add(lblCredit);

                _lblStatus = new Label
                {
                    Text = "Ready to install. Click Install to begin.",
                    Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                    ForeColor = Color.FromArgb(139, 148, 167),
                    Location = new Point(24, 310),
                    Size = new Size(450, 20)
                };
                this.Controls.Add(_lblStatus);

                _progressBar = new ProgressBar
                {
                    Location = new Point(24, 335),
                    Size = new Size(452, 18),
                    Style = ProgressBarStyle.Marquee,
                    MarqueeAnimationSpeed = 30,
                    Visible = false
                };
                this.Controls.Add(_progressBar);

                _btnCancel = new Button
                {
                    Text = "Cancel",
                    Location = new Point(300, 370),
                    Size = new Size(85, 32),
                    BackColor = Color.FromArgb(23, 27, 36),
                    ForeColor = Color.FromArgb(237, 241, 248),
                    FlatStyle = FlatStyle.Flat
                };
                _btnCancel.FlatAppearance.BorderColor = Color.FromArgb(42, 48, 59);
                _btnCancel.Click += (s, e) => {
                    ExitCode = 1;
                    this.Close();
                };
                this.Controls.Add(_btnCancel);

                _btnInstall = new Button
                {
                    Text = "Install",
                    Location = new Point(393, 370),
                    Size = new Size(85, 32),
                    BackColor = Color.FromArgb(105, 230, 173),
                    ForeColor = Color.FromArgb(11, 13, 18),
                    FlatStyle = FlatStyle.Flat,
                    Font = new Font("Segoe UI", 9.5f, FontStyle.Bold)
                };
                _btnInstall.FlatAppearance.BorderColor = Color.FromArgb(105, 230, 173);
                _btnInstall.Click += OnInstallClick;
                this.Controls.Add(_btnInstall);
            }

            private void OnInstallClick(object sender, EventArgs e)
            {
                if (_isFinished)
                {
                    if (_chkLaunch.Checked)
                    {
                        string exePath = Path.Combine(_targetDir, "DownloadSorter.exe");
                        if (File.Exists(exePath))
                        {
                            try
                            {
                                Process.Start(exePath, "--welcome");
                            }
                            catch { }
                        }
                    }
                    this.Close();
                    return;
                }

                _btnInstall.Enabled = false;
                _btnCancel.Enabled = false;
                _chkDesktop.Enabled = false;
                _chkStartMenu.Enabled = false;
                _chkLaunch.Enabled = false;
                _progressBar.Visible = true;
                _lblStatus.Text = "Closing Download Sorter if it is running...";
                Application.DoEvents();

                try
                {
                    StopRunningApp(_targetDir);
                    _lblStatus.Text = "Extracting files and setting up shortcuts...";
                    Application.DoEvents();
                    PerformInstall(_targetDir, _chkDesktop.Checked, _chkStartMenu.Checked);
                    _progressBar.Visible = false;
                    _lblStatus.Text = "Installation completed successfully!";
                    _lblStatus.ForeColor = Color.FromArgb(105, 230, 173);
                    _btnInstall.Text = "Finish";
                    _btnInstall.Enabled = true;
                    _btnCancel.Visible = false;
                    _isFinished = true;
                }
                catch (Exception ex)
                {
                    _progressBar.Visible = false;
                    _lblStatus.Text = "Installation failed: " + ex.Message;
                    _lblStatus.ForeColor = Color.FromArgb(250, 113, 131);
                    _btnInstall.Enabled = true;
                    _btnCancel.Enabled = true;
                }
            }
        }
    }
}
"""
    replacements = {
        "__APP_NAME__": app_name,
        "__APP_VERSION__": version,
        "__PUBLISHER_NAME__": author,
        "__REPO_URL__": repo_url,
        "__CREDIT_TEXT__": credit,
    }
    for placeholder, val in replacements.items():
        source_template = source_template.replace(placeholder, val)
    return source_template


def build_csharp_installer(
    output_exe: Path | str,
    dist_dir: Path | str,
    version: str | None = None,
    icon_path: Path | str | None = None,
) -> Path:
    """Compile a single-file Windows setup executable using csc.exe."""
    csc = find_csc_compiler()
    if not csc:
        raise RuntimeError("Native Windows C# compiler csc.exe was not found.")

    out_exe = Path(output_exe).resolve()
    out_exe.parent.mkdir(parents=True, exist_ok=True)
    dist = Path(dist_dir).resolve()
    if not dist.exists():
        raise FileNotFoundError(f"Distribution directory does not exist: {dist}")

    ver = version or getattr(about, "__version__", "2.2.0")
    author_str = getattr(about, "AUTHOR", "")
    credit_str = getattr(about, "CREDIT", "")
    repo_url_str = getattr(about, "REPO_URL", "")
    app_name_str = getattr(about, "APP_NAME", "Download Sorter")

    # Icon handling
    ico = Path(icon_path).resolve() if icon_path else (REPO_ROOT / "DownloadSorter.ico")
    if not ico.exists():
        ico = generate_app_icon(ico)

    temp_dir = Path(tempfile.mkdtemp(prefix="ds_installer_"))
    try:
        payload_zip = temp_dir / "payload.zip"
        _create_payload_zip(dist, payload_zip)

        cs_source_file = temp_dir / "Installer.cs"
        cs_code = _generate_csharp_source(
            version=ver,
            author=author_str,
            credit=credit_str,
            repo_url=repo_url_str,
            app_name=app_name_str,
        )
        cs_source_file.write_text(cs_code, encoding="utf-8")

        cmd = [
            str(csc),
            "/nologo",
            "/target:winexe",
            "/optimize+",
            f"/resource:{payload_zip},payload.zip",
            "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll",
            "/r:System.IO.Compression.dll",
            "/r:System.IO.Compression.FileSystem.dll",
            "/r:Microsoft.CSharp.dll",
        ]
        if ico.exists():
            cmd.append(f"/win32icon:{ico}")
        cmd.extend([
            f"/out:{out_exe}",
            str(cs_source_file),
        ])

        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(temp_dir))
        if res.returncode != 0:
            raise RuntimeError(f"csc.exe compilation failed (code {res.returncode}):\n{res.stdout}\n{res.stderr}")

        if not out_exe.exists():
            raise FileNotFoundError(f"Setup executable was not produced at {out_exe}")

        return out_exe
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def build_setup_exe(
    dist_dir: Path | str | None = None,
    version: str | None = None,
    output_exe: Path | str | None = None,
    use_inno: bool = False,
) -> Path:
    """High-level builder producing DownloadSorter-Setup-v{VERSION}-windows-x64.exe.

    The single-file installer is always built unless ``use_inno`` is set, so the
    setup file (and its documented /S and /D= options) does not depend on
    whether Inno Setup happens to be installed on the build machine.
    """
    ver = version or getattr(about, "__version__", "2.2.0")
    dist = Path(dist_dir) if dist_dir else (REPO_ROOT / "dist" / "DownloadSorter")
    target_out_dir = dist.parent if dist.name == "DownloadSorter" else (REPO_ROOT / "dist")
    out = Path(output_exe) if output_exe else (target_out_dir / f"DownloadSorter-Setup-v{ver}-windows-x64.exe")

    # Ensure application icon is generated
    ico_path = REPO_ROOT / "DownloadSorter.ico"
    if not ico_path.exists():
        generate_app_icon(ico_path)

    # Optional Inno Setup compilation if ISCC is available and installer.iss exists
    iscc = find_iscc_compiler() if use_inno else None
    iss_file = REPO_ROOT / "installer.iss"
    if iscc and iss_file.exists():
        try:
            cmd = [str(iscc), str(iss_file)]
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
            if res.returncode == 0 and out.exists():
                return out
        except Exception:
            pass

    # Standard fallback: native Windows C# compiler
    return build_csharp_installer(
        output_exe=out,
        dist_dir=dist,
        version=ver,
        icon_path=ico_path,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=f"{about.APP_NAME} Installer Builder")
    parser.add_argument("--icon-only", action="store_true", help="Generate application icon only")
    parser.add_argument("--version", "-V", action="store_true", help="Print version and credit")
    args = parser.parse_args()

    if args.version:
        print(f"{about.APP_NAME} Installer Builder v{about.__version__} - {about.CREDIT}")
        sys.exit(0)

    if args.icon_only:
        p = generate_app_icon()
        print(f"Generated application icon: {p}")
        sys.exit(0)

    setup_exe = build_setup_exe()
    print(f"Built setup executable: {setup_exe}")
