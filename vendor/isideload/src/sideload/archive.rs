//! Single-IPA transfer for Wi-Fi, retaining the existing signing workflow.
use std::{fs::File, io::{Read, Seek, Write}, path::{Path, PathBuf}, time::Instant};
use idevice::{IdeviceService, afc::{AfcClient, opcode::AfcFopenMode},
              installation_proxy::InstallationProxyClient, provider::IdeviceProvider};
use plist_macro::plist;
use rootcause::{prelude::*, option_ext::OptionExt};
use crate::SideloadError as Error;
use zip::{ZipWriter, write::SimpleFileOptions, CompressionMethod};

struct TemporaryArchive(PathBuf);
impl Drop for TemporaryArchive {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn append_directory<W: Write + Seek>(writer: &mut ZipWriter<W>, local: &Path, name: &str, level: i64) -> Result<usize, Report> {
    let options = SimpleFileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .compression_level(Some(level)).unix_permissions(0o755);
    writer.add_directory(format!("{name}/"), options)?;
    let mut count = 0;
    let mut entries = isideload_vfs::fs::read_dir(local)?.collect::<std::io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let child = entry.path();
        let leaf = child.file_name().ok_or_report()?.to_str().ok_or_report()?;
        let entry_name = format!("{name}/{leaf}");
        if isideload_vfs::fs::metadata(&child)?.is_dir() {
            count += append_directory(writer, &child, &entry_name, level)?;
        } else {
            writer.start_file(entry_name, options)?;
            writer.write_all(&isideload_vfs::fs::read(&child)?)?;
            count += 1;
        }
    }
    Ok(count)
}

fn package_signed_app(app: &Path, level: i64) -> Result<(TemporaryArchive, usize), Report> {
    let archive = TemporaryArchive(std::env::temp_dir().join(format!("iloader-wifi-{}.ipa", uuid::Uuid::new_v4())));
    let file = File::options().write(true).create_new(true).open(&archive.0)?;
    let mut writer = ZipWriter::new(file);
    writer.add_directory("Payload/", SimpleFileOptions::default().unix_permissions(0o755))?;
    let name = app.file_name().ok_or_report()?.to_str().ok_or_report()?;
    let count = append_directory(&mut writer, app, &format!("Payload/{name}"), level)?;
    writer.finish()?.sync_all()?;
    Ok((archive, count))
}

/// Export signed bytes for installation through an authenticated remote tunnel.
pub fn export_signed_app(app: &Path, destination: &Path, level: i64) -> Result<(), Report> {
    let outcome = (|| {
        let started = Instant::now();
        let (archive, _) = package_signed_app(app, level)?;
        tracing::info!("Preparation timing: archive_package_seconds={:.3}", started.elapsed().as_secs_f64());
        let exported = Instant::now();
        // Moving within a volume avoids reading and writing the entire IPA again.
        // Cross-volume exports retain the previous copy behavior.
        if std::fs::rename(&archive.0, destination).is_err() {
            std::fs::copy(&archive.0, destination)?;
        }
        tracing::info!("Preparation timing: archive_export_seconds={:.3}", exported.elapsed().as_secs_f64());
        Ok(())
    })();
    let _ = isideload_vfs::fs::remove_dir_all(app);
    outcome
}

