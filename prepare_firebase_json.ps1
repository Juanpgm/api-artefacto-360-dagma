# 🔧 Script para Preparar Firebase JSON para Railway
# prepare_firebase_json.ps1

param(
    [Parameter(Mandatory=$true)]
    [string]$InputFile
)

Write-Host "`n╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     🔥 Preparar Firebase JSON para Railway/Heroku            ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan

# Verificar que el archivo existe
if (-not (Test-Path $InputFile)) {
    Write-Host "❌ ERROR: El archivo no existe: $InputFile" -ForegroundColor Red
    Write-Host "`n💡 Uso correcto:" -ForegroundColor Yellow
    Write-Host "   .\prepare_firebase_json.ps1 -InputFile 'ruta/al/archivo.json'" -ForegroundColor Gray
    exit 1
}

Write-Host "📂 Archivo de entrada: $InputFile" -ForegroundColor Gray

# Leer el archivo JSON
try {
    Write-Host "🔍 Leyendo archivo JSON..." -ForegroundColor Cyan
    $jsonContent = Get-Content $InputFile -Raw -ErrorAction Stop
    $jsonObject = $jsonContent | ConvertFrom-Json -ErrorAction Stop
    Write-Host "✅ Archivo JSON leído correctamente" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: El archivo no es un JSON válido" -ForegroundColor Red
    Write-Host "   Detalles: $($_.Exception.Message)" -ForegroundColor Yellow
    exit 1
}

# Validar campos requeridos
Write-Host "`n🔍 Validando campos obligatorios..." -ForegroundColor Cyan

$requiredFields = @(
    @{Name="type"; Expected="service_account"},
    @{Name="project_id"; Expected=$null},
    @{Name="private_key_id"; Expected=$null},
    @{Name="private_key"; Expected=$null},
    @{Name="client_email"; Expected=$null},
    @{Name="client_id"; Expected=$null}
)

$allValid = $true
foreach ($field in $requiredFields) {
    $fieldName = $field.Name
    $value = $jsonObject.$fieldName
    
    if ($null -eq $value -or $value -eq "") {
        Write-Host "   ❌ Falta campo requerido: $fieldName" -ForegroundColor Red
        $allValid = $false
    } else {
        if ($field.Expected -and $value -ne $field.Expected) {
            Write-Host "   ⚠️  Campo '$fieldName' tiene valor inesperado: $value" -ForegroundColor Yellow
        } else {
            Write-Host "   ✅ $fieldName" -ForegroundColor Green
        }
    }
}

if (-not $allValid) {
    Write-Host "`n❌ El archivo JSON no tiene todos los campos requeridos de Firebase" -ForegroundColor Red
    Write-Host "💡 Asegúrate de descargar el archivo desde Firebase Console > Cuentas de servicio" -ForegroundColor Yellow
    exit 1
}

# Mostrar información del proyecto
Write-Host "`n📊 Información del proyecto:" -ForegroundColor Cyan
Write-Host "   • Project ID: $($jsonObject.project_id)" -ForegroundColor White
Write-Host "   • Client Email: $($jsonObject.client_email)" -ForegroundColor White
Write-Host "   • Type: $($jsonObject.type)" -ForegroundColor White

# Verificar que private_key contiene saltos de línea
if ($jsonObject.private_key -notmatch "\\n") {
    Write-Host "   ⚠️  WARNING: private_key no parece tener saltos de línea (\\n)" -ForegroundColor Yellow
}

# Comprimir JSON (una sola línea, sin espacios)
Write-Host "`n🔄 Comprimiendo JSON..." -ForegroundColor Cyan
$compressedJson = $jsonObject | ConvertTo-Json -Compress -Depth 10

Write-Host "✅ JSON comprimido" -ForegroundColor Green
Write-Host "   Tamaño original: $($jsonContent.Length) caracteres" -ForegroundColor Gray
Write-Host "   Tamaño comprimido: $($compressedJson.Length) caracteres" -ForegroundColor Gray

