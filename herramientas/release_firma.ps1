<#
.SYNOPSIS
Firma o verifica una release Edge con RSA-3072/SHA-256 y clave propia externa.
.DESCRIPTION
El trust store debe proceder de la instalación anterior o de un bootstrap
obtenido por un canal independiente. Nunca se acepta desde el ZIP no verificado.
La clave privada XML no debe colocarse dentro del repositorio ni de la release.
#>
[CmdletBinding(DefaultParameterSetName = 'Verify')]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Sign', 'Verify', 'GenerateLabKey')][string]$Mode,
    [string]$ArchivePath,
    [string]$ManifestPath,
    [string]$ChecksumPath,
    [string]$SignaturePath,
    [string]$PrivateKeyPath,
    [string]$TrustStorePath,
    [string]$ExpectedVersion,
    [string]$LabKeyDirectory
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$algorithm = [Security.Cryptography.CryptoConfig]::MapNameToOID('SHA256')
if (-not $algorithm) { throw 'SHA-256 no disponible.' }

function Read-BoundedFile {
    param([string]$Path, [int64]$MaxBytes)
    if (-not [IO.Path]::IsPathRooted($Path) -or
        -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'Se requiere ruta absoluta de archivo existente.'
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -gt $MaxBytes) {
        throw 'Archivo de firma/verificacion invalido o demasiado grande.'
    }
    return [IO.File]::ReadAllBytes($item.FullName)
}

function Get-FileHashLower {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-PublicKeyId {
    param([string]$PublicXml)
    $bytes = [Text.Encoding]::UTF8.GetBytes($PublicXml)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $sha.Dispose() }
}

function Get-MessageBytes {
    param([object]$Descriptor)
    $lines = @(
        'LosTocayosPOS-Release-Signature-v1',
        [string]$Descriptor.product,
        [string]$Descriptor.version,
        [string]$Descriptor.key_id,
        [string]$Descriptor.archive_sha256,
        [string]$Descriptor.manifest_sha256,
        [string]$Descriptor.checksum_sha256,
        ''
    )
    return [Text.Encoding]::UTF8.GetBytes(($lines -join ([char]10)))
}

function Assert-HashFormat {
    param([string]$Hash)
    if ($Hash -cnotmatch '^[0-9a-f]{64}$') { throw 'Hash SHA-256 invalido.' }
}

