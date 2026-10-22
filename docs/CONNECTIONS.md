# USB, Wi-Fi and remote access

## USB first

Install Apple Devices or iTunes with Apple Mobile Device support. Connect a data
cable, unlock the phone, approve Trust, and verify that Apple's app sees it. In IPA
Manager, select that phone. Selecting a serial number does not itself create the
Apple trust relationship. Enable Developer Mode on iOS if the installed app asks
for it, and approve the developer profile where iOS requires it.

## Local Wi-Fi

Enable **Show this iPhone when on Wi-Fi** / Wi-Fi sync in Apple's device app while
connected by USB. Put both devices on the same network. Guest networks and client
isolation can block discovery even when both devices have internet access.
Windows may separately ask about firewall network access. The application does
not dismiss that request or change firewall permissions automatically.

The local bridge temporarily manages Apple's USB service through a narrowly
scoped Windows service, then restores it. The application does not request
elevation. To install that service, a local administrator must deliberately open
an elevated terminal. First obtain the intended ordinary user's SID from that
user's normal terminal with `whoami /user`; then run:

```powershell
& 'C:\Apps\IPAManager\iPhoneIPAManager.exe' --install-bridge 'S-1-5-21-REPLACE-WITH-OWNER-SID'
```

Replace the path and SID. The service accepts only its fixed start/stop protocol,
restricts the pipe to that owner and SYSTEM, and stores binaries in a protected
Program Files folder. This step is optional for USB and is not performed by the
build or on first launch. Do not install or replace it while another run is active.

## Tailscale and RemotePairing

1. Install official Tailscale clients on PC and iPhone; sign into the same tailnet.
2. Connect USB, keep the iPhone unlocked on home Wi-Fi, and enable Apple Wi-Fi sync.
3. Click **Remote setup**. With multiple iPhones in the tailnet, enter the intended
   phone's Tailscale IPv4 address. Otherwise leave it blank for discovery.
4. Wait for **Remote tunnel ready**. Configure startup in Discord setup if desired;
   it registers ordinary-user tasks at Windows sign-in.
5. Test a refresh while at home before testing roaming to cellular data.

Remote setup verifies the existing trusted device identity, performs RemotePairing,
discovers the listener and authenticates it over Tailscale before saving settings.
It does not open router ports or install VPN software automatically. Optional CLI:

```powershell
python ipa_manager.py --setup-remote --phone-ip 100.64.0.10
```

If trusted LAN pairing is necessary, add `--lan-ip` with the phone's local address.
Pairing material remains in the Windows/Apple and pymobiledevice3 profile folders.
Do not share it or copy it into the repository.

## Limits that remain

- A reachable VPN peer is not the same as a reachable iOS installation service.
  A PC started after the phone has left Wi-Fi may be unable to establish a fresh
  tunnel. Keep a ready connection open before leaving; reconnect on Wi-Fi if lost.
- Roaming, iOS suspension, device restarts and VPN changes can close a tunnel.
- Full installs transfer signed archives. Remote speed has no fixed multiplier;
  profile-only renewal is smaller when its compatibility checks pass.
- Renewal needs a ready remote transport in this version, even at home. Full
  local Wi-Fi installation can work independently of that transport.
- Known rejected or expired profiles can fall back to a full install. Unknown
  write outcomes stop and need inspection. A successful profile readback is
  experimental evidence, not a substitute for testing app launches after expiry.

## Recovery

Use **Stop** or `/stop`, wait for the job to settle, and inspect `/history` or the
activity logs. Do not repeatedly restart an uncertain installation. Check phone
trust, free space, Apple account requirements, VPN connection and Apple device
software. For local Wi-Fi problems, verify that **Apple Mobile Device Service**
was restored. If an interrupted automatic job is unresolved, perform a deliberate
successful full run before expecting the renewal timer to resume.

