<#
.SYNOPSIS
Actualiza exclusivamente la instalación de laboratorio desde una release verificada.
.DESCRIPTION
Verifica ZIP, manifiesto y SHA-256 antes de detener el servicio. Extrae en un
staging externo, conserva el estado operativo y deja la instalación previa como
respaldo. Si la candidata falla, restaura la versión anterior y conserva ambos
árboles para diagnóstico. No descarga artefactos ni es un actualizador desatendido.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\actualizar-laboratorio-desde-release.ps1 -ArchivePath C:\Releases\LosTocayosPOS-Servidor-0.4.0-dev.3.zip -ManifestPath C:\Releases\LosTocayosPOS-Servidor-0.4.0-dev.3.manifest.json -ChecksumPath C:\Releases\LosTocayosPOS-Servidor-0.4.0-dev.3.sha256 -ExpectedVersion 0.4.0-dev.3
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$ChecksumPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$')]
    [string]$ExpectedVersion,
    [string]$InstallationRoot = 'C:\LosTocayosPOS',
    [string]$WorkspaceRoot,
    [string]$VerifierScript = (Join-Path $PSScriptRoot 'herramientas\release_servidor.py'),
    [string]$VerifierPython,
    [switch]$PrepareOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$serviceName = 'LosTocayosPOS'

function Resolve-AbsoluteLiteralPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet('Any', 'Leaf', 'Container')][string]$ExpectedType = 'Any',
        [switch]$MayNotExist
    )
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw 'Todas las rutas deben ser absolutas.'
    }
    try { $fullPath = [IO.Path]::GetFullPath($Path) }
    catch { throw "Ruta absoluta inválida: $Path" }
    if (-not $MayNotExist -and -not (Test-Path -LiteralPath $fullPath -PathType $ExpectedType)) {
        throw "No existe la ruta requerida: $fullPath"
    }
    return $fullPath.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-PathWithin {
    param([string]$Candidate, [string]$Parent)
    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    return $candidateFull.Equals($parentFull, [StringComparison]::OrdinalIgnoreCase) -or
        $candidateFull.StartsWith($parentFull + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-SafePath {
    param([string]$Path, [string]$Description)
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootPath = [IO.Path]::GetPathRoot($fullPath).TrimEnd('\')
    if ($fullPath.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Description no puede ser la raíz de una unidad."
    }
    $cursor = $fullPath
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Description no puede atravesar enlaces ni uniones: $cursor"
            }
        }
        $parent = Split-Path -Parent $cursor
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-NoReparseTree {
    param([string]$Path, [string]$Description)
    $rootItem = Get-Item -LiteralPath $Path -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Description contiene un enlace o unión en su raíz."
    }
    if (-not $rootItem.PSIsContainer) { return }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($rootItem.FullName)
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($child in Get-ChildItem -LiteralPath $current -Force) {
            if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Description contiene un enlace o unión no permitido: $($child.FullName)"
            }
            if ($child.PSIsContainer) { $pending.Push($child.FullName) }
        }
    }
}

function New-PrivateDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (Test-Path -LiteralPath $Path) {
        throw "La carpeta privada ya existe: $Path"
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($adminSid)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity,
            'FullControl',
            'ContainerInherit, ObjectInherit',
            'None',
            'Allow'
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl

    $verified = Get-Acl -LiteralPath $Path
    if (-not $verified.AreAccessRulesProtected) {
        throw 'La carpeta privada conserva herencia de ACL.'
    }
    $unexpected = @(
        $verified.Access | Where-Object {
            $_.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value -notin @('S-1-5-18', 'S-1-5-32-544')
        }
    )
    if ($unexpected.Count -gt 0) {
        throw 'La carpeta privada contiene ACE inesperadas.'
    }
}


function Get-FileSha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return (($sha256.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $sha256.Dispose() }
}

