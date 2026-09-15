param([switch]$SkipAcl)

$ErrorActionPreference = "Stop"
$installer = Join-Path (Split-Path -Parent $PSScriptRoot) "instalar-servicio-lan.ps1"
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "Sintaxis PowerShell invalida." }

$scriptsSeparados = @{
    Instalar = Join-Path (Split-Path -Parent $PSScriptRoot) "instalar-servidor.ps1"
    Actualizar = Join-Path (Split-Path -Parent $PSScriptRoot) "actualizar-servidor.ps1"
    Aprovisionar = Join-Path (Split-Path -Parent $PSScriptRoot) "aprovisionar-sucursal.ps1"
    Reparar = Join-Path (Split-Path -Parent $PSScriptRoot) "reparar-permisos-servidor.ps1"
    Diagnostico = Join-Path (Split-Path -Parent $PSScriptRoot) "iniciar-local.ps1"
    Verificador = Join-Path (Split-Path -Parent $PSScriptRoot) "verificar-servicio-lan.ps1"
    Respaldo = Join-Path (Split-Path -Parent $PSScriptRoot) "respaldar-db-sqlite.ps1"
}
$astsSeparados = @{}
foreach ($nombreScript in $scriptsSeparados.Keys) {
    $tokensScript = $null
    $erroresScript = $null
    $astScript = [Management.Automation.Language.Parser]::ParseFile(
        $scriptsSeparados[$nombreScript], [ref]$tokensScript, [ref]$erroresScript
    )
    if ($erroresScript.Count) { throw "Sintaxis PowerShell inválida en $nombreScript." }
    $astsSeparados[$nombreScript] = $astScript
}
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

function Assert-PrivateBackupFileAcl {
    param([string]$Path)

    $acl = Get-Acl -LiteralPath $Path
    $ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
    $rules = @($acl.GetAccessRules(
        $true, $true, [Security.Principal.SecurityIdentifier]
    ))
    Assert-True $acl.AreAccessRulesProtected "El respaldo conserva herencia: $Path"
    Assert-True ($ownerSid -eq "S-1-5-32-544") "El respaldo no pertenece a Administradores: $Path"
    Assert-True ($rules.Count -eq 2) "El respaldo tiene identidades adicionales: $Path"
    Assert-True (@($rules | Where-Object { $_.IsInherited }).Count -eq 0) "El respaldo tiene ACE heredadas: $Path"
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $matches = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and
            $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $_.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl -and
            $_.InheritanceFlags -eq [Security.AccessControl.InheritanceFlags]::None -and
            $_.PropagationFlags -eq [Security.AccessControl.PropagationFlags]::None
        })
        Assert-True ($matches.Count -eq 1) "Falta FullControl exacto de $sid en $Path"
    }
}

$machinePython = Get-MachinePython
Assert-True (Test-MachinePythonPath $machinePython) "No se detecto Python de maquina."
Assert-True (-not (Test-MachinePythonPath (Join-Path $env:USERPROFILE "Python\python.exe"))) "Se acepto Python por usuario."
$windowsPowerShell = Get-WindowsPowerShellPath
Assert-True (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf) "No se resolvió Windows PowerShell desde el directorio del sistema."
Assert-True ($windowsPowerShell -match '(?i)\\WindowsPowerShell\\v1\.0\\powershell\.exe$') "La tarea de respaldo depende del PSHOME del proceso llamador."
Assert-True (Test-AllowedHost "[::1]") "Se rechazó un host IPv6 válido entre corchetes."
Assert-True (-not (Test-AllowedHost "::1")) "Se aceptó un host IPv6 sin el formato válido para DJANGO_ALLOWED_HOSTS."
Assert-True (-not (Test-AllowedHost "localhost:8000")) "Se aceptó un host con puerto incrustado."
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
Assert-ComesBefore 'if ($RepairPermissions)' '$machinePython = Get-MachinePython' "La reparación todavía depende de Python."
Assert-ComesBefore '$machinePython = Get-MachinePython' 'Stop-Service -Name $nombreServicio' "Se detiene el servicio antes de validar Python."
Assert-ComesBefore 'Set-SafePythonProcessEnvironment' '$machinePython = Get-MachinePython' "Se ejecuta Python antes de sanear su entorno."
Assert-ComesBefore '& $python manage.py migrate' 'Protect-ApplicationTree -Path $raiz' "Se endurece antes de migrar."
Assert-ComesBefore 'validar_despliegue.py' 'Protect-ApplicationTree -Path $raiz' "Se endurece antes de las pruebas."
Assert-ComesBefore '& $python -m pip check' '& $python $servicioPython --check-host' "Se comprueba el host antes de las dependencias."
Assert-ComesBefore '& $python $servicioPython --check-host' 'Protect-ApplicationTree -Path $raiz' "Se endurece antes de comprobar el cargador nativo."
Assert-ComesBefore '& $python $servicioPython --check-host' 'Set-DotEnvValue -Path $entorno' "Se configura produccion antes de comprobar el host."
Assert-ComesBefore '$SucursalClave = ConvertTo-SucursalClave' 'Stop-Service -Name $nombreServicio' "Se detiene antes de validar la identidad."
Assert-ComesBefore '$envHashOriginal = (Get-FileHash' 'Stop-Service -Name $nombreServicio' "Se detiene antes de preservar el hash de .env."
Assert-ComesBefore 'Assert-ServiceBelongsToProject -ServiceName $nombreServicio' 'Stop-Service -Name $nombreServicio' "Se detiene un servicio sin validar su pertenencia."
Assert-ComesBefore '$dbEnginePreflight = ConvertFrom-DotEnvDatabaseEngine' 'Stop-Service -Name $nombreServicio' "Se detiene antes de validar DB_ENGINE."
Assert-ComesBefore '$dbEnginePreflight = ConvertFrom-DotEnvDatabaseEngine' '$sqliteConfigurada = Get-DotEnvValue' "Se interpreta SQLite antes de validar DB_ENGINE."
Assert-ComesBefore 'Set-CanonicalProcessEnvironment -Path $entorno' 'from personas.models import Sucursal' "La identidad se consulta con un entorno heredado."
Assert-ComesBefore 'Invoke-SqliteBackup -PythonPath $python' '& $python manage.py migrate' "Se migra antes del respaldo verificable."
Assert-ComesBefore '$respaldoMutex = Enter-BackupMutex' 'Stop-Service -Name $nombreServicio' "Se muta el servicio antes de excluir la tarea de respaldo."

