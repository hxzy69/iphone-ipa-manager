"""Windows standalone build. Private runtime state is never a build input."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [
    (str(root / "assets/app.png"), "assets"),
    (str(root / 'iphone_detector/windows_notify.ps1'), 'iphone_detector'),
    (str(root / 'docs/START_HERE.html'), 'docs'),
    (str(root / 'tools/netmuxd/netmuxd.exe'), 'tools/netmuxd'),
    (str(root / 'tools/netmuxd/LICENSE'), 'tools/netmuxd'),
    (str(root / 'tools/netmuxd/source.json'), 'tools/netmuxd'),
    (str(root / 'tools/wifi-service/BridgeService.exe'), 'tools/wifi-service'),
    (str(root / 'tools/optimized-iloader/iloader-wifi.exe'), 'tools/optimized-iloader'),
    (str(root / 'native/LICENSE'), 'licenses/iloader'),
    (str(root / 'vendor/apple-codesign-quick/COPYING.LESSER'), 'licenses/apple-codesign-quick'),
    (str(root / 'vendor/isideload/LICENSE'), 'licenses/isideload'),
    (str(root / 'LICENSE'), 'licenses/application'),
    (str(root / 'NOTICE.md'), 'licenses'),
]
datas += collect_data_files('pymobiledevice3') + copy_metadata('pymobiledevice3')

analysis = Analysis(
    [str(root / 'ipa_manager.py')], pathex=[str(root / "vendor/python"), str(root)], binaries=[], datas=datas,
    hiddenimports=['wmi', 'pythoncom', 'pywintypes', 'win32com.client', 'win32timezone',
                   'win32com.shell.shell', 'win32com.propsys.propsys'] + collect_submodules('pmd_pytcp'),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['pytest', 'unittest', 'pydoc', 'matplotlib', 'scipy', 'pandas', 'IPython',
              'pytun_pmd3', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cv2', 'numpy'],
    noarchive=False, optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
          name='iPhoneIPAManager', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False, uac_admin=False, icon=str(root / "assets/app.ico"))

