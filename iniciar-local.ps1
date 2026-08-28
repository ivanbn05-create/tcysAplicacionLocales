param(
    [string]$ListenAddress,
    [ValidateRange(1, 65535)][int]$Port,
    [ValidateRange(2, 64)][int]$Threads,
    [switch]$AllowInsecureHttpLan
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"

Set-Location -LiteralPath $raiz

function Get-ConfigValue {
    param([string]$Name, [string]$Default)

    $proceso = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($proceso)) { return $proceso }
    $entorno = Join-Path $raiz ".env"
    if (Test-Path -LiteralPath $entorno) {
        $patron = "^\s*" + [Regex]::Escape($Name) + "\s*=(.*)$"
        foreach ($linea in Get-Content -LiteralPath $entorno) {
            if ($linea -match $patron) { return $Matches[1].Trim().Trim('"').Trim("'") }
        }
    }
    return $Default
}

function Get-ConfigInt {
    param([string]$Name, [int]$Default, [int]$Minimum, [int]$Maximum)

    $texto = Get-ConfigValue -Name $Name -Default $Default
    $valor = 0
    if (-not [int]::TryParse($texto, [ref]$valor) -or $valor -lt $Minimum -or $valor -gt $Maximum) {
        throw "$Name debe ser un entero entre $Minimum y $Maximum."
    }
    return $valor
}

if ([string]::IsNullOrWhiteSpace($ListenAddress)) {
    $ListenAddress = Get-ConfigValue -Name "WAITRESS_HOST" -Default "127.0.0.1"
}
if (-not $PSBoundParameters.ContainsKey("Port")) {
    $Port = Get-ConfigInt -Name "WAITRESS_PORT" -Default 8000 -Minimum 1 -Maximum 65535
}
if (-not $PSBoundParameters.ContainsKey("Threads")) {
    $Threads = Get-ConfigInt -Name "WAITRESS_THREADS" -Default 8 -Minimum 2 -Maximum 64
}
$connectionLimit = Get-ConfigInt -Name "WAITRESS_CONNECTION_LIMIT" -Default 100 -Minimum 10 -Maximum 500
$channelTimeout = Get-ConfigInt -Name "WAITRESS_CHANNEL_TIMEOUT" -Default 30 -Minimum 5 -Maximum 300
$cleanupInterval = Get-ConfigInt -Name "WAITRESS_CLEANUP_INTERVAL" -Default 10 -Minimum 1 -Maximum 60
$trustedProxy = Get-ConfigValue -Name "WAITRESS_TRUSTED_PROXY" -Default ""
$direccion = $null
if (-not [Net.IPAddress]::TryParse($ListenAddress, [ref]$direccion)) {
    throw "ListenAddress/WAITRESS_HOST debe ser una dirección IP local concreta."
}
if (-not [string]::IsNullOrWhiteSpace($trustedProxy)) {
    $direccionProxy = $null
    if (-not [Net.IPAddress]::TryParse($trustedProxy, [ref]$direccionProxy)) {
        throw "WAITRESS_TRUSTED_PROXY debe ser una dirección IP concreta."
    }
    if (-not [Net.IPAddress]::IsLoopback($direccionProxy)) {
        throw "WAITRESS_TRUSTED_PROXY debe ser una dirección loopback."
    }
}
$httpsActivo = (Get-ConfigValue -Name "DJANGO_HTTPS" -Default "false") -match "^(1|true|si|sí|yes)$"
$httpLanPermitido = $AllowInsecureHttpLan -or
    ((Get-ConfigValue -Name "ALLOW_INSECURE_HTTP_LAN" -Default "false") -match "^(1|true|si|sí|yes)$")
if (-not $httpsActivo -and -not [Net.IPAddress]::IsLoopback($direccion) -and
    -not $httpLanPermitido) {
    throw "HTTP LAN no cifra credenciales ni pedidos. Usa HTTPS o confirma el riesgo con -AllowInsecureHttpLan."
}
if ($httpsActivo -and (-not [Net.IPAddress]::IsLoopback($direccion) -or
    [string]::IsNullOrWhiteSpace($trustedProxy))) {
    throw "Con DJANGO_HTTPS activo, Waitress debe escuchar en loopback detrás del proxy confiable."
}

