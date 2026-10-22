//! No windows, WebView, mouse input, clipboard, or keyboard automation.
use futures::FutureExt;
use idevice::usbmuxd::{Connection, UsbmuxdAddr, UsbmuxdConnection};
use isideload::{
    anisette::remote_v3::RemoteV3AnisetteProvider,
    auth::apple_account::{AppleAccount, TwoFactorCallbackResponse},
    dev::developer_session::DeveloperSession,
    sideload::{SideloaderBuilder, builder::MaxCertsBehavior},
    util::{callbacks::MaxCertsCallbackBox, keyring_storage::KeyringStorage},
};
use rootcause::prelude::*;
use serde::Deserialize;
use serde_json::json;
use std::{
    io::Write,
    path::{Path, PathBuf},
    time::Duration,
};

#[derive(Deserialize)]
struct Account {
    email: String,
    ipa: PathBuf,
    #[serde(default)]
    account: usize,
}
#[derive(Deserialize)]
struct Job {
    serial: String,
    transport: String,
    accounts: Vec<Account>,
    anisette_server: String,
    fast: bool,
    timeout: u64,
    #[serde(default)]
    check_only: bool,
    #[serde(default)]
    prepare_only: bool,
    #[serde(default)]
    profile_refresh: bool,
    parent_pid: u32,
    #[serde(default)]
    device_name: String,
    #[serde(default = "remote_compression_default")]
    remote_compression: u8,
}
fn remote_compression_default() -> u8 {
    9
}

#[link(name = "kernel32")]
unsafe extern "system" {
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut std::ffi::c_void;
    fn WaitForSingleObject(handle: *mut std::ffi::c_void, timeout: u32) -> u32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}
struct ParentProcess(usize);
impl ParentProcess {
    fn open(pid: u32) -> Result<Self, Report> {
        let handle = unsafe { OpenProcess(0x100000, 0, pid) };
        if handle.is_null() {
            bail!("Background controller has exited");
        }
        Ok(Self(handle as usize))
    }
    fn alive(&self) -> bool {
        unsafe { WaitForSingleObject(self.0 as *mut _, 0) == 258 }
    }
}
impl Drop for ParentProcess {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0 as *mut _);
        }
    }
}

fn canonical(value: &str) -> String {
    value
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
        .to_uppercase()
}
fn status(folder: &Path, mut value: serde_json::Value) -> Result<(), Report> {
    // Immutable lines permit progress reads while the backend is writing.
    if let Some(fields) = value.as_object_mut() {
        fields.insert(
            "timestamp_ms".into(),
            json!(
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)?
                    .as_millis()
            ),
        );
    }
    let mut file = std::fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(folder.join("backend-events.jsonl"))?;
    writeln!(file, "{value}")?;
    Ok(())
}
fn result(folder: &Path, value: serde_json::Value) -> Result<(), Report> {
    let temporary = folder.join("backend-result.tmp");
    std::fs::write(&temporary, serde_json::to_vec(&value)?)?;
    std::fs::rename(temporary, folder.join("backend-result.json"))?;
    Ok(())
}

async fn login(
    email: &str,
    server: &str,
) -> Result<isideload::sideload::sideloader::Sideloader<MaxCertsCallbackBox>, Report> {
    let password = keyring::Entry::new("iloader", email)?.get_password()?;
    let two_factor = |_params| {
        async {
        bail!("User action required: sign in through iloader to complete Apple verification, then retry background mode.");
        #[allow(unreachable_code)]
        Ok::<TwoFactorCallbackResponse, Report>(TwoFactorCallbackResponse::SubmitCode(String::new()))
    }.boxed()
    };
    let mut account = AppleAccount::builder(&email.to_lowercase())
        .anisette_provider(
            RemoteV3AnisetteProvider::default()?
                .set_serial_number("0".into())
                .set_storage(Box::new(KeyringStorage::new("iloader".into())))
                .set_url(server),
        )
        .login(&password, Box::new(two_factor))
        .await?;
    let session = DeveloperSession::from_account(&mut account).await?;
    Ok(
        SideloaderBuilder::<MaxCertsCallbackBox>::new(session, email.to_lowercase())
            .machine_name("iloader".into())
            .storage(Box::new(KeyringStorage::new("iloader".into())))
            .max_certs_behavior(MaxCertsBehavior::Error)
            .build(),
    )
}