function Get-VerifiedEnvironmentSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ExpectedHash = ''
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'La instalación anterior no contiene .env.'
    }
    Assert-NoReparseTree -Path $Path -Description 'El archivo .env'
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) {
        throw '.env conserva herencia de ACL.'
    }
    try {
        $ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        try { $ownerSid = (New-Object Security.Principal.SecurityIdentifier($acl.Owner)).Value }
        catch { $ownerSid = '' }
    }
    if ($ownerSid -ne 'S-1-5-32-544') {
        throw '.env debe pertenecer a Administradores.'
    }
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    ))
    if (@($rules | Where-Object { $_.IsInherited }).Count -ne 0 -or $rules.Count -ne 3) {
        throw '.env no tiene exactamente las tres ACE privadas canónicas.'
    }
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $full = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and
            $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $_.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl -and
            ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0
        })
        if ($full.Count -ne 1) { throw '.env no tiene control administrativo canónico.' }
    }
    $localService = @($rules | Where-Object {
        $_.IdentityReference.Value -eq 'S-1-5-19' -and
        $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
        ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0
    })
    $readRights = (
        [Security.AccessControl.FileSystemRights]::Read -bor
        [Security.AccessControl.FileSystemRights]::Synchronize
    )
    if ($localService.Count -ne 1 -or $localService[0].FileSystemRights -ne $readRights) {
        throw '.env no concede sólo lectura canónica a LocalService.'
    }
    if (@($rules | Where-Object {
        $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $_.IdentityReference.Value -notin @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-19')
    }).Count -ne 0) {
        throw '.env contiene identidades o denegaciones no previstas.'
    }
    [byte[]]$bytes = [IO.File]::ReadAllBytes($Path)
    $hash = Get-Sha256Hex -Bytes $bytes
    if (-not [string]::IsNullOrWhiteSpace($ExpectedHash) -and $hash -cne $ExpectedHash) {
        throw 'El contenido de .env cambió durante la preparación de la actualización.'
    }
    return [pscustomobject]@{ Bytes = $bytes; Hash = $hash; Acl = $acl }
}

function Copy-VerifiedEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$ExpectedHash
    )
    $snapshot = Get-VerifiedEnvironmentSnapshot -Path $Source -ExpectedHash $ExpectedHash
    if (Test-Path -LiteralPath $Destination) {
        throw 'El destino de .env ya existe dentro de la release.'
    }
    New-Item -ItemType File -Path $Destination | Out-Null
    Set-Acl -LiteralPath $Destination -AclObject $snapshot.Acl
    [IO.File]::WriteAllBytes($Destination, $snapshot.Bytes)
    $copied = Get-VerifiedEnvironmentSnapshot -Path $Destination -ExpectedHash $ExpectedHash
    if ($copied.Bytes.Length -ne $snapshot.Bytes.Length) {
        throw 'La copia protegida de .env cambió de tamaño.'
    }
}
function Copy-DirectoryContents {
    param([string]$Source, [string]$Destination)
    Assert-NoReparseTree -Path $Source -Description 'El árbol que se copiará'
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
}

