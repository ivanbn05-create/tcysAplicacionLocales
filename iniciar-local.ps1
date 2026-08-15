$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"

Set-Location -LiteralPath $raiz

if (-not $env:PRINT_BACKEND) {
    $env:PRINT_BACKEND = "tcp"
}

if (-not (Test-Path -LiteralPath $python)) {
    py -m venv .venv
}

& $python -m pip install -r requirements.txt
& $python manage.py migrate --noinput
& $python manage.py cargar_datos_iniciales

Write-Host "Los Tocayos POS disponible en http://127.0.0.1:8000" -ForegroundColor Green
& $python manage.py runserver 0.0.0.0:8000
