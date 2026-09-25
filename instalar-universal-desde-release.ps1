<#
.SYNOPSIS
Bootstrap confiable para instalación nueva Edge desde una release firmada.
.DESCRIPTION
Este archivo, su verificador de firma y trust store deben obtenerse por un canal
confiable independiente del ZIP. Nunca ejecutes scripts extraídos antes de
verificar RSA/SHA-256 de los cuatro artefactos de release.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$ChecksumPath,
    [Parameter(Mandatory = $true)][string]$SignaturePath,
    [Parameter(Mandatory = $true)][string]$TrustStorePath,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$InstallationRoot,
    [Parameter(Mandatory = $true)][string]$CentralUrl,
    [Parameter(Mandatory = $true)][string]$EnrollmentCardPath,
    [Parameter(Mandatory = $true)][string]$AllowedHosts,
    [string]$CentralCaBundle,
    [switch]$Https,
    [switch]$AllowInsecureHttpLan,
    [switch]$SkipFirewall,
    [switch]$SkipBackupTask
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ejecuta el bootstrap como administrador.'
}
if ($ExpectedVersion -cnotmatch '^1[.][0-9A-Za-z._+-]{1,61}$') {
    throw 'El bootstrap Production 1.0 requiere una versión 1.x concreta.'
}
function Resolve-SafeAbsolute {
    param([string]$Path, [switch]$MayNotExist)
    if (-not [IO.Path]::IsPathRooted($Path)) { throw 'Todas las rutas deben ser absolutas.' }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $root = [IO.Path]::GetPathRoot($full).TrimEnd('\')
    if ($full -eq $root) { throw 'No se acepta la raíz de una unidad.' }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Una ruta atraviesa un enlace o unión no permitida.'
            }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    if (-not $MayNotExist -and -not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw 'Falta un archivo de release requerido.'
    }
    return $full
}
function File-Hash {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function New-PrivateStage {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) { throw 'Staging ya existe.' }
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
    [void][IO.Directory]::CreateDirectory($Path, $acl)
    $confirmed = Get-Acl -LiteralPath $Path
    if (-not $confirmed.AreAccessRulesProtected -or
        $confirmed.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18', 'S-1-5-32-544')) {
        throw 'No se pudo proteger staging.'
    }
    foreach ($rule in @($confirmed.Access)) {
        try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
        catch { $sid = [string]$rule.IdentityReference.Value }
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $sid -notin @('S-1-5-18', 'S-1-5-32-544')) {
            throw 'Staging privado permite acceso ajeno.'
        }
    }
}
function Assert-ProtectedDestinationParent {
    param([string]$Path)
    $acl = Get-Acl -LiteralPath $Path
    foreach ($rule in @($acl.Access)) {
        if (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
        try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
            catch { $sid = [string]$rule.IdentityReference.Value }
        $dangerous = [Security.AccessControl.FileSystemRights]::Delete -bor
            [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
            [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
            [Security.AccessControl.FileSystemRights]::TakeOwnership
        if ($Path.TrimEnd('\') -ne [IO.Path]::GetPathRoot($Path).TrimEnd('\')) {
            $dangerous = $dangerous -bor [Security.AccessControl.FileSystemRights]::WriteData -bor
                [Security.AccessControl.FileSystemRights]::AppendData
        }
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $sid -in @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545') -and
            (($rule.FileSystemRights -band $dangerous) -ne 0)) {
            throw 'El directorio padre de instalación permite escritura a usuarios no privilegiados.'
        }
    }
}
function New-PrivateTrustRoot {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
        $acl.SetOwner($admins)
        foreach ($pair in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'),
                @('S-1-5-19', 'ReadAndExecute'))) {
            $sid = New-Object Security.Principal.SecurityIdentifier($pair[0])
            $rule = New-Object Security.AccessControl.FileSystemAccessRule(
                $sid, $pair[1], 'ContainerInherit, ObjectInherit', 'None', 'Allow')
            [void]$acl.AddAccessRule($rule)
        }
        [void][IO.Directory]::CreateDirectory($Path, $acl)
    }
    $current = Get-Acl -LiteralPath $Path
    if (-not $current.AreAccessRulesProtected -or
        $current.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18', 'S-1-5-32-544')) {
        throw 'La carpeta de confianza persistente tiene propietario o herencia insegura.'
    }
    foreach ($rule in @($current.Access)) {
        try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
            catch { $sid = [string]$rule.IdentityReference.Value }
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $sid -notin @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-19')) {
            throw 'La carpeta de confianza persistente permite acceso ajeno.'
        }
        if ($sid -eq 'S-1-5-19' -and
            (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -ne 0)) {
            throw 'Local Service no debe escribir la carpeta de confianza.'
        }
    }
}
function Copy-TrustedConfiguration {
    param([string]$Source, [string]$Destination, [switch]$LocalServiceRead)
    $sourceHash = File-Hash $Source
    if (Test-Path -LiteralPath $Destination) {
        $existing = Get-Item -LiteralPath $Destination -Force
        if (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'La configuración persistente no puede ser un enlace.'
        }
        if ((File-Hash $Destination) -cne $sourceHash) {
            throw 'La configuración de confianza existente difiere; requiere rotación administrada.'
        }
    }
    else {
        [IO.File]::Copy($Source, $Destination, $false)
    }
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($admins)
    foreach ($pair in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'))) {
        $sid = New-Object Security.Principal.SecurityIdentifier($pair[0])
        [void]$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, $pair[1], 'None', 'None', 'Allow')))
    }
    if ($LocalServiceRead) {
        $serviceSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
        [void]$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $serviceSid, 'Read', 'None', 'None', 'Allow')))
    }
    Set-Acl -LiteralPath $Destination -AclObject $acl
    if ((File-Hash $Destination) -cne $sourceHash) {
        throw 'La copia persistente de confianza no coincide con el origen verificado.'
    }
}
$archive = Resolve-SafeAbsolute $ArchivePath
$manifest = Resolve-SafeAbsolute $ManifestPath
$checksum = Resolve-SafeAbsolute $ChecksumPath
$signature = Resolve-SafeAbsolute $SignaturePath
$trust = Resolve-SafeAbsolute $TrustStorePath
$verifier = Resolve-SafeAbsolute (Join-Path $PSScriptRoot 'herramientas\release_firma.ps1')
$card = Resolve-SafeAbsolute $EnrollmentCardPath
$centralCa = if ($CentralCaBundle) { Resolve-SafeAbsolute $CentralCaBundle } else { $null }
if ((Get-Item -LiteralPath $card).Length -gt 4096) {
    throw 'Tarjeta de enrolamiento demasiado grande.'
}
$target = Resolve-SafeAbsolute $InstallationRoot -MayNotExist
if (Test-Path -LiteralPath $target) { throw 'La instalación de destino ya existe.' }
if (Get-Service -Name 'LosTocayosPOS' -ErrorAction SilentlyContinue) {
    throw 'El servicio LosTocayosPOS ya existe; no se permite instalación nueva encima.'
}
$parent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw 'El directorio padre de la instalación debe existir.'
}
Assert-ProtectedDestinationParent -Path $parent
foreach ($source in @($archive, $manifest, $checksum, $signature, $trust, $verifier, $card, $centralCa)) {
    if ($source -and $source.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Los artefactos y materiales de confianza deben estar fuera de la instalación.'
    }
}
$hashes = @{}
foreach ($source in @($archive, $manifest, $checksum, $signature, $trust, $verifier, $centralCa)) {
    if ($source) { $hashes[$source] = File-Hash $source }
}
$windowsPowerShell = Resolve-SafeAbsolute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
& $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $verifier -Mode Verify -ArchivePath $archive -ManifestPath $manifest -ChecksumPath $checksum -SignaturePath $signature -TrustStorePath $trust -ExpectedVersion $ExpectedVersion
if ($LASTEXITCODE -ne 0) { throw 'La firma de publicador no es válida; no se extrajo nada.' }
foreach ($source in $hashes.Keys) {
    if ((File-Hash $source) -cne $hashes[$source]) {
        throw 'Un artefacto cambió durante la verificación.'
    }
}
$stage = Join-Path $parent ((Split-Path -Leaf $target) + '-staging-' + [Guid]::NewGuid().ToString('N'))
$stage = Resolve-SafeAbsolute $stage -MayNotExist
if (-not $stage.StartsWith($parent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $stage)) {
    throw 'Ruta de staging inválida.'
}
$stageCreated = $false
$promoted = $false
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        if ($zip.Entries.Count -gt 10000) { throw 'Demasiadas entradas ZIP.' }
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        [int64]$totalBytes = 0
        $embeddedManifest = $null
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('/', '\')
            if (-not $name -or $name.StartsWith('\') -or $name.Contains(':')) {
                throw 'ZIP contiene una ruta inválida.'
            }
            $parts = @($name.TrimEnd('\').Split('\'))
            foreach ($part in $parts) {
                if (-not $part -or $part -in @('.', '..') -or
                    $part.EndsWith(' ') -or $part.EndsWith('.') -or
                    $part -match '[<>:"|?*\x00-\x1f]' -or
                    $part.Split('.')[0] -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])$') {
                    throw 'ZIP contiene componente de ruta Windows no seguro.'
                }
            }
            $destination = [IO.Path]::GetFullPath((Join-Path $stage $name))
            if (-not $destination.StartsWith($stage + '\', [StringComparison]::OrdinalIgnoreCase) -or
                -not $seen.Add($destination.TrimEnd('\'))) {
                throw 'ZIP intenta salir del staging o duplica una ruta.'
            }
            if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
                throw 'ZIP contiene enlace simbólico.'
            }
            if ($entry.Length -gt 268435456) { throw 'Entrada ZIP demasiado grande.' }
            $totalBytes += $entry.Length
            if ($totalBytes -gt 1073741824) { throw 'Payload ZIP demasiado grande.' }
            if ($entry.FullName -ceq '_release/manifest.json') {
                if ($entry.Length -gt 16777216) { throw 'Manifiesto ZIP demasiado grande.' }
                $stream = $entry.Open()
                try {
                    $memory = New-Object IO.MemoryStream
                    $stream.CopyTo($memory)
                    $embeddedManifest = $memory.ToArray()
                    $memory.Dispose()
                }
                finally { $stream.Dispose() }
            }
        }
        if ($null -eq $embeddedManifest) { throw 'ZIP sin manifiesto embebido.' }
        $externalManifest = [IO.File]::ReadAllBytes($manifest)
        if ($externalManifest.Length -ne $embeddedManifest.Length) {
            throw 'Manifiesto externo/embebido discordante.'
        }
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $first = [Convert]::ToBase64String($sha.ComputeHash($externalManifest))
            $second = [Convert]::ToBase64String($sha.ComputeHash($embeddedManifest))
        }
        finally { $sha.Dispose() }
        if ($first -cne $second) { throw 'Manifiesto externo/embebido discordante.' }
        $signedManifest = $externalManifest | ConvertFrom-Json
        if ($signedManifest.dependency_bundle -cne 'wheelhouse') {
            throw 'Production 1.0 instalable requiere wheelhouse incluido en la release firmada.'
        }
        New-PrivateStage -Path $stage
        $stageCreated = $true
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $stage)
    }
    finally { $zip.Dispose() }
    $versionPath = Join-Path $stage 'VERSION'
    $installer = Join-Path $stage 'instalar-universal.ps1'
    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $installer -PathType Leaf) -or
        (Get-Content -LiteralPath $versionPath -Raw).Trim() -cne $ExpectedVersion) {
        throw 'La release extraída no coincide con la versión esperada.'
    }
    foreach ($source in $hashes.Keys) {
        if ((File-Hash $source) -cne $hashes[$source]) {
            throw 'Un artefacto cambió antes de instalar.'
        }
    }
    $wheels = Join-Path $stage 'wheelhouse'
    if (-not (Test-Path -LiteralPath $wheels -PathType Container) -or
        @((Get-ChildItem -LiteralPath $wheels -Filter '*.whl' -File)).Count -eq 0) {
        throw 'La release firmada no contiene wheelhouse instalable.'
    }
    $trustRoot = Resolve-SafeAbsolute (Join-Path $env:ProgramData 'LosTocayosPOS') -MayNotExist
    New-PrivateTrustRoot -Path $trustRoot
    $durableTrust = Join-Path $trustRoot 'release-trust.json'
    Copy-TrustedConfiguration -Source $trust -Destination $durableTrust
    $durableCa = $null
    if ($centralCa) {
        $durableCa = Join-Path $trustRoot 'central-ca.pem'
        Copy-TrustedConfiguration -Source $centralCa -Destination $durableCa -LocalServiceRead
    }
    if (Test-Path -LiteralPath $target) { throw 'La instalación de destino apareció durante staging.' }
    [IO.Directory]::Move($stage, $target)
    $promoted = $true
    $arguments = @{
        CentralUrl = $CentralUrl
        EnrollmentCardPath = $card
        AllowedHosts = $AllowedHosts
        CentralCaBundle = $durableCa
        Https = $Https
        AllowInsecureHttpLan = $AllowInsecureHttpLan
        SkipFirewall = $SkipFirewall
        SkipBackupTask = $SkipBackupTask
    }
    & (Join-Path $target 'instalar-universal.ps1') @arguments
    if (-not $?) { throw 'La instalación firmada no concluyó.' }
}
finally {
    if ($stageCreated -and -not $promoted -and (Test-Path -LiteralPath $stage)) {
        $stageFull = [IO.Path]::GetFullPath($stage)
        if ($stageFull.StartsWith($parent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -and
            $stageFull -ne $parent) {
            Remove-Item -LiteralPath $stageFull -Recurse -Force
        }
    }
}