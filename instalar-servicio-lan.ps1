param(
    [string]$AllowedHosts = "localhost,127.0.0.1,192.168.0.30",
    [string]$SecretKey,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(2, 64)][int]$Threads = 8,
    [string]$ListenAddress = "0.0.0.0",
    [string]$TrustedProxy,
    [switch]$Https,
    [switch]$AllowInsecureHttpLan,
    [switch]$SkipFirewall,
    [ValidatePattern("^(?:[01]\d|2[0-3]):[0-5]\d$")][string]$BackupTime = "03:15",
    [ValidateRange(1, 3650)][int]$BackupRetentionDays = 30,
    [switch]$SkipBackupTask
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$servicioPython = Join-Path $raiz "servicio_windows.py"
$scriptRespaldo = Join-Path $raiz "respaldar-db-sqlite.ps1"
$entorno = Join-Path $raiz ".env"
$nombreServicio = "LosTocayosPOS"
$nombreFirewall = "Los Tocayos POS - LAN privada"
$nombreTareaRespaldo = "LosTocayosPOS-RespaldoSQLite"

Set-Location -LiteralPath $raiz

$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecuta este instalador desde PowerShell como administrador."
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($linea in Get-Content -LiteralPath $Path) {
        if ($linea -match $patron) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)

    if ($Value -match "[\r\n]") {
        throw "$Name contiene un salto de línea no permitido."
    }
    $lineas = if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path) } else { @() }
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*="
    $encontrado = $false
    $actualizadas = foreach ($linea in $lineas) {
        if ($linea -match $patron) {
            if (-not $encontrado) {
                "$Name=$Value"
                $encontrado = $true
            }
        }
        else {
            $linea
        }
    }
    if (-not $encontrado) {
        $actualizadas += "$Name=$Value"
    }
    Set-Content -LiteralPath $Path -Value $actualizadas -Encoding UTF8
}

function New-SecretKey {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Test-AllowedHost {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -ne $Value.Trim() -or
        $Value -match "\s|\*|/|\\|://") {
        return $false
    }
    if ($Value.StartsWith("[") -and $Value.EndsWith("]")) {
        $direccion = $null
        return [Net.IPAddress]::TryParse($Value.Substring(1, $Value.Length - 2), [ref]$direccion) -and
            $direccion.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6
    }
    if ($Value.Contains(":")) {
        return $false
    }
    $ip = $null
    if ([Net.IPAddress]::TryParse($Value, [ref]$ip)) {
        return $true
    }
    return $Value -match "^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
}

function Protect-ApplicationTree {
    param([string]$Path)

    $system = "*S-1-5-18:(OI)(CI)F"
    $admins = "*S-1-5-32-544:(OI)(CI)F"
    $localService = "*S-1-5-19:(OI)(CI)RX"
    # Quita ACE explícitas heredadas del checkout antes de aplicar la allow-list.
    & icacls.exe $Path "/reset" "/T" "/C" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible restablecer las ACL del árbol de la aplicación."
    }
    & icacls.exe $Path "/setowner" "*S-1-5-32-544" "/T" "/C" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible asignar la propiedad del árbol al grupo Administradores."
    }
    & icacls.exe $Path `
        "/inheritance:r" `
        "/remove:g" "*S-1-5-11" "*S-1-5-32-545" `
        "/grant:r" $system $admins $localService `
        "/T" "/C" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible proteger el árbol de la aplicación."
    }
}

function Protect-Path {
    param([string]$Path, [ValidateSet("Read", "Modify", "None")][string]$LocalServiceAccess)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path
    $system = if ($item.PSIsContainer) { "*S-1-5-18:(OI)(CI)F" } else { "*S-1-5-18:F" }
    $admins = if ($item.PSIsContainer) { "*S-1-5-32-544:(OI)(CI)F" } else { "*S-1-5-32-544:F" }
    $argumentos = @($Path, "/inheritance:r", "/grant:r", $system, $admins)
    if ($LocalServiceAccess -ne "None") {
        $permiso = if ($LocalServiceAccess -eq "Modify") { "M" } else { "R" }
        if ($item.PSIsContainer) {
            $permiso = "(OI)(CI)$permiso"
        }
        $argumentos += "*S-1-5-19:$permiso"
    }
    else {
        $argumentos += @("/remove:g", "*S-1-5-19")
    }
    $argumentos += "/C"
    if ($item.PSIsContainer) {
        $argumentos += "/T"
    }
    & icacls.exe @argumentos | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible restringir permisos NTFS en $Path."
    }
}