if ($Mode -eq 'GenerateLabKey') {
    if (-not $LabKeyDirectory -or -not [IO.Path]::IsPathRooted($LabKeyDirectory) -or
        (Test-Path -LiteralPath $LabKeyDirectory)) {
        throw 'La carpeta externa de clave de laboratorio debe ser absoluta y nueva.'
    }
    New-Item -ItemType Directory -Path $LabKeyDirectory | Out-Null
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($ownerSid)
    foreach ($sid in @(
        (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')),
        $ownerSid
    )) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $LabKeyDirectory -AclObject $acl
    $cp = New-Object Security.Cryptography.CspParameters(24)
    $rsa = New-Object Security.Cryptography.RSACryptoServiceProvider(3072, $cp)
    try {
        $privateXml = $rsa.ToXmlString($true)
        $publicXml = $rsa.ToXmlString($false)
        $keyId = Get-PublicKeyId -PublicXml $publicXml
        $privatePath = Join-Path $LabKeyDirectory 'publisher-private.xml'
        $trustPath = Join-Path $LabKeyDirectory 'release-trust.json'
        [IO.File]::WriteAllText($privatePath, $privateXml, (New-Object Text.UTF8Encoding($false)))
        $trust = @{
            schema_version = 1
            keys = @(@{ key_id = $keyId; public_xml = $publicXml; status = 'trusted' })
        }
        [IO.File]::WriteAllText($trustPath, ($trust | ConvertTo-Json -Depth 6),
            (New-Object Text.UTF8Encoding($false)))
        Write-Output ("Lab key id: " + $keyId)
        Write-Output ("Private key path: " + $privatePath)
        Write-Output ("Trust store path: " + $trustPath)
    }
    finally { $rsa.Dispose() }
    exit 0
}

foreach ($required in @($ArchivePath, $ManifestPath, $ChecksumPath, $SignaturePath)) {
    if (-not $required -or -not [IO.Path]::IsPathRooted($required)) {
        throw 'Archivo de release o firma faltante/no absoluto.'
    }
}
if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf) -or
    (Get-Item -LiteralPath $ArchivePath -Force).Length -gt 1073741824) {
    throw 'ZIP de release faltante o demasiado grande.'
}
$manifestBytes = Read-BoundedFile -Path $ManifestPath -MaxBytes 16777216
[void](Read-BoundedFile -Path $ChecksumPath -MaxBytes 8192)
$manifest = [Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
if ($manifest.product -notin @(
    'LosTocayosPOS-Servidor',
    'LosTocayosPOS-Cliente-Windows'
)) {
    throw 'Producto de release desconocido.'
}
$manifestVersion = [string]$manifest.release_version
if ($manifestVersion -cnotmatch '^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$' -or
    ($ExpectedVersion -and $manifestVersion -cne $ExpectedVersion)) {
    throw 'Version de release inesperada.'
}
$descriptor = [ordered]@{
    schema_version = 1
    algorithm = 'RSA-3072-SHA256-PKCS1v15'
    product = [string]$manifest.product
    version = $manifestVersion
    key_id = ''
    archive_sha256 = Get-FileHashLower -Path $ArchivePath
    manifest_sha256 = Get-FileHashLower -Path $ManifestPath
    checksum_sha256 = Get-FileHashLower -Path $ChecksumPath
}
if ($Mode -eq 'Sign') {
    if (-not $PrivateKeyPath -or -not [IO.Path]::IsPathRooted($PrivateKeyPath) -or
        (Test-Path -LiteralPath $SignaturePath)) {
        throw 'Clave privada externa absoluta y firma de salida nueva son obligatorias.'
    }
    $privateXml = [Text.Encoding]::UTF8.GetString(
        (Read-BoundedFile -Path $PrivateKeyPath -MaxBytes 32768)
    )
    $cp = New-Object Security.Cryptography.CspParameters(24)
    $rsa = New-Object Security.Cryptography.RSACryptoServiceProvider(3072, $cp)
    try {
        $rsa.FromXmlString($privateXml)
        if ($rsa.KeySize -lt 3072) { throw 'Clave RSA menor de 3072 bits.' }
        $descriptor.key_id = Get-PublicKeyId -PublicXml $rsa.ToXmlString($false)
        $signature = $rsa.SignData((Get-MessageBytes -Descriptor $descriptor), $algorithm)
        $descriptor.signature_base64 = [Convert]::ToBase64String($signature)
        [IO.File]::WriteAllText(
            $SignaturePath,
            ($descriptor | ConvertTo-Json -Depth 4),
            (New-Object Text.UTF8Encoding($false))
        )
        Write-Output ("Release firmada con key id " + $descriptor.key_id)
    }
    finally { $rsa.Dispose() }
    exit 0
}
if ($Mode -ne 'Verify') { throw 'Modo de firma desconocido.' }
if (-not $TrustStorePath -or -not [IO.Path]::IsPathRooted($TrustStorePath)) {
    throw 'Verificacion requiere trust store externo absoluto.'
}
$signatureBytes = Read-BoundedFile -Path $SignaturePath -MaxBytes 16384
$trustBytes = Read-BoundedFile -Path $TrustStorePath -MaxBytes 65536
$signature = [Text.Encoding]::UTF8.GetString($signatureBytes) | ConvertFrom-Json
$trust = [Text.Encoding]::UTF8.GetString($trustBytes) | ConvertFrom-Json
if ($signature.schema_version -ne 1 -or
    $signature.algorithm -cne $descriptor.algorithm -or
    $signature.product -cne $descriptor.product -or
    $signature.version -cne $descriptor.version -or
    $trust.schema_version -ne 1) {
    throw 'Contrato de firma/release desconocido.'
}
foreach ($field in @('archive_sha256', 'manifest_sha256', 'checksum_sha256')) {
    Assert-HashFormat -Hash ([string]$signature.$field)
    if ($signature.$field -cne $descriptor.$field) {
        throw 'Los hashes de la release no coinciden con la firma.'
    }
}
Assert-HashFormat -Hash ([string]$signature.key_id)
$matching = @($trust.keys | Where-Object {
    $_.key_id -ceq $signature.key_id -and $_.status -ceq 'trusted'
})
if ($matching.Count -ne 1) { throw 'Clave de publicador desconocida o revocada.' }
$publicXml = [string]$matching[0].public_xml
if ((Get-PublicKeyId -PublicXml $publicXml) -cne $signature.key_id) {
    throw 'Identidad de clave publica discordante.'
}
try { $signatureRaw = [Convert]::FromBase64String([string]$signature.signature_base64) }
catch { throw 'Firma base64 invalida.' }
$cp = New-Object Security.Cryptography.CspParameters(24)
$rsa = New-Object Security.Cryptography.RSACryptoServiceProvider(3072, $cp)
try {
    $rsa.FromXmlString($publicXml)
    if ($rsa.KeySize -lt 3072 -or
        -not $rsa.VerifyData((Get-MessageBytes -Descriptor $signature),
            $algorithm, $signatureRaw)) {
        throw 'Firma de release invalida.'
    }
}
finally { $rsa.Dispose() }
Write-Output ("Firma verificada: " + $signature.key_id)