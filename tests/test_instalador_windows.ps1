param([switch]$SkipAcl)

$ErrorActionPreference = "Stop"
$installer = Join-Path (Split-Path -Parent $PSScriptRoot) "instalar-servicio-lan.ps1"
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "Sintaxis PowerShell invalida." }
# Carga solo definiciones: nunca ejecuta el flujo de instalacion en estos tests.
foreach ($definition in $ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst]
}, $false)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$machinePython = Get-MachinePython
Assert-True (Test-MachinePythonPath $machinePython) "No se detecto Python de maquina."
Assert-True (-not (Test-MachinePythonPath (Join-Path $env:USERPROFILE "Python\python.exe"))) "Se acepto Python por usuario."
& {
    function Test-MachinePythonPath { param([string]$Path) return $false }
    $failed = $false
    try { Get-MachinePython | Out-Null } catch { $failed = $true }
    Assert-True $failed "El preflight debe fallar sin Python de maquina."
}

$main = ($ast.EndBlock.Statements | Where-Object {
    $_ -isnot [Management.Automation.Language.FunctionDefinitionAst]
} | ForEach-Object { $_.Extent.Text }) -join [Environment]::NewLine
function Assert-ComesBefore {
    param([string]$Earlier, [string]$Later, [string]$Message)
    $first = $main.IndexOf($Earlier)
    $second = $main.IndexOf($Later)
    Assert-True ($first -ge 0 -and $second -ge 0 -and $first -lt $second) $Message
}
Assert-ComesBefore '$machinePython = Get-MachinePython' 'Restore-AdministrativeAccess' "Se toca ACL antes de validar Python."
Assert-ComesBefore '$machinePython = Get-MachinePython' 'Stop-Service -Name $nombreServicio' "Se detiene el servicio antes de validar Python."
Assert-ComesBefore '& $python manage.py migrate' 'Protect-ApplicationTree -Path $raiz' "Se endurece antes de migrar."
Assert-ComesBefore 'validar_despliegue.py' 'Protect-ApplicationTree -Path $raiz' "Se endurece antes de las pruebas."
Assert-ComesBefore '& $python -m pip check' '& $python $servicioPython --check-host' "Se comprueba el host antes de las dependencias."
Assert-ComesBefore '& $python $servicioPython --check-host' 'Protect-ApplicationTree -Path $raiz' "Se endurece antes de comprobar el cargador nativo."
Assert-ComesBefore '& $python $servicioPython --check-host' 'Set-DotEnvValue -Path $entorno' "Se configura produccion antes de comprobar el host."

# El puerto lo abre el Python base, no el lanzador dentro de .venv/Scripts.
Assert-True ($main.Contains('sys._base_executable')) "No se resuelve el Python que escucha."
$firewallCommands = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'New-NetFirewallRule'
}, $true))
Assert-True ($firewallCommands.Count -eq 1) "No se encontro una unica regla de firewall."
$firewallArguments = @{}
$elements = $firewallCommands[0].CommandElements
for ($i = 0; $i -lt $elements.Count - 1; $i++) {
    if ($elements[$i] -is [Management.Automation.Language.CommandParameterAst]) {
        $firewallArguments[$elements[$i].ParameterName] = $elements[$i + 1].Extent.Text
    }
}
Assert-True ($firewallArguments['Program'] -eq '$pythonEscucha') "El firewall apunta al lanzador del venv."
Assert-True ($firewallArguments['Profile'] -eq 'Private') "El firewall no queda limitado al perfil privado."
Assert-True ($firewallArguments['RemoteAddress'] -eq 'LocalSubnet') "El firewall no queda limitado a la subred local."

