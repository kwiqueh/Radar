param([switch]$Demo)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12 with the Python launcher first." }
    }
    $RadarPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $RadarFingerprint = (Get-FileHash requirements.lock -Algorithm SHA256).Hash + (Get-FileHash pyproject.toml -Algorithm SHA256).Hash
    $RadarMarker = ".venv\skin-radar-install.txt"
    if (-not (Test-Path $RadarMarker) -or (Get-Content $RadarMarker -Raw).Trim() -ne $RadarFingerprint) {
        & $RadarPython -m pip install --require-hashes -r requirements.lock
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
        & $RadarPython -m pip install --no-deps -e .
        if ($LASTEXITCODE -ne 0) { throw "Application installation failed." }
        Set-Content -Path $RadarMarker -Value $RadarFingerprint
    }
    Write-Host "Open http://127.0.0.1:8000 on this PC. Stop: Ctrl+C."
    if ($Demo) { & $RadarPython -m skin_radar run --demo }
    else { & $RadarPython -m skin_radar run }
    if ($LASTEXITCODE -ne 0) { throw "Skin Radar stopped with an error. See the output above." }
}
finally { Pop-Location }
