Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force

$ProjectRoot = $PSScriptRoot
$VenvPath    = Join-Path $ProjectRoot "venv"
$PythonExe   = Join-Path $VenvPath "Scripts\python.exe"
$ActivatePs1 = Join-Path $VenvPath "Scripts\Activate.ps1"

Write-Host ""
Write-Host "API Artefacto 360 DAGMA - Entorno local" -ForegroundColor Cyan
Write-Host "-----------------------------------------" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $PythonExe)) {
    Write-Host "[1/4] Venv no encontrado - creando nuevo..." -ForegroundColor Yellow
    python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: No se pudo crear el venv." -ForegroundColor Red ; exit 1 }
    Write-Host "      Venv creado OK" -ForegroundColor Green
} else {
    Write-Host "[1/4] Venv OK: $PythonExe" -ForegroundColor Green
}

Write-Host "[2/4] Activando entorno virtual..." -ForegroundColor Yellow
& $ActivatePs1

Write-Host "[3/4] Instalando dependencias..." -ForegroundColor Yellow
& $PythonExe -m pip install --upgrade pip setuptools wheel --quiet
& $PythonExe -m pip install shapely --only-binary :all: --quiet
& $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements.txt") --prefer-binary --quiet
Write-Host "      Dependencias OK" -ForegroundColor Green

Write-Host "[4/4] Iniciando API en http://localhost:8000" -ForegroundColor Green
Write-Host "      Docs:  http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
$env:PYTHONUTF8 = "1"
& $PythonExe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000