Assert-True ($main.Contains('$accion = if ($Modo -eq "Actualizar") { "update" } else { "install" }')) "La acción del servicio todavía se infiere por existencia."
Assert-True (-not $main.Contains('$PrepareOnly')) "PrepareOnly continúa en el motor."
Assert-True (($main.Split([string[]]@('manage.py cargar_datos_iniciales'), [StringSplitOptions]::None).Count - 1) -eq 1) "La semilla debe tener una sola ruta explícita."
Assert-True ($main.IndexOf('if ($InicializarDatosArboledas)') -lt $main.IndexOf('manage.py cargar_datos_iniciales')) "La semilla no está protegida por su opción explícita."
Assert-True ($main.Contains('(Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash -ne $envHashOriginal')) "Actualizar no verifica la inmutabilidad de .env."
Assert-True ($main.Contains('$Modo -eq "Instalar" -and $usaSqlite')) "El mensaje/tarea de respaldo no distingue instalación."
Assert-True ($main.Contains('$dbEnginePreflight -ne "sqlite"')) "La instalación Windows permite PostgreSQL sin respaldo pg_dump compatible."

$modoParametro = @($ast.ParamBlock.Parameters | Where-Object {
    $_.Name.VariablePath.UserPath -eq "Modo"
})[0]
Assert-True ($modoParametro.Extent.Text.Contains('ValidateSet("Instalar", "Actualizar")')) "Modo no limita los valores permitidos."
Assert-True ($modoParametro.Extent.Text.Contains('ParameterSetName = "Operacion"')) "Modo no pertenece al contrato de operación."
$reparacionParametro = @($ast.ParamBlock.Parameters | Where-Object {
    $_.Name.VariablePath.UserPath -eq "RepairPermissions"
})[0]
Assert-True ($reparacionParametro.Extent.Text.Contains('ParameterSetName = "Reparacion"')) "La reparación no está separada de las operaciones."

