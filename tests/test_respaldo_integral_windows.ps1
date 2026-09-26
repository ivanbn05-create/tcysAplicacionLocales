<#
Prueba H18 Windows aislada. Requiere PowerShell 5.1 elevado y una unidad BitLocker
externa desbloqueada. Nunca apunta al Edge instalado.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$EncryptedRoot,
    [string]$Python = (Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe")
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "respaldo-integral.ps1"
$lab = Join-Path ([IO.Path]::GetTempPath()) ("h18-windows-lab-" + [Guid]::NewGuid().ToString("N"))
$source = Join-Path $lab "source"
$target = Join-Path $lab "target"
$external = Join-Path $EncryptedRoot ("h18-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $source, $target | Out-Null
[IO.File]::WriteAllText((Join-Path $source "VERSION"), "1.0.0")
New-Item -ItemType Directory -Path (Join-Path $source "runtime") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $source "certs") | Out-Null
[IO.File]::WriteAllText(
    (Join-Path $source ".env"),
    "DB_ENGINE=sqlite" + [char]10 +
    "SQLITE_PATH=runtime/db.sqlite3" + [char]10 +
    "SUCURSAL_CLAVE=H18-LAB" + [char]10 +
    "CENTRAL_CATALOG_TOKEN=token-sintetico-h18" + [char]10 +
    "CENTRAL_API_CA_BUNDLE=certs/central.pem" + [char]10,
    (New-Object Text.UTF8Encoding($false))
)
[IO.File]::WriteAllText((Join-Path $source "certs\central.pem"), "CA-SINTETICA")
[IO.File]::WriteAllText((Join-Path $target "manage.py"), "# release aislada")
[IO.File]::WriteAllText((Join-Path $target "VERSION"), "1.0.0")
[IO.File]::WriteAllText(
    (Join-Path $target ".h18-restauracion-aislada"),
    "H18:ISOLATED" + [char]10,
    (New-Object Text.UTF8Encoding($false))
)
$db = Join-Path $source "runtime\db.sqlite3"
$createDb = @'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("CREATE TABLE ventas_eventooutbox (id TEXT PRIMARY KEY, estado TEXT)")
    db.execute("INSERT INTO ventas_eventooutbox VALUES ('ack-lab', 'pendiente')")
'@
& $Python "-c" $createDb $db
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear SQLite sintética." }

$created = & $script -Action Backup -SourceRoot $source -Python $Python -BackupRoot $external |
    ConvertFrom-Json
if ($created.status -ne "ok") { throw "Backup H18 falló." }
$bundle = [string]$created.bundle
$verified = & $script -Action Verify -SourceRoot $source -Python $Python -Bundle $bundle |
    ConvertFrom-Json
if ($verified.status -ne "ok") { throw "Verify H18 falló." }
$restored = & $script -Action Restore -SourceRoot $source -Python $Python -Bundle $bundle -TargetRoot $target |
    ConvertFrom-Json
if ($restored.status -ne "ok") { throw "Restore H18 falló." }
$checkDb = @'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("SELECT estado FROM ventas_eventooutbox").fetchone() == ("pendiente",)
'@
& $Python "-c" $checkDb (Join-Path $target "runtime\db.sqlite3")
if ($LASTEXITCODE -ne 0) { throw "Outbox restaurado difiere." }
$envText = [IO.File]::ReadAllText((Join-Path $target ".env"))
if (-not $envText.Contains("CENTRAL_CATALOG_TOKEN=token-sintetico-h18") -or
    -not $envText.Contains("CENTRAL_API_CA_BUNDLE=certs/h18/CENTRAL_API_CA_BUNDLE.pem")) {
    throw "Configuración o trust store restaurados difieren."
}
$manifestText = [IO.File]::ReadAllText((Join-Path $bundle "manifest.json"))
if ($manifestText.Contains("token-sintetico-h18")) {
    throw "El manifiesto expone un token."
}
$acl = Get-Acl -LiteralPath (Join-Path $bundle ".env")
$rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
if (-not $acl.AreAccessRulesProtected -or
    @($rules | Where-Object { $_.IdentityReference.Value -notin
        @("S-1-5-18", "S-1-5-32-544") }).Count -ne 0) {
    throw "La ACL del secreto externo no está restringida."
}
Write-Output '{"status":"ok","scope":"h18-windows-isolated"}'