async fn execute(job: Job, folder: &Path, completed: &mut usize) -> Result<(), Report> {
    if job.transport == "Remote" {
        return execute_remote(job, folder, completed).await;
    }
    if job.prepare_only {
        bail!("Signing export requires Remote transport");
    }
    if job.accounts.is_empty()
        || job.accounts.len() > 25
        || !matches!(job.transport.as_str(), "USB" | "Network")
    {
        bail!(
            "Background job must contain between one and 25 accounts and a USB or Network transport"
        );
    }
    for account in &job.accounts {
        if !account.ipa.is_absolute() || !account.ipa.is_file() {
            bail!("IPA file is unavailable: {}", account.ipa.display());
        }
    }
    let mut mux = UsbmuxdConnection::default().await?;
    let devices = mux.get_devices().await?;
    let matches: Vec<_> = devices
        .into_iter()
        .filter(|d| {
            canonical(&d.udid) == canonical(&job.serial)
                && matches!(
                    (&d.connection_type, job.transport.as_str()),
                    (Connection::Usb, "USB") | (Connection::Network(_), "Network")
                )
        })
        .collect();
    if matches.len() != 1 {
        bail!(
            "Saved iPhone is not uniquely connected using {}",
            job.transport
        );
    }
    let device = &matches[0];
    let provider = device.to_provider(UsbmuxdAddr::from_env_var()?, "iloader");
    let mut pair = mux.get_pair_record(&device.udid).await?;
    pair.udid = Some(device.udid.clone());
    let pairing = pair.serialize()?;
    if job.check_only {
        status(folder, json!({"stage":"connection_verified"}))?;
        return Ok(());
    }
    for (index, account) in job.accounts.into_iter().enumerate() {
        // The controller assigns stable account numbers; position-only jobs keep 1-based order.
        let number = if account.account == 0 {
            index + 1
        } else {
            account.account
        };
        status(folder, json!({"stage":"login", "account":number}))?;
        let mut signed_in = None;
        for attempt in 1..=3 {
            match tokio::time::timeout(
                Duration::from_secs(180),
                login(&account.email, &job.anisette_server),
            )
            .await
            .context("Apple login timed out")?
            {
                Ok(value) => {
                    signed_in = Some(value);
                    break;
                }
                Err(error) => {
                    let error = error.to_string();
                    if error.to_string().contains("-22406") && attempt < 3 {
                        status(
                            folder,
                            json!({"stage":"login_retry", "account":number, "attempt":attempt}),
                        )?;
                        tokio::time::sleep(Duration::from_secs(2)).await;
                    } else {
                        bail!("Account {} login: {}", number, error);
                    }
                }
            }
        }
        let mut sideloader = signed_in.ok_or_else(|| report!("Login did not complete"))?;
        sideloader.set_archive_transfer(job.fast && job.transport == "Network");
        status(folder, json!({"stage":"install", "account":number}))?;
        tokio::time::timeout(
            Duration::from_secs(job.timeout),
            sideloader.install_app(
                &provider,
                account.ipa,
                false,
                None::<fn(f32) -> std::future::Ready<()>>,
                Some(&pairing),
            ),
        )
        .await
        .context("Installation timed out; inspect the phone before retrying")??;
        drop(sideloader); // Logout: discard the in-memory authenticated session.
        *completed += 1;
        status(
            folder,
            json!({"stage":"completed_account", "account":number}),
        )?;
    }
    Ok(())
}