$instalarTexto = $astsSeparados["Instalar"].Extent.Text
$actualizarTexto = $astsSeparados["Actualizar"].Extent.Text
$aprovisionarTexto = $astsSeparados["Aprovisionar"].Extent.Text
$repararTexto = $astsSeparados["Reparar"].Extent.Text
$diagnosticoTexto = $astsSeparados["Diagnostico"].Extent.Text
$verificadorTexto = $astsSeparados["Verificador"].Extent.Text
$respaldoTexto = $astsSeparados["Respaldo"].Extent.Text
Assert-True ($instalarTexto.Contains('Modo = "Instalar"')) "El wrapper de instalación no declara su modo."
Assert-True ($instalarTexto.Contains('[Parameter(Mandatory = $true)][string]$AllowedHosts')) "Instalar no exige hosts explícitos."
Assert-True ($instalarTexto.Contains('[ValidateSet("archivo", "tcp")][string]$PrintBackend = "archivo"')) "Instalar no usa impresión segura por defecto."
Assert-True ($instalarTexto.Contains('PrinterCajaHost = $PrinterCajaHost')) "Instalar no transmite la configuración de impresoras."
Assert-True ($main.Contains('manage.py crear_operador_pos --crear-perfil-inicial')) "El instalador omite el perfil POS inicial cuando la base está vacía."
Assert-True ($main.Contains('configurar_modulos_sucursal')) "El instalador no persiste la selección inicial de módulos."
Assert-True ($instalarTexto.Contains('ModulosOpcionales')) "El wrapper no expone la selección de módulos por sucursal."
Assert-True ($actualizarTexto.Contains('-Modo Actualizar')) "El wrapper de actualización no declara su modo."
foreach ($opcionProhibida in @("SucursalClave", "SucursalNombre", "InicializarDatosArboledas", "AllowedHosts", "SecretKey", "PrintBackend", "PrinterCajaHost", "PrinterCocinaHost", "PrinterBarraHost", "PrinterPort")) {
    Assert-True (-not $actualizarTexto.Contains('$' + $opcionProhibida)) "Actualizar expone $opcionProhibida."
}
Assert-True ($aprovisionarTexto.Contains('$managePy, "aprovisionar_sucursal"')) "El wrapper no usa la ruta absoluta del comando de aprovisionamiento."
Assert-True (-not $aprovisionarTexto.Contains('manage.py migrate')) "Aprovisionar ejecuta migraciones."
Assert-True (-not $aprovisionarTexto.Contains('cargar_datos_iniciales')) "Aprovisionar ejecuta la semilla."
Assert-True ($repararTexto.Contains('& $motor -RepairPermissions')) "El wrapper de reparación no está aislado."
Assert-True ($instalarTexto.Contains('$PSBoundParameters.ContainsKey("ListenAddress")')) "Instalar no conserva la omisión explícita de ListenAddress."
Assert-True ($aprovisionarTexto.IndexOf('Copy-Item -LiteralPath $entorno') -lt $aprovisionarTexto.IndexOf('Set-DotEnvValue -Path $entorno')) "Aprovisionar escribe antes de respaldar .env."
Assert-True ($aprovisionarTexto.Contains('Copy-Item -LiteralPath $respaldo -Destination $entorno -Force')) "Aprovisionar no restaura .env al fallar."
Assert-True ($aprovisionarTexto.IndexOf('Set-CanonicalProcessEnvironment -Path $entorno') -lt $aprovisionarTexto.IndexOf('$env:SUCURSAL_CLAVE = $clave')) "Aprovisionar ejecuta Django con variables heredadas."
Assert-True ($aprovisionarTexto.IndexOf('Assert-ServiceBelongsToProject') -lt $aprovisionarTexto.IndexOf('$env:SUCURSAL_CLAVE = $clave')) "Aprovisionar no valida que el servicio pertenezca a esta instalación."
foreach ($operacionProhibida in @('manage.py collectstatic', 'cargar_datos_iniciales', 'createsuperuser', 'pip install -r')) {
    Assert-True (-not $diagnosticoTexto.Contains($operacionProhibida)) "El diagnóstico local muta el despliegue mediante $operacionProhibida."
}
Assert-True ($diagnosticoTexto.Contains('--no-index --report $reportPath')) "El diagnóstico local no distingue un plan pendiente de un lock satisfecho."
Assert-True ($diagnosticoTexto.Contains('manage.py migrate --check --noinput')) "El diagnóstico local no detecta migraciones pendientes sin aplicarlas."
Assert-True ($diagnosticoTexto.Contains('$servicio.Status -ne "Stopped"')) "El diagnóstico local no impide dos servidores simultáneos."
Assert-True ($diagnosticoTexto.Contains('Sucursal.objects.count() == 1')) "El diagnóstico local no valida la identidad de la base."
Assert-True ($diagnosticoTexto.Contains('$env:PRINT_BACKEND = "archivo"')) "El diagnóstico puede conservar un backend TCP de producción."
Assert-True ($diagnosticoTexto.Contains('Global\LosTocayosPOS-Mantenimiento-v1')) "El diagnóstico no comparte el mutex de mantenimiento."
Assert-True ($diagnosticoTexto.IndexOf('$mantenimientoMutex = Enter-MaintenanceMutex') -lt $diagnosticoTexto.IndexOf('& $python -m waitress')) "El diagnóstico abre Waitress antes de bloquear mantenimiento."
Assert-True ($diagnosticoTexto.Contains('$mantenimientoMutex.ReleaseMutex()')) "El diagnóstico no libera el mutex al terminar o interrumpirse."
Assert-True ($verificadorTexto.Contains('$service.PathName')) "El verificador no acredita que el servicio pertenezca a esta raíz."
Assert-True ($verificadorTexto.Contains('DelayedAutoStart')) "El verificador no acredita el inicio automático retardado."
Assert-True ($verificadorTexto.Contains('Ejecuta este verificador desde PowerShell como administrador.')) "El verificador no exige elevación explícita."
Assert-True ($verificadorTexto.Contains('$requiredEnvironmentKeys')) "El verificador no exige un .env completo y no ambiguo."
Assert-True ($verificadorTexto.Contains('$optionalEnvironmentKeys')) "El verificador no rechaza duplicados en la configuración opcional."
Assert-True ($verificadorTexto.Contains('$printBackend =')) "El verificador no valida el backend de impresión."
Assert-True ($verificadorTexto.Contains('PRINT_BACKEND=tcp exige tres hosts')) "El verificador no exige las impresoras concretas del backend TCP."
Assert-True ($verificadorTexto.Contains('$printerPort = ConvertFrom-DotEnvInteger')) "El verificador no valida el puerto de impresora."
Assert-True ($verificadorTexto.Contains('PythonClass')) "El verificador no acredita la clase Python registrada."
Assert-True ($verificadorTexto.Contains('$expectedFailureActions')) "El verificador no acredita la política de recuperación del servicio."
Assert-True ($verificadorTexto.Contains('-ErrorAction SilentlyContinue')) "El verificador exige componentes que pueden omitirse expresamente."
Assert-True ($verificadorTexto.Contains('$dbEngine -eq "sqlite"')) "El verificador presupone SQLite para todos los perfiles."
Assert-True ($verificadorTexto.Contains('$headers["X-Forwarded-Proto"] = "https"')) "El verificador no puede comprobar salud en el perfil HTTPS."
Assert-True ($verificadorTexto.Contains('if (-not (Test-AllowedHost -Value $HealthHost))')) "El verificador no comparte la validación de hosts, incluido IPv6."
Assert-True ($verificadorTexto.Contains('Test-SamePath $action.Execute $expectedPowerShell')) "El verificador no acredita la acción de la tarea de respaldo."
Assert-True ($verificadorTexto.Contains('$task.Settings.StartWhenAvailable')) "El verificador no acredita los ajustes de la tarea de respaldo."
Assert-True ($verificadorTexto.Contains('MSFT_TaskDailyTrigger')) "El verificador no acredita el disparador diario."
Assert-True ($verificadorTexto.Contains('logs\sqlite-backup.log')) "El verificador espera un log distinto al que registra el instalador."
Assert-True ($verificadorTexto.Contains('if ($https)')) "El verificador no distingue una regla residual en HTTPS."
Assert-True ($verificadorTexto.Contains('elseif ($listenAddress -eq "::")')) "El verificador sondea una escucha IPv6 mediante IPv4."
Assert-True ($verificadorTexto.Contains('$basePythonLines = @(& $python -I -c')) "El verificador no resuelve el CPython base de la venv."
Assert-True ($verificadorTexto.Contains('(Test-SamePath $redirector.ExecutablePath $python)')) "El verificador acepta un redirector ajeno a la venv."
Assert-True ($verificadorTexto.Contains('$redirector.ParentProcessId -eq [uint32]$service.ProcessId')) "El verificador no vincula el redirector al servicio."
Assert-True ($verificadorTexto.Contains('"pos.wsgi:application"')) "El verificador no acredita el comando Waitress esperado."
Assert-True ($verificadorTexto.Contains('$acl.AreAccessRulesProtected')) "El verificador no comprueba el aislamiento de ACL."
Assert-True ($verificadorTexto.Contains('propietario distinto de Administradores')) "El verificador no comprueba el propietario de cada ruta."
Assert-True ($verificadorTexto.Contains('$localServiceRights -ne $requiredRead')) "El verificador permite combinar lectura con ACE adicionales de escritura."
Assert-True ($verificadorTexto.Contains('[Security.AccessControl.FileSystemRights]::Synchronize')) "El verificador ignora el bit Synchronize añadido a las ACE positivas."
Assert-True ($verificadorTexto.Contains('$backupCreated = $newBackupFiles[0]')) "RunBackup no acredita el triplete recién creado."
Assert-True ($verificadorTexto.Contains('Compare-Object -ReferenceObject $expectedNames')) "RunBackup no exige sidecars coherentes."
Assert-True ($respaldoTexto.Contains('$acl.SetAccessRuleProtection($true, $false)')) "El respaldo no elimina herencia de sus ACL."
Assert-True ($respaldoTexto.Contains('$acl.SetOwner($adminSid)')) "El respaldo no fija propietario Administradores."
Assert-True ($respaldoTexto.Contains('function Protect-BackupFiles')) "El respaldo no endurece todos los archivos físicos de backups."
Assert-True ($respaldoTexto.Contains('Global\LosTocayosPOS-RespaldoSQLite-v1')) "El respaldo no serializa tarea e instalador."
Assert-True ($ast.Extent.Text.Contains('Global\LosTocayosPOS-RespaldoSQLite-v1')) "El instalador no comparte el mutex del respaldo."
Assert-True (-not $respaldoTexto.Contains('ParentHoldsBackupMutex')) "El wrapper permite omitir públicamente su mutex."
Assert-True ($ast.Extent.Text.Contains('$BackupMutex.ReleaseMutex()')) "El instalador no cede el mutex al hijo que respalda."
Assert-True ($ast.Extent.Text.Contains('Wait-BackupMutex -Mutex $BackupMutex')) "El instalador no recupera el mutex tras el respaldo hijo."
Assert-True ($ast.Extent.Text.Contains('-RetryUntilAcquired')) "El instalador puede continuar o revertir sin recuperar el mutex tras el respaldo hijo."
Assert-True ($ast.Extent.Text.Contains('-WarningAction Continue')) "Una preferencia de avisos heredada puede interrumpir la recuperacion obligatoria del mutex."
Assert-True ($verificadorTexto.Contains('La tarea de respaldo no puede omitir su mutex propio.')) "El verificador permite que la tarea omita el mutex."
Assert-True ($respaldoTexto.Contains('$pythonExitCode = $LASTEXITCODE')) "El respaldo no conserva inmediatamente el código nativo."
Assert-True ($respaldoTexto.IndexOf('$pythonExitCode = $LASTEXITCODE') -lt $respaldoTexto.IndexOf('if ($pythonExitCode -ne 0)')) "El respaldo altera el código nativo antes de evaluarlo."
Assert-True (($respaldoTexto.Split([string[]]@('Protect-BackupFiles -Path'), [StringSplitOptions]::None).Count - 1) -ge 2) "El respaldo no endurece antes y después de publicar."
Assert-True ($respaldoTexto.Contains('$expectedShaSidecar')) "El respaldo no valida el contenido del sidecar SHA-256."
Assert-True ($respaldoTexto.Contains('$metadata.restore_check.integrity_check')) "El respaldo no valida el sidecar JSON de restauración."
Assert-True (-not $diagnosticoTexto.Contains('if (-not $env:PRINT_BACKEND)')) "La impresión de diagnóstico sólo cambia si falta la configuración."
Assert-True ($diagnosticoTexto.IndexOf('Set-CanonicalProcessEnvironment -Path $entorno') -lt $diagnosticoTexto.IndexOf('$env:PRINT_BACKEND = "archivo"')) "El .env TCP puede volver a activar impresoras tras aislar el diagnóstico."
Assert-True ($diagnosticoTexto.Contains('from herramientas.host_servicio_windows import comprobar_host')) "El diagnóstico no comprueba el host de servicio."
Assert-True (-not $diagnosticoTexto.Contains('--check-host')) "El diagnóstico puede modificar el host de servicio."
Assert-True ($main.Contains('$sqliteConfigurada = Get-DotEnvValue')) "Actualizar no resuelve SQLITE_PATH desde .env."
Assert-True ($main.Contains('Set-DotEnvValue -Path $entorno -Name "DB_ENGINE" -Value $dbEnginePreflight')) "Una instalación nueva no hace explícito su motor validado."
Assert-True ($main.Contains('$PrintBackend -eq "tcp" -and -not (Test-AllowedHost -Value $hostImpresora)')) "TCP no exige las tres impresoras explícitas."
Assert-ComesBefore '& $python manage.py verificar_identidad_local' '$migracionIniciada = $true' "Se marca una migración antes de comprobar la identidad."
Assert-ComesBefore '& $python manage.py verificar_identidad_local' '& $python manage.py migrate' "Se migra antes de comprobar la identidad de una base existente."
Assert-True ($ast.Extent.Text.Contains('ALLOW_INSECURE_HTTP_LAN$')) "El saneamiento no limpia ALLOW_INSECURE_HTTP_LAN."
Assert-True (($main.Split([string[]]@('Set-CanonicalProcessEnvironment -Path $entorno'), [StringSplitOptions]::None).Count - 1) -ge 2) "La instalación no recarga SQLITE_PATH después de escribirlo."
Assert-True ($main.Contains('Test-WheelhouseCompleteness')) "No se valida la cobertura offline del lock antes de detener."
Assert-True ($main.IndexOf('$arranqueIntentadoPorScript = $true') -lt $main.IndexOf('Start-Service -Name $nombreServicio')) "No se registra el intento antes de arrancar."
Assert-ComesBefore '$waitressLog = Join-Path $raiz "logs\waitress.log"' 'Protect-ApplicationTree -Path $raiz' "El log de Waitress puede nacer después del endurecimiento inicial."
Assert-True (($main.Split([string[]]@('Protect-Path -Path (Join-Path $raiz $directorioDinamico)'), [StringSplitOptions]::None).Count - 1) -eq 1) "No se normalizan las carpetas dinámicas después de comprobar salud."
Assert-ComesBefore '$saludable = $respuestaSalud.StatusCode -eq 200' 'foreach ($directorioDinamico in @("runtime", "logs", "media"))' "Se normalizan archivos dinámicos antes de acreditar la salud."
Assert-True ($main.Contains('El arranque o la comprobación de salud falló; el servicio quedó detenido.')) "Un arranque sin salud puede quedar activo."
Assert-True ($main.Contains('$envExistiaAntesInstalacion')) "La instalación no conserva el estado previo de .env."
Assert-True ($main.Contains('& sc.exe delete $nombreServicio')) "Una instalación parcial no retira su servicio."
Assert-True ($main.Contains('[IO.File]::WriteAllBytes($entorno, $envBytesAntesInstalacion)')) "Una instalación fallida no restaura .env."
Assert-True ($main.Contains('La base/runtime se conservaron para diagnóstico')) "La recuperación parcial no declara qué estado conserva."
Assert-True ($ast.Extent.Text.Contains('Global\LosTocayosPOS-Mantenimiento-v1')) "El motor no comparte un mutex global de mantenimiento."
Assert-True ($aprovisionarTexto.Contains('Global\LosTocayosPOS-Mantenimiento-v1')) "La adopcion no comparte el mutex del instalador."
Assert-True ($aprovisionarTexto.Contains('Global\LosTocayosPOS-RespaldoSQLite-v1')) "La adopcion no excluye la tarea de respaldo."
Assert-True ($aprovisionarTexto.IndexOf('$respaldoMutex = Enter-BackupMutex') -lt $aprovisionarTexto.IndexOf('Copy-Item -LiteralPath $entorno')) "La adopcion respalda o modifica .env antes de excluir el respaldo SQLite."
Assert-True ($aprovisionarTexto.Contains('$respaldoMutex.ReleaseMutex()')) "La adopcion no libera el mutex de respaldo."
Assert-True ($main.IndexOf('$envReadLock = Open-EnvironmentReadLock') -lt $main.IndexOf('Get-RequiredDotEnvValue -Path $entorno -Name "SUCURSAL_CLAVE"')) "Actualizar interpreta .env antes de bloquearlo."
Assert-ComesBefore 'Assert-EnvironmentUnchanged -Path $entorno' 'Stop-Service -Name $nombreServicio' "Se detiene el servicio sin revalidar .env."
Assert-True (($main.Split([string[]]@('Assert-EnvironmentUnchanged -Path $entorno'), [StringSplitOptions]::None).Count - 1) -ge 3) "No se revalida .env antes de detener, respaldar y migrar."
Assert-True ($main.Contains('$envReadLock.Dispose()')) "No se libera el bloqueo de .env."
Assert-True ($main.Contains('$mantenimientoMutex.ReleaseMutex()')) "No se libera el mutex del motor."
Assert-True ($aprovisionarTexto.Contains('$mantenimientoMutex.ReleaseMutex()')) "No se libera el mutex de adopcion."
Assert-True ($ast.Extent.Text.Contains('[Environment]::SetEnvironmentVariable("PIP_CONFIG_FILE", "NUL", "Process")')) "El instalador permite que pip.ini altere el origen o destino de dependencias."
Assert-True ($ast.Extent.Text.Contains('sys.maxsize > 2**32')) "El instalador no exige Python de 64 bits."
Assert-True ($ast.Extent.Text.Contains('edge-server-v2')) "El instalador no exige la política de manifiesto v2."
Assert-True ($ast.Extent.Text.Contains("'platform', 'python'")) "El instalador no exige un target explícito en el manifiesto."
Assert-True ($ast.Extent.Text.Contains('Test-ReleasePayloadPathAllowed')) "El instalador no aplica la política de secretos/estado al manifiesto."
Assert-True ($main.Contains('Test-RequirementsLockExact -PythonPath $machinePython')) "El lock no se valida antes de modificar el servicio."
Assert-True ($ast.Extent.Text.Contains('"--platform", "win_amd64"')) "pip no resuelve el wheelhouse para el target Windows x64."
Assert-True ($main.IndexOf('Get-NetFirewallRule -DisplayName $nombreFirewall') -lt $main.IndexOf('if (-not $SkipFirewall -and -not $Https)')) "SkipFirewall puede conservar una regla de una instalación anterior."
Assert-True ($main.Contains('[byte[]]$envBytesAntesInstalacion = @()')) "PowerShell 5.1 no puede conservar un .env vacío para rollback."

