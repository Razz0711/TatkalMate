# PyInstaller spec: `pyinstaller TatkalMate.spec` -> dist/TatkalMate/TatkalMate.exe
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=collect_data_files("tzdata") + [(".env.example", ".")],
    hiddenimports=["PySide6.QtNetwork", "zoneinfo", "tzdata"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtMultimedia", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TatkalMate", console=False,
          version=None, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="TatkalMate")
