param(
    [string]$ListenAddress,
    [ValidateRange(1, 65535)][int]$Port,
    [ValidateRange(2, 64)][int]$Threads,
    [switch]$AllowInsecureHttpLan,
    [switch]$AllowExternalSync
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$entorno = Join-Path $raiz ".env"

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

function Set-SafePythonProcessEnvironment {
    foreach ($variable in Get-ChildItem Env:) {
        if ($variable.Name -match '^(?:PYTHON|PIP_)' -or
            $variable.Name -in @("VIRTUAL_ENV", "__PYVENV_LAUNCHER__")) {
            [Environment]::SetEnvironmentVariable($variable.Name, $null, "Process")
        }
    }
    [Environment]::SetEnvironmentVariable("PYTHONNOUSERSITE", "1", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
    [Environment]::SetEnvironmentVariable("PIP_CONFIG_FILE", "NUL", "Process")
    [Environment]::SetEnvironmentVariable("PIP_DISABLE_PIP_VERSION_CHECK", "1", "Process")
    [Environment]::SetEnvironmentVariable("PIP_NO_INPUT", "1", "Process")
}

function Set-CanonicalProcessEnvironment {
    param([string]$Path)

    Set-SafePythonProcessEnvironment
    $patronConfiguracion = '^(?:DJANGO_|WAITRESS_|POSTGRES_|POS_|PRINT_|PRINTER_|PEDIDOS_SUCURSALES_|PEDIDOS_API_|CENTRAL_|VPS_CONSOLIDACION_|THERMAL_|ALLOW_INSECURE_HTTP_LAN$|DB_ENGINE$|SQLITE_PATH$|SUCURSAL_CLAVE$)'
    foreach ($variable in Get-ChildItem Env:) {
        if ($variable.Name -match $patronConfiguracion) {
            [Environment]::SetEnvironmentVariable($variable.Name, $null, "Process")
        }
    }
    $vistas = @{}
    foreach ($linea in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($linea -notmatch '^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$') { continue }
        $nombre = $Matches[1]
        $valorBruto = $Matches[2]
        if ($nombre -notmatch $patronConfiguracion -or $vistas.ContainsKey($nombre)) {
            continue
        }
        [Environment]::SetEnvironmentVariable(
            $nombre,
            $valorBruto.Trim().Trim('"').Trim("'"),
            "Process"
        )
        $vistas[$nombre] = $true
    }
    [Environment]::SetEnvironmentVariable("DJANGO_SETTINGS_MODULE", "pos.settings", "Process")
    [Environment]::SetEnvironmentVariable("DJANGO_ALLOW_INSECURE_DEVELOPMENT", $null, "Process")
    [Environment]::SetEnvironmentVariable("DJANGO_ALLOW_INSECURE_TEST_SETTINGS", $null, "Process")
}

function Disable-ExternalSynchronization {
    # Un espacio deliberado conserva cada clave en el entorno del proceso hijo:
    # asi pos.settings no repone el valor real mediante setdefault.
    foreach ($nombre in @(
        "PEDIDOS_SUCURSALES_DATABASE_URL",
        "PEDIDOS_SUCURSALES_DB_PASSWORD",
        "PEDIDOS_API_BASE_URL",
        "PEDIDOS_API_TOKEN",
        "PEDIDOS_API_CA_BUNDLE",
        "PEDIDOS_API_SUCURSAL_IDS",
        "CENTRAL_API_BASE_URL",
        "CENTRAL_BRANCH_ID",
        "CENTRAL_BRANCH_CODE",
        "CENTRAL_POS_INSTANCE_ID",
        "CENTRAL_INGEST_TOKEN",
        "CENTRAL_CATALOG_TOKEN",
        "CENTRAL_API_CA_BUNDLE",
        "VPS_CONSOLIDACION_URL",
        "VPS_CONSOLIDACION_TOKEN"
    )) {
        [Environment]::SetEnvironmentVariable($nombre, " ", "Process")
    }
    [Environment]::SetEnvironmentVariable(
        "PEDIDOS_SUCURSALES_FUENTE", "desactivada", "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "PEDIDOS_SUCURSALES_AUTO_SYNC", "false", "Process"
    )
    foreach ($nombre in @(
        "CENTRAL_ENABLE_SALES_V2",
        "CENTRAL_ENABLE_CUSTOMERS_V2",
        "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2"
    )) {
        [Environment]::SetEnvironmentVariable($nombre, "false", "Process")
    }
}

function Enter-MaintenanceMutex {
    $nombre = "Global\LosTocayosPOS-Mantenimiento-v1"
    try {
        $mutex = New-Object Threading.Mutex($false, $nombre)
    }
    catch {
        throw "No se pudo abrir el bloqueo global de mantenimiento."
    }
    $adquirido = $false
    try {
        try {
            $adquirido = $mutex.WaitOne(0)
        }
        catch [Threading.AbandonedMutexException] {
            $adquirido = $true
        }
        if (-not $adquirido) {
            throw "Ya hay otra instalacion, actualizacion, reparacion, adopcion o sesion de diagnostico en curso."
        }
        return $mutex
    }
    catch {
        if (-not $adquirido) { $mutex.Dispose() }
        throw
    }
}

function Test-LockedDependencies {
    param([string]$PythonPath, [string]$RequirementsPath)

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { return $false }
    $reportPath = Join-Path ([IO.Path]::GetTempPath()) (
        "tocayos-pip-report-" + [Guid]::NewGuid().ToString("N") + ".json"
    )
    try {
        & $PythonPath -m pip install --dry-run --disable-pip-version-check `
            --no-index --report $reportPath -r $RequirementsPath *> $null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
            return $false
        }
        $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $report -or $report.PSObject.Properties.Name -notcontains "install") {
            return $false
        }
        if (@($report.install).Count -ne 0) { return $false }
        $comprobarConjuntoExacto = @'
import sys
from importlib.metadata import distributions
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

required = {}
for raw in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    raw = raw.strip()
    if not raw or raw.startswith('#'):
        continue
    requirement = Requirement(raw)
    if requirement.marker is not None and not requirement.marker.evaluate():
        continue
    specifiers = list(requirement.specifier)
    if requirement.url or len(specifiers) != 1 or specifiers[0].operator != '==':
        raise SystemExit(2)
    name = canonicalize_name(requirement.name)
    if name in required:
        raise SystemExit(3)
    required[name] = specifiers[0].version

installed = {}
for distribution in distributions():
    raw_name = distribution.metadata.get('Name')
    if not raw_name:
        raise SystemExit(4)
    name = canonicalize_name(raw_name)
    if name in installed:
        raise SystemExit(5)
    installed[name] = distribution.version

raise SystemExit(0 if installed == required else 1)
'@
        & $PythonPath -I -c $comprobarConjuntoExacto $RequirementsPath *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
            try { Remove-Item -LiteralPath $reportPath -Force -ErrorAction Stop }
            catch { }
        }
    }
}

$mantenimientoMutex = $null
$waitressExitCode = 0
try {
$mantenimientoMutex = Enter-MaintenanceMutex
if (-not (Test-Path -LiteralPath $entorno -PathType Leaf)) {
    throw "No existe .env. Usa instalar-servidor.ps1 o restaura la configuración de esta instalación."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "No existe una .venv instalada. Este lanzador de diagnóstico no instala dependencias."
}
$servicio = Get-Service -Name "LosTocayosPOS" -ErrorAction SilentlyContinue
if ($servicio -and $servicio.Status -ne "Stopped") {
    throw "Detén LosTocayosPOS antes de iniciar otro servidor de diagnóstico en consola."
}
Set-CanonicalProcessEnvironment -Path $entorno

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

$env:DJANGO_SETTINGS_MODULE = "pos.settings"
$env:WAITRESS_HOST = $ListenAddress
$env:WAITRESS_TRUSTED_PROXY = $trustedProxy
$env:ALLOW_INSECURE_HTTP_LAN = $(if ($httpLanPermitido) { "true" } else { "false" })
if ($AllowExternalSync) {
    Write-Warning "La sincronizacion externa fue habilitada expresamente para este diagnostico."
}
else {
    Disable-ExternalSynchronization
    Write-Host "Pedidos, Central y consolidacion permanecen aislados en este diagnostico." -ForegroundColor Cyan
}
# El diagnóstico jamás contacta impresoras físicas, aunque producción use TCP.
$env:PRINT_BACKEND = "archivo"
$env:PRINT_SYNC = "true"

if (-not (Test-LockedDependencies `
    -PythonPath $python `
    -RequirementsPath (Join-Path $raiz "requirements-lock.txt"))) {
    throw "La .venv no satisface requirements-lock.txt. Usa actualizar-servidor.ps1."
}
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "La .venv contiene dependencias incompatibles." }
$hostServicio = Join-Path $raiz ".venv\Scripts\pythonservice.exe"
& $python -E -s -c "import sys; from pathlib import Path; from herramientas.host_servicio_windows import comprobar_host; comprobar_host(Path(sys.argv[1]).resolve())" $hostServicio
if ($LASTEXITCODE -ne 0) { throw "El host del servicio no puede cargar Python y sus DLL." }
& $python manage.py check --deploy
if ($LASTEXITCODE -ne 0) {
    throw "La configuración de producción es inválida. Revisa DJANGO_SECRET_KEY y DJANGO_ALLOWED_HOSTS en .env."
}
& $python manage.py migrate --check --noinput
if ($LASTEXITCODE -ne 0) {
    throw "Hay migraciones pendientes. Usa actualizar-servidor.ps1; el diagnóstico no modifica la base."
}
& $python -c "import os; import django; django.setup(); from personas.models import Sucursal; qs=Sucursal.objects.filter(clave=os.environ['SUCURSAL_CLAVE'], activa=True); raise SystemExit(0 if qs.count() == 1 and Sucursal.objects.count() == 1 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "La base no contiene exactamente la identidad activa indicada por SUCURSAL_CLAVE."
}

Write-Host ""
Write-Host "Los Tocayos POS inició un servidor de diagnóstico controlado (puerto $Port)." -ForegroundColor Green
Write-Host "Usa la base local real: las acciones hechas en el navegador sí modifican pedidos, clientes y ventas." -ForegroundColor Yellow
Write-Host "No migra ni instala y nunca contacta impresoras físicas. Mantén esta ventana abierta." -ForegroundColor Cyan
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
$waitressExitCode = $LASTEXITCODE
}
finally {
    if ($null -ne $mantenimientoMutex) {
        try { $mantenimientoMutex.ReleaseMutex() }
        catch [ApplicationException] { }
        finally { $mantenimientoMutex.Dispose() }
    }
}
exit $waitressExitCode