if (-not (Test-Path -LiteralPath $python)) {
    & py -m venv (Join-Path $raiz ".venv")
    if ($LASTEXITCODE -ne 0) { throw "No fue posible crear el entorno virtual." }
}

& $python -c "import pip; p=tuple(int(x) for x in pip.__version__.split('.')[:2]); raise SystemExit(0 if p >= (26, 2) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install --upgrade "pip>=26.2,<27"
    if ($LASTEXITCODE -ne 0) { throw "No fue posible actualizar pip a una versión corregida." }
}
& $python -m pip install -r (Join-Path $raiz "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "No fue posible instalar las dependencias." }

$env:DJANGO_SETTINGS_MODULE = "pos.settings"
$env:WAITRESS_HOST = $ListenAddress
$env:WAITRESS_TRUSTED_PROXY = $trustedProxy
$env:ALLOW_INSECURE_HTTP_LAN = $(if ($httpLanPermitido) { "true" } else { "false" })
if (-not $env:PRINT_BACKEND) { $env:PRINT_BACKEND = "tcp" }
$env:PRINT_SYNC = "true"

& $python manage.py check --deploy
if ($LASTEXITCODE -ne 0) {
    throw "La configuración de producción es inválida. Revisa DJANGO_SECRET_KEY y DJANGO_ALLOWED_HOSTS en .env."
}


& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Fallaron las migraciones." }
& $python manage.py cargar_datos_iniciales
if ($LASTEXITCODE -ne 0) { throw "No fue posible actualizar los catálogos iniciales." }
& $python manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw "No fue posible recopilar los archivos estáticos." }

& $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; raise SystemExit(0 if get_user_model().objects.filter(is_active=True, is_superuser=True).exists() else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "No existe una cuenta administrativa. Crea la primera ahora; la contraseña no se mostrará." -ForegroundColor Yellow
    & $python manage.py createsuperuser
    if ($LASTEXITCODE -ne 0) { throw "Debe existir al menos un superusuario antes de iniciar producción." }
}

& $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; U=get_user_model(); raise SystemExit(0 if U.objects.filter(is_active=True, is_superuser=False, perfil_pos__activo=True, perfil_pos__sucursal__clave=os.environ.get('SUCURSAL_CLAVE','ARBOLEDAS')).exists() else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Crea una cuenta operativa separada de la cuenta administrativa." -ForegroundColor Yellow
    & $python manage.py crear_operador_pos
    if ($LASTEXITCODE -ne 0) { throw "Debe existir al menos una cuenta operativa vinculada a un perfil POS." }
}

Write-Host ""
Write-Host "Los Tocayos POS inició en modo producción con Waitress (puerto $Port)." -ForegroundColor Green
Write-Host "Mantén esta ventana abierta o instala el servicio con instalar-servicio-lan.ps1." -ForegroundColor Cyan
Write-Host ""

$argumentosWaitress = @(
    "--host=$ListenAddress",
    "--port=$Port",
    "--threads=$Threads",
    "--ident=LosTocayosPOS",
    "--max-request-header-size=32768",
    "--max-request-body-size=1048576",
    "--connection-limit=$connectionLimit",
    "--channel-timeout=$channelTimeout",
    "--cleanup-interval=$cleanupInterval"
)
if (-not [string]::IsNullOrWhiteSpace($trustedProxy)) {
    $argumentosWaitress += "--trusted-proxy=$trustedProxy"
    $argumentosWaitress += "--trusted-proxy-count=1"
    $argumentosWaitress += "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto"
    $argumentosWaitress += "--log-untrusted-proxy-headers"
}
$argumentosWaitress += "pos.wsgi:application"
& $python -m waitress @argumentosWaitress
exit $LASTEXITCODE
