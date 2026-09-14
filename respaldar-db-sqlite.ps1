param(
    [string]$Python,
    [string]$DatabasePath,
    [string]$BackupRoot,
    [string]$LogPath,
    [ValidateRange(1, 3650)][int]$RetentionDays = 30,
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 30,
    [ValidateRange(1, 3600)][int]$LockTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$backupFilePattern = '^db-\d{8}-\d{6}(?:-\d+)?\.sqlite3$'

function Get-CanonicalStoragePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Se recibio una ruta de almacenamiento vacia."
    }
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Assert-ExpectedStoragePath {
    param([string]$ProjectRoot, [string]$Path, [string]$ExpectedRelativePath)

    $projectPath = Get-CanonicalStoragePath -Path $ProjectRoot
    $actualPath = Get-CanonicalStoragePath -Path $Path
    $expectedPath = Get-CanonicalStoragePath -Path (Join-Path $projectPath $ExpectedRelativePath)
    if (-not $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "La ruta de almacenamiento queda fuera de su ubicacion admitida: $Path."
    }
    foreach ($candidate in @($projectPath, $actualPath)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        $item = Get-Item -LiteralPath $candidate -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "No se usan enlaces ni junctions para respaldos: $candidate."
        }
    }
    return $actualPath
}

function Get-AclOwnerSid {
    param([Security.AccessControl.FileSystemSecurity]$Acl)
    try {
        return (New-Object Security.Principal.NTAccount($Acl.Owner)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        try {
            return (New-Object Security.Principal.SecurityIdentifier($Acl.Owner)).Value
        }
        catch {
            throw "No se pudo acreditar el propietario de la ACL."
        }
    }
}

function Assert-StorageAcl {
    param(
        [string]$Path,
        [ValidateSet("None", "Modify")][string]$LocalServiceAccess = "None"
    )

    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "No se comprueban ACL sobre enlaces ni junctions: $Path."
    }
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) {
        throw "La ACL conserva herencia en $Path."
    }
    if ((Get-AclOwnerSid -Acl $acl) -ne "S-1-5-32-544") {
        throw "El propietario no es Administradores en $Path."
    }
    $rules = @($acl.GetAccessRules(
        $true, $true, [Security.Principal.SecurityIdentifier]
    ))
    $expected = @{
        "S-1-5-18" = [Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [Security.AccessControl.FileSystemRights]::FullControl
    }
    if ($LocalServiceAccess -eq "Modify") {
        $expected["S-1-5-19"] = (
            [Security.AccessControl.FileSystemRights]::Modify -bor
            [Security.AccessControl.FileSystemRights]::Synchronize
        )
    }
    if ($rules.Count -ne $expected.Count -or @($rules | Where-Object { $_.IsInherited }).Count) {
        throw "La ACL no contiene solo las ACE explicitas esperadas en $Path."
    }
    $expectedInheritance = if ($item.PSIsContainer) {
        [Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit"
    } else {
        [Security.AccessControl.InheritanceFlags]::None
    }
    foreach ($sid in $expected.Keys) {
        $matches = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and
            $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow
        })
        if ($matches.Count -ne 1 -or
            $matches[0].FileSystemRights -ne $expected[$sid] -or
            $matches[0].InheritanceFlags -ne $expectedInheritance -or
            $matches[0].PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
            throw "La ACE de $sid no coincide con el contrato en $Path."
        }
    }
}

function Set-StorageAcl {
    param(
        [string]$Path,
        [ValidateSet("None", "Modify")][string]$LocalServiceAccess = "None"
    )

    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "No se modifican ACL de enlaces ni junctions: $Path."
    }
    $acl = if ($item.PSIsContainer) {
        New-Object Security.AccessControl.DirectorySecurity
    } else {
        New-Object Security.AccessControl.FileSecurity
    }
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $acl.SetOwner($adminSid)
    $inheritance = if ($item.PSIsContainer) { "ContainerInherit, ObjectInherit" } else { "None" }
    $rights = @{
        "S-1-5-18" = "FullControl"
        "S-1-5-32-544" = "FullControl"
    }
    if ($LocalServiceAccess -eq "Modify") {
        $rights["S-1-5-19"] = "Modify"
    }
    foreach ($sid in $rights.Keys) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, $rights[$sid], $inheritance, "None", "Allow"
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    Assert-StorageAcl -Path $Path -LocalServiceAccess $LocalServiceAccess
}

function Protect-BackupFiles {
    param([string]$Path)

    $rootItem = Get-Item -LiteralPath $Path -Force
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "La raiz de respaldos no es una carpeta fisica: $Path."
    }
    Set-StorageAcl -Path $Path
    foreach ($item in Get-ChildItem -LiteralPath $Path -Force) {
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Backups solo admite archivos fisicos directos: $($item.FullName)."
        }
        if (-not $item.DirectoryName.Equals(
            $rootItem.FullName, [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "El archivo queda fuera de la raiz de respaldos: $($item.FullName)."
        }
        Set-StorageAcl -Path $item.FullName
    }
}

function Protect-BackupLog {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "El registro de respaldos no es un archivo fisico: $Path."
    }
    Set-StorageAcl -Path $Path -LocalServiceAccess "Modify"
}

