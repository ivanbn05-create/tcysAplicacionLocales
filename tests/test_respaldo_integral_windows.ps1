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
$trustSource = Join-Path $lab "release-trust-source.json"
$trustRestored = Join-Path $lab "trust-restaurado\release-trust.json"
$external = Join-Path $EncryptedRoot ("h18-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $source, $target | Out-Null
$publicXml = "<RSAKeyValue><Modulus>QUJD</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>"
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $keyId = (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($publicXml)) |
        ForEach-Object { $_.ToString("x2") }) -join "")
} finally { $sha.Dispose() }
@{ schema_version = 1; keys = @(@{
    key_id = $keyId; public_xml = $publicXml; status = "trusted"
}) } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $trustSource -Encoding UTF8
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

$created = & $script -Action Backup -SourceRoot $source -Python $Python -BackupRoot $external -TrustStorePath $trustSource |
    ConvertFrom-Json
if ($created.status -ne "ok") { throw "Backup H18 falló." }
$bundle = [string]$created.bundle
$verified = & $script -Action Verify -Python $Python -Bundle $bundle |
    ConvertFrom-Json
if ($verified.status -ne "ok") { throw "Verify H18 falló." }
Move-Item -LiteralPath $source -Destination (Join-Path $lab "source-perdida")
$restored = & $script -Action Restore -Python $Python -Bundle $bundle -TargetRoot $target -RestoreTrustStorePath $trustRestored |
    ConvertFrom-Json
if ($restored.status -ne "ok") { throw "Restore H18 falló." }
if ((Get-FileHash -LiteralPath $trustSource -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $trustRestored -Algorithm SHA256).Hash) {
    throw "Trust store de release restaurado difiere."
}
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
$nested = Join-Path $bundle "runtime\db.sqlite3"
$nestedAcl = Get-Acl -LiteralPath $nested
$everyone = New-Object Security.Principal.SecurityIdentifier("S-1-1-0")
$rule = New-Object Security.AccessControl.FileSystemAccessRule(
    $everyone, "Read", "Allow"
)
[void]$nestedAcl.AddAccessRule($rule)
Set-Acl -LiteralPath $nested -AclObject $nestedAcl
$verifiedUnsafe = $false
try {
    & $script -Action Verify -Python $Python -Bundle $bundle | Out-Null
    $verifiedUnsafe = $true
} catch { }
if ($verifiedUnsafe) {
    throw "Verify aceptó ACL pública en archivo anidado."
}
Write-Output '{"status":"ok","scope":"h18-windows-isolated"}'