/// Repack the already-signed bundle, transfer one IPA, and install normally.
pub async fn install_app_archive(
    provider: &impl IdeviceProvider,
    app: &Path,
    progress: impl Fn(u64) + Send + Sync,
) -> Result<(), Report> {
    let started = Instant::now();
    let (archive, files) = package_signed_app(app, 1)?;
    let size = std::fs::metadata(&archive.0)?.len();
    tracing::info!("Wi-Fi archive: {files} files, {size} bytes, packaged in {:?}", started.elapsed());
    let mut afc = AfcClient::connect(provider).await.map_err(Error::IdeviceError)?;
    if afc.get_file_info("PublicStaging").await.is_err() {
        afc.mk_dir("PublicStaging").await.map_err(Error::IdeviceError)?;
    }
    let remote = format!("PublicStaging/iloader-wifi-{}.ipa", uuid::Uuid::new_v4());
    let outcome = async {
        let upload_started = Instant::now();
        let mut fd = afc.open(&remote, AfcFopenMode::WrOnly).await.map_err(Error::IdeviceError)?;
        let upload: Result<(), Report> = async {
            let mut file = File::open(&archive.0)?;
            let mut buffer = vec![0; 4 * 1024 * 1024];
            let mut uploaded = 0;
            loop {
                let count = file.read(&mut buffer)?;
                if count == 0 { break; }
                fd.write_entire(&buffer[..count]).await.map_err(Error::IdeviceError)?;
                uploaded += count as u64;
                progress(uploaded * 70 / size.max(1));
            }
            Ok(())
        }.await;
        let closed = fd.close().await.map_err(Error::IdeviceError);
        upload?;
        closed?;
        tracing::info!("Wi-Fi archive upload complete: {size} bytes in {:?}", upload_started.elapsed());
        let mut installer = InstallationProxyClient::connect(provider).await.map_err(Error::IdeviceError)?;
        let info: plist::Value = plist::from_bytes(&isideload_vfs::fs::read(app.join("Info.plist"))?)?;
        let identifier = info.as_dictionary().and_then(|dict| dict.get("CFBundleIdentifier"))
            .and_then(plist::Value::as_string).ok_or_report()?.to_string();
        let installed = installer.get_apps(Some("User"), Some(vec![identifier.clone()]))
            .await.map_err(Error::IdeviceError)?.contains_key(&identifier);
        let options = plist!(dict { "PackageType": "Developer" });
        if installed {
            tracing::info!("Refreshing existing app with Upgrade");
            installer.upgrade_with_callback(remote.clone(), Some(plist::Value::Dictionary(options)),
                async |(percent, _)| { progress(70 + percent * 30 / 100); }, ())
                .await.map_err(Error::IdeviceError)?;
        } else {
            installer.install_with_callback(remote.clone(), Some(plist::Value::Dictionary(options)),
                async |(percent, _)| { progress(70 + percent * 30 / 100); }, ())
                .await.map_err(Error::IdeviceError)?;
        }
        if !installer.get_apps(Some("User"), Some(vec![identifier.clone()]))
            .await.map_err(Error::IdeviceError)?.contains_key(&identifier) {
            bail!("Installed app was not found during verification");
        }
        tracing::info!("Wi-Fi archive installation complete in {:?}", started.elapsed());
        Ok(())
    }.await;
    if afc.get_file_info(&remote).await.is_ok() {
        if let Err(error) = afc.remove(&remote).await {
            tracing::warn!("Could not clean up Wi-Fi staging archive: {error}");
        }
    }
    outcome
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exported_archive_preserves_payload_and_removes_expanded_bundle() {
        let root = std::env::temp_dir().join(format!("iloader-export-test-{}", uuid::Uuid::new_v4()));
        let app = root.join("Test.app");
        std::fs::create_dir_all(&app).unwrap();
        let signed = b"signed payload\0\xff".repeat(1000);
        std::fs::write(app.join("Test"), &signed).unwrap();
        let destination = root.join("output.ipa");
        export_signed_app(&app, &destination, 6).unwrap();
        assert!(!app.exists());
        let mut zip = zip::ZipArchive::new(File::open(&destination).unwrap()).unwrap();
        let mut actual = Vec::new();
        zip.by_name("Payload/Test.app/Test").unwrap().read_to_end(&mut actual).unwrap();
        assert_eq!(actual, signed);
        drop(zip);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    #[ignore = "Local disk benchmark; set IPA_BENCHMARK_APP to a disposable extracted app"]
    fn compare_buffered_packaging() {
        let app = PathBuf::from(std::env::var("IPA_BENCHMARK_APP").unwrap());
        // ABBA alternation; compare exact archive bytes so buffering cannot hide
        // changes in compression, ordering or the signed payload.
        let mut expected = None;
        for buffered in [false, true, true, false] {
            let started = Instant::now();
            let archive = if buffered {
                let archive = TemporaryArchive(std::env::temp_dir().join(format!("iloader-buffered-{}.ipa", uuid::Uuid::new_v4())));
                let file = std::io::BufWriter::with_capacity(1024 * 1024, File::create(&archive.0).unwrap());
                let mut writer = ZipWriter::new(file);
                writer.add_directory("Payload/", SimpleFileOptions::default().unix_permissions(0o755)).unwrap();
                append_directory(&mut writer, &app, &format!("Payload/{}", app.file_name().unwrap().to_str().unwrap()), 6).unwrap();
                let mut file = writer.finish().unwrap();
                file.flush().unwrap();
                file.get_ref().sync_all().unwrap();
                archive
            } else {
                let archive = TemporaryArchive(std::env::temp_dir().join(format!("iloader-unbuffered-{}.ipa", uuid::Uuid::new_v4())));
                let mut writer = ZipWriter::new(File::create(&archive.0).unwrap());
                writer.add_directory("Payload/", SimpleFileOptions::default().unix_permissions(0o755)).unwrap();
                append_directory(&mut writer, &app, &format!("Payload/{}", app.file_name().unwrap().to_str().unwrap()), 6).unwrap();
                writer.finish().unwrap().sync_all().unwrap();
                archive
            };
            let seconds = started.elapsed().as_secs_f64();
            let bytes = std::fs::read(&archive.0).unwrap();
            if let Some(ref previous) = expected { assert_eq!(&bytes, previous); }
            else { expected = Some(bytes.clone()); }
            println!("PACKAGING buffered={buffered} seconds={seconds:.3} bytes={}", bytes.len());
        }
    }

    #[test]
    fn archive_preserves_signed_bytes_and_nested_paths_and_is_removed() {
        let root = std::env::temp_dir().join(format!("iloader-zip-test-{}", uuid::Uuid::new_v4()));
        let app = root.join("Test.app");
        std::fs::create_dir_all(app.join("_CodeSignature")).unwrap();
        std::fs::write(app.join("Test"), b"signed executable bytes\0\xff").unwrap();
        std::fs::write(app.join("_CodeSignature/CodeResources"), b"signed resources").unwrap();
        let (archive, count) = package_signed_app(&app, 1).unwrap();
        assert_eq!(count, 2);
        let path = archive.0.clone();
        {
            let mut zip = zip::ZipArchive::new(File::open(&path).unwrap()).unwrap();
            let mut bytes = Vec::new();
            zip.by_name("Payload/Test.app/Test").unwrap().read_to_end(&mut bytes).unwrap();
            assert_eq!(bytes, b"signed executable bytes\0\xff");
            assert!(zip.by_name("Payload/Test.app/_CodeSignature/CodeResources").is_ok());
        }
        drop(archive);
        assert!(!path.exists());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn strongest_compression_preserves_signed_payload_and_nested_resources() {
        let root = std::env::temp_dir().join(format!("iloader-remote-zip-{}", uuid::Uuid::new_v4()));
        let app = root.join("Test.app");
        std::fs::create_dir_all(app.join("_CodeSignature")).unwrap();
        let signed = b"signed executable bytes\0\xff".repeat(1000);
        std::fs::write(app.join("Test"), &signed).unwrap();
        std::fs::write(app.join("_CodeSignature/CodeResources"), b"signed resources").unwrap();
        let (archive, count) = package_signed_app(&app, 9).unwrap();
        assert_eq!(count, 2);
        let mut zip = zip::ZipArchive::new(File::open(&archive.0).unwrap()).unwrap();
        let mut bytes = Vec::new();
        zip.by_name("Payload/Test.app/Test").unwrap().read_to_end(&mut bytes).unwrap();
        assert_eq!(bytes, signed);
        assert!(zip.by_name("Payload/Test.app/_CodeSignature/CodeResources").is_ok());
        drop(zip);
        std::fs::remove_dir_all(root).unwrap();
    }
}