# En CPython 3.13 para Windows la venv puede usar un redirector; la imagen que
# abre el puerto es sys._base_executable y debe coincidir con Python de máquina.
Assert-True ($ast.Extent.Text.Contains('function Get-VirtualEnvironmentBasePython')) "El instalador no resuelve el CPython base de la venv."
Assert-True ($ast.Extent.Text.Contains('sys._base_executable')) "El firewall no descubre la imagen real del listener."
Assert-True ($main.Contains('$firewallPython = Get-VirtualEnvironmentBasePython -PythonPath $python')) "El firewall no usa la venv validada para resolver su ejecutable."
Assert-True ($main.Contains('$firewallPython.Equals(')) "El CPython del firewall no se compara con Python de máquina."
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
Assert-True ($firewallArguments['Program'] -eq '$firewallPython') "El firewall no apunta al CPython base que abre el puerto."
Assert-True ($firewallArguments['Profile'] -eq 'Private') "El firewall no queda limitado al perfil privado."
Assert-True ($firewallArguments['RemoteAddress'] -eq 'LocalSubnet') "El firewall no queda limitado a la subred local."

$synchronizeRule = New-Object Security.AccessControl.FileSystemAccessRule(
    (New-Object Security.Principal.SecurityIdentifier("S-1-5-19")),
    "ReadAndExecute", "Allow"
)
$synchronizeExpected = (
    [Security.AccessControl.FileSystemRights]::ReadAndExecute -bor
    [Security.AccessControl.FileSystemRights]::Synchronize
)
Assert-True ($synchronizeRule.FileSystemRights -eq $synchronizeExpected) "La plataforma no materializó Synchronize como espera el auditor ACL."

