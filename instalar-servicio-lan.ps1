<#
.SYNOPSIS
Prepara e instala el POS local como servicio Windows con permisos restringidos.
.DESCRIPTION
Ejecutar con Windows PowerShell 5.1 como administrador y Python de maquina.
Valida Python, dependencias y el host pywin32 antes del endurecimiento completo.
Conserva entornos virtuales rotos, restringe datos privados y verifica /salud/.
Trabaja sobre los archivos locales; no descarga ni actualiza codigo desde GitHub.
.PARAMETER RepairPermissions
Recupera acceso efectivo de Administradores y SYSTEM sin ampliar LocalService.
No permite leer este script si su propia ACL ya impide abrirlo; ver la guia.
.PARAMETER PrepareOnly
Prepara dependencias, configuracion y base sin solicitar cuentas ni instalar el
servicio. No es una simulacion: puede detener el servicio y modifica la base.
.NOTES
La carga cargar_datos_iniciales sigue ligada a ARBOLEDAS y puede sobrescribir
catalogos y precios. Este script aun no es un actualizador generico por sucursal.
Revisar DESPLIEGUE_WINDOWS.md antes de repetirlo sobre datos personalizados.
No compartir .env, contrasenas, bases ni certificados privados.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\tcysAplicacionLocales\instalar-servicio-lan.ps1" -AllowedHosts "localhost,127.0.0.1,192.168.0.30" -ListenAddress "0.0.0.0" -AllowInsecureHttpLan -Port 8000
Instala HTTP LAN con consentimiento explicito, firewall privado y subred local.
#>
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
    [switch]$SkipBackupTask,
    [switch]$RepairPermissions,
    [switch]$PrepareOnly
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
    foreach ($linea in Get-Content -LiteralPath $Path -Encoding UTF8) {
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
    $lineas = if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path -Encoding UTF8) } else { @() }
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*="
    $encontrado = $false
    $actualizadas = @(foreach ($linea in $lineas) {
        if ($linea -match $patron) {
            if (-not $encontrado) {
                "$Name=$Value"
                $encontrado = $true
            }
        }
        else {
            $linea
        }
    })
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

    Assert-ProjectPath -Path $Path
    # Protege primero los datos privados; nunca se restablecen a ACL del padre.
    $privados = @(".env", "db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm", "db.sqlite3-journal", "backups", ".git", "tmp")
    $privados += @(Get-ChildItem -LiteralPath $Path -Directory -Force |
        Where-Object Name -Like ".venv-roto-*" | Select-Object -ExpandProperty Name)
    foreach ($nombre in $privados) {
        $acceso = if ($nombre -eq ".env") { "Read" } else { "None" }
        Protect-Path -Path (Join-Path $Path $nombre) -LocalServiceAccess $acceso
    }
    foreach ($nombre in @("runtime", "logs", "media")) {
        Protect-Path -Path (Join-Path $Path $nombre) -LocalServiceAccess "Modify"
    }
    Set-ProductionAcl -Path $Path -LocalServiceAccess "Read"
    foreach ($item in Get-ChildItem -LiteralPath $Path -Force) {
        if ($item.Name -notin ($privados + @("runtime", "logs", "media"))) {
            Protect-Path -Path $item.FullName -LocalServiceAccess "Read"
        }
    }
}

function Assert-ProjectPath {
    param([string]$Path, [switch]$ParentVerified)

    $rootPath = [IO.Path]::GetFullPath($raiz).TrimEnd('\')
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($fullPath -ne $rootPath -and
        -not $fullPath.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "La ruta queda fuera del proyecto: $Path."
    }
    # Rechaza junctions/symlinks, incluso en los padres, antes de modificar ACL.
    $cursor = $fullPath
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "No se modifican enlaces ni junctions: $cursor."
            }
        }
        if ($ParentVerified) { break }
        $cursor = Split-Path -Parent $cursor
    }
}

function Set-ProductionAcl {
    param([string]$Path, [ValidateSet("Read", "Modify", "None")][string]$LocalServiceAccess, [switch]$ParentVerified)

    Assert-ProjectPath -Path $Path -ParentVerified:$ParentVerified
    $item = Get-Item -LiteralPath $Path -Force
    $acl = if ($item.PSIsContainer) {
        New-Object Security.AccessControl.DirectorySecurity
    } else {
        New-Object Security.AccessControl.FileSecurity
    }
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $acl.SetOwner($adminSid)
    $inheritance = if ($item.PSIsContainer) { "ContainerInherit, ObjectInherit" } else { "None" }
    $rights = @{ "S-1-5-18" = "FullControl"; "S-1-5-32-544" = "FullControl" }
    if ($LocalServiceAccess -ne "None") {
        $rights["S-1-5-19"] = if ($LocalServiceAccess -eq "Modify") {
            "Modify"
        } elseif ($item.PSIsContainer -or $item.Name -ne ".env") {
            "ReadAndExecute"
        } else {
            "Read"
        }
    }
    foreach ($sid in $rights.Keys) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        # Los archivos reciben ACE efectivas, nunca marcas de herencia de carpetas.
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, $rights[$sid], $inheritance, "None", "Allow")
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Protect-Path {
    param([string]$Path, [ValidateSet("Read", "Modify", "None")][string]$LocalServiceAccess, [switch]$ParentVerified)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    Set-ProductionAcl -Path $Path -LocalServiceAccess $LocalServiceAccess -ParentVerified:$ParentVerified
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
            Protect-Path -Path $child.FullName -LocalServiceAccess $LocalServiceAccess -ParentVerified
        }
    }
}