function Quote-TaskArgument {
    param([string]$Value)

    if ($Value -match '"') {
        throw "Las rutas de la tarea programada no pueden contener comillas dobles."
    }
    return '"' + $Value + '"'
}

function Register-SqliteBackupTask {
    param(
        [string]$PythonPath,
        [string]$DatabasePath,
        [string]$BackupRoot,
        [string]$LogPath,
        [string]$DailyTime,
        [int]$RetentionDays
    )

    if (-not (Test-Path -LiteralPath $scriptRespaldo)) {
        throw "No se encontro el script de respaldo $scriptRespaldo."
    }
    $hora = [TimeSpan]::ParseExact($DailyTime, "hh\:mm", [Globalization.CultureInfo]::InvariantCulture)
    $powershell = Join-Path $PSHOME "powershell.exe"
    $argumentos = @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Quote-TaskArgument -Value $scriptRespaldo),
        "-Python",
        (Quote-TaskArgument -Value $PythonPath),
        "-DatabasePath",
        (Quote-TaskArgument -Value $DatabasePath),
        "-BackupRoot",
        (Quote-TaskArgument -Value $BackupRoot),
        "-LogPath",
        (Quote-TaskArgument -Value $LogPath),
        "-RetentionDays",
        $RetentionDays
    ) -join " "
    $accion = New-ScheduledTaskAction `
        -Execute $powershell `
        -Argument $argumentos `
        -WorkingDirectory $raiz
    $disparador = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.Add($hora))
    $principalTarea = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
    $configuracionTarea = New-ScheduledTaskSettingsSet `
        -Compatibility Win8 `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName $nombreTareaRespaldo `
        -Action $accion `
        -Trigger $disparador `
        -Principal $principalTarea `
        -Settings $configuracionTarea `
        -Description "Respaldo consistente y verificable de runtime\db.sqlite3 de Los Tocayos POS." `
        -Force | Out-Null
}

function Remove-SqliteBackupTask {
    $tarea = Get-ScheduledTask -TaskName $nombreTareaRespaldo -ErrorAction SilentlyContinue
    if ($tarea) {
        Unregister-ScheduledTask -TaskName $nombreTareaRespaldo -Confirm:$false
    }
}

function Invoke-SqliteBackup {
    param(
        [string]$PythonPath,
        [string]$DatabasePath,
        [string]$BackupRoot,
        [string]$LogPath,
        [int]$RetentionDays
    )

    if (-not (Test-Path -LiteralPath $scriptRespaldo)) {
        throw "No se encontro el script de respaldo $scriptRespaldo."
    }
    & $scriptRespaldo `
        -Python $PythonPath `
        -DatabasePath $DatabasePath `
        -BackupRoot $BackupRoot `
        -LogPath $LogPath `
        -RetentionDays $RetentionDays
    if ($LASTEXITCODE -ne 0) {
        throw "Fallo el respaldo verificable inicial de runtime\db.sqlite3."
    }
}

$hosts = @($AllowedHosts -split ",")
if (-not $hosts.Count -or @($hosts | Where-Object { -not (Test-AllowedHost -Value $_) }).Count) {
    throw "AllowedHosts sólo acepta hosts/IP concretos, sin comodines, espacios, esquemas, rutas ni puertos."
}

