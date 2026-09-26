<#
.SYNOPSIS
Crea, verifica o restaura un paquete integral H18 de una instalación Edge SQLite.
.DESCRIPTION
Windows PowerShell 5.1, Administrador. El restore requiere otra carpeta de
instalación con la marca .h18-restauracion-aislada; nunca sobrescribe datos.
Los paquetes contienen secretos y se guardan con ACL de SYSTEM/Administradores.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Backup", "Verify", "Restore")]
    [string]$Action,
    [string]$SourceRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$Python,
    [string]$BackupRoot,
    [string]$Bundle,
    [string]$TargetRoot
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSEdition -ne "Desktop") {
    throw "H18 requiere Windows PowerShell 5.1."
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "H18 requiere una consola elevada de Administrador."
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$helper = Join-Path $scriptRoot "herramientas\respaldo_integral.py"
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    throw "Falta la herramienta H18."
}
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    throw "Falta la instalación origen."
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $SourceRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Falta Python de la instalación."
}

function Assert-Physical {
    param([string]$Path, [switch]$Directory)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "H18 no acepta enlaces ni junctions."
    }
    if ($Directory -and -not $item.PSIsContainer) {
        throw "H18 esperaba una carpeta física."
    }
    if (-not $Directory -and $item.PSIsContainer) {
        throw "H18 esperaba un archivo físico."
    }
}

function Set-RestrictedAcl {
    param(
        [string]$Path,
        [ValidateSet("None", "Read", "Modify")]
        [string]$LocalService = "None"
    )
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    Assert-Physical -Path $Path -Directory:($item.PSIsContainer)
    $acl = if ($item.PSIsContainer) {
        New-Object Security.AccessControl.DirectorySecurity
    } else {
        New-Object Security.AccessControl.FileSecurity
    }
    $acl.SetAccessRuleProtection($true, $false)
    $admin = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $acl.SetOwner($admin)
    $inheritance = if ($item.PSIsContainer) { "ContainerInherit, ObjectInherit" } else { "None" }
    $rights = @{
        "S-1-5-18" = "FullControl"
        "S-1-5-32-544" = "FullControl"
    }
    if ($LocalService -eq "Read") {
        $rights["S-1-5-19"] = "ReadAndExecute"
    } elseif ($LocalService -eq "Modify") {
        $rights["S-1-5-19"] = "Modify"
    }
    foreach ($sid in $rights.Keys) {
        $who = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $who, $rights[$sid], $inheritance, "None", "Allow"
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Assert-RestrictedAcl {
    param([string]$Path, [switch]$AllowLocalService)
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) {
        throw "La ACL H18 conserva herencia no autorizada."
    }
    $allowed = @("S-1-5-18", "S-1-5-32-544")
    if ($AllowLocalService) { $allowed += "S-1-5-19" }
    $rules = @($acl.GetAccessRules(
        $true, $true, [Security.Principal.SecurityIdentifier]
    ))
    foreach ($rule in $rules) {
        if ($rule.IsInherited -or
            $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $rule.IdentityReference.Value -notin $allowed) {
            throw "La ACL H18 permite una identidad ajena."
        }
    }
    if (@($rules | Where-Object {
        $_.IdentityReference.Value -eq "S-1-5-18"
    }).Count -ne 1 -or @($rules | Where-Object {
        $_.IdentityReference.Value -eq "S-1-5-32-544"
    }).Count -ne 1) {
        throw "La ACL H18 carece de SYSTEM o Administradores."
    }
}

function Protect-Tree {
    param([string]$Path, [string]$LocalService = "None")
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-Physical -Path $Path -Directory
    Set-RestrictedAcl -Path $Path -LocalService $LocalService
    foreach ($item in Get-ChildItem -LiteralPath $Path -Recurse -Force) {
        Assert-Physical -Path $item.FullName -Directory:($item.PSIsContainer)
        Set-RestrictedAcl -Path $item.FullName -LocalService $LocalService
    }
}


function Assert-EncryptedExternalVolume {
    param([string]$Path, [string]$AgainstRoot)
    $full = [IO.Path]::GetFullPath($Path)
    $volume = [IO.Path]::GetPathRoot($full)
    $sourceVolume = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($AgainstRoot))
    if ([string]::IsNullOrWhiteSpace($volume) -or
        $volume.StartsWith("\\") -or
        $volume.Equals($sourceVolume, [StringComparison]::OrdinalIgnoreCase)) {
        throw "H18 exige un volumen externo distinto del Edge."
    }
    if (-not (Test-Path -LiteralPath $volume -PathType Container)) {
        throw "Falta el volumen externo."
    }
    $candidate = $full
    while ($true) {
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "H18 no acepta junctions en el volumen de respaldo."
            }
        }
        if ($candidate.Equals($volume.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
            $candidate.Equals($volume, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $parent = Split-Path -Parent $candidate
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $candidate) {
            throw "No se pudo verificar el volumen externo."
        }
        $candidate = $parent
    }
    try {
        $state = Get-BitLockerVolume -MountPoint $volume.TrimEnd('') -ErrorAction Stop
    } catch {
        throw "No se pudo acreditar BitLocker en el volumen externo."
    }
    if ($null -eq $state -or
        [string]$state.ProtectionStatus -ne "On" -or
        [string]$state.VolumeStatus -ne "FullyEncrypted" -or
        [int]$state.EncryptionPercentage -ne 100) {
        throw "El volumen externo no está completamente cifrado y protegido con BitLocker."
    }
}

