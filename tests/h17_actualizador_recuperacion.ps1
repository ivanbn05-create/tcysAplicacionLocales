# H17. Se ejecuta sólo en Windows runner con fixtures efímeros bajo RUNNER_TEMP.
param(
    [ValidateSet('SelfTest', 'Create', 'Recover')][string]$Mode = 'SelfTest',
    [string]$Phase = '',
    [string]$HandoffPath = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'actualizar-laboratorio-desde-release.ps1'
$sqliteHelper = Join-Path $PSScriptRoot 'h17_sqlite_fixture.py'
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
$script:failDisable = $false
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
function Disable-LabManagedTasks {
    param([object[]]$Snapshots)
    if ($script:failDisable) { throw 'task fixture failure' }
}
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
    & python $sqliteHelper create (Join-Path $Root 'runtime\db.sqlite3') $Database $Outbox
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear SQLite H17.' }
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
    & python $sqliteHelper verify (Join-Path $Fixture.Installation 'runtime\db.sqlite3') 'old-db' 'old-outbox'
    if ($LASTEXITCODE -ne 0) { throw 'SQLite/outbox anterior sin integridad.' }
    Assert-Equal (Get-Content -LiteralPath (Join-Path $Fixture.Installation '.env') -Raw) 'LAB_ONLY=1' '.env'
    if (Test-Path -LiteralPath $Fixture.Journal) { throw 'journal pendiente tras recuperación' }
}
function Recover-Fixture {
    param([object]$Fixture)
    Recover-LabJournal -Path $Fixture.Journal -Installation $Fixture.Installation -Workspace $Fixture.Workspace
}
if ($Mode -eq 'Create') {
    $backupExists = $Phase -ne 'prepared'
    $candidateExists = $Phase -in @('state_copied', 'engine_running', 'engine_complete')
    $fixture = New-Fixture -Phase $Phase -BackupExists $backupExists -CandidateExists $candidateExists
    [IO.File]::WriteAllText($HandoffPath, ($fixture | ConvertTo-Json -Compress))
    # El padre mata este proceso tras observar el marcador durable.
    while ($true) { Start-Sleep -Seconds 1 }
}
if ($Mode -eq 'Recover') {
    $fixture = Get-Content -LiteralPath $HandoffPath -Raw | ConvertFrom-Json
    if ($Phase -eq 'engine_running') {
        try { Recover-Fixture -Fixture $fixture; throw 'Se esperaba cuarentena.' }
        catch { if ($_.Exception.Message -notmatch 'motor pudo migrar') { throw } }
        if (-not (Test-Path -LiteralPath $fixture.Journal) -or
            -not (Test-Path -LiteralPath $fixture.Backup) -or
            -not (Test-Path -LiteralPath $fixture.Installation)) {
            throw 'Pérdida de proceso destruyó un árbol o journal.'
        }
        & python $sqliteHelper verify (Join-Path $fixture.Installation 'runtime\db.sqlite3') 'candidate-db' 'candidate-outbox'
        if ($LASTEXITCODE -ne 0) { throw 'SQLite/outbox ambiguo perdió integridad.' }
    }
    elseif ($Phase -eq 'engine_complete') {
        Recover-Fixture -Fixture $fixture
        & python $sqliteHelper verify (Join-Path $fixture.Installation 'runtime\db.sqlite3') 'candidate-db' 'candidate-outbox'
        if ($LASTEXITCODE -ne 0) { throw 'SQLite/outbox posterior a migración perdió integridad.' }
        if (-not (Test-Path -LiteralPath $fixture.Backup)) { throw 'El respaldo desapareció.' }
    }
    else {
        Recover-Fixture -Fixture $fixture
        Assert-OldRestored -Fixture $fixture
    }
    Write-Host "H17 proceso nuevo: fase $Phase OK."
    return
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
& python $sqliteHelper verify (Join-Path $fixture.Failed 'runtime\db.sqlite3') 'candidate-db' 'candidate-outbox'
if ($LASTEXITCODE -ne 0) { throw 'SQLite/outbox de candidata fallida perdió integridad.' }
# Corte después del motor y antes del health externo: se conserva candidata con datos nuevos.
$fixture = New-Fixture -Phase 'engine_complete' -BackupExists $true -CandidateExists $true
Recover-Fixture -Fixture $fixture
& python $sqliteHelper verify (Join-Path $fixture.Installation 'runtime\db.sqlite3') 'candidate-db' 'candidate-outbox'
if ($LASTEXITCODE -ne 0) { throw 'SQLite/outbox nuevo perdió integridad.' }
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

# Pérdida del proceso: journal y árboles se crean en un proceso, se mata ese
# proceso y un segundo PowerShell ejecuta la recuperación desde cero.
$powerShell = (Get-Process -Id $PID).Path
foreach ($phase in @('prepared', 'old_moved', 'state_copied', 'engine_running', 'engine_complete')) {
    $handoff = Join-Path $env:RUNNER_TEMP ('h17-handoff-' + [Guid]::NewGuid().ToString('N') + '.json')
    $createArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        $PSCommandPath + '" -Mode Create -Phase ' + $phase + ' -HandoffPath "' + $handoff + '"'
    $creator = Start-Process -FilePath $powerShell -ArgumentList $createArguments -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(30)
    try {
        while (-not (Test-Path -LiteralPath $handoff -PathType Leaf) -and
            (Get-Date) -lt $deadline -and -not $creator.HasExited) {
            Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Path -LiteralPath $handoff -PathType Leaf)) {
            throw "El proceso creador $phase no publicó journal."
        }
    }
    finally {
        if (-not $creator.HasExited) {
            Stop-Process -Id $creator.Id -Force -ErrorAction Stop
            [void]$creator.WaitForExit(10000)
        }
        $creator.Dispose()
    }
    $stdout = $handoff + '.stdout'
    $stderr = $handoff + '.stderr'
    $recoverArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        $PSCommandPath + '" -Mode Recover -Phase ' + $phase + ' -HandoffPath "' + $handoff + '"'
    $recovery = Start-Process -FilePath $powerShell -ArgumentList $recoverArguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    try {
        if (-not $recovery.WaitForExit(30000)) {
            Stop-Process -Id $recovery.Id -Force
            throw "La recuperación nueva $phase excedió 30 segundos."
        }
        if ($recovery.ExitCode -ne 0) {
            throw ("Recuperación nueva $phase falló: " +
                (Get-Content -LiteralPath $stdout -Raw) +
                (Get-Content -LiteralPath $stderr -Raw))
        }
    }
    finally { $recovery.Dispose() }
}
$beforeStops = $script:stops
$script:failDisable = $true
try {
    Suspend-LabCandidate -Snapshots @()
    throw 'La cuarentena incompleta debió fallar.'
}
catch {
    if ($_.Exception.Message -notmatch 'Cuarentena H17 incompleta') { throw }
}
$script:failDisable = $false
if ($script:stops -le $beforeStops) { throw 'Fallo de tarea impidió Stop-LabService.' }
Assert-Equal $script:startMode 'Manual' 'inicio manual pese a fallo de tarea'
Write-Host 'H17 fixtures: seis ventanas, cinco pérdidas de proceso y Stop independiente OK.'