function Copy-OperationalState {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedEnvironmentHash
    )

    foreach ($relative in @('.env', '.venv', 'runtime', 'media', 'logs', 'backups')) {
        $source = Join-Path $SourceRoot $relative
        if (-not (Test-Path -LiteralPath $source)) {
            if ($relative -in @('.env', '.venv', 'runtime')) {
                throw "La instalación anterior no contiene el estado obligatorio $relative."
            }
            continue
        }
        $destination = Join-Path $DestinationRoot $relative
        if (Test-Path -LiteralPath $destination) {
            throw "La release incluyó una ruta de estado prohibida: $relative."
        }
        if ((Get-Item -LiteralPath $source -Force).PSIsContainer) {
            Copy-DirectoryContents -Source $source -Destination $destination
        }
        else {
            Assert-NoReparseTree -Path $source -Description 'El estado que se copiará'
            if ($relative -eq '.env') {
                Copy-VerifiedEnvironment `
                    -Source $source `
                    -Destination $destination `
                    -ExpectedHash $ExpectedEnvironmentHash
            }
            else {
                Copy-Item -LiteralPath $source -Destination $destination -Force
            }
        }
    }
    foreach ($relative in @('db.sqlite3', 'db.sqlite3-wal', 'db.sqlite3-shm', 'db.sqlite3-journal')) {
        $source = Join-Path $SourceRoot $relative
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Assert-NoReparseTree -Path $source -Description 'La base heredada'
            Copy-Item -LiteralPath $source -Destination (Join-Path $DestinationRoot $relative) -Force
        }
    }
    # certs es contenido versionado y validado por el manifiesto. No se mezcla:
    # cualquier copia anterior permanece íntegra dentro del respaldo completo.

}

function Get-ServiceExecutablePath {
    param([string]$CommandLine)
    $value = ([string]$CommandLine).Trim()
    if ($value.StartsWith('"')) {
        $closingQuote = $value.IndexOf('"', 1)
        if ($closingQuote -lt 2) { throw 'PathName del servicio no contiene un ejecutable válido.' }
        return [IO.Path]::GetFullPath($value.Substring(1, $closingQuote - 1))
    }
    $executable = ($value -split '\s+', 2)[0]
    if ([string]::IsNullOrWhiteSpace($executable)) { throw 'PathName del servicio está vacío.' }
    return [IO.Path]::GetFullPath($executable)
}

function Assert-ServiceTargetsInstallation {
    param([string]$Root)
    $record = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    $actual = Get-ServiceExecutablePath -CommandLine $record.PathName
    $expected = [IO.Path]::GetFullPath((Join-Path $Root '.venv\Scripts\pythonservice.exe'))
    if (-not $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El servicio $serviceName pertenece a otra instalación."
    }
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match ('^\s*' + [Regex]::Escape($Name) + '\s*=\s*(.*)$')) {
            $value = ([string]$Matches[1]).Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return ''
}

function Get-HealthContext {
    param([string]$Root)
    $environmentPath = Join-Path $Root '.env'
    $listenAddress = Get-DotEnvValue -Path $environmentPath -Name 'WAITRESS_HOST'
    $allowedHosts = Get-DotEnvValue -Path $environmentPath -Name 'DJANGO_ALLOWED_HOSTS'
    $portText = Get-DotEnvValue -Path $environmentPath -Name 'WAITRESS_PORT'
    $httpsText = Get-DotEnvValue -Path $environmentPath -Name 'DJANGO_HTTPS'
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw 'WAITRESS_PORT no es válido en la configuración existente.'
    }
    $hostHeader = @($allowedHosts -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })[0]
    if ([string]::IsNullOrWhiteSpace($hostHeader)) { throw 'DJANGO_ALLOWED_HOSTS está vacío.' }
    $healthAddress = if ($listenAddress -eq '0.0.0.0') { '127.0.0.1' } elseif ($listenAddress -eq '::') { '::1' } else { $listenAddress }
    if ($healthAddress.Contains(':') -and -not $healthAddress.StartsWith('[')) { $healthAddress = "[$healthAddress]" }
    $headers = @{ Host = $hostHeader }
    if ($httpsText.Trim().ToLowerInvariant() -eq 'true') { $headers['X-Forwarded-Proto'] = 'https' }
    return @{ Uri = ('http://' + $healthAddress + ':' + $port + '/salud/'); Headers = $headers }
}

function Wait-LabHealth {
    param([string]$Root, [ValidateRange(1, 120)][int]$TimeoutSeconds = 30)
    $context = Get-HealthContext -Root $Root
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $context.Uri -Headers $context.Headers -TimeoutSec 2
            $body = $response.Content | ConvertFrom-Json
            if ($response.StatusCode -eq 200 -and $body.estado -eq 'ok') { return }
        }
        catch { }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw 'La comprobación local /salud/ no respondió con estado ok.'
}

function Start-LabServiceAndVerify {
    param([string]$Root)
    $service = Get-Service -Name $serviceName -ErrorAction Stop
    if ($service.Status -ne 'Running') {
        Start-Service -Name $serviceName
        $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
    Wait-LabHealth -Root $Root -TimeoutSeconds 30
}

function Stop-LabService {
    $service = Get-Service -Name $serviceName -ErrorAction Stop
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name $serviceName
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
}

function Enter-LabMaintenanceMutex {
    $mutex = New-Object Threading.Mutex($false, 'Global\LosTocayosPOS-Mantenimiento-v1')
    $acquired = $false
    try {
        try { $acquired = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw 'Ya existe otro mantenimiento del POS en curso.' }
        return $mutex
    }
    catch {
        if (-not $acquired) { $mutex.Dispose() }
        throw
    }
}

function Enter-LabBackupMutex {
    $security = New-Object Security.AccessControl.MutexSecurity
    $security.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
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
        'Global\LosTocayosPOS-RespaldoSQLite-v1',
        [ref]$createdNew,
        $security
    )
    $acquired = $false
    try {
        try { $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds(600)) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw 'Hay un respaldo SQLite en curso después de 600 segundos.' }
        return $mutex
    }
    catch {
        if (-not $acquired) { $mutex.Dispose() }
        throw
    }
}

function Release-LabMutex {
    param([AllowNull()][Threading.Mutex]$Mutex)
    if ($null -eq $Mutex) { return }
    try { $Mutex.ReleaseMutex() }
    finally { $Mutex.Dispose() }
}

function Get-ManagedTaskSnapshots {
    $managedNames = @(
        'LosTocayosPOS-RespaldoSQLite',
        'LosTocayosPOS-PurgasFisicas'
    )
    $rootTasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop)
    foreach ($name in $managedNames) {
        $matches = @($rootTasks | Where-Object { $_.TaskName -ieq $name })
        if ($matches.Count -gt 1) {
            throw "Existe más de una tarea administrada con el nombre $name."
        }
        if ($matches.Count -eq 1) {
            $xml = [string](Export-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop)
            if ([string]::IsNullOrWhiteSpace($xml)) {
                throw "No fue posible exportar la tarea administrada $name."
            }
            [pscustomobject]@{ Name = $name; Existed = $true; Xml = $xml }
        }
        else {
            [pscustomobject]@{ Name = $name; Existed = $false; Xml = $null }
        }
    }
}

function Restore-ManagedTaskSnapshots {
    param([Parameter(Mandatory = $true)][object[]]$Snapshots)

    foreach ($snapshot in $Snapshots) {
        $name = [string]$snapshot.Name
        $matches = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop |
            Where-Object { $_.TaskName -ieq $name })
        if ($matches.Count -gt 1) {
            throw "Existe más de una tarea administrada con el nombre $name."
        }
        if ([bool]$snapshot.Existed) {
            $xml = [string]$snapshot.Xml
            if ([string]::IsNullOrWhiteSpace($xml)) {
                throw "El snapshot de la tarea $name no contiene XML."
            }
            Register-ScheduledTask -TaskName $name -TaskPath '\' -Xml $xml -Force -ErrorAction Stop | Out-Null
        }
        elseif ($matches.Count -eq 1) {
            Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false -ErrorAction Stop
        }
    }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ejecuta este actualizador de laboratorio desde PowerShell como administrador.'
}

$installation = Resolve-AbsoluteLiteralPath -Path $InstallationRoot -ExpectedType Container
$archive = Resolve-AbsoluteLiteralPath -Path $ArchivePath -ExpectedType Leaf
$manifest = Resolve-AbsoluteLiteralPath -Path $ManifestPath -ExpectedType Leaf
$checksum = Resolve-AbsoluteLiteralPath -Path $ChecksumPath -ExpectedType Leaf
$verifier = Resolve-AbsoluteLiteralPath -Path $VerifierScript -ExpectedType Leaf
if ([string]::IsNullOrWhiteSpace($VerifierPython)) {
    $VerifierPython = Join-Path $installation '.venv\Scripts\python.exe'
}
$verifierPythonPath = Resolve-AbsoluteLiteralPath -Path $VerifierPython -ExpectedType Leaf
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $installationParent = Split-Path -Parent $installation
    $installationLeaf = Split-Path -Leaf $installation
    $WorkspaceRoot = Join-Path $installationParent ($installationLeaf + '-lab-actualizaciones')
}
$workspace = Resolve-AbsoluteLiteralPath -Path $WorkspaceRoot -ExpectedType Container -MayNotExist
Assert-SafePath -Path $installation -Description 'InstallationRoot'
Assert-SafePath -Path $workspace -Description 'WorkspaceRoot'
if ((Test-PathWithin -Candidate $workspace -Parent $installation) -or
    (Test-PathWithin -Candidate $installation -Parent $workspace)) {
    throw 'WorkspaceRoot debe estar fuera de InstallationRoot y no puede contenerlo.'
}
if (-not ([IO.Path]::GetPathRoot($workspace)).Equals(
    [IO.Path]::GetPathRoot($installation), [StringComparison]::OrdinalIgnoreCase
)) {
    throw 'WorkspaceRoot debe estar en la misma unidad que InstallationRoot.'
}
foreach ($externalPath in @($archive, $manifest, $checksum, $verifier)) {
    Assert-SafePath -Path $externalPath -Description 'La ruta de verificación'
    if (Test-PathWithin -Candidate $externalPath -Parent $installation) {
        throw 'Los artefactos y el verificador deben residir fuera de la instalación reemplazada.'
    }
}

Assert-ServiceTargetsInstallation -Root $installation
$serviceBefore = Get-Service -Name $serviceName -ErrorAction Stop
if ($serviceBefore.Status -ne 'Running') {
    throw 'La actualización real del laboratorio exige que el servicio anterior esté Running y saludable.'
}
Wait-LabHealth -Root $installation -TimeoutSeconds 10
Assert-NoReparseTree -Path $installation -Description 'La instalación de laboratorio'
$environmentBeforeSwap = Get-VerifiedEnvironmentSnapshot -Path (Join-Path $installation '.env')
$environmentHashBeforeSwap = $environmentBeforeSwap.Hash
$artifactSources = [ordered]@{
    archive = $archive
    manifest = $manifest
    checksum = $checksum
    verifier = $verifier
}
$artifactHashes = @{}
foreach ($entry in $artifactSources.GetEnumerator()) {
    $artifactHashes[$entry.Key] = Get-FileSha256Hex -Path $entry.Value
}

Write-Host 'Verificando ZIP, manifiesto y SHA-256 antes de detener el servicio...' -ForegroundColor Yellow
$verificationOutput = @(& $verifierPythonPath $verifier verify --archive $archive --manifest $manifest --checksum $checksum)
if ($LASTEXITCODE -ne 0) { throw 'La release no superó la verificación criptográfica y estructural.' }
try { $verification = ($verificationOutput -join [Environment]::NewLine) | ConvertFrom-Json }
catch { throw 'El verificador no devolvió un resultado JSON válido.' }
if ($verification.status -ne 'ok' -or [string]$verification.version -cne $ExpectedVersion) {
    throw 'La release verificada no coincide con ExpectedVersion.'
}
foreach ($entry in $artifactSources.GetEnumerator()) {
    if ((Get-FileSha256Hex -Path $entry.Value) -cne $artifactHashes[$entry.Key]) {
        throw 'Un artefacto o el verificador cambió durante la verificación inicial.'
    }
}

New-Item -ItemType Directory -Path $workspace -Force | Out-Null
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$sealed = Join-Path $workspace ("sellada-$ExpectedVersion-$stamp")
$staging = Join-Path $workspace ("stage-$ExpectedVersion-$stamp")
$backup = Join-Path (Split-Path -Parent $installation) ((Split-Path -Leaf $installation) + "-respaldo-lab-$stamp")
$failed = Join-Path $workspace ("fallida-$ExpectedVersion-$stamp")
foreach ($path in @($sealed, $staging, $backup, $failed)) {
    Assert-SafePath -Path $path -Description 'La ruta temporal'
    if (Test-Path -LiteralPath $path) { throw "La ruta temporal ya existe: $path" }
}

$sourceLeaves = @($artifactSources.Values | ForEach-Object { Split-Path -Leaf $_ })
if (@($sourceLeaves | Sort-Object -Unique).Count -ne $sourceLeaves.Count) {
    throw 'Los artefactos y el verificador deben tener nombres de archivo distintos.'
}
New-PrivateDirectory -Path $sealed
$sealedArchive = Join-Path $sealed (Split-Path -Leaf $archive)
$sealedManifest = Join-Path $sealed (Split-Path -Leaf $manifest)
$sealedChecksum = Join-Path $sealed (Split-Path -Leaf $checksum)
$sealedVerifier = Join-Path $sealed (Split-Path -Leaf $verifier)
Copy-Item -LiteralPath $archive -Destination $sealedArchive
Copy-Item -LiteralPath $manifest -Destination $sealedManifest
Copy-Item -LiteralPath $checksum -Destination $sealedChecksum
Copy-Item -LiteralPath $verifier -Destination $sealedVerifier
$sealedArtifacts = [ordered]@{
    archive = $sealedArchive
    manifest = $sealedManifest
    checksum = $sealedChecksum
    verifier = $sealedVerifier
}
foreach ($entry in $sealedArtifacts.GetEnumerator()) {
    if ((Get-FileSha256Hex -Path $entry.Value) -cne $artifactHashes[$entry.Key]) {
        throw 'La copia privada no coincide con los artefactos verificados.'
    }
}

$sealedVerificationOutput = @(
    & $verifierPythonPath $sealedVerifier verify --archive $sealedArchive --manifest $sealedManifest --checksum $sealedChecksum
)
if ($LASTEXITCODE -ne 0) { throw 'La copia privada de la release no superó la verificación.' }
try {
    $sealedVerification = ($sealedVerificationOutput -join [Environment]::NewLine) | ConvertFrom-Json
}
catch { throw 'El verificador privado no devolvió un resultado JSON válido.' }
if (
    $sealedVerification.status -ne 'ok' -or
    [string]$sealedVerification.version -cne [string]$verification.version -or
    [string]$sealedVerification.commit -cne [string]$verification.commit -or
    [int]$sealedVerification.files -ne [int]$verification.files
) {
    throw 'La copia privada no coincide con la identidad de la release verificada.'
}
foreach ($entry in $sealedArtifacts.GetEnumerator()) {
    if ((Get-FileSha256Hex -Path $entry.Value) -cne $artifactHashes[$entry.Key]) {
        throw 'La copia privada cambió después de su verificación.'
    }
}

New-PrivateDirectory -Path $staging
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($sealedArchive, $staging)
Assert-NoReparseTree -Path $staging -Description 'El staging extraído'
$stagedVersionPath = Join-Path $staging 'VERSION'
$stagedUpdater = Join-Path $staging 'actualizar-servidor.ps1'
if (-not (Test-Path -LiteralPath $stagedVersionPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $stagedUpdater -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $staging '_release\manifest.json') -PathType Leaf)) {
    throw 'El staging no contiene los contratos mínimos de una release instalable.'
}
$stagedVersion = ([IO.File]::ReadAllText($stagedVersionPath, [Text.Encoding]::ASCII)).Trim()
if ($stagedVersion -cne $ExpectedVersion) { throw 'VERSION del staging no coincide con la release verificada.' }

if ($PrepareOnly) {
    Write-Host "Preparación terminada sin detener ni modificar el servicio. Staging: $staging" -ForegroundColor Green
    return
}

$serviceStopped = $false
$oldRootMoved = $false
$candidatePromoted = $false
$maintenanceMutex = $null
$copyBackupMutex = $null
$taskSnapshots = $null
try {
    $maintenanceMutex = Enter-LabMaintenanceMutex
    $copyBackupMutex = Enter-LabBackupMutex

    # Repite la precondición después de adquirir exclusión: la verificación del ZIP
    # es deliberadamente previa y otro mantenimiento pudo terminar mientras tanto.
    Assert-ServiceTargetsInstallation -Root $installation
    $serviceBeforeSwap = Get-Service -Name $serviceName -ErrorAction Stop
    if ($serviceBeforeSwap.Status -ne 'Running') {
        throw 'El servicio dejó de estar Running antes de la conmutación.'
    }
    Wait-LabHealth -Root $installation -TimeoutSeconds 10

    # Captura configuración o ausencia antes de que el motor de la candidata pueda
    # registrar tareas. El XML permanece sólo en memoria y no expone .env.
    $taskSnapshots = @(Get-ManagedTaskSnapshots)

    $environmentImmediatelyBeforeStop = Get-VerifiedEnvironmentSnapshot `
        -Path (Join-Path $installation '.env') `
        -ExpectedHash $environmentHashBeforeSwap

    Write-Host 'Deteniendo el servicio saludable para conmutar el laboratorio...' -ForegroundColor Yellow
    Stop-LabService
    $serviceStopped = $true

    # El respaldo completo mantiene código y estado previos. No se elimina al concluir.
    Move-Item -LiteralPath $installation -Destination $backup
    $oldRootMoved = $true
    Move-Item -LiteralPath $staging -Destination $installation
    $candidatePromoted = $true
    Copy-OperationalState `
        -SourceRoot $backup `
        -DestinationRoot $installation `
        -ExpectedEnvironmentHash $environmentHashBeforeSwap

    # La copia consistente ya terminó. El motor oficial volverá a adquirir este
    # mutex para su respaldo verificable sin bloquear a su propio proceso hijo.
    Release-LabMutex -Mutex $copyBackupMutex
    $copyBackupMutex = $null

    Write-Host 'Aplicando dependencias, migraciones, estáticos, servicio y comprobación interna...' -ForegroundColor Yellow
    $global:LASTEXITCODE = 0
    & (Join-Path $installation 'actualizar-servidor.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'actualizar-servidor.ps1 devolvió un código de error.' }
    Assert-ServiceTargetsInstallation -Root $installation
    Start-LabServiceAndVerify -Root $installation

    Write-Host "Laboratorio actualizado a $ExpectedVersion." -ForegroundColor Green
    Write-Host "Respaldo anterior conservado en: $backup"
}
catch {
    $originalFailure = $_
    Write-Warning 'La candidata no completó actualización y salud; se restaurará el laboratorio anterior.'
    $rollbackIssues = New-Object 'System.Collections.Generic.List[string]'
    $rootRestored = $false

    try {
        # El motor cambia la ubicación actual a la release; salir de ella permite
        # renombrar el árbol incluso cuando Windows protege el directorio en uso.
        Set-Location -LiteralPath $workspace
        if ($serviceStopped) { Stop-LabService }
        if ($candidatePromoted -and (Test-Path -LiteralPath $installation -PathType Container)) {
            Move-Item -LiteralPath $installation -Destination $failed
        }
        if ($oldRootMoved -and (Test-Path -LiteralPath $backup -PathType Container)) {
            Move-Item -LiteralPath $backup -Destination $installation
            Assert-ServiceTargetsInstallation -Root $installation
            $rootRestored = $true
        }
        elseif (-not $oldRootMoved -and (Test-Path -LiteralPath $installation -PathType Container)) {
            $rootRestored = $true
        }
    }
    catch {
        [void]$rollbackIssues.Add('No se pudo restaurar el árbol anterior: ' + $_.Exception.Message)
    }

    if ($null -ne $taskSnapshots) {
        try {
            Restore-ManagedTaskSnapshots -Snapshots $taskSnapshots
        }
        catch {
            [void]$rollbackIssues.Add('No se pudieron restaurar las tareas administradas: ' + $_.Exception.Message)
        }
    }

    if ($serviceStopped -and $rootRestored) {
        try { Start-LabServiceAndVerify -Root $installation }
        catch {
            [void]$rollbackIssues.Add('La versión anterior no recuperó servicio y salud: ' + $_.Exception.Message)
        }
    }

    if ($rollbackIssues.Count -gt 0) {
        throw ('Falló la actualización y el rollback requiere atención: ' + ($rollbackIssues -join ' | '))
    }
    if ($oldRootMoved) {
        Write-Warning "Se restauró la versión anterior y sus tareas: el respaldo volvió a ser la instalación activa. La candidata fallida permanece en $failed."
    }
    throw $originalFailure
}
finally {
    Release-LabMutex -Mutex $copyBackupMutex
    Release-LabMutex -Mutex $maintenanceMutex
}