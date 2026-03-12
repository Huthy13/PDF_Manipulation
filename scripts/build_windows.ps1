param(
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

if ($OneFile) {
  pyinstaller --noconfirm --clean --onefile --windowed --name pdf_merge_gui run_gui.py
} else {
  pyinstaller --noconfirm --clean --windowed pdf_merge_gui.spec
}

Write-Host "Build completed. Check dist/ for artifacts."