if ($Https -and -not $PSBoundParameters.ContainsKey("ListenAddress")) {
    $ListenAddress = "127.0.0.1"
}
$direccionEscucha = $null
if (-not [Net.IPAddress]::TryParse($ListenAddress, [ref]$direccionEscucha)) {
    throw "ListenAddress debe ser una dirección IP local concreta."
}
if ($Https -and [string]::IsNullOrWhiteSpace($TrustedProxy)) {
    $TrustedProxy = if ($direccionEscucha.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6) {
        "::1"
    }
    else {
        "127.0.0.1"
    }
}
if (-not [string]::IsNullOrWhiteSpace($TrustedProxy)) {
    $direccionProxy = $null
    if (-not [Net.IPAddress]::TryParse($TrustedProxy, [ref]$direccionProxy)) {
        throw "TrustedProxy debe ser una dirección IP concreta."
    }
    if (-not [Net.IPAddress]::IsLoopback($direccionProxy)) {
        throw "TrustedProxy debe ser una dirección loopback del mismo servidor."
    }
}
if ($Https -and (-not [Net.IPAddress]::IsLoopback($direccionEscucha) -or
    -not [Net.IPAddress]::IsLoopback($direccionProxy))) {
    throw "Con -Https, Waitress y el proxy confiable deben usar direcciones loopback."
}
if ($Https -and $AllowInsecureHttpLan) {
    throw "-Https y -AllowInsecureHttpLan son opciones mutuamente excluyentes."
}
if (-not $Https -and -not [Net.IPAddress]::IsLoopback($direccionEscucha) -and
    -not $AllowInsecureHttpLan) {
    throw "HTTP LAN no cifra credenciales ni pedidos. Usa -Https o confirma el riesgo con -AllowInsecureHttpLan."
}

$existente = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
if ($existente -and $existente.Status -ne "Stopped") {
    Write-Host "Deteniendo la versión anterior antes de actualizar archivos o base de datos..." -ForegroundColor Yellow
    Stop-Service -Name $nombreServicio
    $existente.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
}

$claveExistente = Get-DotEnvValue -Path $entorno -Name "DJANGO_SECRET_KEY"
if ([string]::IsNullOrWhiteSpace($SecretKey)) {
    $SecretKey = if ([string]::IsNullOrWhiteSpace($claveExistente)) { New-SecretKey } else { $claveExistente }
}
if ($SecretKey.Length -lt 50 -or
    @($SecretKey.ToCharArray() | Sort-Object -Unique).Count -lt 5 -or
    $SecretKey.ToLowerInvariant().StartsWith("cambiar") -or
    $SecretKey.ToLowerInvariant().StartsWith("solo-desarrollo") -or
    $SecretKey.ToLowerInvariant().StartsWith("clave-exclusiva-de-prueba") -or
    $SecretKey.ToLowerInvariant().StartsWith("django-insecure-")) {
    throw "SecretKey debe tener al menos 50 caracteres aleatorios y no ser un marcador de ejemplo."
}

Set-DotEnvValue -Path $entorno -Name "DJANGO_SECRET_KEY" -Value $SecretKey
Set-DotEnvValue -Path $entorno -Name "DJANGO_DEBUG" -Value "false"
Set-DotEnvValue -Path $entorno -Name "DJANGO_ALLOWED_HOSTS" -Value $AllowedHosts
Set-DotEnvValue -Path $entorno -Name "DJANGO_HTTPS" -Value $(if ($Https) { "true" } else { "false" })
Set-DotEnvValue -Path $entorno -Name "ALLOW_INSECURE_HTTP_LAN" -Value $(if ($AllowInsecureHttpLan) { "true" } else { "false" })
Set-DotEnvValue -Path $entorno -Name "WAITRESS_HOST" -Value $ListenAddress
Set-DotEnvValue -Path $entorno -Name "WAITRESS_PORT" -Value $Port
Set-DotEnvValue -Path $entorno -Name "WAITRESS_THREADS" -Value $Threads
Set-DotEnvValue -Path $entorno -Name "WAITRESS_CONNECTION_LIMIT" -Value "100"
Set-DotEnvValue -Path $entorno -Name "WAITRESS_CHANNEL_TIMEOUT" -Value "30"
Set-DotEnvValue -Path $entorno -Name "WAITRESS_CLEANUP_INTERVAL" -Value "10"
Set-DotEnvValue -Path $entorno -Name "WAITRESS_TRUSTED_PROXY" -Value $TrustedProxy