$raiz = Join-Path ([IO.Path]::GetTempPath()) ("tocayos-installer-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $raiz | Out-Null
$outsideFailed = $false
try { Assert-ProjectPath (Split-Path -Parent $raiz) } catch { $outsideFailed = $true }
Assert-True $outsideFailed "Se acepto una ruta fuera del proyecto."

# Caso .env nuevo/con una sola linea: no debe concatenar las variables.
$testEnv = Join-Path $raiz ".env"
Set-DotEnvValue -Path $testEnv -Name "FIRST" -Value "one"
Set-DotEnvValue -Path $testEnv -Name "SECOND" -Value "two"
Set-DotEnvValue -Path $testEnv -Name "FIRST" -Value "updated"
Assert-True ((Get-DotEnvValue $testEnv "FIRST") -eq "updated") "No se actualizo una variable."
Assert-True ((Get-DotEnvValue $testEnv "SECOND") -eq "two") "Se concatenaron variables de .env."

$broken = Join-Path $raiz ".venv"
New-Item -ItemType Directory -Path $broken | Out-Null
Set-Content -LiteralPath (Join-Path $broken "preservar.txt") -Value "fixture"
Assert-True (-not (Test-VirtualEnvironment $broken)) "No se detecto .venv incompleta."
Initialize-VirtualEnvironment -MachinePython $machinePython
Assert-True (Test-VirtualEnvironment $broken) "No se creo una .venv funcional."
$saved = @(Get-ChildItem -LiteralPath $raiz -Directory -Filter ".venv-roto-*")
Assert-True ($saved.Count -eq 1) "No se conservo el entorno roto."
Assert-True (Test-Path -LiteralPath (Join-Path $saved[0].FullName "preservar.txt")) "Se perdio el entorno anterior."
Initialize-VirtualEnvironment -MachinePython $machinePython
Assert-True (@(Get-ChildItem -LiteralPath $raiz -Directory -Filter ".venv-roto-*").Count -eq 1) "Se recreo un entorno sano."

if (-not $SkipAcl) {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Assert-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) "Ejecuta las pruebas ACL como administrador o usa -SkipAcl."
    foreach ($directory in @("backups", "certs", "runtime", "logs", "media", ".git", "tmp")) {
        $folder = Join-Path $raiz $directory
        New-Item -ItemType Directory -Path $folder | Out-Null
        Set-Content -LiteralPath (Join-Path $folder "fixture.txt") -Value "fixture"
    }
    $code = Join-Path $raiz "manage.py"
    Set-Content -LiteralPath $code -Value "# fixture"
    $emptyAcl = New-Object Security.AccessControl.FileSecurity
    $emptyAcl.SetAccessRuleProtection($true, $false)
    $emptyAcl.SetOwner((New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")))
    Set-Acl -LiteralPath $code -AclObject $emptyAcl
    Restore-AdministrativeAccess -Path $code
    Assert-True ([IO.File]::ReadAllText($code).Contains("fixture")) "No se recupero el archivo con DACL vacia."
    $repairedRules = (Get-Acl -LiteralPath $code).GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
    Assert-True (@($repairedRules | Where-Object { $_.IdentityReference.Value -eq "S-1-5-19" }).Count -eq 0) "La reparacion concedio acceso a LocalService."
    & icacls.exe $code /grant:r '*S-1-5-19:M' | Out-Null
    Protect-ApplicationTree -Path $raiz
    foreach ($item in @(Get-Item -LiteralPath $raiz) + @(Get-ChildItem -LiteralPath $raiz -Force -Recurse)) {
        $acl = Get-Acl -LiteralPath $item.FullName
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        Assert-True $acl.AreAccessRulesProtected "Quedo herencia externa en $($item.FullName)."
        foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
            $effective = @($rules | Where-Object {
                $_.IdentityReference.Value -eq $sid -and
                $_.AccessControlType -eq "Allow" -and
                $_.PropagationFlags -eq "None" -and
                ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl
            })
            Assert-True ($effective.Count -eq 1) "Falta acceso administrativo efectivo en $($item.FullName)."
        }
        Assert-True (@($rules | Where-Object { $_.IdentityReference.Value -notin @("S-1-5-18", "S-1-5-32-544", "S-1-5-19") }).Count -eq 0) "Quedo una identidad no permitida."
        $relative = $item.FullName.Substring($raiz.Length).TrimStart('\')
        $ls = @($rules | Where-Object { $_.IdentityReference.Value -eq "S-1-5-19" })
        Assert-True (@($ls | Where-Object {
            ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]"ChangePermissions,TakeOwnership") -ne 0
        }).Count -eq 0) "LocalService puede cambiar permisos."
        if ($relative -match '^(backups|\.git|tmp|\.venv-roto-[^\\]+)(\\|$)') {
            Assert-True ($ls.Count -eq 0) "LocalService puede acceder a datos privados."
        } elseif ($relative -match '^(runtime|logs|media)(\\|$)') {
            Assert-True ($ls.Count -eq 1 -and ($ls[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -eq [Security.AccessControl.FileSystemRights]::Modify) "Falta escritura operativa."
        } else {
            Assert-True ($ls.Count -eq 1 -and ($ls[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -eq 0) "LocalService puede modificar codigo/configuracion."
        }
    }
    # Una segunda pasada no quita permisos efectivos ni agrega identidades.
    Protect-ApplicationTree -Path $raiz
    Assert-True ([IO.File]::ReadAllText($code).Contains("fixture")) "El administrador perdio lectura."
    [IO.File]::AppendAllText($code, "# write-check")
    Write-Host "OK: ACL efectivas, datos privados, escritura operativa e idempotencia."
}
Write-Host "OK: preflight de maquina/host, orden seguro, firewall, .env y recuperacion de .venv."
Write-Host "Fixture conservado en $raiz (sin secretos ni datos reales)."