$raiz = Join-Path ([IO.Path]::GetTempPath()) ("tocayos-installer-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $raiz | Out-Null
$lockReal = Join-Path (Split-Path -Parent $PSScriptRoot) "requirements-lock.txt"
Assert-True (Test-RequirementsLockExact -PythonPath $machinePython -RequirementsPath $lockReal) "El lock real no es canónico para Windows CPython 3.13."
$lockInvalido = Join-Path $raiz "requirements-invalido.txt"
Set-Content -LiteralPath $lockInvalido -Encoding ASCII -Value "demo>=1.0`npywin32==311"
Assert-True (-not (Test-RequirementsLockExact -PythonPath $machinePython -RequirementsPath $lockInvalido)) "Se aceptó un rango de versiones en el lock."
Set-Content -LiteralPath $lockInvalido -Encoding ASCII -Value "demo @ file:///C:/fuera/demo.whl`npywin32==311"
Assert-True (-not (Test-RequirementsLockExact -PythonPath $machinePython -RequirementsPath $lockInvalido)) "Se aceptó una URL externa en el lock."
Set-Content -LiteralPath $lockInvalido -Encoding ASCII -Value "demo==1.0"
Assert-True (-not (Test-RequirementsLockExact -PythonPath $machinePython -RequirementsPath $lockInvalido)) "Se aceptó un lock del servicio Windows sin pywin32."
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
$configEnv = Join-Path $raiz "config.env"
Set-DotEnvValue -Path $configEnv -Name "DB_ENGINE" -Value "sqlite"
Set-DotEnvValue -Path $configEnv -Name "SQLITE_PATH" -Value "runtime/correcta.sqlite3"
Set-DotEnvValue -Path $configEnv -Name "ALLOW_INSECURE_HTTP_LAN" -Value "false"
Set-DotEnvValue -Path $configEnv -Name "SUCURSAL_CLAVE" -Value "PRUEBA"
$env:DB_ENGINE = "postgres"
$env:SQLITE_PATH = "otra-base.sqlite3"
$env:ALLOW_INSECURE_HTTP_LAN = "true"
$env:DJANGO_SETTINGS_MODULE = "pos.settings_development"
$env:PYTHONPATH = "C:\ruta-no-confiable"
$env:PYTHONUSERBASE = "C:\perfil-no-confiable"
$env:PIP_TARGET = "C:\destino-no-confiable"
$env:PIP_CONFIG_FILE = "C:\configuracion-pip-no-confiable.ini"
$env:VIRTUAL_ENV = "C:\venv-ajena"
Set-CanonicalProcessEnvironment -Path $configEnv
Assert-True ($env:DB_ENGINE -eq "sqlite") "Se heredó otro motor de base."
Assert-True ($env:SQLITE_PATH -eq "runtime/correcta.sqlite3") "Se heredó otra SQLite."
Assert-True ($env:ALLOW_INSECURE_HTTP_LAN -eq "false") "Se heredó la exposición HTTP."
Assert-True ($env:DJANGO_SETTINGS_MODULE -eq "pos.settings") "Se heredaron settings no productivos."
Assert-True (-not $env:PYTHONPATH) "Se heredo PYTHONPATH."
Assert-True (-not $env:PYTHONUSERBASE) "Se heredo PYTHONUSERBASE."
Assert-True (-not $env:PIP_TARGET) "Se heredo PIP_TARGET."
Assert-True ($env:PIP_CONFIG_FILE -eq "NUL") "pip puede cargar configuración global o de usuario no confiable."
Assert-True (-not $env:VIRTUAL_ENV) "Se heredo otra venv."
Assert-True ($env:PYTHONNOUSERSITE -eq "1") "No se desactivo el user-site de Python."
Assert-True ($env:PIP_NO_INPUT -eq "1") "pip elevado puede usar configuracion interactiva heredada."
$mutexPrueba = Enter-MaintenanceMutex
try {
    Assert-True ($mutexPrueba -is [Threading.Mutex]) "El bloqueo global no devolvio un mutex adquirido."
    $codigoHijo = @'
$mutex = New-Object Threading.Mutex($false, "Global\LosTocayosPOS-Mantenimiento-v1")
$adquirido = $false
try {
    try { $adquirido = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $adquirido = $true }
}
finally {
    if ($adquirido) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
if ($adquirido) { exit 1 }
exit 0
'@
    $codigoCodificado = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($codigoHijo))
    & $windowsPowerShell -NoProfile -NonInteractive -EncodedCommand $codigoCodificado
    Assert-True ($LASTEXITCODE -eq 0) "Otro proceso adquirió el mutex mientras había mantenimiento activo."
}
finally {
    $mutexPrueba.ReleaseMutex()
    $mutexPrueba.Dispose()
}
$lockEnv = Join-Path $raiz "lock.env"
Set-Content -LiteralPath $lockEnv -Value "DB_ENGINE=sqlite" -Encoding ASCII
$readLock = Open-EnvironmentReadLock -Path $lockEnv
$escrituraBloqueada = $false
try {
    [IO.File]::WriteAllText($lockEnv, "DB_ENGINE=postgres")
}
catch [IO.IOException] {
    $escrituraBloqueada = $true
}
finally {
    $readLock.Dispose()
}
Assert-True $escrituraBloqueada "El bloqueo de .env permite cambiar el motor durante una actualizacion."
Assert-True ((ConvertFrom-DotEnvDatabaseEngine "") -eq "sqlite") "Un despliegue histórico sin DB_ENGINE no conserva SQLite."
Assert-True ((ConvertFrom-DotEnvDatabaseEngine " POSTGRES ") -eq "postgres") "No se normalizó DB_ENGINE."
$falloMotor = $false
try { ConvertFrom-DotEnvDatabaseEngine "oracle" | Out-Null } catch { $falloMotor = $true }
Assert-True $falloMotor "Se aceptó un DB_ENGINE desconocido."
Assert-True ((ConvertTo-SucursalClave " norte_2 ") -eq "NORTE_2") "No se normalizó la clave de sucursal."
foreach ($claveInvalida in @("", "-NORTE", "NORTE-", "CON ESPACIOS", ("A" * 31))) {
    $falloClave = $false
    try { ConvertTo-SucursalClave $claveInvalida | Out-Null } catch { $falloClave = $true }
    Assert-True $falloClave "Se aceptó una clave de sucursal inválida."
}
$fakePython = Join-Path $raiz "fake-python.cmd"
$fakeRequirements = Join-Path $raiz "fake-requirements.txt"
Set-Content -LiteralPath $fakeRequirements -Value "demo-tocayos==1.0" -Encoding ASCII
Set-Content -LiteralPath $fakePython -Encoding ASCII -Value @'
@echo off
:scan
if "%~1"=="" exit /b 2
if /I "%~1"=="--report" goto report
shift
goto scan
:report
shift
> "%~1" echo {"version":"1","install":[{"metadata":{"name":"demo-tocayos"}}]}
exit /b 0
'@
Assert-True (-not (Test-LockedDependencies -PythonPath $fakePython -RequirementsPath $fakeRequirements -WheelhousePath (Join-Path $raiz "sin-wheelhouse"))) "pip con exit 0 y un plan pendiente se trató como lock satisfecho."
Set-Content -LiteralPath $fakePython -Encoding ASCII -Value @'
@echo off
if /I "%~1"=="-I" exit /b 1
:scan
if "%~1"=="" exit /b 2
if /I "%~1"=="--report" goto report
shift
goto scan
:report
shift
> "%~1" echo {"version":"1","install":[]}
exit /b 0
'@
Assert-True (-not (Test-LockedDependencies -PythonPath $fakePython -RequirementsPath $fakeRequirements -WheelhousePath (Join-Path $raiz "sin-wheelhouse"))) "Un plan vacío ocultó un conjunto instalado distinto del lock."
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
    Assert-True (Test-LockedDependencies -PythonPath $projectPython -RequirementsPath (Join-Path $projectRoot "requirements-lock.txt") -WheelhousePath (Join-Path $projectRoot "wheelhouse")) "La venv validada del proyecto no coincide exactamente con el lock."
}
$envVacio = Join-Path $raiz "env-vacio"
[IO.File]::WriteAllBytes($envVacio, [byte[]]@())
[byte[]]$bytesVacios = @()
$bytesVacios = [IO.File]::ReadAllBytes($envVacio)
[IO.File]::WriteAllText($envVacio, "temporal")
[IO.File]::WriteAllBytes($envVacio, $bytesVacios)
Assert-True ((Get-Item -LiteralPath $envVacio).Length -eq 0) "No se pudo restaurar byte por byte un .env originalmente vacío."
$rutaServicio = Get-ServiceExecutablePath '"C:\Program Files\Python313\pythonservice.exe" --flag'
Assert-True ($rutaServicio -eq "C:\Program Files\Python313\pythonservice.exe") "No se interpretó PathName entre comillas."