# Copiar al portapapeles
try {
    $compressedJson | Set-Clipboard
    Write-Host "`n✅ JSON copiado al portapapeles" -ForegroundColor Green
} catch {
    Write-Host "`n⚠️  No se pudo copiar al portapapeles" -ForegroundColor Yellow
}

# Guardar en archivo
$outputFile = "firebase-config-compressed.txt"
try {
    $compressedJson | Out-File -FilePath $outputFile -Encoding UTF8 -NoNewline
    Write-Host "✅ JSON guardado en: $outputFile" -ForegroundColor Green
} catch {
    Write-Host "❌ No se pudo guardar en archivo" -ForegroundColor Red
}

# Mostrar preview
Write-Host "`n📄 Preview del JSON comprimido:" -ForegroundColor Cyan
Write-Host "   Primeros 100 caracteres:" -ForegroundColor Gray
$preview = $compressedJson.Substring(0, [Math]::Min(100, $compressedJson.Length))
Write-Host "   $preview..." -ForegroundColor White

# Instrucciones finales
Write-Host "`n╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                    ✅ JSON PREPARADO                           ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Green

Write-Host "`n📋 Próximos pasos en Railway:" -ForegroundColor Yellow
Write-Host "   1. Ve a tu proyecto en Railway" -ForegroundColor White
Write-Host "   2. Click en Variables" -ForegroundColor White
Write-Host "   3. Agrega nueva variable:" -ForegroundColor White
Write-Host "      - Nombre: FIREBASE_SERVICE_ACCOUNT_JSON" -ForegroundColor Cyan
Write-Host "      - Valor: [Pegar desde portapapeles - Ctrl+V]" -ForegroundColor Cyan
Write-Host "   4. Guarda y re-despliega" -ForegroundColor White

Write-Host "`n📁 Archivos generados:" -ForegroundColor Yellow
Write-Host "   • $outputFile (backup del JSON comprimido)" -ForegroundColor White

Write-Host "`n🔒 Seguridad:" -ForegroundColor Yellow
Write-Host "   ⚠️  NO subas el archivo JSON al repositorio" -ForegroundColor Red
Write-Host "   ⚠️  Agrega *.json a .gitignore" -ForegroundColor Red
Write-Host "   ⚠️  Elimina $outputFile después de configurar Railway" -ForegroundColor Red

Write-Host "`n💡 Verificar configuración:" -ForegroundColor Yellow
Write-Host "   python verify_config.py" -ForegroundColor Cyan

Write-Host "`n"

# Preguntar si desea validar localmente
$validate = Read-Host "¿Deseas validar el JSON localmente antes de subirlo? (s/n)"
if ($validate -eq "s" -or $validate -eq "S") {
    Write-Host "`n🔍 Validando JSON con Python..." -ForegroundColor Cyan
    
    # Crear script temporal de Python para validar
    $pythonScript = @"
import json
import sys

json_str = '''$compressedJson'''

try:
    data = json.loads(json_str)
    print('✅ JSON válido para Python')
    print(f'   • Project ID: {data.get("project_id")}')
    print(f'   • Client Email: {data.get("client_email")}')
    
    # Validar private_key
    pk = data.get('private_key', '')
    if pk.startswith('-----BEGIN PRIVATE KEY-----'):
        print('✅ private_key tiene formato correcto')
    else:
        print('⚠️  private_key puede no tener formato correcto')
    
    print('\n✅ El JSON está listo para usarse en Railway')
    sys.exit(0)
    
except json.JSONDecodeError as e:
    print(f'❌ Error: {e}')
    sys.exit(1)
except Exception as e:
    print(f'❌ Error inesperado: {e}')
    sys.exit(1)
"@
    
    $pythonScript | python
}

Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║                    🎉 PROCESO COMPLETADO                       ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan
