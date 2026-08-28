$ErrorActionPreference = "Stop"

$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$url = "http://192.168.0.30:8001"

Set-Location -LiteralPath $raiz

$servidorExistente = $null
try {
    $respuesta = Invoke-WebRequest -UseBasicParsing -Uri "$url/salud/" -TimeoutSec 2
    $servidorExistente = $respuesta
}
catch {
    # El servidor todavía no está activo; continúa con el arranque normal.
}
if ($null -ne $servidorExistente) {
    $contenido = $null
    try { $contenido = $servidorExistente.Content | ConvertFrom-Json } catch { }
    $servidorHttp = [string]$servidorExistente.Headers["Server"]
    if ($servidorExistente.StatusCode -eq 200 -and $contenido.estado -eq "ok" -and
        $servidorHttp.StartsWith("WSGIServer/", [StringComparison]::OrdinalIgnoreCase)) {
        Write-Host "El modo de prueba aislado ya está activo en $url" -ForegroundColor Green
        exit 0
    }
    throw "El puerto 8001 ya responde, pero no corresponde al runserver de prueba aislado. Detén ese proceso antes de continuar."
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Creando el entorno local por primera vez..." -ForegroundColor Yellow
    & py -m venv (Join-Path $raiz ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible crear el entorno de Python."
    }
}

& $python -c "import pip; p=tuple(int(x) for x in pip.__version__.split('.')[:2]); raise SystemExit(0 if p >= (26, 2) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Actualizando pip a una versión corregida..." -ForegroundColor Yellow
    & $python -m pip install --upgrade "pip>=26.2,<27"
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible actualizar pip a una versión corregida."
    }
}

& $python -c "import django, openpyxl, PIL, psycopg, whitenoise"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando las dependencias necesarias..." -ForegroundColor Yellow
    & $python -m pip install -r (Join-Path $raiz "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible instalar las dependencias."
    }
}

$env:DJANGO_SETTINGS_MODULE = "pos.settings_development"
$env:DJANGO_ALLOW_INSECURE_DEVELOPMENT = "true"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1,192.168.0.30"
$env:DB_ENGINE = "sqlite"
$env:SQLITE_PATH = "runtime/prueba/db.sqlite3"
$env:PEDIDOS_SUCURSALES_FUENTE = "desactivada"
$env:PEDIDOS_SUCURSALES_AUTO_SYNC = "false"
$env:PRINT_BACKEND = "archivo"
$env:PRINT_SYNC = "true"

New-Item -ItemType Directory -Force -Path (Join-Path $raiz "runtime\prueba") | Out-Null

Write-Host "Aplicando actualizaciones locales..." -ForegroundColor Yellow
& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) {
    throw "Fallaron las migraciones de la base local."
}

& $python manage.py cargar_datos_iniciales
if ($LASTEXITCODE -ne 0) {
        throw "No fue posible actualizar los catalogos iniciales."
}

Write-Host ""
Write-Host "MODO DE PRUEBA AISLADO: DEBUG activo, base separada, sin Supabase y sin impresoras reales." -ForegroundColor Yellow
Write-Host "Los Tocayos POS disponible en $url" -ForegroundColor Green
Write-Host "Manten esta ventana abierta. Presiona Ctrl+C para detener el servidor." -ForegroundColor Cyan
Write-Host ""

& $python manage.py runserver 192.168.0.30:8001 --noreload
exit $LASTEXITCODE
