<#
.SYNOPSIS
Adopta o valida una identidad de sucursal sin instalar, migrar ni sembrar datos.
.DESCRIPTION
Uso único y supervisado con el servicio detenido. Es también la ruta de adopción
para instalaciones antiguas cuyo .env no contenía SUCURSAL_CLAVE.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SucursalClave,
    [Parameter(Mandatory = $true)][string]$SucursalNombre,
    [string]$SucursalId
)

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
$entorno = Join-Path $raiz ".env"
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$managePy = Join-Path $raiz "manage.py"
$nombreServicio = "LosTocayosPOS"

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($linea in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($linea -match $patron) { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $null
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
    $patronConfiguracion = '^(?:DJANGO_|WAITRESS_|POSTGRES_|POS_|PRINT_|PRINTER_|PEDIDOS_SUCURSALES_|THERMAL_|ALLOW_INSECURE_HTTP_LAN$|DB_ENGINE$|SQLITE_PATH$|SUCURSAL_CLAVE$)'
    foreach ($variable in Get-ChildItem Env:) {
        if ($variable.Name -match $patronConfiguracion) {
            [Environment]::SetEnvironmentVariable($variable.Name, $null, "Process")
        }
    }
    $vistas = @{}
    foreach ($linea in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($linea -notmatch '^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$') { continue }
        $variableNombre = $Matches[1]
        $valorBruto = $Matches[2]
        if ($variableNombre -notmatch $patronConfiguracion -or
            $vistas.ContainsKey($variableNombre)) { continue }
        $valor = $valorBruto.Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($variableNombre, $valor, "Process")
        $vistas[$variableNombre] = $true
    }
    [Environment]::SetEnvironmentVariable("DJANGO_SETTINGS_MODULE", "pos.settings", "Process")
    [Environment]::SetEnvironmentVariable("DJANGO_ALLOW_INSECURE_DEVELOPMENT", $null, "Process")
    [Environment]::SetEnvironmentVariable("DJANGO_ALLOW_INSECURE_TEST_SETTINGS", $null, "Process")
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    if ($Value -match "[\r\n]") { throw "$Name contiene un salto de línea no permitido." }
    $lineas = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*="
    $encontrado = $false
    $actualizadas = @(foreach ($linea in $lineas) {
        if ($linea -match $patron) {
            if (-not $encontrado) {
                "$Name=$Value"
                $encontrado = $true
            }
        }
        else { $linea }
    })
    if (-not $encontrado) { $actualizadas += "$Name=$Value" }
    Set-Content -LiteralPath $Path -Value $actualizadas -Encoding UTF8
}

function Protect-SecretBackup {
    param([string]$Path)

    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $acl.SetOwner($adminSid)
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, "FullControl", "Allow"
        )
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Get-ServiceExecutablePath {
    param([string]$PathName)

    $valor = ([string]$PathName).Trim()
    if ($valor.StartsWith('"')) {
        if ($valor -notmatch '^"([^"]+)"(?:\s|$)') {
            throw "PathName del servicio no es válido."
        }
        return [IO.Path]::GetFullPath($Matches[1])
    }
    $ejecutable = ($valor -split '\s+', 2)[0]
    if ([string]::IsNullOrWhiteSpace($ejecutable)) {
        throw "PathName del servicio está vacío."
    }
    return [IO.Path]::GetFullPath($ejecutable)
}

function Assert-ServiceBelongsToProject {
    $registro = Get-CimInstance Win32_Service -Filter "Name='$nombreServicio'" -ErrorAction Stop
    $actual = Get-ServiceExecutablePath -PathName $registro.PathName
    $esperado = [IO.Path]::GetFullPath((Join-Path $raiz ".venv\Scripts\pythonservice.exe"))
    if (-not $actual.Equals($esperado, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El servicio $nombreServicio pertenece a otra instalación ($actual)."
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

function Enter-BackupMutex {
    param([ValidateRange(1, 3600)][int]$TimeoutSeconds = 600)

    if ($PSVersionTable.PSEdition -ne "Desktop") {
        throw "El aprovisionamiento protegido debe ejecutarse con Windows PowerShell 5.1."
    }
    $security = New-Object Security.AccessControl.MutexSecurity
    $security.SetAccessRuleProtection($true, $false)
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.MutexAccessRule(
            $identity,
            [Security.AccessControl.MutexRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$security.AddAccessRule($rule)
    }
    $createdNew = $false
    $mutex = New-Object Threading.Mutex(
        $false,
        "Global\LosTocayosPOS-RespaldoSQLite-v1",
        [ref]$createdNew,
        $security
    )
    $acquired = $false
    try {
        try {
            $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
        }
        catch [Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if (-not $acquired) {
            throw "Hay un respaldo SQLite en curso despues de $TimeoutSeconds segundos."
        }
        return $mutex
    }
    catch {
        if (-not $acquired) { $mutex.Dispose() }
        throw
    }
}

$mantenimientoMutex = $null
$respaldoMutex = $null
try {
$mantenimientoMutex = Enter-MaintenanceMutex
$identidadWindows = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidadWindows)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecuta el aprovisionamiento desde PowerShell como administrador."
}
if (-not (Test-Path -LiteralPath $entorno -PathType Leaf)) {
    throw "No existe .env. Ejecuta primero la instalación o prepara su configuración segura."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "No existe el Python del entorno virtual. Ejecuta primero la instalación."
}
if (-not (Test-Path -LiteralPath $managePy -PathType Leaf)) {
    throw "No existe manage.py en el directorio del servidor."
}
$servicio = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
if ($servicio) {
    Assert-ServiceBelongsToProject
    if ($servicio.Status -ne "Stopped") {
        throw "Detén $nombreServicio antes de adoptar o validar la identidad de sucursal."
    }
}

$clave = ([string]$SucursalClave).Trim().ToUpperInvariant()
if ($clave -notmatch '^[A-Z0-9](?:[A-Z0-9_-]{0,28}[A-Z0-9])?$') {
    throw "SucursalClave no tiene un formato válido."
}
$nombre = ([string]$SucursalNombre).Trim()
if ([string]::IsNullOrWhiteSpace($nombre) -or $nombre.Length -gt 120 -or
    $nombre -match '[\x00-\x1f\x7f]') {
    throw "SucursalNombre no tiene un formato válido."
}
if (-not [string]::IsNullOrWhiteSpace($SucursalId)) {
    $sucursalGuid = [Guid]::Empty
    if (-not [Guid]::TryParse($SucursalId.Trim(), [ref]$sucursalGuid) -or
        $sucursalGuid -eq [Guid]::Empty) {
        throw "SucursalId debe ser un UUID válido y distinto del UUID vacío."
    }
    $SucursalId = $sucursalGuid.ToString()
}
$claveExistente = Get-DotEnvValue -Path $entorno -Name "SUCURSAL_CLAVE"
if (-not [string]::IsNullOrWhiteSpace($claveExistente) -and
    $claveExistente.Trim().ToUpperInvariant() -ne $clave) {
    throw "La identidad existente de .env no coincide. Este flujo no cambia una instalación a otra sucursal."
}

$respaldoMutex = Enter-BackupMutex
$respaldo = $null
$hashOriginal = (Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash
if ([string]::IsNullOrWhiteSpace($claveExistente)) {
    $backupRoot = Join-Path $raiz "backups"
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $respaldo = Join-Path $backupRoot (
        "env-antes-aprovisionamiento-" + (Get-Date -Format "yyyyMMdd-HHmmss") +
        "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".backup"
    )
    Copy-Item -LiteralPath $entorno -Destination $respaldo
    Protect-SecretBackup -Path $respaldo
    if ((Get-FileHash -LiteralPath $respaldo -Algorithm SHA256).Hash -ne $hashOriginal) {
        throw "El respaldo de .env no coincide; no se escribirá la identidad."
    }
}

Set-CanonicalProcessEnvironment -Path $entorno
$env:SUCURSAL_CLAVE = $clave
$argumentos = @($managePy, "aprovisionar_sucursal", "--clave", $clave, "--nombre", $nombre)
if (-not [string]::IsNullOrWhiteSpace($SucursalId)) {
    $argumentos += @("--sucursal-id", $SucursalId)
}
try {
    if ($respaldo) {
        Set-DotEnvValue -Path $entorno -Name "SUCURSAL_CLAVE" -Value $clave
    }
    & $python @argumentos
    if ($LASTEXITCODE -ne 0) { throw "La base rechazó el aprovisionamiento." }
}
catch {
    $falloOriginal = $_
    if ($respaldo) {
        try {
            Copy-Item -LiteralPath $respaldo -Destination $entorno -Force
            if ((Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash -ne $hashOriginal) {
                throw "El archivo restaurado no coincide con el original."
            }
        }
        catch {
            throw "Falló el aprovisionamiento y no pudo restaurarse .env. Conserva $respaldo y no inicies el servicio."
        }
    }
    throw $falloOriginal
}
Write-Host "Identidad aprovisionada y guardada: $clave. El servicio permanece detenido." -ForegroundColor Green
}
finally {
    if ($null -ne $respaldoMutex) {
        try { $respaldoMutex.ReleaseMutex() }
        catch { Write-Warning "No fue posible liberar normalmente el mutex de respaldo." }
        finally { $respaldoMutex.Dispose() }
    }
    if ($null -ne $mantenimientoMutex) {
        try { $mantenimientoMutex.ReleaseMutex() }
        catch { Write-Warning "No fue posible liberar normalmente el mutex de mantenimiento." }
        finally { $mantenimientoMutex.Dispose() }
    }
}
