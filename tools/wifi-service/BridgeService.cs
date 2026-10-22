using System;
using System.IO;
using System.IO.Pipes;
using System.Diagnostics;
using System.ServiceProcess;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;

// Fixed-purpose privileged broker. No caller-supplied paths, arguments or commands.
public sealed class BridgeService : ServiceBase {
    volatile bool stopping;
    NamedPipeServerStream pipe;
    Thread thread;
    public BridgeService() { ServiceName = "iPhoneIPAManagerBridge"; }
    public static void Main() { ServiceBase.Run(new BridgeService()); }
    protected override void OnStart(string[] args) {
        thread = new Thread(Serve); thread.IsBackground = true; thread.Start();
    }
    protected override void OnStop() {
        stopping = true;
        if (pipe != null) pipe.Dispose();
        if (thread != null) thread.Join(45000);
    }
    void Serve() {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string sid = File.ReadAllText(Path.Combine(root, "owner.sid")).Trim();
        // Recovery after a service crash: only our protected companion may be terminated.
        foreach (var old in Process.GetProcessesByName("netmuxd")) {
            try { if (String.Equals(old.MainModule.FileName, Path.Combine(root, "netmuxd.exe"), StringComparison.OrdinalIgnoreCase)) { old.Kill(); old.WaitForExit(8000); } } catch { }
        }
        if (File.Exists(Path.Combine(root, "restore.pending"))) {
            if (File.ReadAllText(Path.Combine(root, "restore.pending")) == "1") using (var apple = new ServiceController("Apple Mobile Device Service")) {
                apple.Refresh(); if (apple.Status == ServiceControllerStatus.Stopped) apple.Start();
                apple.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(25));
            }
            File.Delete(Path.Combine(root, "restore.pending"));
        }
        while (!stopping) {
            bool restore = false;
            Process bridge = null;
            StreamWriter writer = null;
            try {
                var acl = new PipeSecurity();
                acl.SetAccessRuleProtection(true, false);
                acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
                acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(sid), PipeAccessRights.ReadWrite, AccessControlType.Allow));
                acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
                pipe = new NamedPipeServerStream("iPhoneIPAManagerBridge", PipeDirection.InOut, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 4096, 4096, acl);
                pipe.WaitForConnection();
                var reader = new StreamReader(pipe);
                writer = new StreamWriter(pipe); writer.AutoFlush = true;
                // Read a single fixed request with a deadline; a stalled client cannot hold the broker forever.
                var request = reader.ReadLineAsync();
                if (!request.Wait(5000) || request.Result != "START") throw new Exception("Invalid bridge request");
                using (var apple = new ServiceController("Apple Mobile Device Service")) {
                    apple.Refresh();
                    if (apple.Status != ServiceControllerStatus.Running && apple.Status != ServiceControllerStatus.Stopped)
                        throw new Exception("Apple service is changing state");
                    if (apple.Status == ServiceControllerStatus.Running) {
                        restore = true; File.WriteAllText(Path.Combine(root, "restore.pending"), "1");
                        apple.Stop(); apple.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(25));
                    }
                }
                // A previous abrupt termination may have interrupted restoration.
                File.WriteAllText(Path.Combine(root, "restore.pending"), restore ? "1" : "0");
                bridge = Process.Start(new ProcessStartInfo(Path.Combine(root, "netmuxd.exe"), "--disable-usb --host 127.0.0.1 --port 27015") {
                    UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = root
                });
                writer.WriteLine("RUNNING");
                var end = reader.ReadLineAsync();
                while (!stopping && !end.Wait(500)) {
                    if (bridge.HasExited) throw new Exception("Wi-Fi bridge exited");
                }
                // EOF (controller death) also releases the lease and restores Apple service.
            } catch (Exception) {
                try { if (writer != null) writer.WriteLine("ERROR"); } catch { }
            } finally {
                bool restored = true;
                try { if (bridge != null && !bridge.HasExited) { bridge.Kill(); bridge.WaitForExit(8000); } } catch { restored = false; }
                try {
                    if (restore) using (var apple = new ServiceController("Apple Mobile Device Service")) {
                        apple.Refresh(); if (apple.Status != ServiceControllerStatus.Running) apple.Start();
                        apple.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(25));
                    }
                } catch { restored = false; }
                if (restored) { try { File.Delete(Path.Combine(root, "restore.pending")); } catch { } }
                try { if (writer != null) writer.WriteLine(restored ? "STOPPED" : "RESTORE_ERROR"); } catch { }
                if (pipe != null) { pipe.Dispose(); pipe = null; }
                // Fail closed; SCM restarts us and retries recovery before accepting another lease.
                if (!restored) Environment.Exit(1);
            }
        }
    }
}
