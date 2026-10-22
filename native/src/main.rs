//! Private subprocess protocol. This executable never opens the iloader interface.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod account_auth;
mod background;

fn main() {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .expect("TLS provider unavailable");
    isideload::init().expect("Error reporting unavailable");
    let arguments: Vec<String> = std::env::args().collect();
    let runtime = tokio::runtime::Runtime::new().expect("Async runtime unavailable");
    let code = match arguments.get(1).map(String::as_str) {
        Some("--account-auth") if arguments.len() == 2 => runtime.block_on(account_auth::run()),
        Some("--background-job") if arguments.len() == 3 => {
            runtime.block_on(background::run(std::path::Path::new(&arguments[2])))
        }
        _ => 2,
    };
    std::process::exit(code);
}