async fn execute_remote(job: Job, folder: &Path, completed: &mut usize) -> Result<(), Report> {
    use isideload::dev::devices::DevicesApi;
    if job.accounts.is_empty()
        || job.accounts.len() > 25
        || job.serial.is_empty()
        || job.device_name.is_empty()
    {
        bail!("Remote job requires between one and 25 accounts and a verified device identity");
    }
    if !(1..=9).contains(&job.remote_compression) {
        bail!("Invalid remote compression level");
    }
    let mut previous = 0usize;
    for (index, account) in job.accounts.into_iter().enumerate() {
        // Archive names and handshake markers must use the controller-assigned account
        // number, because installation order no longer implies which account it is.
        let number = if account.account == 0 {
            index + 1
        } else {
            account.account
        };
        if !account.ipa.is_absolute() || !account.ipa.is_file() {
            bail!("IPA file unavailable");
        }
        status(folder, json!({"stage":"login", "account":number}))?;
        let mut signed_in = None;
        for attempt in 1..=3 {
            match tokio::time::timeout(
                Duration::from_secs(180),
                login(&account.email, &job.anisette_server),
            )
            .await
            .context("Apple login timed out")?
            {
                Ok(value) => {
                    signed_in = Some(value);
                    break;
                }
                Err(error) => {
                    let error = error.to_string();
                    if error.to_string().contains("-22406") && attempt < 3 {
                        status(folder, json!({"stage":"login_retry", "account":number}))?;
                        tokio::time::sleep(Duration::from_secs(2)).await;
                    } else {
                        bail!("Account {} login: {}", number, error);
                    }
                }
            }
        }
        let mut sideloader = signed_in.ok_or_else(|| report!("Login did not complete"))?;
        let team = sideloader.get_team().await?;
        sideloader
            .get_dev_session()
            .ensure_device_registered(&team, &job.device_name, &job.serial, None)
            .await?;
        let ipa = folder.join(format!("remote-{}.ipa", number));
        if job.profile_refresh && !job.prepare_only {
            status(
                folder,
                json!({"stage":"prepare_profiles", "account":number}),
            )?;
            if isideload::sideload::refresh::export_profiles(
                sideloader.get_dev_session(),
                &team,
                &account.ipa,
                &ipa,
            )
            .await?
            {
                // A profile-only archive is never an installable IPA. The controller either
                // acknowledges verified renewal, or explicitly requests a newly signed IPA.
                std::fs::write(folder.join(format!("remote-{}.profiles", number)), b"")?;
                std::fs::write(folder.join(format!("remote-{}.ready", number)), b"")?;
                tokio::time::timeout(Duration::from_secs(job.timeout), async {
                    while !folder.join(format!("remote-{}.installed", number)).exists()
                        && !folder
                            .join(format!("remote-{}.full-install", number))
                            .exists()
                    {
                        tokio::time::sleep(Duration::from_millis(100)).await;
                    }
                })
                .await
                .context("Profile verification timed out")?;
                if folder.join(format!("remote-{}.installed", number)).exists() {
                    acknowledge(folder, number, job.timeout, completed).await?;
                    continue;
                }
                std::fs::remove_file(&ipa)?;
            }
        }
        status(folder, json!({"stage":"sign", "account":number}))?;
        let (app, _) = sideloader
            .sign_app(
                account.ipa,
                Some(team),
                false,
                None::<fn(f32) -> std::future::Ready<()>>,
                None,
                Some(&job.serial),
            )
            .await?;
        let exported = isideload::sideload::install::export_signed_app(
            &app,
            &ipa,
            i64::from(job.remote_compression),
        );
        exported?;
        drop(sideloader);
        std::fs::write(folder.join(format!("remote-{}.ready", number)), b"")?;
        if job.profile_refresh {
            std::fs::write(folder.join(format!("remote-{}.full-ready", number)), b"")?;
        }
        if job.prepare_only {
            status(
                folder,
                json!({"stage":"prepared_account", "account":number}),
            )?;
            continue;
        }
        status(folder, json!({"stage":"install", "account":number}))?;
        // Sign one account ahead while Python uploads/installs the previous one.
        // Installation remains strictly ordered; only verified acknowledgements count.
        if job.profile_refresh {
            acknowledge(folder, number, job.timeout, completed).await?;
        } else if previous > 0 {
            acknowledge(folder, previous, job.timeout, completed).await?;
        }
        previous = number;
    }
    if !job.prepare_only && !job.profile_refresh {
        acknowledge(folder, previous, job.timeout, completed).await?;
    }
    Ok(())
}

async fn acknowledge(
    folder: &Path,
    account: usize,
    timeout: u64,
    completed: &mut usize,
) -> Result<(), Report> {
    tokio::time::timeout(Duration::from_secs(timeout), async {
        while !folder.join(format!("remote-{account}.installed")).exists() {
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    })
    .await
    .context("Remote installation timed out; inspect phone before retrying")?;
    let _ = std::fs::remove_file(folder.join(format!("remote-{account}.ipa")));
    *completed += 1;
    status(
        folder,
        json!({"stage":"completed_account", "account":account}),
    )?;
    Ok(())
}

pub async fn run(path: &Path) -> i32 {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_ansi(false)
        .init();
    let folder = path.parent().unwrap_or(Path::new("."));
    let mut completed = 0;
    let mut prepared_only = false;
    let outcome = async {
        let job: Job = serde_json::from_slice(&std::fs::read(path)?)?;
        prepared_only = job.prepare_only;
        let parent = ParentProcess::open(job.parent_pid)?;
        tokio::select! {
            value = execute(job, folder, &mut completed) => value,
            _ = async { loop {
                if folder.join("backend-stop").exists() || !parent.alive() { break; }
                tokio::time::sleep(Duration::from_millis(200)).await;
            }} => Err(report!("Background installation cancelled; inspect the phone before retrying")),
        }
    }.await;
    match outcome {
        Ok(()) => match result(
            folder,
            json!({"ok":true,"completed_accounts":completed,"prepared_only":prepared_only}),
        ) {
            Ok(()) => 0,
            Err(_) => 1,
        },
        Err(error) => {
            let _ = result(
                folder,
                json!({"ok":false,"completed_accounts":completed,"error":format!("{error}")}),
            );
            1
        }
    }
}
