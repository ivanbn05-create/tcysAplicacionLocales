# H17. Se ejecuta sólo en Windows runner con fixtures efímeros bajo RUNNER_TEMP.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'actualizar-laboratorio-desde-release.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw ($errors -join [Environment]::NewLine) }
foreach ($definition in $ast.FindAll({
    param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]
}, $false)) {
    Invoke-Expression $definition.Extent.Text
}
$serviceName = 'LosTocayosPOS'
$script:stops = 0
$script:healthChecks = 0
$script:restores = 0
$script:startMode = ''
$script:failHealth = $false
function Assert-ServiceTargetsInstallation { param([string]$Root) }
function Stop-LabService { $script:stops++ }
function Start-LabServiceAndVerify {
    param([string]$Root)
    $script:healthChecks++
    if ($script:failHealth) { throw 'health fixture failed' }
}
function Set-LabStartMode {
    param([string]$Mode, [bool]$Delayed)
    $script:startMode = $Mode
}
function Disable-LabManagedTasks { param([object[]]$Snapshots) }
function Restore-ManagedTaskSnapshots {
    param([object[]]$Snapshots)
    if ($Snapshots.Count -ne 2) { throw 'task fixture invalid' }
    $script:restores++
}
function Get-VerifiedEnvironmentSnapshot {
    param([string]$Path, [string]$ExpectedHash = '')
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'missing env fixture' }
    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ExpectedHash -and $hash -cne $ExpectedHash) { throw 'env fixture mismatch' }
    return [pscustomobject]@{ Hash = $hash }
}
function Assert-Equal {
    param($Actual, $Expected, [string]$Description)
    if ($Actual -cne $Expected) {
        throw "$Description : se esperaba [$Expected] y llegó [$Actual]"
    }
}
function New-Tree {
    param([string]$Root, [string]$Version, [string]$Database, [string]$Outbox)
    [void](New-Item -ItemType Directory -Path (Join-Path $Root 'runtime') -Force)
    [IO.File]::WriteAllText((Join-Path $Root 'VERSION'), $Version)
    [IO.File]::WriteAllText((Join-Path $Root '.env'), 'LAB_ONLY=1')
    [IO.File]::WriteAllText((Join-Path $Root 'runtime\db.sqlite3'), $Database)
    [IO.File]::WriteAllText((Join-Path $Root 'runtime\outbox.fixture'), $Outbox)
}
function New-Fixture {
    param([string]$Phase, [bool]$BackupExists, [bool]$CandidateExists)
    if (-not $env:RUNNER_TEMP) { throw 'H17 requiere RUNNER_TEMP en Windows runner.' }
    $fixtureParent = Join-Path $env:RUNNER_TEMP ('h17-' + [Guid]::NewGuid().ToString('N'))
    $installation = Join-Path $fixtureParent 'App'
    $workspace = Join-Path $fixtureParent 'Workspace'
    $stamp = '20260926-120000-abcdef12'
    $backup = Join-Path $fixtureParent ('App-respaldo-lab-' + $stamp)
    $failed = Join-Path $workspace ('fallida-1.0.0-dev.2-' + $stamp)
    $staging = Join-Path $workspace ('stage-1.0.0-dev.2-' + $stamp)
    [void](New-Item -ItemType Directory -Path $workspace -Force)
    if ($BackupExists) {
        New-Tree -Root $backup -Version '0.4.0-dev.10' -Database 'old-db' -Outbox 'old-outbox'
    }
    if ($CandidateExists) {
        New-Tree -Root $installation -Version '1.0.0-dev.2' -Database 'candidate-db' -Outbox 'candidate-outbox'
    }
    elseif (-not $BackupExists) {
        New-Tree -Root $installation -Version '0.4.0-dev.10' -Database 'old-db' -Outbox 'old-outbox'
    }
    $environment = if ($BackupExists) {
        Join-Path $backup '.env'
    } else {
        Join-Path $installation '.env'
    }
    $journal = [pscustomobject]@{
        schema = 1
        id = [Guid]::NewGuid().ToString('N')
        stamp = $stamp
        installation = $installation
        workspace = $workspace
        backup = $backup
        staging = $staging
        failed = $failed
        oldVersion = '0.4.0-dev.10'
        newVersion = '1.0.0-dev.2'
        environmentHash = (Get-FileHash -LiteralPath $environment -Algorithm SHA256).Hash.ToLowerInvariant()
        startMode = 'Auto'
        delayed = $true
        tasks = @(
            [pscustomobject]@{ Name = 'LosTocayosPOS-RespaldoSQLite'; Existed = $true; Xml = '<Task />' },
            [pscustomobject]@{ Name = 'LosTocayosPOS-PurgasFisicas'; Existed = $false; Xml = $null }
        )
        phase = $Phase
    }
    $journalPath = Join-Path $workspace 'actualizacion-pendiente.json'
    Write-LabJournal -Journal $journal -Path $journalPath
    return [pscustomobject]@{
        Installation = $installation
        Workspace = $workspace
        Backup = $backup
        Failed = $failed
        Journal = $journalPath
        Id = $journal.id
    }
}
function Assert-OldRestored {
    param([object]$Fixture)
    Assert-Equal (Get-Content -LiteralPath (Join-Path $Fixture.Installation 'VERSION') -Raw).Trim() '0.4.0-dev.10' 'VERSION'
    Assert-Equal (Get-Content -LiteralPath (Join-Path $Fixture.Installation 'runtime\db.sqlite3') -Raw) 'old-db' 'SQLite'
    Assert-Equal (Get-Content -LiteralPath (Join-Path $Fixture.Installation 'runtime\outbox.fixture') -Raw) 'old-outbox' 'outbox'
    Assert-Equal (Get-Content -LiteralPath (Join-Path $Fixture.Installation '.env') -Raw) 'LAB_ONLY=1' '.env'
    if (Test-Path -LiteralPath $Fixture.Journal) { throw 'journal pendiente tras recuperación' }
}
function Recover-Fixture {
    param([object]$Fixture)
    Recover-LabJournal -Path $Fixture.Journal -Installation $Fixture.Installation -Workspace $Fixture.Workspace
}
# Corte previo a Stop-Service.
$fixture = New-Fixture -Phase 'prepared' -BackupExists $false -CandidateExists $false
Recover-Fixture -Fixture $fixture
Assert-OldRestored -Fixture $fixture
# Corte con root ausente durante la conmutación atómica.
$fixture = New-Fixture -Phase 'old_moved' -BackupExists $true -CandidateExists $false
Recover-Fixture -Fixture $fixture
Assert-OldRestored -Fixture $fixture
# Corte después de instalar candidata y copiar estado, antes del motor.
$fixture = New-Fixture -Phase 'state_copied' -BackupExists $true -CandidateExists $true
Recover-Fixture -Fixture $fixture
Assert-OldRestored -Fixture $fixture
Assert-Equal (Get-Content -LiteralPath (Join-Path $fixture.Failed 'runtime\db.sqlite3') -Raw) 'candidate-db' 'candidata conservada'
# Corte después del motor y antes del health externo: se conserva candidata con datos nuevos.
$fixture = New-Fixture -Phase 'engine_complete' -BackupExists $true -CandidateExists $true
Recover-Fixture -Fixture $fixture
Assert-Equal (Get-Content -LiteralPath (Join-Path $fixture.Installation 'runtime\db.sqlite3') -Raw) 'candidate-db' 'SQLite nueva'
Assert-Equal (Get-Content -LiteralPath (Join-Path $fixture.Installation 'runtime\outbox.fixture') -Raw) 'candidate-outbox' 'outbox nuevo'
if (-not (Test-Path -LiteralPath $fixture.Backup)) { throw 'respaldo anterior perdido' }
if (Test-Path -LiteralPath $fixture.Journal) { throw 'journal pendiente tras health verificado' }
Recover-Fixture -Fixture $fixture
# Corte ambiguo mientras corre el motor: detener y preservar ambos árboles.
$fixture = New-Fixture -Phase 'engine_running' -BackupExists $true -CandidateExists $true
foreach ($attempt in 1..2) {
    try {
        Recover-Fixture -Fixture $fixture
        throw 'engine_running debió fallar cerrado'
    }
    catch {
        if ($_.Exception.Message -notmatch 'motor pudo migrar') { throw }
    }
    if (-not (Test-Path -LiteralPath $fixture.Journal) -or
        -not (Test-Path -LiteralPath $fixture.Backup) -or
        -not (Test-Path -LiteralPath $fixture.Installation)) {
        throw 'El estado ambiguo perdió un árbol o el journal.'
    }
    Assert-Equal $script:startMode 'Manual' 'modo seguro'
}
# Health posterior fallido también conserva ambos y puede reintentarse.
$fixture = New-Fixture -Phase 'engine_complete' -BackupExists $true -CandidateExists $true
$script:failHealth = $true
try {
    Recover-Fixture -Fixture $fixture
    throw 'health fallido debió fallar cerrado'
}
catch {
    if ($_.Exception.Message -notmatch 'sin salud verificada') { throw }
}
if (-not (Test-Path -LiteralPath $fixture.Journal) -or
    -not (Test-Path -LiteralPath $fixture.Backup) -or
    -not (Test-Path -LiteralPath $fixture.Installation)) {
    throw 'Health fallido perdió un árbol o el journal.'
}
$script:failHealth = $false
Recover-Fixture -Fixture $fixture
if (Test-Path -LiteralPath $fixture.Journal) { throw 'Health reintentado no cerró journal.' }
Write-Host 'H17 fixtures: 6 ventanas y reintento idempotente OK.'
