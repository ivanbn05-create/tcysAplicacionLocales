<#
.SYNOPSIS
Instalador universal Edge Production 1.0 desde una release ya verificada.
.DESCRIPTION
Lee la tarjeta privada de un uso sin exponer el código en argumentos, reclama la identidad
por HTTPS y exige confirmar la sucursal entregada por Central. La selección de
sucursal y módulos no es un parámetro de este instalador.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CentralUrl,
    [Parameter(Mandatory = $true)][string]$EnrollmentCardPath,
    [Parameter(Mandatory = $true)][string]$AllowedHosts,
    [string]$EnrollmentEndpoint = '/api/v1/enrollment/claim/',
    [string]$CentralCaBundle,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(2, 64)][int]$Threads = 8,
    [string]$ListenAddress = '0.0.0.0',
    [switch]$Https,
    [switch]$AllowInsecureHttpLan,
    [ValidateSet('archivo', 'tcp')][string]$PrintBackend = 'archivo',
    [switch]$SkipFirewall,
    [switch]$SkipBackupTask,
    [switch]$AllowOnlineDependencies,
    [switch]$AllowUnverifiedDevelopmentTree
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ejecuta el instalador universal desde PowerShell como administrador.'
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$client = Join-Path $root 'herramientas\enrolamiento_edge.py'
$motor = Join-Path $root 'instalar-servicio-lan.ps1'
if (-not (Test-Path -LiteralPath $client -PathType Leaf) -or
    -not (Test-Path -LiteralPath $motor -PathType Leaf)) {
    throw 'La release no contiene los componentes de enrolamiento.'
}
if (-not $AllowUnverifiedDevelopmentTree -and
    -not (Test-Path -LiteralPath (Join-Path $root '_release\manifest.json') -PathType Leaf)) {
    throw 'Production 1.0 debe instalarse desde una release verificada, no desde Git.'
}
if ($AllowOnlineDependencies -and -not $AllowUnverifiedDevelopmentTree) {
    throw 'La instalación firmada Production 1.0 exige dependencias empaquetadas; online sólo en desarrollo explícito.'
}
if ($AllowUnverifiedDevelopmentTree -and
    (Test-Path -LiteralPath (Join-Path $root '_release\manifest.json') -PathType Leaf)) {
    throw 'El switch de desarrollo no se admite dentro de una release empaquetada.'
}
if ($CentralCaBundle -and -not (Test-Path -LiteralPath $CentralCaBundle -PathType Leaf)) {
    throw 'No existe la CA indicada para Central.'
}
if (-not [IO.Path]::IsPathRooted($EnrollmentCardPath) -or
    -not (Test-Path -LiteralPath $EnrollmentCardPath -PathType Leaf)) {
    throw 'La tarjeta de Central debe ser un archivo privado absoluto.'
}
$cardItem = Get-Item -LiteralPath $EnrollmentCardPath -Force
if (($cardItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
    $cardItem.Length -gt 4096) {
    throw 'Tarjeta de Central inválida o demasiado grande.'
}
$cardAcl = Get-Acl -LiteralPath $cardItem.FullName
foreach ($rule in @($cardAcl.Access)) {
    $sid = $rule.IdentityReference.Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
    if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
        $sid -notin @('S-1-5-18', 'S-1-5-32-544', $identity.User.Value)) {
        throw 'La tarjeta permite lectura a otras identidades; muévela a una carpeta privada.'
    }
}
function Get-MachinePythonForEnrollment {
    $userRoots = @((Join-Path $env:SystemDrive 'Users'), $env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA)
    $candidates = @(Get-ChildItem 'HKLM:\SOFTWARE\Python\PythonCore\*\InstallPath' -ErrorAction SilentlyContinue |
        ForEach-Object { $_.GetValue('ExecutablePath') })
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ([string]::IsNullOrWhiteSpace($candidate) -or -not [IO.Path]::IsPathRooted($candidate)) { continue }
        $full = [IO.Path]::GetFullPath($candidate)
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
        $userWritable = $false
        foreach ($userRoot in $userRoots) {
            if ($userRoot -and $full.StartsWith($userRoot.TrimEnd('\') + '\',
                [StringComparison]::OrdinalIgnoreCase)) { $userWritable = $true }
        }
        if ($userWritable) { continue }
        $cursor = $full
        while ($cursor) {
            $acl = Get-Acl -LiteralPath $cursor
            foreach ($rule in @($acl.Access)) {
        if (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
                try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
            catch { $sid = [string]$rule.IdentityReference.Value }
                $write = [Security.AccessControl.FileSystemRights]::Delete -bor
                    [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
                    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                    [Security.AccessControl.FileSystemRights]::TakeOwnership
                if ($cursor.TrimEnd('\') -ne [IO.Path]::GetPathRoot($cursor).TrimEnd('\')) {
                    $write = $write -bor [Security.AccessControl.FileSystemRights]::WriteData -bor
                        [Security.AccessControl.FileSystemRights]::AppendData
                }
                if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
                    $sid -notin @('S-1-5-18', 'S-1-5-32-544', 'S-1-3-0') -and
                    -not $sid.StartsWith('S-1-5-80-') -and
                    (($rule.FileSystemRights -band $write) -ne 0)) { $userWritable = $true }
            }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { break }
            $cursor = $parent
        }
        if ($userWritable) { continue }
        try {
            $ok = & $full -I -c 'import sys; assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32'
            if ($LASTEXITCODE -eq 0) { return $full }
        }
        catch { continue }
    }
    throw 'Se requiere Python 3.13 de 64 bits instalado para todos los usuarios en una ruta protegida de máquina.'
}
$python = Get-MachinePythonForEnrollment
if (-not $AllowUnverifiedDevelopmentTree) {
    $wheelhouse = Join-Path $root 'wheelhouse'
    $lock = Join-Path $root 'requirements-lock.txt'
    if (-not (Test-Path -LiteralPath $wheelhouse -PathType Container) -or
        -not (Test-Path -LiteralPath $lock -PathType Leaf)) {
        throw 'Release firmada incompleta: falta wheelhouse o requirements-lock.txt.'
    }
    $pipArgs = @('-I', '-m', 'pip', 'install', '--dry-run', '--ignore-installed',
        '--disable-pip-version-check', '--no-index', '--only-binary=:all:',
        '--find-links', $wheelhouse, '-r', $lock)
    & $python @pipArgs 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Wheelhouse offline incompleto para el Python de máquina; el código de enrolamiento no se consumió.'
    }
}
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$privateDir = Join-Path $tempRoot ('tocayos-enrollment-' + [Guid]::NewGuid().ToString('N'))
$receipt = Join-Path $privateDir 'receipt.json'
try {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($adminSid)
    foreach ($sidText in @('S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object Security.Principal.SecurityIdentifier($sidText)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'
        )
        [void]$acl.AddAccessRule($rule)
    }
    if (Test-Path -LiteralPath $privateDir) { throw 'La carpeta temporal privada ya existe.' }
    [void][IO.Directory]::CreateDirectory($privateDir, $acl)
    $verified = Get-Acl -LiteralPath $privateDir
    if (-not $verified.AreAccessRulesProtected) {
        throw 'No se pudo proteger la carpeta temporal del recibo.'
    }
    foreach ($rule in @($verified.Access)) {
        $sid = $rule.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
        if ($sid -notin @('S-1-5-18', 'S-1-5-32-544')) {
            throw 'La carpeta temporal contiene ACL no autorizadas.'
        }
    }
    $argsClient = @($client, '--central-url', $CentralUrl,
        '--endpoint', $EnrollmentEndpoint, '--card', $cardItem.FullName,
        '--receipt', $receipt)
    if ($CentralCaBundle) { $argsClient += @('--ca-bundle', $CentralCaBundle) }
    & $python @argsClient
    if ($LASTEXITCODE -ne 0) {
        throw 'No fue posible reclamar el enrolamiento; revisa el estado en Central.'
    }
    $data = Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
    $branchCode = [string]$data.branch.code
    $branchName = [string]$data.branch.name
    $edgeId = [string]$data.edge.id
    Write-Host ("Central autorizó {0} ({1}), Edge {2}." -f $branchName, $branchCode, $edgeId)
    $answer = Read-Host "Para confirmar escribe la clave exacta $branchCode"
    if ($answer -cne $branchCode) {
        throw 'Identidad no confirmada. El código pudo consumirse: revoca Edge/credenciales en Central y emite otra tarjeta.'
    }
    $arguments = @{
        Modo = 'Instalar'
        EnrollmentReceiptPath = $receipt
        CentralApiBaseUrl = $CentralUrl
        CentralApiCaBundle = $CentralCaBundle
        AllowedHosts = $AllowedHosts
        Port = $Port
        Threads = $Threads
        ListenAddress = $ListenAddress
        Https = $Https
        AllowInsecureHttpLan = $AllowInsecureHttpLan
        PrintBackend = $PrintBackend
        SkipFirewall = $SkipFirewall
        SkipBackupTask = $SkipBackupTask
        AllowOnlineDependencies = $AllowOnlineDependencies
        AllowUnverifiedDevelopmentTree = $AllowUnverifiedDevelopmentTree
    }
    & $motor @arguments
    if (-not $?) { throw 'La instalación Edge no concluyó; revisa Central y la instalación parcial antes de reemitir.' }
}
catch {
    Write-Warning 'El enrolamiento pudo consumirse. Si no se completó la instalación, revoca Edge/credenciales en Central antes de emitir otra tarjeta.'
    throw
}
finally {
    if (Test-Path -LiteralPath $receipt -PathType Leaf) {
        Remove-Item -LiteralPath $receipt -Force
    }
    if (Test-Path -LiteralPath $privateDir -PathType Container) {
        Remove-Item -LiteralPath $privateDir -Force
    }
}