# ?? Script de Testing Automatizado - API Artefacto 360 DAGMA
# run_tests.ps1

param(
    [Parameter(Mandatory = $false)]
    [ValidateSet('all', 'quick', 'auth', 'firebase', 'artefacto', 'coverage', 'verbose', 'integration')]
    [string]$Mode = 'all',
    
    [Parameter(Mandatory = $false)]
    [switch]$OpenReport,
    
    [Parameter(Mandatory = $false)]
    [switch]$Verbose
)

# Colores para output
function Write-ColorOutput {
    param([string]$Message, [string]$Color = "White")
    Write-Host $Message -ForegroundColor $Color
}

# Banner
Write-ColorOutput "`n??????????????????????????????????????????????????" "Cyan"
Write-ColorOutput "?   ?? Testing - API Artefacto 360 DAGMA       ?" "Cyan"
Write-ColorOutput "??????????????????????????????????????????????????`n" "Cyan"

# Verificar que pytest est? instalado
try {
    $pytestVersion = pytest --version 2>&1
    Write-ColorOutput "? Pytest instalado: $pytestVersion" "Green"
}
catch {
    Write-ColorOutput "? ERROR: Pytest no est? instalado" "Red"
    Write-ColorOutput "Ejecuta: pip install -r requirements.txt" "Yellow"
    exit 1
}

# Cambiar al directorio del proyecto
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-ColorOutput "?? Directorio: $scriptPath`n" "Gray"

# Configurar argumentos base
$baseArgs = @("test_all_endpoints.py")
if ($Verbose) {
    $baseArgs += "-vv"
}
else {
    $baseArgs += "-v"
}

# Ejecutar tests seg?n el modo
switch ($Mode) {
    'all' {
        Write-ColorOutput "?? Ejecutando TODOS los tests...`n" "Cyan"
        & pytest @baseArgs --cov=app --cov-report=html --cov-report=term
    }
    
    'quick' {
        Write-ColorOutput "? Ejecutando tests r?pidos...`n" "Yellow"
        & pytest @baseArgs -q --tb=line
    }
    
    'auth' {
        Write-ColorOutput "?? Ejecutando tests de autenticaci?n...`n" "Magenta"
        & pytest test_all_endpoints.py::TestAuthRoutes -v
    }
    
    'firebase' {
        Write-ColorOutput "?? Ejecutando tests de Firebase...`n" "Red"
        & pytest test_all_endpoints.py::TestFirebaseRoutes -v
    }
    
    'artefacto' {
        Write-ColorOutput "?? Ejecutando tests de Artefacto 360...`n" "Blue"
        & pytest test_all_endpoints.py::TestArtefacto360Routes -v
    }
    
    'coverage' {
        Write-ColorOutput "?? Generando reporte de cobertura completo...`n" "Cyan"
        & pytest @baseArgs --cov=app --cov-report=html --cov-report=term-missing --durations=10
        
        if ($LASTEXITCODE -eq 0) {
            Write-ColorOutput "`n? Reporte de cobertura generado en: htmlcov/index.html" "Green"
            if ($OpenReport) {
                Write-ColorOutput "?? Abriendo reporte en navegador..." "Cyan"
                Start-Process "htmlcov/index.html"
            }
        }
    }
    
    'verbose' {
        Write-ColorOutput "?? Ejecutando tests con output detallado...`n" "Cyan"
        & pytest test_all_endpoints.py -vv --tb=long --durations=10
    }
    
    'integration' {
        Write-ColorOutput "?? Ejecutando tests de integraci?n...`n" "Green"
        & pytest test_all_endpoints.py::TestIntegration -v
    }
}

# Verificar resultado
if ($LASTEXITCODE -eq 0) {
    Write-ColorOutput "`n??????????????????????????????????????????????????" "Green"
    Write-ColorOutput "?          ? TODOS LOS TESTS PASARON           ?" "Green"
    Write-ColorOutput "??????????????????????????????????????????????????" "Green"
}
else {
    Write-ColorOutput "`n??????????????????????????????????????????????????" "Red"
    Write-ColorOutput "?          ? ALGUNOS TESTS FALLARON            ?" "Red"
    Write-ColorOutput "??????????????????????????????????????????????????" "Red"
    Write-ColorOutput "`n?? Tip: Ejecuta con -Verbose para m?s detalles" "Yellow"
}

# Resumen de comandos disponibles
Write-ColorOutput "`n?? Comandos disponibles:" "Cyan"
Write-ColorOutput "  .\run_tests.ps1 -Mode all         (Todos los tests + cobertura)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode quick       (Tests r?pidos)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode auth        (Tests de autenticaci?n)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode firebase    (Tests de Firebase)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode artefacto   (Tests de Artefacto 360)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode coverage    (Cobertura completa)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode verbose     (Output detallado)" "Gray"
Write-ColorOutput "  .\run_tests.ps1 -Mode integration (Tests de integraci?n)" "Gray"
Write-ColorOutput "`n  Agregar -OpenReport para abrir reporte HTML autom?ticamente" "Gray"
Write-ColorOutput "  Agregar -Verbose para output muy detallado`n" "Gray"

exit $LASTEXITCODE
