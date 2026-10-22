//! Download renewal profiles without extracting, signing, or repacking executables.
//! The controller must verify installed versions, identities and every profile before use.
use std::{fs::File, io::{Read, Write}, path::Path};
use plist::Value;
use rootcause::prelude::*;
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};
use crate::dev::{app_ids::AppIdsApi, developer_session::DeveloperSession, teams::DeveloperTeam};

fn metadata(path: &Path, team: &str) -> Result<Vec<(String, Value, String)>, Report> {
    let mut archive = ZipArchive::new(File::open(path)?)?;
    let names: Vec<String> = archive.file_names().map(str::to_owned).collect();
    let mains: Vec<_> = names.iter().filter(|name| name.starts_with("Payload/")
        && name.matches('/').count() == 2 && name.ends_with(".app/Info.plist")).collect();
    if mains.len() != 1 { bail!("Refresh requires one main app"); }
    let main = mains[0].rsplit_once('/').unwrap().0;
    let mut selected = vec![mains[0].to_string()];
    for name in &names {
        if name == mains[0] { continue; }
        if name.starts_with(&format!("{main}/")) && name.ends_with(".app/Info.plist") {
            bail!("Nested apps require full installation");
        }
        if name.starts_with(&format!("{main}/")) && name.ends_with(".appex/Info.plist") {
            selected.push(name.clone());
        }
    }
    let mut output = Vec::new();
    let mut original_main = String::new();
    for (index, name) in selected.into_iter().enumerate() {
        let mut bytes = Vec::new();
        archive.by_name(&name)?.take(2 * 1024 * 1024).read_to_end(&mut bytes)?;
        let mut info = Value::from_reader(std::io::Cursor::new(bytes))?;
        let dictionary = info.as_dictionary_mut().ok_or_else(|| report!("Invalid Info.plist"))?;
        let old = dictionary.get("CFBundleIdentifier").and_then(Value::as_string)
            .ok_or_else(|| report!("Missing bundle identifier"))?.to_owned();
        if index == 0 { original_main = old.clone(); }
        if index > 0 && !old.starts_with(&format!("{original_main}.")) {
            bail!("Extension does not belong to the main app");
        }
        let identifier = format!("{original_main}.{team}{}", &old[original_main.len()..]);
        dictionary.insert("CFBundleIdentifier".into(), Value::String(identifier.clone()));
        output.push((name, info, identifier));
    }
    Ok(output)
}

/// False selects the existing full signing/install path without changing the phone.
/// The output deliberately contains no executable and must never be sent to InstallationProxy.
pub async fn export_profiles(session: &mut DeveloperSession, team: &DeveloperTeam,
                             source: &Path, destination: &Path) -> Result<bool, Report> {
    let bundles = match metadata(source, &team.team_id) {
        Ok(value) => value,
        Err(_) => return Ok(false),
    };
    let registered = session.list_app_ids(team, None).await?.app_ids;
    if bundles.iter().any(|(_, _, identifier)| !registered.iter().any(|id| &id.identifier == identifier)) {
        return Ok(false);
    }
    let mut downloaded = Vec::new();
    for (name, info, identifier) in bundles {
        let app = registered.iter().find(|id| id.identifier == identifier).unwrap();
        let profile = session.download_team_provisioning_profile(team, app, None).await?;
        downloaded.push((name, info, profile));
    }
    let mut writer = ZipWriter::new(File::create(destination)?);
    for (name, info, profile) in downloaded {
        writer.start_file(&name, SimpleFileOptions::default())?;
        info.to_writer_xml(&mut writer)?;
        writer.start_file(format!("{}/embedded.mobileprovision", name.rsplit_once('/').unwrap().0),
                          SimpleFileOptions::default())?;
        writer.write_all(profile.encoded_profile.as_ref())?;
    }
    writer.finish()?.sync_all()?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn source(extension: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!("refresh-test-{}.ipa", uuid::Uuid::new_v4()));
        let mut zip = ZipWriter::new(File::create(&path).unwrap());
        for (name, id) in [("Payload/Test.app/Info.plist", "com.test"),
                          ("Payload/Test.app/PlugIns/Share.appex/Info.plist", extension)] {
            zip.start_file(name, SimpleFileOptions::default()).unwrap();
            let mut d = plist::Dictionary::new();
            d.insert("CFBundleIdentifier".into(), Value::String(id.into()));
            d.insert("CFBundleVersion".into(), Value::String("123".into()));
            Value::Dictionary(d).to_writer_binary(&mut zip).unwrap();
        }
        zip.finish().unwrap();
        path
    }
    #[test]
    fn metadata_maps_team_and_extensions_without_extracting_executables() {
        let path = source("com.test.share");
        let result = metadata(&path, "TEAM").unwrap();
        std::fs::remove_file(path).unwrap();
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].2, "com.test.TEAM");
        assert_eq!(result[1].2, "com.test.TEAM.share");
        assert_eq!(result[0].1.as_dictionary().unwrap()["CFBundleVersion"].as_string(), Some("123"));
    }
    #[test]
    fn rejects_unrelated_extension() {
        let path = source("com.other.share");
        assert!(metadata(&path, "TEAM").is_err());
        std::fs::remove_file(path).unwrap();
    }
}