$releaseRoot = Join-Path $raiz "release-valida"
New-Item -ItemType Directory -Path $releaseRoot | Out-Null
$archivosRelease = @(
    ".env.example", "VERSION", "requirements.txt", "requirements-lock.txt",
    "manage.py", "servicio_windows.py", "pos/settings.py",
    "personas/identidad.py",
    "personas/management/commands/aprovisionar_sucursal.py",
    "personas/management/commands/verificar_identidad_local.py",
    "herramientas/validar_despliegue.py", "certs/prod-ca-2021.crt",
    "datos/Listado-Productos.xlsx",
    "instalar-servicio-lan.ps1",
    "instalar-servidor.ps1", "actualizar-servidor.ps1",
    "aprovisionar-sucursal.ps1", "reparar-permisos-servidor.ps1",
    "respaldar-db-sqlite.ps1"
)
foreach ($nombre in $archivosRelease) {
    $contenido = if ($nombre -eq "VERSION") { "0.4.0-dev.1" } else { "fixture-$nombre" }
    $rutaFixture = Join-Path $releaseRoot $nombre
    $carpetaFixture = Split-Path -Parent $rutaFixture
    if (-not (Test-Path -LiteralPath $carpetaFixture)) {
        New-Item -ItemType Directory -Path $carpetaFixture -Force | Out-Null
    }
    Set-Content -LiteralPath $rutaFixture -Value $contenido -NoNewline -Encoding ASCII
}
$entradasRelease = @($archivosRelease | Sort-Object | ForEach-Object {
    $archivo = Get-Item -LiteralPath (Join-Path $releaseRoot $_)
    [PSCustomObject][ordered]@{
        path = $_
        sha256 = (Get-FileHash -LiteralPath $archivo.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        size = $archivo.Length
    }
})
$carpetaManifest = Join-Path $releaseRoot "_release"
New-Item -ItemType Directory -Path $carpetaManifest | Out-Null
$manifest = [ordered]@{
    artifact_name = "LosTocayosPOS-Servidor-0.4.0-dev.1.zip"
    content_policy = "edge-server-v2"
    created_utc = "2026-09-09T16:00:00Z"
    dependency_bundle = "none"
    file_count = $entradasRelease.Count
    files = $entradasRelease
    format_version = 2
    payload_size = ($entradasRelease | Measure-Object -Property size -Sum).Sum
    product = "LosTocayosPOS-Servidor"
    release_version = "0.4.0-dev.1"
    source_commit = ("a" * 40)
    source_date_epoch = 1788969600
    source_dirty = $false
    target = [ordered]@{
        abi = "cp313"
        bits = 64
        implementation = "cp"
        platform = "win_amd64"
        python = "3.13"
    }
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $carpetaManifest "manifest.json") -Encoding UTF8
Assert-True ((Test-ReleaseTreeManifest -Root $releaseRoot) -eq "0.4.0-dev.1") "No se aceptó un manifiesto íntegro."
Assert-True (-not (Test-ReleasePayloadPathAllowed ".env")) "La política de release aceptó .env."
Assert-True (-not (Test-ReleasePayloadPathAllowed "db.sqlite3")) "La política de release aceptó una base local."
Assert-True (-not (Test-ReleasePayloadPathAllowed "runtime/codigo.py")) "La política de release aceptó estado runtime."
Assert-True (Test-ReleasePayloadPathAllowed "datos/Listado-Productos.xlsx") "La política rechazó el catálogo histórico permitido."
$manifest["campo_inesperado"] = "no"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $carpetaManifest "manifest.json") -Encoding UTF8
$falloEsquema = $false
try { Test-ReleaseTreeManifest -Root $releaseRoot | Out-Null } catch { $falloEsquema = $true }
Assert-True $falloEsquema "Se aceptó un manifiesto con campos no reconocidos."
$manifest.Remove("campo_inesperado")
$manifest.target["platform"] = "linux_x86_64"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $carpetaManifest "manifest.json") -Encoding UTF8
$falloTarget = $false
try { Test-ReleaseTreeManifest -Root $releaseRoot | Out-Null } catch { $falloTarget = $true }
Assert-True $falloTarget "Se aceptó una release para otra plataforma."
$manifest.target["platform"] = "win_amd64"
$manifest.source_dirty = $true
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $carpetaManifest "manifest.json") -Encoding UTF8
$falloDirty = $false
try { Test-ReleaseTreeManifest -Root $releaseRoot | Out-Null } catch { $falloDirty = $true }
Assert-True $falloDirty "Se aceptó una release construida desde un árbol sucio o no verificable."
$manifest.source_dirty = $false
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $carpetaManifest "manifest.json") -Encoding UTF8
$obsoleto = Join-Path $releaseRoot "django\__init__.py"
New-Item -ItemType Directory -Path (Split-Path -Parent $obsoleto) | Out-Null
Set-Content -LiteralPath $obsoleto -Value "# no declarado" -Encoding ASCII
$falloObsoleto = $false
try { Test-ReleaseTreeManifest -Root $releaseRoot | Out-Null } catch { $falloObsoleto = $true }
Assert-True $falloObsoleto "Se aceptó código obsoleto que no figura en el manifiesto."
Remove-Item -LiteralPath $obsoleto
[IO.Directory]::Delete((Split-Path -Parent $obsoleto))
$wheelExtra = Join-Path $releaseRoot "wheelhouse"
New-Item -ItemType Directory -Path $wheelExtra | Out-Null
Set-Content -LiteralPath (Join-Path $wheelExtra "extra-1.0-py3-none-any.whl") -Value "extra" -Encoding ASCII
$falloWheelExtra = $false
try { Test-ReleaseTreeManifest -Root $releaseRoot | Out-Null } catch { $falloWheelExtra = $true }
Assert-True $falloWheelExtra "Se aceptó un wheelhouse adicional no declarado."
[IO.Directory]::Delete($wheelExtra, $true)
Set-Content -LiteralPath (Join-Path $releaseRoot "requirements-lock.txt") -Value "alterado" -Encoding ASCII
$falloManifest = $false
try { Test-ReleaseTreeManifest -Root $releaseRoot | Out-Null } catch { $falloManifest = $true }
Assert-True $falloManifest "No se detectó un archivo alterado tras crear el manifiesto."