$dbEngine = Get-DotEnvValue -Path $entorno -Name "DB_ENGINE"
$usaSqlite = [string]::IsNullOrWhiteSpace($dbEngine) -or $dbEngine.ToLowerInvariant() -eq "sqlite"
$runtime = Join-Path $raiz "runtime"
$sqliteDestino = Join-Path $runtime "db.sqlite3"
$backupRoot = Join-Path $raiz "backups"
$backupLog = Join-Path $raiz "logs\sqlite-backup.log"
if ($usaSqlite) {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    if (-not (Test-Path -LiteralPath $sqliteDestino) -and (Test-Path -LiteralPath (Join-Path $raiz "db.sqlite3"))) {
        Copy-Item -LiteralPath (Join-Path $raiz "db.sqlite3") -Destination $sqliteDestino
    }
    Set-DotEnvValue -Path $entorno -Name "SQLITE_PATH" -Value "runtime/db.sqlite3"
}

@("runtime", "backups", "logs", "media", "certs") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $raiz $_) | Out-Null
}

# Desde este punto sólo Administradores puede modificar código, dependencias o
# configuración. LocalService obtiene lectura/ejecución y escritura únicamente
# en los directorios operativos declarados.
Protect-ApplicationTree -Path $raiz
Protect-Path -Path (Join-Path $raiz ".env") -LocalServiceAccess "Read"
Protect-Path -Path (Join-Path $raiz "db.sqlite3") -LocalServiceAccess "None"
Protect-Path -Path $backupRoot -LocalServiceAccess "None"
@("runtime", "logs", "media") | ForEach-Object {
    Protect-Path -Path (Join-Path $raiz $_) -LocalServiceAccess "Modify"
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Yellow
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Instala Python para todos los usuarios y vuelve a ejecutar el instalador."
    }
    & py -m venv (Join-Path $raiz ".venv")
    if ($LASTEXITCODE -ne 0) { throw "No fue posible crear el entorno virtual." }
}

$pythonBase = (& $python -c "import sys; print(sys.base_prefix)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pythonBase)) {
    throw "No fue posible validar el Python base del entorno virtual."
}
$pythonBaseCompleto = [IO.Path]::GetFullPath($pythonBase)
$perfilesUsuarios = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive "Users"))
if ($pythonBaseCompleto.StartsWith($perfilesUsuarios + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "El entorno virtual depende de un Python instalado en un perfil de usuario. Instala Python para todos los usuarios, elimina .venv y repite."
}

Write-Host "Instalando dependencias de producción..." -ForegroundColor Yellow
& $python -c "import pip; p=tuple(int(x) for x in pip.__version__.split('.')[:2]); raise SystemExit(0 if p >= (26, 2) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install --upgrade "pip>=26.2,<27"
    if ($LASTEXITCODE -ne 0) { throw "No fue posible actualizar pip a una versión corregida." }
}
& $python -m pip install -r (Join-Path $raiz "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de dependencias." }

& $python manage.py check --deploy
if ($LASTEXITCODE -ne 0) { throw "La configuración Django de producción no es válida." }
& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Fallaron las migraciones." }
& $python manage.py cargar_datos_iniciales
if ($LASTEXITCODE -ne 0) { throw "Falló la carga de catálogos iniciales." }
& $python manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw "Falló la recopilación de archivos estáticos." }

& $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; raise SystemExit(0 if get_user_model().objects.filter(is_active=True, is_superuser=True).exists() else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "No existe una cuenta administrativa. Crea la primera ahora; la contraseña no se mostrará." -ForegroundColor Yellow
    & $python manage.py createsuperuser
    if ($LASTEXITCODE -ne 0) {
        throw "Debe existir al menos un superusuario antes de iniciar producción."
    }
}

& $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; U=get_user_model(); raise SystemExit(0 if U.objects.filter(is_active=True, is_superuser=False, perfil_pos__activo=True, perfil_pos__sucursal__clave=os.environ.get('SUCURSAL_CLAVE','ARBOLEDAS')).exists() else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Crea una cuenta operativa separada de la cuenta administrativa." -ForegroundColor Yellow
    & $python manage.py crear_operador_pos
    if ($LASTEXITCODE -ne 0) {
        throw "Debe existir al menos una cuenta operativa vinculada a un perfil POS."
    }
}

