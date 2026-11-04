# Local build. In PowerShell from the repo root:
#   py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
#   pip install -r requirements-dev.txt
#   .\scripts\build_windows.ps1
$ErrorActionPreference = "Stop"
python -m pytest
pyinstaller --noconfirm TatkalMate.spec
Write-Host "Built dist\TatkalMate\TatkalMate.exe"