function Get-ServiceExecutablePath {
    param([string]$PathName)
    $value = ([string]$PathName).Trim()
    if ($value.StartsWith('"')) {
        if ($value -notmatch '^"([^"]+)"(?:\s|$)') {
            throw "PathName de servicio inválido."
        }
        return [IO.Path]::GetFullPath($Matches[1])
    }
    $executable = ($value -split '\s+', 2)[0]
    if ([string]::IsNullOrWhiteSpace($executable)) {
        throw "PathName de servicio vacío."
    }
    return [IO.Path]::GetFullPath($executable)
}

function Invoke-H18 {
    param([string[]]$Arguments)
    $output = @(& $Python "-I" $helper @Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "H18 falló; el paquete o destino no cumple el contrato."
    }
    $lines = @($output | ForEach-Object { ([string]$_).Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 1) {
        throw "H18 no devolvió un único resultado."
    }
    try {
        $result = $lines[0] | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "H18 no devolvió JSON válido."
    }
    if ([string]$result.status -ne "ok") {
        throw "H18 no confirmó el resultado."
    }
    return $lines[0]
}

function Enter-BackupMutex {
    $security = New-Object Security.AccessControl.MutexSecurity
    $security.SetAccessRuleProtection($true, $false)
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $who = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.MutexAccessRule(
            $who, [Security.AccessControl.MutexRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$security.AddAccessRule($rule)
    }
    $created = $false
    $mutex = New-Object Threading.Mutex(
        $false, "Global\LosTocayosPOS-RespaldoSQLite-v1",
        [ref]$created, $security
    )
    try {
        if (-not $mutex.WaitOne([TimeSpan]::FromMinutes(10))) {
            $mutex.Dispose()
            throw "Otro respaldo conserva el mutex H18."
        }
    } catch [Threading.AbandonedMutexException] {
        return $mutex
    }
    return $mutex
}

Assert-Physical -Path $SourceRoot -Directory
$mutex = $null
$restartService = $false
try {
    if ($Action -eq "Backup") {
        if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
            throw "Backup exige -BackupRoot en volumen externo cifrado."
        }
        $BackupRoot = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
        Assert-EncryptedExternalVolume -Path $BackupRoot -AgainstRoot $SourceRoot
        if (-not (Test-Path -LiteralPath $BackupRoot)) {
            New-Item -ItemType Directory -Path $BackupRoot | Out-Null
        }
        Assert-Physical -Path $BackupRoot -Directory
        Set-RestrictedAcl -Path $BackupRoot
        Assert-RestrictedAcl -Path $BackupRoot
        $mutex = Enter-BackupMutex
        $service = Get-CimInstance Win32_Service -Filter "Name='LosTocayosPOS'" -ErrorAction Stop
        if ($null -ne $service) {
            $executable = Get-ServiceExecutablePath -PathName $service.PathName
            $expected = [IO.Path]::GetFullPath(
                (Join-Path $SourceRoot ".venv\Scripts\pythonservice.exe")
            )
            if ($executable.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -and
                $service.State -eq "Running") {
                Stop-Service -Name "LosTocayosPOS" -ErrorAction Stop
                (Get-Service -Name "LosTocayosPOS").WaitForStatus(
                    "Stopped", [TimeSpan]::FromSeconds(60)
                )
                $restartService = $true
            }
        }
        $result = Invoke-H18 -Arguments @(
            "create", "--source-root", $SourceRoot, "--output-root", $BackupRoot
        )
        $parsed = $result | ConvertFrom-Json
        $bundlePath = [IO.Path]::GetFullPath([string]$parsed.bundle)
        if (-not ($bundlePath.StartsWith(
            $BackupRoot + "\", [StringComparison]::OrdinalIgnoreCase
        ))) {
            throw "H18 publicó fuera de la raíz protegida."
        }
        Protect-Tree -Path $bundlePath
        Write-Output $result
    } elseif ($Action -eq "Verify") {
        if ([string]::IsNullOrWhiteSpace($Bundle)) {
            throw "Verify requiere -Bundle."
        }
        Assert-EncryptedExternalVolume -Path $Bundle -AgainstRoot $SourceRoot
        Assert-Physical -Path $Bundle -Directory
        Assert-RestrictedAcl -Path $Bundle
        Assert-RestrictedAcl -Path (Join-Path $Bundle ".env")
        Write-Output (Invoke-H18 -Arguments @("verify", "--bundle", $Bundle))
    } else {
        if ([string]::IsNullOrWhiteSpace($Bundle) -or
            [string]::IsNullOrWhiteSpace($TargetRoot)) {
            throw "Restore requiere -Bundle y -TargetRoot."
        }
        Assert-EncryptedExternalVolume -Path $Bundle -AgainstRoot $SourceRoot
        Assert-Physical -Path $Bundle -Directory
        Assert-RestrictedAcl -Path $Bundle
        Assert-RestrictedAcl -Path (Join-Path $Bundle ".env")
        $TargetRoot = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\')
        Assert-Physical -Path $TargetRoot -Directory
        $marker = Join-Path $TargetRoot ".h18-restauracion-aislada"
        Assert-Physical -Path $marker
        if ([IO.File]::ReadAllText($marker) -cne ("H18:ISOLATED" + [char]10)) {
            throw "Falta la marca de restauración aislada."
        }
        if ($TargetRoot.Equals($SourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $TargetRoot.StartsWith($SourceRoot + "\", [StringComparison]::OrdinalIgnoreCase) -or
            $SourceRoot.StartsWith($TargetRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Restore exige otra instalación aislada."
        }
        $service = Get-CimInstance Win32_Service -Filter "Name='LosTocayosPOS'" -ErrorAction Stop
        if ($null -ne $service) {
            $executable = Get-ServiceExecutablePath -PathName $service.PathName
            $targetExecutable = [IO.Path]::GetFullPath(
                (Join-Path $TargetRoot ".venv\Scripts\pythonservice.exe")
            )
            if ($executable.Equals($targetExecutable, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Restore no puede apuntar a la instalación con servicio registrado."
            }
        }
        Set-RestrictedAcl -Path $TargetRoot -LocalService "Read"
        Assert-RestrictedAcl -Path $TargetRoot -AllowLocalService
        $mutex = Enter-BackupMutex
        $result = Invoke-H18 -Arguments @(
            "restore", "--bundle", $Bundle,
            "--target-root", $TargetRoot, "--source-root", $SourceRoot
        )
        Set-RestrictedAcl -Path (Join-Path $TargetRoot ".env") -LocalService "Read"
        Protect-Tree -Path (Join-Path $TargetRoot "runtime") -LocalService "Modify"
        Protect-Tree -Path (Join-Path $TargetRoot "media") -LocalService "Modify"
        Protect-Tree -Path (Join-Path $TargetRoot "certs") -LocalService "Read"
        Write-Output $result
    }
} finally {
    if ($restartService) {
        Start-Service -Name "LosTocayosPOS" -ErrorAction Stop
    }
    if ($null -ne $mutex) {
        try { $mutex.ReleaseMutex() } catch { }
        $mutex.Dispose()
    }
}
