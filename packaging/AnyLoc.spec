# PyInstaller build description for the standalone macOS app.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


# PyInstaller sets SPECPATH to the directory containing this spec file.
ROOT = Path(SPECPATH).parent

hiddenimports = []
for package in ("pymobiledevice3", "uvicorn", "anyloc"):
    hiddenimports.extend(collect_submodules(package))

datas = collect_data_files("anyloc", include_py_files=False)
datas.append((str(ROOT / "anyloc" / "web"), "anyloc/web"))

analysis = Analysis(
    [str(ROOT / "packaging" / "app_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    name="AnyLoc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="AnyLoc",
)

app = BUNDLE(
    coll,
    name="AnyLoc.app",
    icon=None,
    bundle_identifier="com.bicy.anyloc.mac",
    version=os.environ.get("ANYLOC_VERSION", "0.1.0"),
)
