Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force

$ProjectRoot = $PSScriptRoot
$BackendPort = 8000
$VenvPath    = Join-Path $ProjectRoot "venv"
$PythonExe   = Join-Path $VenvPath "Scripts\python.exe"
$ActivatePs1 = Join-Path $VenvPath "Scripts\Activate.ps1"
$RequirementsFile = Join-Path $ProjectRoot "requirements.txt"
$DepsStampFile = Join-Path $VenvPath ".requirements.sha256"
$ForceSyncDeps = $env:FORCE_PIP_SYNC -eq "1"

Write-Host ""
Write-Host "API Artefacto 360 DAGMA - Entorno local" -ForegroundColor Cyan
Write-Host "-----------------------------------------" -ForegroundColor Cyan
Write-Host ""

# Evita lanzar un segundo backend cuando ya hay uno en el puerto local.
$existingListener = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existingListener) {
    $existingProcess = Get-Process -Id $existingListener.OwningProcess -ErrorAction SilentlyContinue
    if ($existingProcess -and ($existingProcess.ProcessName -match "python|uvicorn")) {
        Write-Host "[0/4] Backend ya activo en http://localhost:$BackendPort (PID $($existingProcess.Id))." -ForegroundColor Yellow
        Write-Host "      Reutilizando proceso existente para evitar consumo duplicado." -ForegroundColor Yellow
        Write-Host "      Si quieres reiniciar, detén el proceso actual y vuelve a ejecutar este script." -ForegroundColor Yellow
        exit 0
    }
}

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

$requirementsHash = ""
if (Test-Path $RequirementsFile) {
    $requirementsHash = (Get-FileHash -Algorithm SHA256 -Path $RequirementsFile).Hash
}

$previousHash = ""
if (Test-Path $DepsStampFile) {
    $previousHash = (Get-Content -Path $DepsStampFile -Raw -ErrorAction SilentlyContinue).Trim()
}

$needsInstall = $ForceSyncDeps -or [string]::IsNullOrWhiteSpace($requirementsHash) -or ($requirementsHash -ne $previousHash)

if ($needsInstall) {
    Write-Host "[3/4] Sincronizando dependencias (cambio detectado en requirements o sync forzado)..." -ForegroundColor Yellow
    & $PythonExe -m pip install --upgrade pip setuptools wheel --quiet
    & $PythonExe -m pip install shapely --only-binary :all: --quiet
    & $PythonExe -m pip install -r $RequirementsFile --prefer-binary --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Fallo instalando dependencias." -ForegroundColor Red
        exit 1
    }
    if (-not [string]::IsNullOrWhiteSpace($requirementsHash)) {
        Set-Content -Path $DepsStampFile -Value $requirementsHash -Encoding ASCII
    }
    Write-Host "      Dependencias OK" -ForegroundColor Green
} else {
    Write-Host "[3/4] Dependencias sin cambios (cache activo)." -ForegroundColor Green
}

# Sincronizar puerto con el frontend (.env.development.local) para evitar
# que el proxy de Vite apunte a un puerto incorrecto y devuelva 500 en
# /api/auth/login y /api/auth/validate-session.
$FrontEnvFile = Join-Path $ProjectRoot "..\front\frontend\.env.development.local"
if (Test-Path $FrontEnvFile) {
    $expected = @(
        "# Local development - points API calls to local FastAPI instead of Railway production.",
        "# This file is automatically ignored by git (.local suffix) and only loaded in dev mode.",
        "# Managed by back/start_dev.ps1 - keep VITE_API_* aligned with backend port.",
        "VITE_API_BASE_URL=http://localhost:$BackendPort",
        "VITE_API_URL=http://localhost:$BackendPort"
    )
    $current = Get-Content $FrontEnvFile -ErrorAction SilentlyContinue
    $hasBase = $current -match "VITE_API_BASE_URL=http://localhost:$BackendPort"
    $hasUrl  = $current -match "VITE_API_URL=http://localhost:$BackendPort"
    if (-not ($hasBase -and $hasUrl)) {
        Write-Host "      Sincronizando front .env.development.local -> puerto $BackendPort" -ForegroundColor Yellow
        Set-Content -Path $FrontEnvFile -Value $expected -Encoding UTF8
    }
}

Write-Host "[4/4] Iniciando API en http://localhost:$BackendPort" -ForegroundColor Green
Write-Host "      Docs:  http://localhost:$BackendPort/docs" -ForegroundColor Cyan
Write-Host ""
$env:PYTHONUTF8 = "1"
& $PythonExe -m uvicorn app.main:app --reload --host 0.0.0.0 --port $BackendPort --reload-dir (Join-Path $ProjectRoot "app")