# El script de respaldo usa exit; debe ejecutarse en un proceso hijo para no
# terminar silenciosamente el instalador.
$scriptRespaldoReal = $scriptRespaldo
$scriptRespaldo = Join-Path $raiz "respaldo-fixture.ps1"
Set-Content -LiteralPath $scriptRespaldo -Encoding ASCII -Value @'
param(
    [string]$Python,
    [string]$DatabasePath,
    [string]$BackupRoot,
    [string]$LogPath,
    [int]$RetentionDays
)
$fixtureMutex = New-Object Threading.Mutex($false, $Python)
$fixtureAcquired = $false
try {
    try {
        $fixtureAcquired = $fixtureMutex.WaitOne([TimeSpan]::FromSeconds(5))
    }
    catch [Threading.AbandonedMutexException] {
        $fixtureAcquired = $true
    }
    if (-not $fixtureAcquired) {
        exit 23
    }
    $fixtureExitCode = if ($DatabasePath -eq "fixture-fail") { 7 } else { 0 }
}
finally {
    if ($fixtureAcquired) {
        $fixtureMutex.ReleaseMutex()
    }
    $fixtureMutex.Dispose()
}
exit $fixtureExitCode
'@
$fixtureBackupMutexName = "Local\LosTocayosPOS-Backup-Fixture-" + [Guid]::NewGuid().ToString("N")
$fixtureBackupMutex = New-Object Threading.Mutex($false, $fixtureBackupMutexName)
[void]$fixtureBackupMutex.WaitOne()
$continuoTrasRespaldo = $false
Invoke-SqliteBackup -PythonPath $fixtureBackupMutexName -DatabasePath "fixture-ok" -BackupRoot "fixture-backups" -LogPath "fixture.log" -RetentionDays 1 -BackupMutex $fixtureBackupMutex
$continuoTrasRespaldo = $true
Assert-True $continuoTrasRespaldo "Invoke-SqliteBackup terminó el proceso padre."
$falloRespaldoHijo = $false
try {
    Invoke-SqliteBackup -PythonPath $fixtureBackupMutexName -DatabasePath "fixture-fail" -BackupRoot "fixture-backups" -LogPath "fixture.log" -RetentionDays 1 -BackupMutex $fixtureBackupMutex
}
catch {
    $falloRespaldoHijo = $true
}
Assert-True $falloRespaldoHijo "No se propagó el fallo del proceso de respaldo."
$fixtureBackupMutex.ReleaseMutex()
$fixtureBackupMutex.Dispose()
$scriptRespaldo = $scriptRespaldoReal

