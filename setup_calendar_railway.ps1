#
# Script para preparar y configurar Google Calendar en Railway
# Uso: .\setup_calendar_railway.ps1
#

Write-Host "========================================================="
Write-Host "CONFIGURADOR DE GOOGLE CALENDAR PARA RAILWAY"
Write-Host "========================================================="
Write-Host ""

# Paso 1: Encontrar y leer el archivo JSON
Write-Host "PASO 1: Localizando archivo de credenciales..." -ForegroundColor Green

$possiblePaths = @(
    "dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json",
    "env/dagma-85aad-b7afe1c0f77f.json"
)

$jsonPath = $null
foreach ($path in $possiblePaths) {
    if (Test-Path $path) {
        $jsonPath = $path
        Write-Host "   [OK] Encontrado: $path" -ForegroundColor Green
        break
    }
}

if (-not $jsonPath) {
    Write-Host "   [ERROR] No se encontr el archivo JSON en:" -ForegroundColor Red
    foreach ($path in $possiblePaths) {
        Write-Host "      - $path"
    }
    exit 1
}

# Paso 2: Validar JSON
Write-Host ""
Write-Host "PASO 2: Validando contenido JSON..." -ForegroundColor Green

try {
    $jsonContent = Get-Content $jsonPath -Raw
    $jsonObj = $jsonContent | ConvertFrom-Json
    
    Write-Host "   [OK] JSON is valid" -ForegroundColor Green
    Write-Host "   Project: $($jsonObj.project_id)" -ForegroundColor Cyan
    Write-Host "   Email: $($jsonObj.client_email)" -ForegroundColor Cyan
}
catch {
    Write-Host "   [ERROR] Error validating JSON: $_" -ForegroundColor Red
    exit 1
}

# Paso 3: Copiar al portapapeles
Write-Host ""
Write-Host "PASO 3: Preparando para Railway..." -ForegroundColor Green

$jsonContent | Set-Clipboard
Write-Host "   [OK] JSON copied to clipboard" -ForegroundColor Green

# Paso 4: Instrucciones
Write-Host ""
Write-Host "========================================================="
Write-Host "NEXT STEPS IN RAILWAY DASHBOARD"
Write-Host "========================================================="
Write-Host ""

$instructions = @"
1. Open: https://railway.app/project
   
2. Select your project and environment
   
3. Click on "Variables" or "Environment Variables"
   
4. Add a new variable:
   - Name: FIREBASE_SERVICE_ACCOUNT_JSON
   - Value: [Paste here - Ctrl+V - JSON is in clipboard]
   
5. Click "Save"
   
6. Wait for app to redeploy 
   (you'll see status: Building -> Deploying -> Running)
   
7. Check Logs:
   - Look for: "[OK] Firebase initialized successfully"
   
8. Test by sending a request to:
    POST /programar_actividad
   
Expected Response:
{
  "success": true,
  "calendar_event_id": "...",
  "calendar_event_link": "https://www.google.com/calendar/..."
}
"@

Write-Host $instructions -ForegroundColor Yellow

Write-Host ""
Write-Host "[OK] JSON is in clipboard. Paste it in Railway Dashboard." -ForegroundColor Green
Write-Host ""
Write-Host "SUPPORT:"
Write-Host "If you see errors in Railway Logs:" -ForegroundColor Cyan
Write-Host "  - [Errno 2] No file: Missing environment variable" -ForegroundColor Yellow
Write-Host "  - JSON invalid: Content is not valid JSON" -ForegroundColor Yellow
Write-Host "  - Calendar API error: Problem in Google Cloud" -ForegroundColor Yellow
Write-Host ""
