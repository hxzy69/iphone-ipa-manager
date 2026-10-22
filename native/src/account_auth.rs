//! Headless Apple authentication. Secrets enter only through a private stdin pipe.
use futures::FutureExt;
use isideload::{
    anisette::remote_v3::RemoteV3AnisetteProvider,
    auth::apple_account::{AppleAccount, TwoFactorCallbackParams, TwoFactorCallbackResponse},
    dev::developer_session::DeveloperSession,
    util::keyring_storage::KeyringStorage,
};
use rootcause::prelude::*;
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{BufRead, Read, Write};

#[derive(Deserialize)]
struct Credentials {
    email: String,
    password: String,
    server: String,
}

fn emit(value: Value) -> Result<(), Report> {
    let mut out = std::io::stdout().lock();
    writeln!(out, "{value}")?;
    out.flush()?;
    Ok(())
}

fn read() -> Result<String, Report> {
    let mut text = String::new();
    let bytes = std::io::stdin().lock().take(32769).read_line(&mut text)?;
    if bytes == 0 || bytes > 32768 {
        bail!("Authentication input ended");
    }
    Ok(text)
}

async fn execute() -> Result<(), Report> {
    let credentials: Credentials = serde_json::from_str(&read()?)?;
    if credentials.email.len() > 254
        || !credentials.email.contains('@')
        || credentials.password.is_empty()
        || credentials.password.len() > 4096
        || !credentials.server.starts_with("https://")
    {
        bail!("Invalid authentication input");
    }
    let email = credentials.email.trim().to_lowercase();
    let callback = |params: TwoFactorCallbackParams| {
        async move {
            emit(
                json!({"stage":"challenge", "sms":params.sms,"unknown":params.unknown,
            "retry":params.last_error.is_some(), "numbers":params.numbers.iter().map(|number|
                json!({"id":number.id,"last_digits":number.last_two_digits})).collect::<Vec<_>>()}),
            )?;
            let input = tokio::task::spawn_blocking(read).await??;
            let value: Value = serde_json::from_str(&input)?;
            let response = match value["action"].as_str() {
                Some("code") => {
                    let code = value["code"].as_str().unwrap_or("");
                    if code.len() != 6 || !code.bytes().all(|byte| byte.is_ascii_digit()) {
                        bail!("Invalid verification code");
                    }
                    TwoFactorCallbackResponse::SubmitCode(code.to_string())
                }
                Some("sms") => {
                    let id = value["number"]
                        .as_u64()
                        .ok_or_else(|| report!("Invalid number"))?;
                    if !params
                        .numbers
                        .iter()
                        .any(|number| u64::from(number.id) == id)
                    {
                        bail!("Unrecognized number");
                    }
                    TwoFactorCallbackResponse::SendSms(id as u32)
                }
                Some("devices") => TwoFactorCallbackResponse::SendToDevices,
                Some("resend") => TwoFactorCallbackResponse::ResendCode,
                _ => TwoFactorCallbackResponse::Abort,
            };
            Ok::<_, Report>(response)
        }
        .boxed()
    };
    emit(json!({"stage":"connecting"}))?;
    let mut account = AppleAccount::builder(&email)
        .anisette_provider(
            RemoteV3AnisetteProvider::default()?
                .set_serial_number("0".into())
                .set_storage(Box::new(KeyringStorage::new("iloader".into())))
                .set_url(&credentials.server),
        )
        .login(&credentials.password, Box::new(callback))
        .await?;
    emit(json!({"stage":"developer_session"}))?;
    let _session = DeveloperSession::from_account(&mut account).await?;
    keyring::Entry::new("iloader", &email)?.set_password(&credentials.password)?;
    emit(json!({"stage":"complete", "email":email}))?;
    Ok(())
}

pub async fn run() -> i32 {
    match tokio::time::timeout(std::time::Duration::from_secs(600), execute()).await {
        Ok(Ok(())) => 0,
        _ => {
            // Never emit raw errors, HTTP dumps, credentials, tokens or submitted codes.
            let _ = emit(
                json!({"stage":"failed","message":"Apple sign-in did not complete. Check your credentials, verification method or Apple account requirements, then start a new sign-in."}),
            );
            1
        }
    }
}
