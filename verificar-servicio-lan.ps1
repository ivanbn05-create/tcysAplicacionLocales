param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$HealthHost = "localhost",
    [switch]$RunBackup
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$service = Get-CimInstance Win32_Service -Filter "Name='LosTocayosPOS'"
if (-not $service -or $service.State -ne "Running" -or $service.StartName -ne "NT AUTHORITY\LocalService") {
    throw "El servicio no esta activo como LocalService."
}
$task = Get-ScheduledTask -TaskName "LosTocayosPOS-RespaldoSQLite"
if ($task.Principal.UserId -notin @("SYSTEM", "S-1-5-18", "NT AUTHORITY\SYSTEM")) {
    throw "La tarea de respaldo no usa SYSTEM."
}
$database = Join-Path $root "runtime\db.sqlite3"
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { throw "Falta runtime\db.sqlite3." }
if ($RunBackup) {
    $started = Get-Date
    Start-ScheduledTask -InputObject $task
    $deadline = $started.AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 500
        $info = Get-ScheduledTaskInfo -InputObject $task
        $task = Get-ScheduledTask -TaskName $task.TaskName
    } while (($info.LastRunTime -lt $started.AddSeconds(-1) -or $task.State -eq "Running") -and (Get-Date) -lt $deadline)
    if ($task.State -eq "Running" -or $info.LastTaskResult -ne 0 -or $info.LastRunTime -lt $started.AddSeconds(-1)) {
        throw "La tarea no completo un respaldo correcto dentro de 60 segundos."
    }
}
$info = Get-ScheduledTaskInfo -InputObject $task
$response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/salud/" -Headers @{ Host = $HealthHost } -TimeoutSec 5
if ($response.estado -ne "ok") { throw "/salud/ no confirma estado ok." }

$firewall = @(Get-NetFirewallRule -DisplayName "Los Tocayos POS - LAN privada")
if ($firewall.Count -ne 1 -or [string]$firewall[0].Enabled -ne "True" -or
    [string]$firewall[0].Profile -ne "Private" -or [string]$firewall[0].Action -ne "Allow" -or
    [string]$firewall[0].Direction -ne "Inbound") {
    throw "La regla de firewall no esta limitada a entrada en perfil privado."
}
$program = $firewall | Get-NetFirewallApplicationFilter
$portFilter = $firewall | Get-NetFirewallPortFilter
$addressFilter = $firewall | Get-NetFirewallAddressFilter
$listenerPaths = @(Get-NetTCPConnection -LocalPort $Port -State Listen | ForEach-Object {
    (Get-CimInstance Win32_Process -Filter ("ProcessId=" + $_.OwningProcess)).ExecutablePath
})
if ($program.Program -notin $listenerPaths -or $portFilter.LocalPort -ne [string]$Port -or
    $portFilter.Protocol -ne "TCP" -or @($addressFilter.RemoteAddress).Count -ne 1 -or
    @($addressFilter.RemoteAddress)[0] -ne "LocalSubnet") {
    throw "El firewall no coincide con el proceso/puerto que escucha o con la subred local."
}

$count = 0
$violations = New-Object 'System.Collections.Generic.List[string]'
$queue = New-Object 'System.Collections.Generic.Queue[string]'
$queue.Enqueue($root)
while ($queue.Count) {
    $path = $queue.Dequeue()
    $item = Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Se encontro un enlace/junction; no se sigue durante la auditoria."
    }
    $relative = $path.Substring($root.Length).TrimStart('\')
    $rules = @((Get-Acl -LiteralPath $path).GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $full = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and $_.AccessControlType -eq "Allow" -and
            ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and
            ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl
        })
        if (-not $full.Count) { $violations.Add($relative + ": falta control administrativo efectivo") }
    }
    if (@($rules | Where-Object {
        $_.AccessControlType -ne "Allow" -or
        $_.IdentityReference.Value -notin @("S-1-5-18", "S-1-5-32-544", "S-1-5-19")
    }).Count) { $violations.Add($relative + ": identidad o denegacion no prevista") }
    $ls = @($rules | Where-Object { $_.IdentityReference.Value -eq "S-1-5-19" })
    if (@($ls | Where-Object {
        ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]"ChangePermissions,TakeOwnership") -ne 0
    }).Count) { $violations.Add($relative + ": LocalService puede cambiar permisos") }
    if ($relative -match '^(backups|\.git|tmp|\.venv-roto-[^\\]+)(\\|$)' -or $relative -match '^db\.sqlite3(?:-wal|-shm|-journal)?$') {
        if ($ls.Count) { $violations.Add($relative + ": LocalService accede a datos privados") }
    } elseif ($relative -match '^(runtime|logs|media)(\\|$)') {
        if (-not @($ls | Where-Object {
            ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -eq [Security.AccessControl.FileSystemRights]::Modify
        }).Count) { $violations.Add($relative + ": falta escritura operativa") }
    } else {
        if (-not $ls.Count -or @($ls | Where-Object {
            ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]"Write,Delete,ChangePermissions,TakeOwnership") -ne 0
        }).Count) { $violations.Add($relative + ": acceso de servicio incorrecto") }
    }
    $count++
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $path -Force) { $queue.Enqueue($child.FullName) }
    }
}
if ($violations.Count) { throw ("ACL no conformes: " + $violations.Count + "; " + ($violations | Select-Object -First 10) -join "; ") }
[pscustomobject]@{
    Service = $service.Name
    ServiceState = $service.State
    ServiceAccount = $service.StartName
    BackupTask = $task.TaskName
    BackupTaskState = [string]$task.State
    BackupLastResult = $info.LastTaskResult
    BackupLastRun = $info.LastRunTime
    Database = $database
    Health = $response.estado
    AuditedAclItems = $count
    AclViolations = $violations.Count
    FirewallProgram = $program.Program
    FirewallProfile = [string]$firewall[0].Profile
    FirewallRemoteAddress = $addressFilter.RemoteAddress
}