function Test-MachinePythonPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) { return $false }
    $fullPath = [IO.Path]::GetFullPath($Path)
    $userRoots = @((Join-Path $env:SystemDrive "Users"), $env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA)
    foreach ($userRoot in $userRoots) {
        if ($userRoot -and $fullPath.StartsWith(
            $userRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return Test-Path -LiteralPath $fullPath -PathType Leaf
}

function Get-MachinePython {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += @(& py -0p 2>$null | ForEach-Object {
            if ($_ -match '([A-Za-z]:\\.+?python(?:3)?\.exe)\s*$') { $Matches[1] }
        })
    }
    $candidates += @(Get-ChildItem 'HKLM:\SOFTWARE\Python\PythonCore\*\InstallPath' -ErrorAction SilentlyContinue |
        ForEach-Object { $_.GetValue('ExecutablePath') })
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-MachinePythonPath -Path $candidate)) { continue }
        try {
            $base = & $candidate -I -c "import sys; from pathlib import Path; assert sys.version_info >= (3, 10); print(Path(sys.base_prefix) / 'python.exe')" 2>$null
            if ($LASTEXITCODE -eq 0 -and (Test-MachinePythonPath -Path ([string]$base))) {
                return $candidate
            }
        } catch { continue }
    }
    throw "No hay un Python de maquina ejecutable (3.10+). Instala Python para todos los usuarios y comprueba py -0p. No se han cambiado ACL ni detenido el servicio."
}

function Restore-AdministrativeAccess {
    param([string]$Path = $raiz, [switch]$ParentVerified)

    Assert-ProjectPath -Path $Path -ParentVerified:$ParentVerified
    $item = Get-Item -LiteralPath $Path -Force
    $acl = Get-Acl -LiteralPath $Path
    $inheritance = if ($item.PSIsContainer) { "ContainerInherit, ObjectInherit" } else { "None" }
    # Conserva las demas identidades. La reparacion nunca concede acceso al servicio.
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, "FullControl", $inheritance, "None", "Allow")
        $acl.SetAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
            Restore-AdministrativeAccess -Path $child.FullName -ParentVerified
        }
    }
}

function Test-VirtualEnvironment {
    param([string]$Path)

    try {
        if (-not (Test-Path -LiteralPath (Join-Path $Path 'pyvenv.cfg') -PathType Leaf)) { return $false }
        $executable = Join-Path $Path 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { return $false }
        $base = & $executable -I -c "import sys, pip; from pathlib import Path; assert sys.prefix != sys.base_prefix; print(Path(sys.base_prefix) / 'python.exe')" 2>$null
        return $LASTEXITCODE -eq 0 -and (Test-MachinePythonPath -Path ([string]$base))
    } catch { return $false }
}