function Initialize-BackupStorage {
    param([string]$ProjectRoot, [string]$BackupPath, [string]$BackupLogPath)

    $canonicalBackup = Assert-ExpectedStoragePath -ProjectRoot $ProjectRoot -Path $BackupPath -ExpectedRelativePath "backups"
    $canonicalLog = Assert-ExpectedStoragePath -ProjectRoot $ProjectRoot -Path $BackupLogPath -ExpectedRelativePath "logs\sqlite-backup.log"
    $logsPath = Split-Path -Parent $canonicalLog
    foreach ($directory in @($canonicalBackup, $logsPath)) {
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Path $directory | Out-Null
        }
        $item = Get-Item -LiteralPath $directory -Force
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "El almacenamiento esperado no es una carpeta fisica: $directory."
        }
    }
    Set-StorageAcl -Path $canonicalBackup
    Set-StorageAcl -Path $logsPath -LocalServiceAccess "Modify"
    Protect-BackupFiles -Path $canonicalBackup
    Protect-BackupLog -Path $canonicalLog
    return [pscustomobject]@{
        BackupRoot = $canonicalBackup
        LogPath = $canonicalLog
    }
}

function Enter-BackupMutex {
    param([int]$TimeoutSeconds)

    if ($PSVersionTable.PSEdition -ne "Desktop") {
        throw "El respaldo protegido debe ejecutarse con Windows PowerShell 5.1."
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
        $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
    }
    catch [Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        $mutex.Dispose()
        throw "Otro respaldo sigue activo despues de $TimeoutSeconds segundos."
    }
    return $mutex
}

if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $raiz ".venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($DatabasePath)) {
    $DatabasePath = Join-Path $raiz "runtime\db.sqlite3"
}
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
    $BackupRoot = Join-Path $raiz "backups"
}
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $raiz "logs\sqlite-backup.log"
}

$script = Join-Path $raiz "herramientas\respaldo_sqlite.py"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "No se encontro el Python virtual para respaldos: $Python"
}
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    throw "No se encontro la herramienta de respaldo: $script"
}

$backupMutex = $null
try {
    $backupMutex = Enter-BackupMutex -TimeoutSeconds $LockTimeoutSeconds
    $storage = Initialize-BackupStorage -ProjectRoot $raiz -BackupPath $BackupRoot -BackupLogPath $LogPath
    $pythonArguments = @(
        "-I", $script,
        "--database", $DatabasePath,
        "--backup-root", $storage.BackupRoot,
        "--log", $storage.LogPath,
        "--retention-days", $RetentionDays,
        "--timeout", $TimeoutSeconds
    )
    $pythonOutput = @(& $Python $pythonArguments)
    $pythonExitCode = $LASTEXITCODE

    # Esta pasada tambien protege publicaciones parciales dejadas por un error.
    Protect-BackupFiles -Path $storage.BackupRoot
    Protect-BackupLog -Path $storage.LogPath
    if ($pythonExitCode -ne 0) {
        exit $pythonExitCode
    }

    $resultLines = @($pythonOutput | ForEach-Object { ([string]$_).Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($resultLines.Count -ne 1) {
        throw "La herramienta de respaldo no devolvio un unico resultado JSON."
    }
    try {
        $result = $resultLines[0] | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "La herramienta de respaldo devolvio JSON invalido."
    }
    $backupFile = [string]$result.backup_file
    $sha256 = [string]$result.sha256
    $sizeBytes = 0L
    if ([string]$result.status -ne "ok" -or
        $backupFile -cnotmatch $backupFilePattern -or
        $sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        -not [long]::TryParse([string]$result.size_bytes, [ref]$sizeBytes) -or
        $sizeBytes -le 0) {
        throw "El resultado JSON del respaldo no cumple el contrato."
    }
    $published = @(
        (Join-Path $storage.BackupRoot $backupFile),
        (Join-Path $storage.BackupRoot ($backupFile + ".sha256")),
        (Join-Path $storage.BackupRoot ($backupFile + ".json"))
    )
    foreach ($path in $published) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Falta un artefacto publicado por el respaldo: $path."
        }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "La salida publicada no es un archivo fisico: $path."
        }
        Assert-StorageAcl -Path $path
    }
    $backupItem = Get-Item -LiteralPath $published[0]
    if ($backupItem.Length -ne $sizeBytes -or
        (Get-FileHash -LiteralPath $published[0] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha256) {
        throw "Tamano o SHA-256 discordante en el respaldo publicado."
    }
    $expectedShaSidecar = $sha256 + "  " + $backupFile + [char]10
    if ([IO.File]::ReadAllText($published[1]) -cne $expectedShaSidecar) {
        throw "El sidecar SHA-256 no coincide exactamente con el respaldo."
    }
    try {
        $metadata = [IO.File]::ReadAllText($published[2]) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "El sidecar JSON del respaldo no es valido."
    }
    if ([string]$metadata.status -ne "ok" -or
        [string]$metadata.backup_file -cne $backupFile -or
        [string]$metadata.sha256 -cne $sha256 -or
        [long]$metadata.size_bytes -ne $sizeBytes -or
        [string]$metadata.sqlite_backup_check.integrity_check -ne "ok" -or
        [string]$metadata.restore_check.integrity_check -ne "ok") {
        throw "El sidecar JSON no acredita el triplete publicado."
    }
    Write-Output $resultLines[0]
    exit 0
}
finally {
    if ($null -ne $backupMutex) {
        try { $backupMutex.ReleaseMutex() } catch { }
        $backupMutex.Dispose()
    }
}
