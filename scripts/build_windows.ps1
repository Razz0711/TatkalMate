# Builds a single-folder Windows app in dist\TatkalMate. Run from the repo root in PowerShell:
#   py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
#   pip install -r requirements-dev.txt
#   .\scripts\build_windows.ps1
$ErrorActionPreference = "Stop"
python -m pytest
pyinstaller --noconfirm --windowed --name TatkalMate `
  --collect-data tzdata `
  --hidden-import PySide6.QtNetwork `
  main.py
Write-Host "Built dist\TatkalMate\TatkalMate.exe"
