param(
    [string]$Python,
    [string]$DatabasePath,
    [string]$BackupRoot,
    [string]$LogPath,
    [ValidateRange(1, 3650)][int]$RetentionDays = 30,
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path

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
if (-not (Test-Path -LiteralPath $Python)) {
    throw "No se encontro el Python virtual para respaldos: $Python"
}
if (-not (Test-Path -LiteralPath $script)) {
    throw "No se encontro la herramienta de respaldo: $script"
}

& $Python $script `
    --database $DatabasePath `
    --backup-root $BackupRoot `
    --log $LogPath `
    --retention-days $RetentionDays `
    --timeout $TimeoutSeconds
exit $LASTEXITCODE