function Initialize-VirtualEnvironment {
    param([string]$MachinePython)

    $venvPath = Join-Path $raiz '.venv'
    Assert-ProjectPath -Path $venvPath
    if ((Test-Path -LiteralPath $venvPath) -and -not (Test-VirtualEnvironment -Path $venvPath)) {
        $savedPath = Join-Path $raiz ('.venv-roto-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
        Assert-ProjectPath -Path $savedPath
        try {
            Move-Item -LiteralPath $venvPath -Destination $savedPath
        } catch {
            throw "La .venv esta rota o no es accesible. No se borro nada. Desde PowerShell Administrador revisa sus ACL y renombra exactamente $venvPath antes de repetir."
        }
        Write-Host "Entorno anterior conservado en $savedPath" -ForegroundColor Yellow
    }
    if (-not (Test-Path -LiteralPath $venvPath)) {
        & $MachinePython -I -m venv $venvPath
        if ($LASTEXITCODE -ne 0) { throw "No fue posible crear .venv; puede repetirse el instalador para recuperarla." }
    }
    if (-not (Test-VirtualEnvironment -Path $venvPath)) {
        throw "La .venv no supero la validacion de Python/pip. No se han endurecido las ACL del proyecto."
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

# Esta comprobacion precede a cualquier cambio de ACL, .env o servicio.
$machinePython = Get-MachinePython
Assert-ProjectPath -Path $raiz
if ($RepairPermissions) { Restore-AdministrativeAccess }
foreach ($archivo in @("requirements.txt", "manage.py", "servicio_windows.py")) {
    $stream = [IO.File]::OpenRead((Join-Path $raiz $archivo))
    $stream.Dispose()
}

$existente = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
if ($existente -and $existente.Status -ne "Stopped") {
    Write-Host "Deteniendo la versión anterior antes de actualizar archivos o base de datos..." -ForegroundColor Yellow
    Stop-Service -Name $nombreServicio
    $existente.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
}

Initialize-VirtualEnvironment -MachinePython $machinePython
$pythonEscucha = [string](& $python -I -c "import sys; print(sys._base_executable)")
if ($LASTEXITCODE -ne 0 -or -not (Test-MachinePythonPath -Path $pythonEscucha.Trim())) {
    throw "No fue posible resolver el ejecutable de Python que escuchara en la LAN."
}
$pythonEscucha = $pythonEscucha.Trim()
Write-Host "Instalando dependencias de produccion..." -ForegroundColor Yellow
& $python -c "import pip; p=tuple(int(x) for x in pip.__version__.split('.')[:2]); raise SystemExit(0 if p >= (26, 2) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install --upgrade "pip>=26.2,<27"
    if ($LASTEXITCODE -ne 0) { throw "No fue posible actualizar pip a una version corregida." }
}
& $python -m pip install -r (Join-Path $raiz "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de dependencias; no se endurecieron las ACL." }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Las dependencias instaladas no son compatibles." }
& $python $servicioPython --check-host
if ($LASTEXITCODE -ne 0) { throw "El host del servicio no puede cargar Python y sus DLL. No se han endurecido las ACL." }
& $python (Join-Path $raiz "herramientas\validar_despliegue.py")
if ($LASTEXITCODE -ne 0) { throw "Fallo la validacion aislada; no se modifico .env ni se endurecieron las ACL." }

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

# Un .env nuevo se restringe antes de escribir secretos. Los existentes no se copian.
if (-not (Test-Path -LiteralPath $entorno)) {
    New-Item -ItemType File -Path $entorno | Out-Null
}
Protect-Path -Path $entorno -LocalServiceAccess "Read"
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
        & $python -c "import sqlite3, sys; from pathlib import Path; from contextlib import closing; src=Path(sys.argv[1]).resolve().as_uri() + '?mode=ro'; exec('with closing(sqlite3.connect(src, uri=True)) as source, closing(sqlite3.connect(sys.argv[2])) as dest:\n source.backup(dest)')" (Join-Path $raiz "db.sqlite3") $sqliteDestino
        if ($LASTEXITCODE -ne 0) { throw "No fue posible copiar SQLite de forma consistente a runtime." }
    }
    Set-DotEnvValue -Path $entorno -Name "SQLITE_PATH" -Value "runtime/db.sqlite3"
}

@("runtime", "backups", "logs", "media", "certs") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $raiz $_) | Out-Null
}

# Los respaldos y certificados permanecen restringidos incluso si falla una migracion.
Protect-Path -Path $backupRoot -LocalServiceAccess "None"
Protect-Path -Path (Join-Path $raiz "certs") -LocalServiceAccess "Read"

& $python manage.py check --deploy
if ($LASTEXITCODE -ne 0) { throw "La configuración Django de producción no es válida." }
if ($usaSqlite -and (Test-Path -LiteralPath $sqliteDestino)) {
    Write-Host "Respaldo verificable antes de migrar..." -ForegroundColor Yellow
    Invoke-SqliteBackup -PythonPath $python -DatabasePath $sqliteDestino -BackupRoot $backupRoot -LogPath $backupLog -RetentionDays $BackupRetentionDays
}
& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Fallaron las migraciones." }
& $python manage.py cargar_datos_iniciales
if ($LASTEXITCODE -ne 0) { throw "Falló la carga de catálogos iniciales." }
& $python manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw "Falló la recopilación de archivos estáticos." }

if ($PrepareOnly) {
    Write-Host "Preparacion completa. Repite sin -PrepareOnly para crear las cuentas faltantes e instalar el servicio." -ForegroundColor Green
    return
}

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
        -Program $pythonEscucha | Out-Null
}
elseif ($Https) {
    Get-NetFirewallRule -DisplayName $nombreFirewall -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
}

# Solo tras dependencias, pruebas, checks, migraciones y registro del servicio.
# Cada ACL conserva acceso efectivo para Administradores y SYSTEM, aun si falla
# la siguiente operacion. Nunca se restaura una ACL vacia del intento anterior.
Write-Host "Aplicando permisos de produccion..." -ForegroundColor Yellow
Protect-ApplicationTree -Path $raiz

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