# Reaplica la allow-list después de instalar paquetes o generar estáticos.
Protect-ApplicationTree -Path $raiz
Protect-Path -Path (Join-Path $raiz ".env") -LocalServiceAccess "Read"
Protect-Path -Path (Join-Path $raiz "db.sqlite3") -LocalServiceAccess "None"
Protect-Path -Path $backupRoot -LocalServiceAccess "None"
@("runtime", "logs", "media") | ForEach-Object {
    Protect-Path -Path (Join-Path $raiz $_) -LocalServiceAccess "Modify"
}

if ($usaSqlite -and -not $SkipBackupTask) {
    Write-Host "Creando respaldo inicial verificable de runtime\db.sqlite3..." -ForegroundColor Yellow
    Invoke-SqliteBackup `
        -PythonPath $python `
        -DatabasePath $sqliteDestino `
        -BackupRoot $backupRoot `
        -LogPath $backupLog `
        -RetentionDays $BackupRetentionDays
    Register-SqliteBackupTask `
        -PythonPath $python `
        -DatabasePath $sqliteDestino `
        -BackupRoot $backupRoot `
        -LogPath $backupLog `
        -DailyTime $BackupTime `
        -RetentionDays $BackupRetentionDays
}
else {
    Remove-SqliteBackupTask
    if ($usaSqlite) {
        Write-Host "Se omitio la tarea programada de respaldo por -SkipBackupTask." -ForegroundColor Yellow
    }
    else {
        Write-Host "DB_ENGINE no usa SQLite; se retiro la tarea programada de respaldo local." -ForegroundColor Yellow
    }
}

$accion = if ($existente) { "update" } else { "install" }
& $python $servicioPython --startup delayed --username "NT AUTHORITY\LocalService" $accion
if ($LASTEXITCODE -ne 0) { throw "No fue posible $accion el servicio de Windows." }

& sc.exe failure $nombreServicio reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "No fue posible configurar la recuperación automática del servicio." }

if (-not $SkipFirewall -and -not $Https) {
    Get-NetFirewallRule -DisplayName $nombreFirewall -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    New-NetFirewallRule `
        -DisplayName $nombreFirewall `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Private `
        -RemoteAddress LocalSubnet `
        -Program $python | Out-Null
}
elseif ($Https) {
    Get-NetFirewallRule -DisplayName $nombreFirewall -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
}

Start-Service -Name $nombreServicio
(Get-Service -Name $nombreServicio).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))

$hostSalud = if ($ListenAddress -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $ListenAddress }
if ($hostSalud.Contains(":") -and -not $hostSalud.StartsWith("[")) { $hostSalud = "[$hostSalud]" }
$urlSalud = "http://${hostSalud}:$Port/salud/"
$cabecerasSalud = @{ "Host" = $hosts[0] }
if ($Https) { $cabecerasSalud["X-Forwarded-Proto"] = "https" }
$limiteSalud = (Get-Date).AddSeconds(30)
$saludable = $false
do {
    try {
        $respuestaSalud = Invoke-WebRequest -UseBasicParsing -Uri $urlSalud -Headers $cabecerasSalud -TimeoutSec 2
        $contenidoSalud = $respuestaSalud.Content | ConvertFrom-Json
        $saludable = $respuestaSalud.StatusCode -eq 200 -and $contenidoSalud.estado -eq "ok"
    }
    catch {
        $saludable = $false
    }
    if (-not $saludable) { Start-Sleep -Milliseconds 500 }
} while (-not $saludable -and (Get-Date) -lt $limiteSalud)
if (-not $saludable) {
    throw "El servicio se instaló, pero /salud/ no respondió correctamente. Revisa logs\waitress.log."
}

Write-Host ""
Write-Host "Servicio Los Tocayos POS instalado e iniciado con Waitress." -ForegroundColor Green
Write-Host "Hosts permitidos: $AllowedHosts"
if ($Https) {
    Write-Host "Waitress escucha sólo en $ListenAddress y confía en el proxy $TrustedProxy. Publica HTTPS desde el proxy."
}
else {
    Write-Host "Puerto LAN: $Port (sólo perfil privado/subred local)"
}
Write-Host "La clave secreta quedó guardada en .env con permisos NTFS restringidos."
if ($usaSqlite -and -not $SkipBackupTask) {
    Write-Host "Respaldo diario: tarea $nombreTareaRespaldo a las $BackupTime; retencion $BackupRetentionDays dias."
}
