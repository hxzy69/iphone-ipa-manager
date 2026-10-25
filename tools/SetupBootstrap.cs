using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Windows.Forms;

// This launcher uses the current user's token and never requests elevation.
internal static class SetupBootstrap
{
    [STAThread]
    private static int Main(string[] arguments)
    {
        string temporary = Path.GetFullPath(Path.GetTempPath());
        string folder = Path.Combine(temporary, "IPAManagerSetup-" + Guid.NewGuid().ToString("N"));
        try
        {
            byte[] bytes;
            Assembly assembly = Assembly.GetExecutingAssembly();
            using (Stream stream = assembly.GetManifestResourceStream("payload.zip"))
            using (MemoryStream output = new MemoryStream())
            {
                stream.CopyTo(output);
                bytes = output.ToArray();
            }
            string expected;
            using (StreamReader reader = new StreamReader(assembly.GetManifestResourceStream("payload.sha256")))
                expected = reader.ReadToEnd().Trim();
            using (SHA256 sha = SHA256.Create())
                if (BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant() != expected)
                    throw new InvalidDataException("The setup download is damaged. Download a fresh copy.");
            Directory.CreateDirectory(folder);
            using (ZipArchive archive = new ZipArchive(new MemoryStream(bytes), ZipArchiveMode.Read))
            {
                if (archive.Entries.Count > 25000 || archive.Entries.Sum(e => e.Length) > 512L * 1024 * 1024)
                    throw new InvalidDataException("The setup payload exceeds its supported size.");
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string target = Path.GetFullPath(Path.Combine(folder, entry.FullName));
                    if (!target.StartsWith(folder + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                        || entry.FullName.Contains(":") || entry.FullName.Contains("\\"))
                        throw new InvalidDataException("The setup payload contains an unsafe path.");
                    if (entry.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    entry.ExtractToFile(target, false);
                }
            }
            ProcessStartInfo start = new ProcessStartInfo(Path.Combine(folder, "iPhoneIPAManager.exe"));
            bool verifyOnly = arguments.Length == 2 && arguments[0] == "--verify-payload";
            start.Arguments = verifyOnly
                ? "--self-test --report-file \"" + Path.Combine(folder, "setup-check.json") + "\""
                : "--desktop-setup \"" + folder + "\"";
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["PYINSTALLER_RESET_ENVIRONMENT"] = "1";
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                if (verifyOnly && File.Exists(Path.Combine(folder, "setup-check.json")))
                    File.Copy(Path.Combine(folder, "setup-check.json"), Path.GetFullPath(arguments[1]), true);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "iPhone IPA Manager setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            // The target is a fresh, fixed-prefix child of this user's temp folder.
            string resolved = Path.GetFullPath(folder);
            if (resolved.StartsWith(temporary, StringComparison.OrdinalIgnoreCase)
                && Path.GetDirectoryName(resolved).TrimEnd('\\') == temporary.TrimEnd('\\')
                && Path.GetFileName(resolved).StartsWith("IPAManagerSetup-", StringComparison.Ordinal))
            {
                try { if (Directory.Exists(resolved)) Directory.Delete(resolved, true); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }
    }
}
