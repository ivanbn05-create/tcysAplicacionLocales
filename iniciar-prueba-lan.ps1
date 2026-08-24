$ErrorActionPreference = "Stop"

$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$url = "http://192.168.0.30:8000"

Set-Location -LiteralPath $raiz

try {
    $respuesta = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/estado/" -TimeoutSec 2
    if ($respuesta.StatusCode -ge 200 -and $respuesta.StatusCode -lt 400) {
        Write-Host "Los Tocayos POS ya está activo en $url" -ForegroundColor Green
        exit 0
    }
}
catch {
    # El servidor todavía no está activo; continúa con el arranque normal.
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Creando el entorno local por primera vez..." -ForegroundColor Yellow
    & py -m venv (Join-Path $raiz ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible crear el entorno de Python."
    }
}

& $python -c "import django, openpyxl, PIL, psycopg"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando las dependencias necesarias..." -ForegroundColor Yellow
    & $python -m pip install -r (Join-Path $raiz "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible instalar las dependencias."
    }
}

$env:DJANGO_ALLOWED_HOSTS = "*"
$env:DJANGO_DEBUG = "true"
$env:PRINT_BACKEND = "tcp"
$env:PRINT_SYNC = "true"
$env:PRINTER_CAJA_HOST = "192.168.0.33"
$env:PRINTER_COCINA_HOST = "192.168.0.33"
$env:PRINTER_BARRA_HOST = "192.168.0.33"
$env:PRINTER_PORT = "9100"

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
Write-Host "Los Tocayos POS disponible en $url" -ForegroundColor Green
Write-Host "Manten esta ventana abierta. Presiona Ctrl+C para detener el servidor." -ForegroundColor Cyan
Write-Host ""

& $python manage.py runserver 192.168.0.30:8000 --noreload
exit $LASTEXITCODE