$wheelhouseVacio = Join-Path $raiz "wheelhouse-vacio"
New-Item -ItemType Directory -Path $wheelhouseVacio | Out-Null
$lockVacio = Join-Path $raiz "lock-vacio.txt"
Set-Content -LiteralPath $lockVacio -Value "# sin dependencias" -Encoding ASCII
Assert-True (Test-WheelhouseCompleteness -PythonPath $machinePython -RequirementsPath $lockVacio -WheelhousePath $wheelhouseVacio) "Se rechazó un lock vacío válido."
Set-Content -LiteralPath $lockVacio -Value "paquete-inexistente-tocayos==987.654.321" -Encoding ASCII
Assert-True (-not (Test-WheelhouseCompleteness -PythonPath $machinePython -RequirementsPath $lockVacio -WheelhousePath $wheelhouseVacio)) "Se aceptó un wheelhouse que no cubre el lock."

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

    # Ejecuta el wrapper real en otra raiz: repara archivos previos y temporales,
    # publica un triplete, valida sidecars y conserva el codigo de error nativo.
    $backupFixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ("tocayos-backup-wrapper-test-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $backupFixtureRoot | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $backupFixtureRoot "herramientas") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $backupFixtureRoot "runtime") | Out-Null
    Copy-Item -LiteralPath $scriptsSeparados["Respaldo"] -Destination $backupFixtureRoot
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) "herramientas\respaldo_sqlite.py") -Destination (Join-Path $backupFixtureRoot "herramientas")
    $backupDatabase = Join-Path $backupFixtureRoot "runtime\db.sqlite3"
    & $machinePython -I -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('create table prueba(id integer primary key, valor text)'); c.execute('insert into prueba(valor) values (?)',('ok',)); c.commit(); c.close()" $backupDatabase
    Assert-True ($LASTEXITCODE -eq 0) "No se pudo crear la SQLite de prueba del wrapper."
    $backupFixtureDirectory = Join-Path $backupFixtureRoot "backups"
    New-Item -ItemType Directory -Path $backupFixtureDirectory | Out-Null
    $legacyBackup = Join-Path $backupFixtureDirectory "db-20990101-010101.sqlite3"
    Copy-Item -LiteralPath $backupDatabase -Destination $legacyBackup
    [IO.File]::WriteAllText($legacyBackup + ".sha256", "fixture")
    [IO.File]::WriteAllText($legacyBackup + ".json", "{}")
    $orphanFile = Join-Path $backupFixtureDirectory ".db-publicacion-interrumpida.tmp"
    [IO.File]::WriteAllText($orphanFile, "fixture")
    $localService = New-Object Security.Principal.SecurityIdentifier("S-1-5-19")
    foreach ($path in @(
        $legacyBackup,
        ($legacyBackup + ".sha256"),
        ($legacyBackup + ".json"),
        $orphanFile
    )) {
        $acl = Get-Acl -LiteralPath $path
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($localService, "Modify", "Allow")
        [void]$acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $path -AclObject $acl
    }
    $legacyHash = (Get-FileHash -LiteralPath $legacyBackup -Algorithm SHA256).Hash
    $backupWrapper = Join-Path $backupFixtureRoot "respaldar-db-sqlite.ps1"
    $backupFixtureLog = Join-Path $backupFixtureRoot "logs\sqlite-backup.log"
    $wrapperOutput = @(& $windowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $backupWrapper -Python $machinePython -DatabasePath $backupDatabase -BackupRoot $backupFixtureDirectory -LogPath $backupFixtureLog -RetentionDays 30 -LockTimeoutSeconds 30)
    Assert-True ($LASTEXITCODE -eq 0) "El wrapper real de respaldo fallo en la prueba ACL."
    Assert-True ($wrapperOutput.Count -eq 1) "El wrapper no devolvio un unico resultado JSON."
    $wrapperResult = $wrapperOutput[0] | ConvertFrom-Json
    Assert-True ($wrapperResult.status -eq "ok") "El wrapper no acredito estado ok."
    $backupPattern = '^db-\d{8}-\d{6}(?:-\d+)?\.sqlite3(?:\.(?:json|sha256))?$'
    $firstArtifacts = @(Get-ChildItem -LiteralPath $backupFixtureDirectory -File | Where-Object { $_.Name -cmatch $backupPattern })
    Assert-True ($firstArtifacts.Count -eq 6) "No quedaron exactamente el triplete previo y el nuevo."
    foreach ($item in $firstArtifacts) { Assert-PrivateBackupFileAcl -Path $item.FullName }
    Assert-PrivateBackupFileAcl -Path $orphanFile
    Assert-True ((Get-FileHash -LiteralPath $legacyBackup -Algorithm SHA256).Hash -eq $legacyHash) "El wrapper modifico el respaldo previo al reparar ACL."

    & $windowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $backupWrapper -Python $machinePython -DatabasePath $backupDatabase -BackupRoot $backupFixtureDirectory -LogPath $backupFixtureLog -RetentionDays 30 -LockTimeoutSeconds 30 | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) "La segunda ejecucion del wrapper no fue idempotente."
    $secondArtifacts = @(Get-ChildItem -LiteralPath $backupFixtureDirectory -File | Where-Object { $_.Name -cmatch $backupPattern })
    Assert-True ($secondArtifacts.Count -eq 9) "La segunda ejecucion no agrego un unico triplete."
    foreach ($item in $secondArtifacts) { Assert-PrivateBackupFileAcl -Path $item.FullName }

    $failurePrior = Join-Path $backupFixtureDirectory "db-20990101-010102.sqlite3"
    Copy-Item -LiteralPath $backupDatabase -Destination $failurePrior
    $failureAcl = Get-Acl -LiteralPath $failurePrior
    [void]$failureAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($localService, "Modify", "Allow")))
    Set-Acl -LiteralPath $failurePrior -AclObject $failureAcl
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $windowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $backupWrapper -Python $machinePython -DatabasePath (Join-Path $backupFixtureRoot "runtime\inexistente.sqlite3") -BackupRoot $backupFixtureDirectory -LogPath $backupFixtureLog -RetentionDays 30 -LockTimeoutSeconds 30 2>$null
    $failureCode = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    Assert-True ($failureCode -eq 1) "El wrapper no conservo el codigo de error de Python."
    Assert-PrivateBackupFileAcl -Path $failurePrior
    Write-Host "OK: wrapper real, ACL de tripletes/temporales, fallo e idempotencia."

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
