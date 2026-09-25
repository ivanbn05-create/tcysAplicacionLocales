<#
.SYNOPSIS
Motor de instalación y actualización supervisada del servidor POS local.
.DESCRIPTION
Ejecutar con Windows PowerShell 5.1 como administrador y Python de maquina.
El modo se declara expresamente: Instalar crea una instalación y Actualizar conserva
la identidad, secretos y configuración existentes. Trabaja sobre una release ya
colocada en este directorio; no descarga ni actualiza código desde GitHub.
.PARAMETER RepairPermissions
Ejecuta únicamente la reparación de acceso para Administradores y SYSTEM y termina.
.NOTES
La semilla histórica sigue ligada a ARBOLEDAS y sólo puede solicitarse de forma
explícita en una instalación inicial. Nunca forma parte de una actualización.
El cambio atómico entre releases y el rollback de código aún están pendientes.
No compartir .env, contrasenas, bases ni certificados privados.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\tcysAplicacionLocales\instalar-servidor.ps1" -SucursalClave "ARBOLEDAS" -SucursalNombre "Arboledas" -AllowedHosts "localhost,127.0.0.1,192.168.0.30" -ListenAddress "0.0.0.0" -AllowInsecureHttpLan
#>
[CmdletBinding(DefaultParameterSetName = "Operacion")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Operacion")]
    [ValidateSet("Instalar", "Actualizar")]
    [string]$Modo,
    [string]$SucursalClave,
    [string]$SucursalNombre,
    [string]$SucursalId,
    [string]$EnrollmentReceiptPath,
    [string]$CentralApiBaseUrl,
    [string]$CentralApiCaBundle,
    [switch]$InicializarDatosArboledas,
    [string[]]$ModulosOpcionales,
    [string]$AllowedHosts = "localhost,127.0.0.1,192.168.0.30",
    [string]$SecretKey,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(2, 64)][int]$Threads = 8,
    [string]$ListenAddress = "0.0.0.0",
    [string]$TrustedProxy,
    [switch]$Https,
    [switch]$AllowInsecureHttpLan,
    [ValidateSet("archivo", "tcp")][string]$PrintBackend = "archivo",
    [string]$PrinterCajaHost,
    [string]$PrinterCocinaHost,
    [string]$PrinterBarraHost,
    [ValidateRange(1, 65535)][int]$PrinterPort = 9100,
    [string]$VpsConsolidacionUrl = "",
    [string]$VpsConsolidacionToken = "",
    [ValidateRange(1, 60)][int]$VpsConsolidacionTimeout = 10,
    [switch]$SkipFirewall,
    [ValidatePattern("^(?:[01]\d|2[0-3]):[0-5]\d$")][string]$BackupTime = "03:15",
    [ValidateRange(1, 3650)][int]$BackupRetentionDays = 30,
    [switch]$SkipBackupTask,
    [switch]$AllowOnlineDependencies,
    [switch]$AllowUnverifiedDevelopmentTree,
    [Parameter(Mandatory = $true, ParameterSetName = "Reparacion")]
    [switch]$RepairPermissions
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$servicioPython = Join-Path $raiz "servicio_windows.py"
$scriptRespaldo = Join-Path $raiz "respaldar-db-sqlite.ps1"
$entorno = Join-Path $raiz ".env"
$nombreServicio = "LosTocayosPOS"
$nombreFirewall = "Los Tocayos POS - LAN privada"
$nombreTareaRespaldo = "LosTocayosPOS-RespaldoSQLite"
$nombreTareaPurgas = "LosTocayosPOS-PurgasFisicas"

Set-Location -LiteralPath $raiz

$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecuta este instalador desde PowerShell como administrador."
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($linea in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($linea -match $patron) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-RequiredDotEnvValue {
    param([string]$Path, [string]$Name)

    $value = Get-DotEnvValue -Path $Path -Name $Name
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "La actualización requiere $Name en .env. Corrige el aprovisionamiento antes de detener el servicio."
    }
    return $value
}

function ConvertTo-SucursalClave {
    param([string]$Value)

    $clave = ([string]$Value).Trim().ToUpperInvariant()
    if ($clave -notmatch '^[A-Z0-9](?:[A-Z0-9_-]{0,28}[A-Z0-9])?$') {
        throw "SucursalClave debe tener entre 1 y 30 caracteres: letras A-Z, números, guion o guion bajo; debe comenzar y terminar con letra o número."
    }
    return $clave
}

function ConvertTo-SucursalNombre {
    param([string]$Value)

    $nombre = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($nombre) -or $nombre.Length -gt 120 -or $nombre -match '[\x00-\x1f\x7f]') {
        throw "SucursalNombre es obligatorio, admite hasta 120 caracteres y no permite caracteres de control."
    }
    return $nombre
}

function ConvertFrom-DotEnvBoolean {
    param([string]$Value, [string]$Name)

    switch (([string]$Value).Trim().ToLowerInvariant()) {
        "true" { return $true }
        "false" { return $false }
        default { throw "$Name debe ser true o false en .env." }
    }
}

function ConvertFrom-DotEnvInteger {
    param([string]$Value, [string]$Name, [int]$Minimum, [int]$Maximum)

    $number = 0
    if (-not [int]::TryParse(([string]$Value).Trim(), [ref]$number) -or
        $number -lt $Minimum -or $number -gt $Maximum) {
        throw "$Name debe ser un entero entre $Minimum y $Maximum en .env."
    }
    return $number
}

function ConvertFrom-DotEnvDatabaseEngine {
    param([AllowEmptyString()][string]$Value)

    $engine = ([string]$Value).Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($engine)) {
        return "sqlite"
    }
    if ($engine -notin @("sqlite", "postgres")) {
        throw "DB_ENGINE debe ser sqlite o postgres en .env; no se modificará el servicio."
    }
    return $engine
}

function Assert-VpsConsolidationConfiguration {
    param(
        [AllowEmptyString()][string]$Url,
        [AllowEmptyString()][string]$Token
    )

    $urlNormalizada = ([string]$Url).Trim()
    $tokenNormalizado = ([string]$Token).Trim()
    if ([string]::IsNullOrWhiteSpace($urlNormalizada)) {
        if (-not [string]::IsNullOrWhiteSpace($tokenNormalizado)) {
            throw "VPS_CONSOLIDACION_URL y VPS_CONSOLIDACION_TOKEN deben configurarse juntos."
        }
        return
    }
    if ([string]::IsNullOrWhiteSpace($tokenNormalizado)) {
        throw "VPS_CONSOLIDACION_URL y VPS_CONSOLIDACION_TOKEN deben configurarse juntos."
    }

    $uri = $null
    if (-not [Uri]::TryCreate($urlNormalizada, [UriKind]::Absolute, [ref]$uri) -or
        -not $uri.Scheme.Equals([Uri]::UriSchemeHttps, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::IsNullOrWhiteSpace($uri.Host) -or
        $urlNormalizada -match '\s' -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw "VPS_CONSOLIDACION_URL debe ser una URL HTTPS absoluta sin credenciales embebidas."
    }
}

function Set-SafePythonProcessEnvironment {
    foreach ($variable in Get-ChildItem Env:) {
        if ($variable.Name -match '^(?:PYTHON|PIP_)' -or
            $variable.Name -in @("VIRTUAL_ENV", "__PYVENV_LAUNCHER__")) {
            [Environment]::SetEnvironmentVariable($variable.Name, $null, "Process")
        }
    }
    [Environment]::SetEnvironmentVariable("PYTHONNOUSERSITE", "1", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
    # NUL es os.devnull en Windows: impide que pip.ini global o de usuario
    # agregue índices, destinos u opciones ajenos al contrato de la release.
    [Environment]::SetEnvironmentVariable("PIP_CONFIG_FILE", "NUL", "Process")
    [Environment]::SetEnvironmentVariable("PIP_DISABLE_PIP_VERSION_CHECK", "1", "Process")
    [Environment]::SetEnvironmentVariable("PIP_NO_INPUT", "1", "Process")
}

function Set-CanonicalProcessEnvironment {
    param([string]$Path)

    Set-SafePythonProcessEnvironment
    $patronConfiguracion = '^(?:DJANGO_|WAITRESS_|POSTGRES_|POS_|PRINT_|PRINTER_|PEDIDOS_SUCURSALES_|PEDIDOS_API_|CENTRAL_|VPS_CONSOLIDACION_|THERMAL_|ALLOW_INSECURE_HTTP_LAN$|DB_ENGINE$|SQLITE_PATH$|SUCURSAL_CLAVE$)'
    foreach ($variable in Get-ChildItem Env:) {
        if ($variable.Name -match $patronConfiguracion) {
            [Environment]::SetEnvironmentVariable($variable.Name, $null, "Process")
        }
    }
    $vistas = @{}
    foreach ($linea in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($linea -notmatch '^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$') { continue }
        $nombre = $Matches[1]
        $valorBruto = $Matches[2]
        if ($nombre -notmatch $patronConfiguracion -or $vistas.ContainsKey($nombre)) { continue }
        $valor = $valorBruto.Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($nombre, $valor, "Process")
        $vistas[$nombre] = $true
    }
    [Environment]::SetEnvironmentVariable("DJANGO_SETTINGS_MODULE", "pos.settings", "Process")
    [Environment]::SetEnvironmentVariable("DJANGO_ALLOW_INSECURE_DEVELOPMENT", $null, "Process")
    [Environment]::SetEnvironmentVariable("DJANGO_ALLOW_INSECURE_TEST_SETTINGS", $null, "Process")
}

function Get-ServiceExecutablePath {
    param([string]$PathName)

    $valor = ([string]$PathName).Trim()
    if ($valor.StartsWith('"')) {
        if ($valor -notmatch '^"([^"]+)"(?:\s|$)') { throw "PathName del servicio no es válido." }
        return [IO.Path]::GetFullPath($Matches[1])
    }
    $ejecutable = ($valor -split '\s+', 2)[0]
    if ([string]::IsNullOrWhiteSpace($ejecutable)) { throw "PathName del servicio está vacío." }
    return [IO.Path]::GetFullPath($ejecutable)
}

function Assert-ServiceBelongsToProject {
    param([string]$ServiceName)

    $registro = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
    $actual = Get-ServiceExecutablePath -PathName $registro.PathName
    $esperado = [IO.Path]::GetFullPath((Join-Path $raiz ".venv\Scripts\pythonservice.exe"))
    if (-not $actual.Equals($esperado, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El servicio $ServiceName pertenece a otra instalación ($actual). No se modificará desde $raiz."
    }
}

function Test-ReleasePayloadPathAllowed {
    param([string]$RelativePath)

    $normalized = ([string]$RelativePath).Replace('\', '/')
    $parts = @($normalized -split '/')
    if (-not $parts.Count) { return $false }
    $reservedDirectories = @(
        '.cache', '.git', '.hg', '.mypy_cache', '.nox', '.pytest_cache',
        '.ruff_cache', '.svn', '.tox', '.venv', '__pycache__', 'backups',
        'cache', 'data', 'datos', 'desktop', 'htmlcov', 'logs', 'media',
        'node_modules', 'release', 'releases', 'runtime', 'secrets',
        'staticfiles', 'temp', 'tmp'
    )
    for ($index = 0; $index -lt $parts.Count - 1; $index++) {
        $part = $parts[$index].ToLowerInvariant()
        if (($part -in $reservedDirectories -or $part.StartsWith('.venv-')) -and
            $normalized -cne 'datos/Listado-Productos.xlsx') {
            return $false
        }
    }
    $name = $parts[-1].ToLowerInvariant()
    if ($name -eq '.env.example') { return $true }
    if ($name -in @(
        '.coverage', '.env', 'credentials.json', 'id_dsa', 'id_ecdsa',
        'id_ed25519', 'id_rsa', 'secrets.json'
    ) -or $name.StartsWith('.env.')) {
        return $false
    }
    foreach ($suffix in @(
        '.bak', '.cred', '.credentials', '.db', '.jks', '.key', '.log',
        '.p12', '.pem', '.pfx', '.pyc', '.pyo', '.secret', '.sqlite',
        '.sqlite-journal', '.sqlite-shm', '.sqlite-wal', '.sqlite3',
        '.sqlite3-journal', '.sqlite3-shm', '.sqlite3-wal', '.tmp'
    )) {
        if ($name.EndsWith($suffix)) { return $false }
    }
    return $true
}

function Test-OperationalStatePath {
    param([string]$RelativePath)

    $normalized = ([string]$RelativePath).Replace('\', '/')
    $parts = @($normalized -split '/')
    $first = $parts[0].ToLowerInvariant()
    if ($first -in @('.git', '.venv', 'backups', 'logs', 'media', 'runtime', 'staticfiles', 'tmp') -or
        $first.StartsWith('.venv-roto-')) {
        return $true
    }
    $lower = $normalized.ToLowerInvariant()
    return $lower -in @('.env', 'db.sqlite3', 'db.sqlite3-wal', 'db.sqlite3-shm', 'db.sqlite3-journal')
}

function Test-ReleaseTreeManifest {
    param(
        [string]$Root,
        [switch]$AllowDevelopmentTree
    )

    $versionPath = Join-Path $Root "VERSION"
    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
        throw "La release no contiene VERSION."
    }
    $version = ([IO.File]::ReadAllText($versionPath, [Text.Encoding]::ASCII)).Trim()
    if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$' -or $version.Contains("..")) {
        throw "VERSION no tiene un formato válido."
    }

    $manifestPath = Join-Path $Root "_release\manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        if ($AllowDevelopmentTree) {
            Write-Warning "Árbol de desarrollo sin manifiesto: se omite sólo por autorización explícita."
            return $version
        }
        throw "Falta _release\manifest.json. Usa una release verificada o autoriza expresamente el árbol de desarrollo."
    }
    if ((Get-Item -LiteralPath $manifestPath).Length -gt 16MB) {
        throw "El manifiesto de release excede el límite permitido."
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "El manifiesto de release no es JSON válido."
    }
    $expectedProperties = @(
        'artifact_name', 'content_policy', 'created_utc', 'dependency_bundle',
        'file_count', 'files', 'format_version', 'payload_size', 'product',
        'release_version', 'source_commit', 'source_date_epoch', 'source_dirty',
        'target'
    ) | Sort-Object
    $actualProperties = @($manifest.PSObject.Properties.Name | Sort-Object)
    if (($expectedProperties -join "`n") -cne ($actualProperties -join "`n")) {
        throw "El manifiesto no contiene exactamente el esquema esperado."
    }
    if ($manifest.product -ne "LosTocayosPOS-Servidor" -or
        $manifest.format_version -ne 2 -or
        $manifest.content_policy -ne "edge-server-v2" -or
        $manifest.release_version -ne $version -or
        $manifest.artifact_name -cne "LosTocayosPOS-Servidor-$version.zip") {
        throw "El manifiesto no coincide con el producto, formato o VERSION."
    }
    $targetProperties = @($manifest.target.PSObject.Properties.Name | Sort-Object)
    $expectedTargetProperties = @('abi', 'bits', 'implementation', 'platform', 'python') | Sort-Object
    if (($targetProperties -join "`n") -cne ($expectedTargetProperties -join "`n") -or
        $manifest.target.implementation -cne 'cp' -or
        $manifest.target.python -cne '3.13' -or
        $manifest.target.abi -cne 'cp313' -or
        $manifest.target.platform -cne 'win_amd64' -or
        $manifest.target.bits -ne 64) {
        throw "La release no declara el destino Windows CPython 3.13 de 64 bits."
    }
    if ($manifest.source_dirty -isnot [bool] -or $manifest.source_dirty) {
        throw "La release procede de un árbol sucio o no verificable. Sólo se instalan releases construidas desde un checkout Git limpio."
    }
    if ([string]$manifest.source_commit -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' -or
        ($manifest.source_date_epoch -isnot [int] -and $manifest.source_date_epoch -isnot [long]) -or
        [long]$manifest.source_date_epoch -lt 315532800 -or
        [long]$manifest.source_date_epoch -gt 4354819199) {
        throw "El manifiesto no contiene una procedencia Git/fecha válida."
    }
    $expectedCreatedUtc = [DateTimeOffset]::FromUnixTimeSeconds(
        [long]$manifest.source_date_epoch
    ).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ssZ', [Globalization.CultureInfo]::InvariantCulture)
    if ([string]$manifest.created_utc -cne $expectedCreatedUtc) {
        throw "La fecha legible del manifiesto no coincide con SOURCE_DATE_EPOCH."
    }
    $dependencyBundle = [string]$manifest.dependency_bundle
    if ($dependencyBundle -notin @("none", "wheelhouse")) {
        throw "El manifiesto no declara un paquete de dependencias válido."
    }
    $files = @($manifest.files)
    if (($manifest.file_count -isnot [int] -and $manifest.file_count -isnot [long]) -or
        $files.Count -ne [int]$manifest.file_count -or -not $files.Count -or
        ($manifest.payload_size -isnot [int] -and $manifest.payload_size -isnot [long]) -or
        [long]$manifest.payload_size -lt 0) {
        throw "El conteo de archivos del manifiesto no coincide."
    }
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $declaradas = @{}
    [long]$payloadSize = 0
    foreach ($file in $files) {
        $fileProperties = @($file.PSObject.Properties.Name | Sort-Object)
        if (($fileProperties -join "`n") -cne ((@('path', 'sha256', 'size') | Sort-Object) -join "`n") -or
            ($file.size -isnot [int] -and $file.size -isnot [long]) -or [long]$file.size -lt 0 -or
            [long]$file.size -gt 256MB) {
            throw "El manifiesto contiene una entrada de archivo inválida."
        }
        $relative = [string]$file.path
        if ([string]::IsNullOrWhiteSpace($relative) -or
            $relative -match '\\|:|//|(^|/)\.\.?(/|$)|^/|/$|[\x00-\x1f\x7f]') {
            throw "El manifiesto contiene una ruta no segura: $relative."
        }
        foreach ($pathPart in @($relative -split '/')) {
            $baseName = @($pathPart -split '\.', 2)[0].ToLowerInvariant()
            if ($pathPart.EndsWith(' ') -or $pathPart.EndsWith('.') -or
                $baseName -in @('con', 'prn', 'aux', 'nul', 'clock$') -or
                $baseName -match '^(?:com|lpt)[1-9]$') {
                throw "El manifiesto contiene un nombre reservado de Windows: $relative."
            }
        }
        if (-not (Test-ReleasePayloadPathAllowed -RelativePath $relative)) {
            throw "El manifiesto declara una ruta excluida o con estado local: $relative."
        }
        $key = $relative.ToLowerInvariant()
        if ($declaradas.ContainsKey($key)) { throw "El manifiesto contiene rutas duplicadas." }
        $declaradas[$key] = $true
        $target = [IO.Path]::GetFullPath((Join-Path $Root ($relative -replace '/', '\')))
        if (-not $target.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Falta un archivo declarado por la release: $relative."
        }
        Assert-ProjectPath -Path $target
        $item = Get-Item -LiteralPath $target
        if ($item.Length -ne [long]$file.size) {
            throw "El tamaño no coincide para $relative."
        }
        $sha = [string]$file.sha256
        if ($sha -notmatch '^[0-9a-f]{64}$' -or
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) {
            throw "El SHA-256 no coincide para $relative."
        }
        $payloadSize += [long]$file.size
        if ($payloadSize -gt 1GB) { throw "El payload excede el límite permitido." }
    }
    if ($payloadSize -ne [long]$manifest.payload_size) {
        throw "El tamaño total del manifiesto no coincide."
    }
    foreach ($required in @(
        ".env.example", "VERSION", "requirements.txt", "requirements-lock.txt",
        "manage.py", "servicio_windows.py", "pos/settings.py",
    "personas/identidad.py",
    "personas/management/commands/aprovisionar_sucursal.py",
    "personas/management/commands/inicializar_operacion_sucursal.py",
    "personas/management/commands/verificar_identidad_local.py",
        "herramientas/validar_despliegue.py",
        "certs/prod-ca-2021.crt", "datos/Listado-Productos.xlsx",
        "instalar-servicio-lan.ps1",
        "instalar-servidor.ps1", "actualizar-servidor.ps1",
        "aprovisionar-sucursal.ps1", "reparar-permisos-servidor.ps1",
        "respaldar-db-sqlite.ps1"
    )) {
        if (-not $declaradas.ContainsKey($required.ToLowerInvariant())) {
            throw "El manifiesto no contiene el archivo obligatorio $required."
        }
    }
    # Recorre toda la raíz para impedir módulos no declarados capaces de secuestrar
    # imports. Sólo se omiten directorios de estado explícitos; código, scripts y
    # configuraciones deben aparecer en el manifiesto aunque estén en una carpeta
    # que la versión anterior no conocía.
    $directoriosPendientes = New-Object 'System.Collections.Generic.Queue[string]'
    $directoriosPendientes.Enqueue($rootPath)
    while ($directoriosPendientes.Count) {
        $directorioActual = $directoriosPendientes.Dequeue()
        foreach ($entradaReal in Get-ChildItem -LiteralPath $directorioActual -Force) {
            if ($entradaReal.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "La release contiene un enlace no declarado: $($entradaReal.FullName)."
            }
            $relativaReal = $entradaReal.FullName.Substring($rootPath.Length + 1).Replace('\', '/')
            if ($entradaReal.PSIsContainer) {
                if (-not (Test-OperationalStatePath -RelativePath $relativaReal)) {
                    $directoriosPendientes.Enqueue($entradaReal.FullName)
                }
                continue
            }
            if ($relativaReal -ceq '_release/manifest.json' -or
                (Test-OperationalStatePath -RelativePath $relativaReal)) {
                continue
            }
            if (-not $declaradas.ContainsKey($relativaReal.ToLowerInvariant())) {
                throw "El árbol contiene un archivo obsoleto o no declarado: $relativaReal. Extrae la release en staging limpio."
            }
        }
    }
    $wheelsDeclarados = @()
    foreach ($file in $files) {
        $wheelPath = [string]$file.path
        $wheelParts = @($wheelPath -split '/')
        if (-not $wheelParts[0].Equals("wheelhouse", [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        if ($wheelParts[0] -ne "wheelhouse" -or $wheelParts.Count -ne 2 -or
            -not $wheelParts[1].EndsWith(".whl", [StringComparison]::OrdinalIgnoreCase)) {
            throw "El wheelhouse del manifiesto sólo admite wheels en un directorio plano."
        }
        $wheelsDeclarados += $wheelPath
    }
    if (($dependencyBundle -eq "wheelhouse") -ne ($wheelsDeclarados.Count -gt 0)) {
        throw "La declaración de dependencias no coincide con el wheelhouse de la release."
    }
    $wheelhousePath = Join-Path $Root "wheelhouse"
    $wheelsEnDisco = @()
    if (Test-Path -LiteralPath $wheelhousePath) {
        Assert-ProjectPath -Path $wheelhousePath
        if (-not (Test-Path -LiteralPath $wheelhousePath -PathType Container)) {
            throw "wheelhouse debe ser un directorio real."
        }
        $entradasWheelhouse = @(Get-ChildItem -LiteralPath $wheelhousePath -Force)
        if (@($entradasWheelhouse | Where-Object { $_.PSIsContainer }).Count) {
            throw "wheelhouse debe ser plano."
        }
        $wheelsEnDisco = @($entradasWheelhouse | ForEach-Object {
            "wheelhouse/" + $_.Name
        })
    }
    $declaradosOrdenados = @($wheelsDeclarados | Sort-Object)
    $discoOrdenados = @($wheelsEnDisco | Sort-Object)
    if (($declaradosOrdenados -join "\n") -cne ($discoOrdenados -join "\n")) {
        throw "El contenido real de wheelhouse no coincide exactamente con el manifiesto."
    }
    return $version
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)

    if ($Value -match "[\r\n]") {
        throw "$Name contiene un salto de línea no permitido."
    }
    $lineas = if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path -Encoding UTF8) } else { @() }
    $patron = "^\s*" + [Regex]::Escape($Name) + "\s*="
    $encontrado = $false
    $actualizadas = @(foreach ($linea in $lineas) {
        if ($linea -match $patron) {
            if (-not $encontrado) {
                "$Name=$Value"
                $encontrado = $true
            }
        }
        else {
            $linea
        }
    })
    if (-not $encontrado) {
        $actualizadas += "$Name=$Value"
    }
    Set-Content -LiteralPath $Path -Value $actualizadas -Encoding UTF8
}

function New-SecretKey {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Test-AllowedHost {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -ne $Value.Trim() -or
        $Value -match "\s|\*|/|\\|://") {
        return $false
    }
    if ($Value.StartsWith("[") -and $Value.EndsWith("]")) {
        $direccion = $null
        return [Net.IPAddress]::TryParse($Value.Substring(1, $Value.Length - 2), [ref]$direccion) -and
            $direccion.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6
    }
    if ($Value.Contains(":")) {
        return $false
    }
    $ip = $null
    if ([Net.IPAddress]::TryParse($Value, [ref]$ip)) {
        return $true
    }
    return $Value -match "^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
}

function Protect-ApplicationTree {
    param([string]$Path)

    Assert-ProjectPath -Path $Path
    # Protege primero los datos privados; nunca se restablecen a ACL del padre.
    $privados = @(".env", "db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm", "db.sqlite3-journal", "backups", ".git", "tmp")
    $privados += @(Get-ChildItem -LiteralPath $Path -Directory -Force |
        Where-Object Name -Like ".venv-roto-*" | Select-Object -ExpandProperty Name)
    foreach ($nombre in $privados) {
        $acceso = if ($nombre -eq ".env") { "Read" } else { "None" }
        Protect-Path -Path (Join-Path $Path $nombre) -LocalServiceAccess $acceso
    }
    foreach ($nombre in @("runtime", "logs", "media")) {
        Protect-Path -Path (Join-Path $Path $nombre) -LocalServiceAccess "Modify"
    }
    Set-ProductionAcl -Path $Path -LocalServiceAccess "Read"
    foreach ($item in Get-ChildItem -LiteralPath $Path -Force) {
        if ($item.Name -notin ($privados + @("runtime", "logs", "media"))) {
            Protect-Path -Path $item.FullName -LocalServiceAccess "Read"
        }
    }
}

function Assert-ProjectPath {
    param([string]$Path, [switch]$ParentVerified)

    $rootPath = [IO.Path]::GetFullPath($raiz).TrimEnd('\')
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($fullPath -ne $rootPath -and
        -not $fullPath.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "La ruta queda fuera del proyecto: $Path."
    }
    # Rechaza junctions/symlinks, incluso en los padres, antes de modificar ACL.
    $cursor = $fullPath
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "No se modifican enlaces ni junctions: $cursor."
            }
        }
        if ($ParentVerified) { break }
        $cursor = Split-Path -Parent $cursor
    }
}

function Set-ProductionAcl {
    param([string]$Path, [ValidateSet("Read", "Modify", "None")][string]$LocalServiceAccess, [switch]$ParentVerified)

    Assert-ProjectPath -Path $Path -ParentVerified:$ParentVerified
    $item = Get-Item -LiteralPath $Path -Force
    $acl = if ($item.PSIsContainer) {
        New-Object Security.AccessControl.DirectorySecurity
    } else {
        New-Object Security.AccessControl.FileSecurity
    }
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $acl.SetOwner($adminSid)
    $inheritance = if ($item.PSIsContainer) { "ContainerInherit, ObjectInherit" } else { "None" }
    $rights = @{ "S-1-5-18" = "FullControl"; "S-1-5-32-544" = "FullControl" }
    if ($LocalServiceAccess -ne "None") {
        $rights["S-1-5-19"] = if ($LocalServiceAccess -eq "Modify") {
            "Modify"
        } elseif ($item.PSIsContainer -or $item.Name -ne ".env") {
            "ReadAndExecute"
        } else {
            "Read"
        }
    }
    foreach ($sid in $rights.Keys) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        # Los archivos reciben ACE efectivas, nunca marcas de herencia de carpetas.
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, $rights[$sid], $inheritance, "None", "Allow")
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Protect-Path {
    param([string]$Path, [ValidateSet("Read", "Modify", "None")][string]$LocalServiceAccess, [switch]$ParentVerified)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    Set-ProductionAcl -Path $Path -LocalServiceAccess $LocalServiceAccess -ParentVerified:$ParentVerified
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
            Protect-Path -Path $child.FullName -LocalServiceAccess $LocalServiceAccess -ParentVerified
        }
    }
}

function Test-MachinePythonPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) { return $false }
    $fullPath = [IO.Path]::GetFullPath($Path)
    $userRoots = @((Join-Path $env:SystemDrive "Users"), $env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA)
    foreach ($userRoot in $userRoots) {
        if ($userRoot -and $fullPath.StartsWith(
            $userRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { return $false }
    $cursor = $fullPath
    while ($cursor) {
        $acl = Get-Acl -LiteralPath $cursor
        foreach ($rule in @($acl.Access)) {
        if (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
            try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
            catch { $sid = [string]$rule.IdentityReference.Value }
            $write = [Security.AccessControl.FileSystemRights]::Delete -bor
                [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
                [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                [Security.AccessControl.FileSystemRights]::TakeOwnership
            if ($cursor.TrimEnd('\') -ne [IO.Path]::GetPathRoot($cursor).TrimEnd('\')) {
                $write = $write -bor [Security.AccessControl.FileSystemRights]::WriteData -bor
                    [Security.AccessControl.FileSystemRights]::AppendData
            }
            if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
                $sid -notin @('S-1-5-18', 'S-1-5-32-544', 'S-1-3-0') -and
                -not $sid.StartsWith('S-1-5-80-') -and
                (($rule.FileSystemRights -band $write) -ne 0)) { return $false }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $true
}

function Get-VirtualEnvironmentBasePython {
    param([string]$PythonPath)

    $baseLines = @(
        & $PythonPath -I -c "import sys; print(sys._base_executable)" 2>$null
    )
    if ($LASTEXITCODE -ne 0 -or $baseLines.Count -ne 1 -or
        [string]::IsNullOrWhiteSpace([string]$baseLines[0])) {
        throw "No se pudo resolver el CPython base de la .venv."
    }
    $basePython = [IO.Path]::GetFullPath(([string]$baseLines[0]).Trim())
    if (-not (Test-MachinePythonPath -Path $basePython)) {
        throw "La .venv no resuelve un Python de máquina permitido."
    }
    return $basePython
}

function Get-MachinePython {
    # Nunca ejecutar un launcher resuelto por PATH dentro del instalador elevado.
    $candidates = @(Get-ChildItem 'HKLM:\SOFTWARE\Python\PythonCore\*\InstallPath' -ErrorAction SilentlyContinue |
        ForEach-Object { $_.GetValue('ExecutablePath') })
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-MachinePythonPath -Path $candidate)) { continue }
        try {
            $base = & $candidate -I -c "import sys; from pathlib import Path; assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32; print(Path(sys.base_prefix) / 'python.exe')" 2>$null
            if ($LASTEXITCODE -eq 0 -and (Test-MachinePythonPath -Path ([string]$base))) {
                return $candidate
            }
        } catch { continue }
    }
    throw "No hay un Python 3.13 de 64 bits de maquina ejecutable. Instala Python 3.13 de 64 bits para todos los usuarios y verifica el registro HKLM. No se han cambiado ACL ni detenido el servicio."
}

function Restore-AdministrativeAccess {
    param([string]$Path = $raiz, [switch]$ParentVerified)

    Assert-ProjectPath -Path $Path -ParentVerified:$ParentVerified
    $item = Get-Item -LiteralPath $Path -Force
    $acl = Get-Acl -LiteralPath $Path
    $inheritance = if ($item.PSIsContainer) { "ContainerInherit, ObjectInherit" } else { "None" }
    # Conserva las demas identidades. La reparacion nunca concede acceso al servicio.
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, "FullControl", $inheritance, "None", "Allow")
        $acl.SetAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
            Restore-AdministrativeAccess -Path $child.FullName -ParentVerified
        }
    }
}

function Test-VirtualEnvironment {
    param([string]$Path)

    try {
        if (-not (Test-Path -LiteralPath (Join-Path $Path 'pyvenv.cfg') -PathType Leaf)) { return $false }
        $executable = Join-Path $Path 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { return $false }
        $base = & $executable -I -c "import sys, pip; from pathlib import Path; assert sys.prefix != sys.base_prefix; assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32; print(Path(sys.base_prefix) / 'python.exe')" 2>$null
        return $LASTEXITCODE -eq 0 -and (Test-MachinePythonPath -Path ([string]$base))
    } catch { return $false }
}

function Initialize-VirtualEnvironment {
    param([string]$MachinePython)

    $venvPath = Join-Path $raiz '.venv'
    Assert-ProjectPath -Path $venvPath
    if ((Test-Path -LiteralPath $venvPath) -and -not (Test-VirtualEnvironment -Path $venvPath)) {
        $savedPath = Join-Path $raiz ('.venv-roto-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
        Assert-ProjectPath -Path $savedPath
        try {
            Move-Item -LiteralPath $venvPath -Destination $savedPath
        } catch {
            throw "La .venv esta rota o no es accesible. No se borro nada. Desde PowerShell Administrador revisa sus ACL y renombra exactamente $venvPath antes de repetir."
        }
        Write-Host "Entorno anterior conservado en $savedPath" -ForegroundColor Yellow
    }
    if (-not (Test-Path -LiteralPath $venvPath)) {
        & $MachinePython -I -m venv $venvPath
        if ($LASTEXITCODE -ne 0) { throw "No fue posible crear .venv; puede repetirse el instalador para recuperarla." }
    }
    if (-not (Test-VirtualEnvironment -Path $venvPath)) {
        throw "La .venv no supero la validacion de Python/pip. No se han endurecido las ACL del proyecto."
    }
}

function Test-RequirementsLockExact {
    param([string]$PythonPath, [string]$RequirementsPath)

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf)) {
        return $false
    }
    $validateLock = @'
import sys
from pathlib import Path
try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name
    from pip._vendor.packaging.version import Version

path = Path(sys.argv[1])
if not path.is_file() or path.stat().st_size > 1024 * 1024:
    raise SystemExit(1)
environment = default_environment()
environment.update({
    'implementation_name': 'cpython', 'implementation_version': '3.13.0',
    'os_name': 'nt', 'platform_machine': 'AMD64',
    'platform_python_implementation': 'CPython', 'platform_release': '',
    'platform_system': 'Windows', 'platform_version': '',
    'python_full_version': '3.13.0', 'python_version': '3.13',
    'sys_platform': 'win32', 'extra': '',
})
seen = set()
active = set()
try:
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    for raw in lines:
        raw = raw.strip()
        if not raw or raw.startswith('#'):
            continue
        requirement = Requirement(raw)
        specifiers = list(requirement.specifier)
        if (requirement.url or requirement.extras or len(specifiers) != 1 or
                specifiers[0].operator != '==' or specifiers[0].version.endswith('.*')):
            raise ValueError('non-canonical lock')
        name = canonicalize_name(requirement.name)
        if name in seen:
            raise ValueError('duplicate')
        seen.add(name)
        Version(specifiers[0].version)
        if requirement.marker is None or requirement.marker.evaluate(environment):
            active.add(name)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if active and 'pywin32' in active else 1)
'@
    try {
        & $PythonPath -I -c $validateLock $RequirementsPath *> $null
        return $LASTEXITCODE -eq 0
    }
    catch { return $false }
}

function Test-LockedDependencies {
    param(
        [string]$PythonPath,
        [string]$RequirementsPath,
        [string]$WheelhousePath
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { return $false }
    $reportPath = Join-Path ([IO.Path]::GetTempPath()) (
        "tocayos-pip-report-" + [Guid]::NewGuid().ToString("N") + ".json"
    )
    $argumentos = @(
        "-m", "pip", "install", "--dry-run", "--disable-pip-version-check",
        "--no-index", "--report", $reportPath
    )
    if (Test-Path -LiteralPath $WheelhousePath -PathType Container) {
        $argumentos += @("--find-links", $WheelhousePath)
    }
    $argumentos += @("-r", $RequirementsPath)
    try {
        & $PythonPath @argumentos *> $null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
            return $false
        }
        $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $report -or $report.PSObject.Properties.Name -notcontains "install") {
            return $false
        }
        # pip devuelve 0 aunque haya calculado un plan de instalación. El lock
        # sólo está satisfecho cuando ese plan existe y está vacío.
        if (@($report.install).Count -ne 0) { return $false }
        $comprobarConjuntoExacto = @'
import sys
from importlib.metadata import distributions
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

required = {}
for raw in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    raw = raw.strip()
    if not raw or raw.startswith('#'):
        continue
    requirement = Requirement(raw)
    if requirement.marker is not None and not requirement.marker.evaluate():
        continue
    specifiers = list(requirement.specifier)
    if requirement.url or len(specifiers) != 1 or specifiers[0].operator != '==':
        raise SystemExit(2)
    name = canonicalize_name(requirement.name)
    if name in required:
        raise SystemExit(3)
    required[name] = specifiers[0].version

installed = {}
for distribution in distributions():
    raw_name = distribution.metadata.get('Name')
    if not raw_name:
        raise SystemExit(4)
    name = canonicalize_name(raw_name)
    if name in installed:
        raise SystemExit(5)
    installed[name] = distribution.version

raise SystemExit(0 if installed == required else 1)
'@
        & $PythonPath -I -c $comprobarConjuntoExacto $RequirementsPath *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
            try { Remove-Item -LiteralPath $reportPath -Force -ErrorAction Stop }
            catch { }
        }
    }
}

function Test-WheelhouseCompleteness {
    param(
        [string]$PythonPath,
        [string]$RequirementsPath,
        [string]$WheelhousePath
    )

    if (-not (Test-Path -LiteralPath $WheelhousePath -PathType Container)) {
        return $false
    }
    $reportPath = Join-Path ([IO.Path]::GetTempPath()) (
        "tocayos-pip-wheelhouse-" + [Guid]::NewGuid().ToString("N") + ".json"
    )
    $argumentos = @(
        "-m", "pip", "install", "--dry-run", "--ignore-installed",
        "--disable-pip-version-check", "--no-index", "--only-binary=:all:",
        "--find-links", $WheelhousePath,
        "--platform", "win_amd64", "--implementation", "cp",
        "--python-version", "3.13", "--abi", "cp313",
        "--report", $reportPath, "-r", $RequirementsPath
    )
    try {
        & $PythonPath @argumentos *> $null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
            return $false
        }
        $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $report -or $report.PSObject.Properties.Name -notcontains "install") {
            return $false
        }
        $wheelhouseRoot = [IO.Path]::GetFullPath($WheelhousePath).TrimEnd('\')
        $selected = @(
            foreach ($installation in @($report.install)) {
                $url = [string]$installation.download_info.url
                $uri = $null
                if (-not [Uri]::TryCreate($url, [UriKind]::Absolute, [ref]$uri) -or
                    -not $uri.IsFile -or $uri.Query -or $uri.Fragment -or
                    ($uri.Host -and -not $uri.Host.Equals('localhost', [StringComparison]::OrdinalIgnoreCase)) -or
                    -not $uri.LocalPath.EndsWith(".whl", [StringComparison]::OrdinalIgnoreCase)) {
                    return $false
                }
                $localPath = [IO.Path]::GetFullPath($uri.LocalPath)
                if (-not (Test-Path -LiteralPath $localPath -PathType Leaf) -or
                    -not [IO.Path]::GetDirectoryName($localPath).Equals(
                        $wheelhouseRoot, [StringComparison]::OrdinalIgnoreCase
                    )) {
                    return $false
                }
                $localPath.ToLowerInvariant()
            }
        )
        $delivered = @()
        foreach ($entry in Get-ChildItem -LiteralPath $WheelhousePath -Force) {
            if ($entry.PSIsContainer -or
                -not $entry.Name.EndsWith(".whl", [StringComparison]::OrdinalIgnoreCase)) {
                return $false
            }
            $delivered += [IO.Path]::GetFullPath($entry.FullName).ToLowerInvariant()
        }
        $selectedSorted = @($selected | Sort-Object)
        $deliveredSorted = @($delivered | Sort-Object)
        if (@($selectedSorted | Sort-Object -Unique).Count -ne $selectedSorted.Count -or
            @($deliveredSorted | Sort-Object -Unique).Count -ne $deliveredSorted.Count -or
            $selectedSorted.Count -ne $deliveredSorted.Count) {
            return $false
        }
        for ($index = 0; $index -lt $selectedSorted.Count; $index++) {
            if ($selectedSorted[$index] -ne $deliveredSorted[$index]) { return $false }
        }
        return $true
    }
    catch {
        return $false
    }
    finally {
        if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
            try { Remove-Item -LiteralPath $reportPath -Force -ErrorAction Stop }
            catch { }
        }
    }
}

function Quote-TaskArgument {
    param([string]$Value)

    if ($Value -match '"') {
        throw "Las rutas de la tarea programada no pueden contener comillas dobles."
    }
    return '"' + $Value + '"'
}

function Get-WindowsPowerShellPath {
    $systemDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::System)
    if ([string]::IsNullOrWhiteSpace($systemDirectory)) {
        throw "Windows no informó su directorio de sistema."
    }
    $powershell = Join-Path $systemDirectory "WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
        throw "No se encontró Windows PowerShell en la ruta del sistema: $powershell"
    }
    return (Resolve-Path -LiteralPath $powershell).Path
}

function Test-ScheduledTaskPathEquals {
    param([string]$Actual, [string]$Expected)

    try {
        return [IO.Path]::GetFullPath($Actual).Equals(
            [IO.Path]::GetFullPath($Expected),
            [StringComparison]::OrdinalIgnoreCase
        )
    }
    catch {
        return $false
    }
}

function Get-SqliteBackupTaskArguments {
    param(
        [string]$ScriptPath,
        [string]$PythonPath,
        [string]$DatabasePath,
        [string]$BackupRoot,
        [string]$LogPath,
        [ValidateRange(1, 3650)][int]$RetentionDays
    )

    return @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Quote-TaskArgument -Value $ScriptPath),
        "-Python",
        (Quote-TaskArgument -Value $PythonPath),
        "-DatabasePath",
        (Quote-TaskArgument -Value $DatabasePath),
        "-BackupRoot",
        (Quote-TaskArgument -Value $BackupRoot),
        "-LogPath",
        (Quote-TaskArgument -Value $LogPath),
        "-RetentionDays",
        $RetentionDays
    ) -join " "
}

function Assert-SqliteScheduledTaskBase {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [string]$Description,
        [string]$ExpectedPowerShell,
        [string]$ExpectedWorkingDirectory,
        [string]$ExpectedArguments
    )

    if ($Task.Principal.UserId -notin @("SYSTEM", "S-1-5-18", "NT AUTHORITY\SYSTEM") -or
        [string]$Task.Principal.RunLevel -ne "Highest") {
        throw "$Description no usa SYSTEM con privilegios máximos."
    }
    if ([string]$Task.State -eq "Disabled" -or
        -not [bool]$Task.Settings.Enabled -or
        -not [bool]$Task.Settings.StartWhenAvailable -or
        [string]$Task.Settings.MultipleInstances -ne "IgnoreNew" -or
        [string]$Task.Settings.ExecutionTimeLimit -ne "PT1H") {
        throw "$Description no conserva sus ajustes de recuperación."
    }
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) {
        throw "$Description no tiene una acción única."
    }
    $action = $actions[0]
    if (-not (Test-ScheduledTaskPathEquals -Actual ([string]$action.Execute) -Expected $ExpectedPowerShell) -or
        -not (Test-ScheduledTaskPathEquals -Actual ([string]$action.WorkingDirectory) -Expected $ExpectedWorkingDirectory)) {
        throw "$Description usa otro ejecutable o directorio de trabajo."
    }
    if (-not ([string]$action.Arguments).Equals(
        $ExpectedArguments,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Description no conserva la acción administrada esperada."
    }
}

function Get-SqliteTaskConfigurationFromTasks {
    param(
        [AllowNull()]$BackupTask,
        [AllowNull()]$PurgeTask,
        [string]$ProjectRoot,
        [string]$PythonPath,
        [string]$DatabasePath
    )

    if ($null -eq $BackupTask) {
        if ($null -ne $PurgeTask) {
            throw "Existe la tarea de purgas físicas sin la tarea SQLite principal."
        }
        return [pscustomobject]@{
            Configured = $false
            DailyTime = $null
            RetentionDays = $null
            PurgeConfigured = $false
        }
    }

    $backupRoot = Join-Path $ProjectRoot "backups"
    $backupLog = Join-Path $ProjectRoot "logs\sqlite-backup.log"
    $backupScript = Join-Path $ProjectRoot "respaldar-db-sqlite.ps1"
    $powershell = Get-WindowsPowerShellPath
    $actions = @($BackupTask.Actions)
    if ($actions.Count -ne 1) {
        throw "La tarea de respaldo no tiene una acción única."
    }
    $retentionMatches = [Regex]::Matches(
        [string]$actions[0].Arguments,
        '(?i)(?:^|\s)-RetentionDays\s+([0-9]+)(?=\s|$)'
    )
    $retentionDays = 0
    if ($retentionMatches.Count -ne 1 -or
        -not [int]::TryParse($retentionMatches[0].Groups[1].Value, [ref]$retentionDays) -or
        $retentionDays -lt 1 -or $retentionDays -gt 3650) {
        throw "La retención de la tarea de respaldo no es válida."
    }
    $expectedArguments = Get-SqliteBackupTaskArguments `
        -ScriptPath $backupScript `
        -PythonPath $PythonPath `
        -DatabasePath $DatabasePath `
        -BackupRoot $backupRoot `
        -LogPath $backupLog `
        -RetentionDays $retentionDays
    Assert-SqliteScheduledTaskBase `
        -Task $BackupTask `
        -Description "La tarea de respaldo" `
        -ExpectedPowerShell $powershell `
        -ExpectedWorkingDirectory $ProjectRoot `
        -ExpectedArguments $expectedArguments

    $triggers = @($BackupTask.Triggers)
    if ($triggers.Count -ne 1 -or
        [string]$triggers[0].CimClass.CimClassName -ne "MSFT_TaskDailyTrigger" -or
        -not [bool]$triggers[0].Enabled -or
        [int]$triggers[0].DaysInterval -ne 1) {
        throw "La tarea de respaldo no contiene un único disparador diario habilitado."
    }
    $startBoundary = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        [string]$triggers[0].StartBoundary,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AllowWhiteSpaces,
        [ref]$startBoundary
    )) {
        throw "La hora diaria de la tarea de respaldo no es válida."
    }
    $dailyTime = $startBoundary.ToString(
        "HH:mm",
        [Globalization.CultureInfo]::InvariantCulture
    )

    if ($null -ne $PurgeTask) {
        Assert-SqliteScheduledTaskBase `
            -Task $PurgeTask `
            -Description "La tarea de purgas físicas" `
            -ExpectedPowerShell $powershell `
            -ExpectedWorkingDirectory $ProjectRoot `
            -ExpectedArguments ($expectedArguments + " -OnlyIfPurgePending")
        $purgeTriggers = @($PurgeTask.Triggers)
        if ($purgeTriggers.Count -ne 1 -or
            [string]$purgeTriggers[0].CimClass.CimClassName -ne "MSFT_TaskTimeTrigger" -or
            -not [bool]$purgeTriggers[0].Enabled -or
            [string]$purgeTriggers[0].Repetition.Interval -ne "PT5M") {
            throw "La tarea de purgas físicas no conserva su intervalo de cinco minutos."
        }
    }

    return [pscustomobject]@{
        Configured = $true
        DailyTime = $dailyTime
        RetentionDays = $retentionDays
        PurgeConfigured = $null -ne $PurgeTask
    }
}

function Get-ExistingSqliteTaskConfiguration {
    param(
        [string]$ProjectRoot,
        [string]$PythonPath,
        [string]$DatabasePath
    )

    $rootTasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop)
    $backupTasks = @($rootTasks | Where-Object {
        $_.TaskName -ieq $nombreTareaRespaldo
    })
    $purgeTasks = @($rootTasks | Where-Object {
        $_.TaskName -ieq $nombreTareaPurgas
    })
    if ($backupTasks.Count -gt 1 -or $purgeTasks.Count -gt 1) {
        throw "Existe más de una tarea administrada con el mismo nombre."
    }
    return Get-SqliteTaskConfigurationFromTasks `
        -BackupTask $(if ($backupTasks.Count) { $backupTasks[0] } else { $null }) `
        -PurgeTask $(if ($purgeTasks.Count) { $purgeTasks[0] } else { $null }) `
        -ProjectRoot $ProjectRoot `
        -PythonPath $PythonPath `
        -DatabasePath $DatabasePath
}

function Register-SqliteBackupTask {
    param(
        [string]$PythonPath,
        [string]$DatabasePath,
        [string]$BackupRoot,
        [string]$LogPath,
        [string]$DailyTime,
        [int]$RetentionDays
    )

    if (-not (Test-Path -LiteralPath $scriptRespaldo)) {
        throw "No se encontro el script de respaldo $scriptRespaldo."
    }
    $hora = [TimeSpan]::ParseExact($DailyTime, "hh\:mm", [Globalization.CultureInfo]::InvariantCulture)
    $powershell = Get-WindowsPowerShellPath
    $argumentos = Get-SqliteBackupTaskArguments `
        -ScriptPath $scriptRespaldo `
        -PythonPath $PythonPath `
        -DatabasePath $DatabasePath `
        -BackupRoot $BackupRoot `
        -LogPath $LogPath `
        -RetentionDays $RetentionDays
    $accion = New-ScheduledTaskAction `
        -Execute $powershell `
        -Argument $argumentos `
        -WorkingDirectory $raiz
    $disparador = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.Add($hora))
    $principalTarea = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
    $configuracionTarea = New-ScheduledTaskSettingsSet `
        -Compatibility Win8 `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName $nombreTareaRespaldo `
        -Action $accion `
        -Trigger $disparador `
        -Principal $principalTarea `
        -Settings $configuracionTarea `
        -Description "Respaldo consistente y verificable de runtime\db.sqlite3 de Los Tocayos POS." `
        -Force | Out-Null

    $accionPurgas = New-ScheduledTaskAction `
        -Execute $powershell `
        -Argument ($argumentos + " -OnlyIfPurgePending") `
        -WorkingDirectory $raiz
    $disparadorPurgas = New-ScheduledTaskTrigger `
        -Once `
        -At ((Get-Date).AddMinutes(1)) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    Register-ScheduledTask `
        -TaskName $nombreTareaPurgas `
        -Action $accionPurgas `
        -Trigger $disparadorPurgas `
        -Principal $principalTarea `
        -Settings $configuracionTarea `
        -Description "Procesa cada cinco minutos las purgas físicas confirmadas; no crea respaldos sin solicitudes." `
        -Force | Out-Null
}

function Remove-SqliteBackupTask {
    foreach ($nombreTarea in @($nombreTareaRespaldo, $nombreTareaPurgas)) {
        $tarea = Get-ScheduledTask -TaskName $nombreTarea -ErrorAction SilentlyContinue
        if ($tarea) {
            Unregister-ScheduledTask -TaskName $nombreTarea -Confirm:$false
        }
    }
}

function Invoke-SqliteBackup {
    param(
        [string]$PythonPath,
        [string]$DatabasePath,
        [string]$BackupRoot,
        [string]$LogPath,
        [int]$RetentionDays,
        [Parameter(Mandatory = $true)][Threading.Mutex]$BackupMutex,
        [ValidateRange(1, 3600)][int]$LockTimeoutSeconds = 600
    )

    if (-not (Test-Path -LiteralPath $scriptRespaldo)) {
        throw "No se encontro el script de respaldo $scriptRespaldo."
    }
    $powershell = Get-WindowsPowerShellPath
    $backupExitCode = $null
    $released = $false
    try {
        # El hijo debe ser propietario del mutex mientras lee la venv y publica.
        # El padre no muta nada hasta que el hijo termine y recupere el bloqueo.
        $BackupMutex.ReleaseMutex()
        $released = $true
        & $powershell `
            -NoProfile `
            -NonInteractive `
            -ExecutionPolicy Bypass `
            -File $scriptRespaldo `
            -Python $PythonPath `
            -DatabasePath $DatabasePath `
            -BackupRoot $BackupRoot `
            -LogPath $LogPath `
            -RetentionDays $RetentionDays
        $backupExitCode = $LASTEXITCODE
    }
    finally {
        if ($released) {
            # Una vez iniciada la mutacion, ni la ruta normal ni el rollback
            # pueden continuar sin recuperar la exclusion del respaldo.
            Wait-BackupMutex -Mutex $BackupMutex -TimeoutSeconds 30 -RetryUntilAcquired
        }
    }
    if ($backupExitCode -ne 0) {
        throw "Falló el respaldo verificable de la base SQLite configurada."
    }
}

function Enter-MaintenanceMutex {
    $nombre = "Global\LosTocayosPOS-Mantenimiento-v1"
    try {
        $mutex = New-Object Threading.Mutex($false, $nombre)
    }
    catch {
        throw "No se pudo abrir el bloqueo global de mantenimiento."
    }
    $adquirido = $false
    try {
        try {
            $adquirido = $mutex.WaitOne(0)
        }
        catch [Threading.AbandonedMutexException] {
            $adquirido = $true
        }
        if (-not $adquirido) {
            throw "Ya hay otra instalacion, actualizacion, reparacion, adopcion o sesion de diagnostico en curso."
        }
        return $mutex
    }
    catch {
        if (-not $adquirido) { $mutex.Dispose() }
        throw
    }
}

function Wait-BackupMutex {
    param(
        [Parameter(Mandatory = $true)][Threading.Mutex]$Mutex,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600,
        [switch]$RetryUntilAcquired
    )

    do {
        $acquired = $false
        try {
            $acquired = $Mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
        }
        catch [Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if ($acquired) {
            return
        }
        if (-not $RetryUntilAcquired) {
            throw "Hay un respaldo SQLite en curso despues de $TimeoutSeconds segundos."
        }
        Write-Warning "El respaldo hijo termino, pero otro respaldo conserva el mutex; se esperara antes de continuar o revertir." -WarningAction Continue
    } while ($true)
}

function Enter-BackupMutex {
    param([ValidateRange(1, 3600)][int]$TimeoutSeconds = 600)

    if ($PSVersionTable.PSEdition -ne "Desktop") {
        throw "La instalacion protegida debe ejecutarse con Windows PowerShell 5.1."
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
    try {
        Wait-BackupMutex -Mutex $mutex -TimeoutSeconds $TimeoutSeconds
        return $mutex
    }
    catch {
        $mutex.Dispose()
        throw
    }
}

function Open-EnvironmentReadLock {
    param([string]$Path)

    try {
        # FileShare.Read permite que Django y el servicio lean el mismo archivo,
        # pero impide escribirlo, sustituirlo o borrarlo durante la actualizacion.
        return [IO.File]::Open(
            $Path,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
    }
    catch {
        throw "No se pudo bloquear .env para una actualizacion coherente."
    }
}

function Assert-EnvironmentUnchanged {
    param([string]$Path, [string]$ExpectedHash)

    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $ExpectedHash) {
        throw ".env cambio durante la actualizacion; no se continuara."
    }
}

$mantenimientoMutex = $null
$respaldoMutex = $null
$envReadLock = $null
try {
$mantenimientoMutex = Enter-MaintenanceMutex
Assert-ProjectPath -Path $raiz
if ($RepairPermissions) {
    $respaldoMutex = Enter-BackupMutex
    Restore-AdministrativeAccess
    Write-Host "Acceso administrativo reparado. Repite ahora la instalación o actualización sin -RepairPermissions." -ForegroundColor Green
    return
}
Set-SafePythonProcessEnvironment
$machinePython = Get-MachinePython
foreach ($archivo in @("VERSION", "requirements.txt", "requirements-lock.txt", "manage.py", "servicio_windows.py")) {
    $stream = [IO.File]::OpenRead((Join-Path $raiz $archivo))
    $stream.Dispose()
}
$releaseVersion = Test-ReleaseTreeManifest `
    -Root $raiz `
    -AllowDevelopmentTree:$AllowUnverifiedDevelopmentTree
$requirementsLock = Join-Path $raiz "requirements-lock.txt"
$wheelhouse = Join-Path $raiz "wheelhouse"
if (-not (Test-RequirementsLockExact -PythonPath $machinePython -RequirementsPath $requirementsLock)) {
    throw "requirements-lock.txt debe contener sólo pins exactos y activar pywin32 para Windows CPython 3.13."
}
$dependenciasFijadasPresentes = Test-LockedDependencies `
    -PythonPath $python `
    -RequirementsPath $requirementsLock `
    -WheelhousePath $wheelhouse
$wheelhousePresente = Test-Path -LiteralPath $wheelhouse -PathType Container
$pythonResolucionWheelhouse = if (Test-VirtualEnvironment -Path (Join-Path $raiz ".venv")) {
    $python
}
else {
    $machinePython
}
if ($wheelhousePresente -and
    -not (Test-WheelhouseCompleteness `
        -PythonPath $pythonResolucionWheelhouse `
        -RequirementsPath $requirementsLock `
        -WheelhousePath $wheelhouse)) {
    throw "El wheelhouse no contiene un conjunto válido y completo para requirements-lock.txt y este Python."
}
if (-not $dependenciasFijadasPresentes -and
    -not $wheelhousePresente -and
    -not $AllowOnlineDependencies) {
    throw "Faltan dependencias fijadas y no hay wheelhouse local. Entrega una release completa o usa -AllowOnlineDependencies sólo durante desarrollo supervisado."
}

$existente = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
$envHashOriginal = $null
$dbEnginePreflight = $null
$enrollmentReceipt = $null
$releaseVersion = (Get-Content -LiteralPath (Join-Path $raiz "VERSION") -Raw).Trim()
if ($Modo -eq "Instalar" -and $releaseVersion -match '^1[.]' -and
    [string]::IsNullOrWhiteSpace($EnrollmentReceiptPath) -and
    -not $AllowUnverifiedDevelopmentTree) {
    throw "Production 1.0 requiere el instalador universal y un recibo Central."
}
if ($Modo -eq "Instalar" -and -not [string]::IsNullOrWhiteSpace($EnrollmentReceiptPath)) {
    if ($PSBoundParameters.ContainsKey("SucursalClave") -or
        $PSBoundParameters.ContainsKey("SucursalNombre") -or
        $PSBoundParameters.ContainsKey("SucursalId") -or
        $PSBoundParameters.ContainsKey("ModulosOpcionales") -or
        $InicializarDatosArboledas) {
        throw "El enrolamiento asigna identidad y modulos; no se aceptan selecciones manuales."
    }
    if (-not [IO.Path]::IsPathRooted($EnrollmentReceiptPath) -or
        -not (Test-Path -LiteralPath $EnrollmentReceiptPath -PathType Leaf)) {
        throw "El recibo de enrolamiento debe ser un archivo absoluto y existente."
    }
    $receiptItem = Get-Item -LiteralPath $EnrollmentReceiptPath -Force
    if (($receiptItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $receiptItem.Length -gt 65536) {
        throw "El recibo de enrolamiento no es un archivo ordinario acotado."
    }
    $receiptDirAcl = Get-Acl -LiteralPath (Split-Path -Parent $receiptItem.FullName)
    if (-not $receiptDirAcl.AreAccessRulesProtected) {
        throw "El recibo debe ubicarse en una carpeta privada sin herencia ACL."
    }
    foreach ($rule in @($receiptDirAcl.Access)) {
        $sid = $rule.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
        if ($sid -notin @('S-1-5-18', 'S-1-5-32-544')) {
            throw "La carpeta del recibo permite acceso ajeno a SYSTEM/Administradores."
        }
    }
    try {
        $enrollmentReceipt = Get-Content -LiteralPath $receiptItem.FullName -Raw -Encoding UTF8 |
            ConvertFrom-Json
    }
    catch {
        throw "El recibo de enrolamiento no es JSON valido."
    }
    if ($enrollmentReceipt.schema_version -ne 1 -or
        @($enrollmentReceipt.PSObject.Properties.Name | Sort-Object) -join ',' -cne
        'branch,credentials,edge,modules,request_id,schema_version') {
        throw "El recibo no cumple el contrato de enrolamiento v1."
    }
    $branchGuid = [Guid]::Empty
    $edgeGuid = [Guid]::Empty
    $requestGuid = [Guid]::Empty
    if (-not [Guid]::TryParse([string]$enrollmentReceipt.branch.id, [ref]$branchGuid) -or
        -not [Guid]::TryParse([string]$enrollmentReceipt.edge.id, [ref]$edgeGuid) -or
        -not [Guid]::TryParse([string]$enrollmentReceipt.request_id, [ref]$requestGuid) -or
        $branchGuid -eq [Guid]::Empty -or $edgeGuid -eq [Guid]::Empty -or
        $requestGuid -eq [Guid]::Empty -or $branchGuid -eq $edgeGuid) {
        throw "El recibo contiene UUID invalidos o reutilizados."
    }
    $SucursalClave = ConvertTo-SucursalClave -Value $enrollmentReceipt.branch.code
    if ($SucursalClave -notin @('ARBOLEDAS', 'AGUILAS', 'ESTANCIA', 'PLAZA_DEL_SOL', 'SANTA_ANITA')) {
        throw "La sucursal enrolada no esta en Production 1.0."
    }
    $SucursalNombre = ConvertTo-SucursalNombre -Value $enrollmentReceipt.branch.name
    $SucursalId = $branchGuid.ToString()
    $mandatoryModules = @('pos', 'catalogo', 'impresion', 'respaldos',
        'domicilios', 'pedidos_programados', 'reparto')
    $enrolledModules = @($enrollmentReceipt.modules | ForEach-Object { [string]$_ })
    $unknownModules = @($enrolledModules | Where-Object {
        $_ -notin ($mandatoryModules + @('pedidos_sucursales'))
    })
    foreach ($mandatoryModule in $mandatoryModules) {
        if ($mandatoryModule -notin $enrolledModules) {
            throw "El recibo omite un modulo obligatorio."
        }
    }
    if ($unknownModules.Count -gt 0 -or
        @($enrolledModules | Select-Object -Unique).Count -ne $enrolledModules.Count -or
        ('pedidos_sucursales' -in $enrolledModules -and $SucursalClave -ne 'ARBOLEDAS')) {
        throw "El recibo contiene modulos no autorizados."
    }
    $ingestToken = [string]$enrollmentReceipt.credentials.central_ingest_token
    $catalogToken = [string]$enrollmentReceipt.credentials.central_catalog_token
    if ($ingestToken -cnotmatch '^[\x21-\x7e]{32,512}$' -or
        $catalogToken -cnotmatch '^[\x21-\x7e]{32,512}$' -or
        $ingestToken -ceq $catalogToken) {
        throw "El recibo no contiene credenciales independientes validas."
    }
    $centralUri = $null
    if (-not [Uri]::TryCreate($CentralApiBaseUrl, [UriKind]::Absolute, [ref]$centralUri) -or
        $centralUri.Scheme -ne 'https' -or
        [string]::IsNullOrWhiteSpace($centralUri.Host) -or
        $centralUri.UserInfo -or $centralUri.Query -or $centralUri.Fragment -or
        $centralUri.AbsolutePath -ne '/') {
        throw "El enrolamiento requiere un origen Central HTTPS concreto."
    }
    if ($CentralApiCaBundle -and
        -not (Test-Path -LiteralPath $CentralApiCaBundle -PathType Leaf)) {
        throw "La CA configurada para Central no existe."
    }
}
if ($Modo -eq "Instalar") {
    $dbEnginePreflight = ConvertFrom-DotEnvDatabaseEngine (
        Get-DotEnvValue -Path $entorno -Name "DB_ENGINE"
    )
    if ($dbEnginePreflight -ne "sqlite") {
        throw "El instalador Windows actual sólo admite SQLite con respaldo verificable. PostgreSQL queda reservado para Docker/VPS hasta implementar pg_dump y restauración local."
    }
    if ($existente) {
        throw "El servicio $nombreServicio ya existe. Usa actualizar-servidor.ps1; la instalación inicial no modifica una instancia registrada."
    }
    if (-not $PSBoundParameters.ContainsKey("AllowedHosts")) {
        throw "La instalación exige AllowedHosts explícito; no se reutiliza una IP predeterminada de otra sucursal."
    }
    $SucursalClave = ConvertTo-SucursalClave -Value $SucursalClave
    $SucursalNombre = ConvertTo-SucursalNombre -Value $SucursalNombre
    if (-not [string]::IsNullOrWhiteSpace($SucursalId)) {
        $sucursalGuid = [Guid]::Empty
        if (-not [Guid]::TryParse($SucursalId.Trim(), [ref]$sucursalGuid) -or
            $sucursalGuid -eq [Guid]::Empty) {
            throw "SucursalId debe ser un UUID válido y distinto del UUID vacío."
        }
        $SucursalId = $sucursalGuid.ToString()
    }
    $sucursalExistente = Get-DotEnvValue -Path $entorno -Name "SUCURSAL_CLAVE"
    if (-not [string]::IsNullOrWhiteSpace($sucursalExistente) -and
        (ConvertTo-SucursalClave -Value $sucursalExistente) -ne $SucursalClave) {
        throw "La identidad de .env no coincide con SucursalClave. No se puede reutilizar una instalación para otra sucursal."
    }
    if ($InicializarDatosArboledas -and $SucursalClave -ne "ARBOLEDAS") {
        throw "InicializarDatosArboledas sólo es válido para la sucursal ARBOLEDAS."
    }
    $VpsConsolidacionUrl = ([string]$VpsConsolidacionUrl).Trim()
    $VpsConsolidacionToken = ([string]$VpsConsolidacionToken).Trim()
    Assert-VpsConsolidationConfiguration `
        -Url $VpsConsolidacionUrl `
        -Token $VpsConsolidacionToken
    $impresoras = @{
        PRINTER_CAJA_HOST = $PrinterCajaHost
        PRINTER_COCINA_HOST = $PrinterCocinaHost
        PRINTER_BARRA_HOST = $PrinterBarraHost
    }
    foreach ($nombreImpresora in $impresoras.Keys) {
        $hostImpresora = [string]$impresoras[$nombreImpresora]
        if ($PrintBackend -eq "tcp" -and -not (Test-AllowedHost -Value $hostImpresora)) {
            throw "$nombreImpresora es obligatorio y debe ser una IP o nombre concreto cuando PrintBackend=tcp."
        }
        if ($PrintBackend -eq "archivo" -and -not [string]::IsNullOrWhiteSpace($hostImpresora) -and
            -not (Test-AllowedHost -Value $hostImpresora)) {
            throw "$nombreImpresora no es una IP o nombre válido."
        }
    }
}
else {
    if (-not $existente) {
        throw "No existe el servicio $nombreServicio. Usa instalar-servidor.ps1 para una instalación inicial."
    }
    Assert-ServiceBelongsToProject -ServiceName $nombreServicio
    foreach ($parametroProhibido in @(
        "SucursalClave", "SucursalNombre", "SucursalId", "EnrollmentReceiptPath",
        "CentralApiBaseUrl", "CentralApiCaBundle", "InicializarDatosArboledas", "SecretKey",
        "AllowedHosts", "Port", "Threads", "ListenAddress", "TrustedProxy", "Https",
        "AllowInsecureHttpLan", "PrintBackend", "PrinterCajaHost", "PrinterCocinaHost",
        "PrinterBarraHost", "PrinterPort", "VpsConsolidacionUrl", "VpsConsolidacionToken",
        "VpsConsolidacionTimeout", "SkipFirewall", "BackupTime", "BackupRetentionDays",
        "SkipBackupTask"
    )) {
        if ($PSBoundParameters.ContainsKey($parametroProhibido)) {
            throw "El modo Actualizar no acepta $parametroProhibido; identidad y configuración operativa se conservan desde .env."
        }
    }
    if (-not (Test-Path -LiteralPath $entorno -PathType Leaf)) {
        throw "La actualización requiere un archivo .env ya aprovisionado."
    }
    $envReadLock = Open-EnvironmentReadLock -Path $entorno
    $envHashOriginal = (Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash
    $dbEnginePreflight = ConvertFrom-DotEnvDatabaseEngine (
        Get-DotEnvValue -Path $entorno -Name "DB_ENGINE"
    )
    $SucursalClave = ConvertTo-SucursalClave -Value (
        Get-RequiredDotEnvValue -Path $entorno -Name "SUCURSAL_CLAVE"
    )
    $AllowedHosts = Get-RequiredDotEnvValue -Path $entorno -Name "DJANGO_ALLOWED_HOSTS"
    $ListenAddress = Get-RequiredDotEnvValue -Path $entorno -Name "WAITRESS_HOST"
    $Port = ConvertFrom-DotEnvInteger `
        -Value (Get-RequiredDotEnvValue -Path $entorno -Name "WAITRESS_PORT") `
        -Name "WAITRESS_PORT" -Minimum 1 -Maximum 65535
    $Https = ConvertFrom-DotEnvBoolean `
        -Value (Get-RequiredDotEnvValue -Path $entorno -Name "DJANGO_HTTPS") `
        -Name "DJANGO_HTTPS"
    $AllowInsecureHttpLan = ConvertFrom-DotEnvBoolean `
        -Value (Get-RequiredDotEnvValue -Path $entorno -Name "ALLOW_INSECURE_HTTP_LAN") `
        -Name "ALLOW_INSECURE_HTTP_LAN"
    $TrustedProxy = Get-DotEnvValue -Path $entorno -Name "WAITRESS_TRUSTED_PROXY"
    if ($dbEnginePreflight -eq "postgres") {
        throw "La actualización supervisada de PostgreSQL requiere un respaldo pg_dump aún no implementado; no se detendrá el servicio."
    }
    $sqliteConfigurada = Get-DotEnvValue -Path $entorno -Name "SQLITE_PATH"
    if ([string]::IsNullOrWhiteSpace($sqliteConfigurada)) { $sqliteConfigurada = "db.sqlite3" }
    $sqliteDestino = if ([IO.Path]::IsPathRooted($sqliteConfigurada)) {
        [IO.Path]::GetFullPath($sqliteConfigurada)
    }
    else {
        [IO.Path]::GetFullPath((Join-Path $raiz $sqliteConfigurada))
    }
    Assert-ProjectPath -Path $sqliteDestino
    if (-not (Test-Path -LiteralPath $sqliteDestino -PathType Leaf)) {
        throw "La base SQLite configurada no existe: $sqliteDestino. No se detendrá el servicio."
    }
    $taskConfiguration = Get-ExistingSqliteTaskConfiguration `
        -ProjectRoot $raiz `
        -PythonPath $python `
        -DatabasePath $sqliteDestino
    if ($taskConfiguration.Configured) {
        $BackupTime = [string]$taskConfiguration.DailyTime
        $BackupRetentionDays = [int]$taskConfiguration.RetentionDays
    }
}

$hosts = @($AllowedHosts -split ",")
if (-not $hosts.Count -or @($hosts | Where-Object { -not (Test-AllowedHost -Value $_) }).Count) {
    throw "AllowedHosts sólo acepta hosts/IP concretos, sin comodines, espacios, esquemas, rutas ni puertos."
}

if ($Modo -eq "Instalar" -and $Https -and -not $PSBoundParameters.ContainsKey("ListenAddress")) {
    $ListenAddress = "127.0.0.1"
}
$direccionEscucha = $null
if (-not [Net.IPAddress]::TryParse($ListenAddress, [ref]$direccionEscucha)) {
    throw "ListenAddress debe ser una dirección IP local concreta."
}
if ($Modo -eq "Actualizar" -and $Https -and [string]::IsNullOrWhiteSpace($TrustedProxy)) {
    throw "La actualización HTTPS requiere WAITRESS_TRUSTED_PROXY explícito en .env."
}
if ($Modo -eq "Instalar" -and $Https -and [string]::IsNullOrWhiteSpace($TrustedProxy)) {
    $TrustedProxy = if ($direccionEscucha.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6) {
        "::1"
    }
    else {
        "127.0.0.1"
    }
}
if (-not [string]::IsNullOrWhiteSpace($TrustedProxy)) {
    $direccionProxy = $null
    if (-not [Net.IPAddress]::TryParse($TrustedProxy, [ref]$direccionProxy)) {
        throw "TrustedProxy debe ser una dirección IP concreta."
    }
    if (-not [Net.IPAddress]::IsLoopback($direccionProxy)) {
        throw "TrustedProxy debe ser una dirección loopback del mismo servidor."
    }
}
if ($Https -and (-not [Net.IPAddress]::IsLoopback($direccionEscucha) -or
    -not [Net.IPAddress]::IsLoopback($direccionProxy))) {
    throw "Con -Https, Waitress y el proxy confiable deben usar direcciones loopback."
}
if ($Https -and $AllowInsecureHttpLan) {
    throw "-Https y -AllowInsecureHttpLan son opciones mutuamente excluyentes."
}
if (-not $Https -and -not [Net.IPAddress]::IsLoopback($direccionEscucha) -and
    -not $AllowInsecureHttpLan) {
    throw "HTTP LAN no cifra credenciales ni pedidos. Usa -Https o confirma el riesgo con -AllowInsecureHttpLan."
}

# Las comprobaciones de actualización son de sólo lectura y preceden al Stop-Service.
if ($Modo -eq "Actualizar") {
    if (-not (Test-VirtualEnvironment -Path (Join-Path $raiz ".venv"))) {
        throw "La actualización requiere una .venv sana. El servicio continúa sin cambios."
    }
    Set-CanonicalProcessEnvironment -Path $entorno
    & $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from personas.models import Sucursal; qs=Sucursal.objects.filter(clave=os.environ['SUCURSAL_CLAVE'], activa=True); raise SystemExit(0 if qs.count() == 1 and Sucursal.objects.count() == 1 else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "La base no contiene exactamente la identidad activa configurada. Ejecuta el aprovisionamiento controlado antes de actualizar."
    }
}

$envExistiaAntesInstalacion = $Modo -eq "Instalar" -and (Test-Path -LiteralPath $entorno -PathType Leaf)
[byte[]]$envBytesAntesInstalacion = @()
if ($envExistiaAntesInstalacion) {
    # La asignación tipada conserva correctamente un archivo de cero bytes en
    # Windows PowerShell 5.1; una expresión de pipeline lo convertiría en null.
    $envBytesAntesInstalacion = [IO.File]::ReadAllBytes($entorno)
}
$servicioDetenidoPorScript = $false
$migracionIniciada = $false
$runtimeModificado = $false
$arranqueIntentadoPorScript = $false
$retencionRespaldoPreMigracion = if ($Modo -eq "Actualizar") { 3650 } else { $BackupRetentionDays }
$respaldoMutex = Enter-BackupMutex
try {
if ($Modo -eq "Actualizar") {
    Assert-EnvironmentUnchanged -Path $entorno -ExpectedHash $envHashOriginal
}
if ($existente -and $existente.Status -ne "Stopped") {
    Write-Host "Deteniendo la versión anterior después de validar modo, identidad y configuración..." -ForegroundColor Yellow
    Stop-Service -Name $nombreServicio
    $servicioDetenidoPorScript = $true
    $existente.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
}

$runtimeModificado = $true
$venvPath = Join-Path $raiz '.venv'
if (-not $dependenciasFijadasPresentes -and (Test-VirtualEnvironment -Path $venvPath)) {
    # pip instala o actualiza requisitos, pero no elimina distribuciones ajenas al
    # lock. Se conserva el entorno anterior y se parte de una venv limpia para que
    # el chequeo exacto final pueda garantizar ausencia de paquetes residuales.
    $savedVenv = Join-Path $raiz (
        '.venv-roto-dependencias-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' +
        [Guid]::NewGuid().ToString('N').Substring(0, 8)
    )
    Assert-ProjectPath -Path $savedVenv
    try {
        Move-Item -LiteralPath $venvPath -Destination $savedVenv
    }
    catch {
        throw "La .venv no coincide con el lock y no pudo conservarse en $savedVenv. El servicio permanece detenido para revisión."
    }
    Write-Host "Entorno con dependencias distintas conservado en $savedVenv" -ForegroundColor Yellow
}
Initialize-VirtualEnvironment -MachinePython $machinePython
Assert-ProjectPath -Path $python
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "No existe el ejecutable de la .venv que atendera la LAN."
}
$firewallPython = Get-VirtualEnvironmentBasePython -PythonPath $python
if (-not $firewallPython.Equals(
    [IO.Path]::GetFullPath($machinePython), [StringComparison]::OrdinalIgnoreCase
)) {
    throw "La .venv no pertenece al Python de máquina seleccionado."
}
Write-Host "Instalando dependencias de produccion..." -ForegroundColor Yellow
if (-not $dependenciasFijadasPresentes) {
    $argumentosPip = @("-m", "pip", "install")
    if (Test-Path -LiteralPath $wheelhouse -PathType Container) {
        $argumentosPip += @("--no-index", "--find-links", $wheelhouse)
    }
    elseif (-not $AllowOnlineDependencies) {
        throw "No se permite resolver dependencias contra Internet sin autorización explícita."
    }
    $argumentosPip += @("-r", $requirementsLock)
    & $python @argumentosPip
    if ($LASTEXITCODE -ne 0) {
        throw "Falló la instalación de dependencias fijadas; no se endurecieron las ACL."
    }
}
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Las dependencias instaladas no son compatibles." }
if (-not (Test-LockedDependencies -PythonPath $python -RequirementsPath $requirementsLock -WheelhousePath $wheelhouse)) {
    throw "El entorno no coincide con requirements-lock.txt después de instalar."
}
& $python $servicioPython --check-host
if ($LASTEXITCODE -ne 0) { throw "El host del servicio no puede cargar Python y sus DLL. No se han endurecido las ACL." }
& $python (Join-Path $raiz "herramientas\validar_despliegue.py")
if ($LASTEXITCODE -ne 0) { throw "Fallo la validacion aislada; no se modifico .env ni se endurecieron las ACL." }

if ($Modo -eq "Instalar") {
    $claveExistente = Get-DotEnvValue -Path $entorno -Name "DJANGO_SECRET_KEY"
    if ([string]::IsNullOrWhiteSpace($SecretKey)) {
        $SecretKey = if ([string]::IsNullOrWhiteSpace($claveExistente)) { New-SecretKey } else { $claveExistente }
    }
    if ($SecretKey.Length -lt 50 -or
        @($SecretKey.ToCharArray() | Sort-Object -Unique).Count -lt 5 -or
        $SecretKey.ToLowerInvariant().StartsWith("cambiar") -or
        $SecretKey.ToLowerInvariant().StartsWith("solo-desarrollo") -or
        $SecretKey.ToLowerInvariant().StartsWith("clave-exclusiva-de-prueba") -or
        $SecretKey.ToLowerInvariant().StartsWith("django-insecure-")) {
        throw "SecretKey debe tener al menos 50 caracteres aleatorios y no ser un marcador de ejemplo."
    }

    # Un .env nuevo se restringe antes de escribir secretos. Nunca se copia a una release.
    if (-not (Test-Path -LiteralPath $entorno)) {
        New-Item -ItemType File -Path $entorno | Out-Null
    }
    Protect-Path -Path $entorno -LocalServiceAccess "Read"
    Set-DotEnvValue -Path $entorno -Name "DJANGO_SECRET_KEY" -Value $SecretKey
    Set-DotEnvValue -Path $entorno -Name "DJANGO_DEBUG" -Value "false"
    Set-DotEnvValue -Path $entorno -Name "DJANGO_ALLOWED_HOSTS" -Value $AllowedHosts
    Set-DotEnvValue -Path $entorno -Name "DJANGO_HTTPS" -Value $(if ($Https) { "true" } else { "false" })
    Set-DotEnvValue -Path $entorno -Name "ALLOW_INSECURE_HTTP_LAN" -Value $(if ($AllowInsecureHttpLan) { "true" } else { "false" })
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_HOST" -Value $ListenAddress
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_PORT" -Value $Port
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_THREADS" -Value $Threads
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_CONNECTION_LIMIT" -Value "100"
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_CHANNEL_TIMEOUT" -Value "30"
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_CLEANUP_INTERVAL" -Value "10"
    Set-DotEnvValue -Path $entorno -Name "WAITRESS_TRUSTED_PROXY" -Value $TrustedProxy
    Set-DotEnvValue -Path $entorno -Name "DB_ENGINE" -Value $dbEnginePreflight
    Set-DotEnvValue -Path $entorno -Name "SUCURSAL_CLAVE" -Value $SucursalClave
    if ($enrollmentReceipt) {
        Set-DotEnvValue -Path $entorno -Name "SUCURSAL_ID" -Value $SucursalId
        Set-DotEnvValue -Path $entorno -Name "SUCURSAL_NOMBRE" -Value $SucursalNombre
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_API_BASE_URL" -Value $CentralApiBaseUrl
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_API_CA_BUNDLE" -Value $CentralApiCaBundle
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_BRANCH_ID" -Value $SucursalId
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_BRANCH_CODE" -Value $SucursalClave
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_POS_INSTANCE_ID" -Value $edgeGuid.ToString()
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_ENROLLMENT_REQUEST_ID" -Value $requestGuid.ToString()
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_EDGE_LABEL" -Value $enrollmentReceipt.edge.label
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_INGEST_CREDENTIAL_ID" -Value $enrollmentReceipt.credentials.ingest_credential_id
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_CATALOG_CREDENTIAL_ID" -Value $enrollmentReceipt.credentials.catalog_credential_id
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_INGEST_TOKEN" -Value $ingestToken
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_CATALOG_TOKEN" -Value $catalogToken
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_ENABLE_SALES_V2" -Value "false"
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_ENABLE_CUSTOMERS_V2" -Value "false"
        Set-DotEnvValue -Path $entorno -Name "CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2" -Value "false"
    }
    Set-DotEnvValue -Path $entorno -Name "PRINT_BACKEND" -Value $PrintBackend
    Set-DotEnvValue -Path $entorno -Name "PRINT_SYNC" -Value "false"
    Set-DotEnvValue -Path $entorno -Name "PRINTER_CAJA_HOST" -Value $PrinterCajaHost
    Set-DotEnvValue -Path $entorno -Name "PRINTER_COCINA_HOST" -Value $PrinterCocinaHost
    Set-DotEnvValue -Path $entorno -Name "PRINTER_BARRA_HOST" -Value $PrinterBarraHost
    Set-DotEnvValue -Path $entorno -Name "PRINTER_PORT" -Value $PrinterPort
    Set-DotEnvValue -Path $entorno -Name "VPS_CONSOLIDACION_URL" -Value $VpsConsolidacionUrl
    Set-DotEnvValue -Path $entorno -Name "VPS_CONSOLIDACION_TOKEN" -Value $VpsConsolidacionToken
    Set-DotEnvValue -Path $entorno -Name "VPS_CONSOLIDACION_TIMEOUT" -Value $VpsConsolidacionTimeout
}
Set-CanonicalProcessEnvironment -Path $entorno

$dbEngine = ConvertFrom-DotEnvDatabaseEngine (
    Get-DotEnvValue -Path $entorno -Name "DB_ENGINE"
)
$usaSqlite = $dbEngine -eq "sqlite"
$runtime = Join-Path $raiz "runtime"
if ($Modo -eq "Instalar") {
    $sqliteDestino = Join-Path $runtime "db.sqlite3"
}
$backupRoot = Join-Path $raiz "backups"
$backupLog = Join-Path $raiz "logs\sqlite-backup.log"
if ($usaSqlite) {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    if ($Modo -eq "Instalar" -and
        -not (Test-Path -LiteralPath $sqliteDestino) -and
        (Test-Path -LiteralPath (Join-Path $raiz "db.sqlite3"))) {
        & $python -c "import sqlite3, sys; from pathlib import Path; from contextlib import closing; src=Path(sys.argv[1]).resolve().as_uri() + '?mode=ro'; exec('with closing(sqlite3.connect(src, uri=True)) as source, closing(sqlite3.connect(sys.argv[2])) as dest:\n source.backup(dest)')" (Join-Path $raiz "db.sqlite3") $sqliteDestino
        if ($LASTEXITCODE -ne 0) { throw "No fue posible copiar SQLite de forma consistente a runtime." }
    }
    if ($Modo -eq "Instalar") {
        Set-DotEnvValue -Path $entorno -Name "SQLITE_PATH" -Value "runtime/db.sqlite3"
    }
}

# SQLITE_PATH puede haberse normalizado durante una instalación. Recarga el
# entorno después de escribirlo para que checks, respaldo, migraciones y servicio
# operen sobre exactamente la misma base.
Set-CanonicalProcessEnvironment -Path $entorno

@("runtime", "backups", "logs", "media", "certs") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $raiz $_) | Out-Null
}
$waitressLog = Join-Path $raiz "logs\waitress.log"
if (-not (Test-Path -LiteralPath $waitressLog -PathType Leaf)) {
    New-Item -ItemType File -Path $waitressLog | Out-Null
}

# Los respaldos y certificados permanecen restringidos incluso si falla una migracion.
Protect-Path -Path $backupRoot -LocalServiceAccess "None"
Protect-Path -Path (Join-Path $raiz "certs") -LocalServiceAccess "Read"
if ($Modo -eq "Actualizar") {
    Assert-EnvironmentUnchanged -Path $entorno -ExpectedHash $envHashOriginal
    $respaldoEntorno = Join-Path $backupRoot (
        "env-antes-actualizacion-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".backup"
    )
    Copy-Item -LiteralPath $entorno -Destination $respaldoEntorno
    Protect-Path -Path $respaldoEntorno -LocalServiceAccess "None"
    if ((Get-FileHash -LiteralPath $respaldoEntorno -Algorithm SHA256).Hash -ne $envHashOriginal) {
        throw "El respaldo previo de .env no coincide con el archivo original."
    }
}

& $python manage.py check --deploy
if ($LASTEXITCODE -ne 0) { throw "La configuración Django de producción no es válida." }
if ($usaSqlite -and (Test-Path -LiteralPath $sqliteDestino)) {
    Write-Host "Respaldo verificable antes de migrar..." -ForegroundColor Yellow
    Invoke-SqliteBackup -PythonPath $python -DatabasePath $sqliteDestino -BackupRoot $backupRoot -LogPath $backupLog -RetentionDays $retencionRespaldoPreMigracion -BackupMutex $respaldoMutex
}
& $python manage.py verificar_identidad_local
if ($LASTEXITCODE -ne 0) {
    throw "La base existente no pertenece de forma inequívoca a la sucursal configurada; no se ejecutaron migraciones."
}
if ($Modo -eq "Actualizar") {
    Assert-EnvironmentUnchanged -Path $entorno -ExpectedHash $envHashOriginal
}
$migracionIniciada = $true
& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Fallaron las migraciones." }
if ($Modo -eq "Instalar") {
    $argumentosAprovisionamiento = @(
        "manage.py", "aprovisionar_sucursal",
        "--clave", $SucursalClave,
        "--nombre", $SucursalNombre
    )
    if (-not [string]::IsNullOrWhiteSpace($SucursalId)) {
        $argumentosAprovisionamiento += @("--sucursal-id", $SucursalId)
    }
    if ($enrollmentReceipt) {
        $argumentosAprovisionamiento += @("--edge-id", $edgeGuid.ToString())
    }
    & $python @argumentosAprovisionamiento
    if ($LASTEXITCODE -ne 0) { throw "Falló el aprovisionamiento explícito de la sucursal." }
    & $python manage.py inicializar_operacion_sucursal
    if ($LASTEXITCODE -ne 0) { throw "Falló la inicialización de posiciones operativas." }
    if ($InicializarDatosArboledas) {
        Write-Host "Aplicando la semilla histórica solicitada expresamente para ARBOLEDAS..." -ForegroundColor Yellow
        & $python manage.py cargar_datos_iniciales
        if ($LASTEXITCODE -ne 0) { throw "Falló la carga explícita de datos iniciales de Arboledas." }
    }

    if ($enrollmentReceipt) {
        if ('pedidos_sucursales' -in $enrolledModules) {
            & $python manage.py configurar_modulos_sucursal --modulos pedidos_sucursales
        }
        else {
            & $python manage.py configurar_modulos_sucursal --sin-opcionales
        }
    }
    else {
        # Camino manual conservado exclusivamente para laboratorios legados.
        $modulosDisponibles = @('pedidos_sucursales')
        if ($PSBoundParameters.ContainsKey("ModulosOpcionales")) {
            $modulosSeleccionados = @($ModulosOpcionales | ForEach-Object {
                @(([string]$_) -split ',')
            } | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
        }
        else {
            & $python manage.py configurar_modulos_sucursal --iniciales
            if ($LASTEXITCODE -ne 0) { throw "Falló la configuración inicial de módulos." }
            $modulosSeleccionados = $null
        }
        if ($null -ne $modulosSeleccionados) {
            $modulosDesconocidos = @($modulosSeleccionados | Where-Object {
                $_ -notin $modulosDisponibles
            })
            if ($modulosDesconocidos.Count) {
                throw "Módulos opcionales desconocidos: $($modulosDesconocidos -join ', ')."
            }
            if ($modulosSeleccionados.Count) {
                & $python manage.py configurar_modulos_sucursal --modulos ($modulosSeleccionados -join ',')
            }
            else {
                & $python manage.py configurar_modulos_sucursal --sin-opcionales
            }
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Falló la configuración inicial de módulos." }
}
& $python manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw "Falló la recopilación de archivos estáticos." }

if ($Modo -eq "Instalar") {
    & $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; raise SystemExit(0 if get_user_model().objects.filter(is_active=True, is_superuser=True).exists() else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No existe una cuenta administrativa. Crea la primera ahora; la contraseña no se mostrará." -ForegroundColor Yellow
        & $python manage.py createsuperuser
        if ($LASTEXITCODE -ne 0) {
            throw "Debe existir al menos un superusuario antes de iniciar producción."
        }
    }

}
# Dueño explícito: no se eleva automáticamente a un encargado legado durante update.
& $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pos.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; U=get_user_model(); raise SystemExit(0 if U.objects.filter(is_active=True, is_superuser=False, perfil_pos__activo=True, perfil_pos__es_sistema=False, perfil_pos__rol__tipo='dueno', perfil_pos__sucursal__clave=os.environ['SUCURSAL_CLAVE']).exists() else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Crea la cuenta del dueño de sucursal explícito. Un encargado legado no se promociona." -ForegroundColor Yellow
    & $python manage.py crear_operador_pos --crear-perfil-inicial
    if ($LASTEXITCODE -ne 0) {
        throw "Debe existir un dueño de sucursal humano antes de iniciar el servicio."
    }
}

if ($usaSqlite -and -not $SkipBackupTask) {
    if ($Modo -eq "Instalar") {
        Write-Host "Creando respaldo inicial verificable de runtime\db.sqlite3..." -ForegroundColor Yellow
        Invoke-SqliteBackup `
            -PythonPath $python `
            -DatabasePath $sqliteDestino `
            -BackupRoot $backupRoot `
            -LogPath $backupLog `
            -RetentionDays $BackupRetentionDays `
            -BackupMutex $respaldoMutex
    }
    Register-SqliteBackupTask `
        -PythonPath $python `
        -DatabasePath $sqliteDestino `
        -BackupRoot $backupRoot `
        -LogPath $backupLog `
        -DailyTime $BackupTime `
        -RetentionDays $BackupRetentionDays
}
elseif ($Modo -eq "Instalar") {
    Remove-SqliteBackupTask
    if ($usaSqlite) {
        Write-Host "Se omitieron las tareas programadas de respaldo y purga por -SkipBackupTask." -ForegroundColor Yellow
    }
    else {
        Write-Host "DB_ENGINE no usa SQLite; se retiraron las tareas locales de respaldo y purga." -ForegroundColor Yellow
    }
}

if ($Modo -eq "Actualizar" -and
    (Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash -ne $envHashOriginal) {
    throw "La actualización intentó modificar .env; el servicio permanece detenido para revisión."
}

$accion = if ($Modo -eq "Actualizar") { "update" } else { "install" }
& $python $servicioPython --startup delayed --username "NT AUTHORITY\LocalService" $accion
if ($LASTEXITCODE -ne 0) { throw "No fue posible $accion el servicio de Windows." }

if ($Modo -eq "Instalar") {
    & sc.exe failure $nombreServicio reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "No fue posible configurar la recuperación automática del servicio." }

    # Una instalación explícita siempre retira una regla homónima residual. Así,
    # -SkipFirewall y HTTPS significan realmente que la aplicación no deja esa
    # apertura activa de una instalación anterior o incompleta.
    Get-NetFirewallRule -DisplayName $nombreFirewall -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    if (-not $SkipFirewall -and -not $Https) {
        New-NetFirewallRule `
            -DisplayName $nombreFirewall `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort $Port `
            -Profile Private `
            -RemoteAddress LocalSubnet `
            -Program $firewallPython | Out-Null
    }
}

# Solo tras dependencias, pruebas, checks, migraciones y registro del servicio.
# Cada ACL conserva acceso efectivo para Administradores y SYSTEM, aun si falla
# la siguiente operacion. Nunca se restaura una ACL vacia del intento anterior.
Write-Host "Aplicando permisos de produccion..." -ForegroundColor Yellow
Protect-ApplicationTree -Path $raiz

if ($Modo -eq "Actualizar" -and
    (Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash -ne $envHashOriginal) {
    throw "El contenido de .env cambió durante la actualización; no se iniciará el servicio."
}

$arranqueIntentadoPorScript = $true
Start-Service -Name $nombreServicio
(Get-Service -Name $nombreServicio).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))

$hostSalud = if ($ListenAddress -eq "0.0.0.0") {
    "127.0.0.1"
} elseif ($ListenAddress -eq "::") {
    "::1"
} else {
    $ListenAddress
}
if ($hostSalud.Contains(":") -and -not $hostSalud.StartsWith("[")) { $hostSalud = "[$hostSalud]" }
$urlSalud = "http://${hostSalud}:$Port/salud/"
$cabecerasSalud = @{ "Host" = $hosts[0] }
if ($Https) { $cabecerasSalud["X-Forwarded-Proto"] = "https" }
$limiteSalud = (Get-Date).AddSeconds(30)
$saludable = $false
do {
    try {
        $respuestaSalud = Invoke-WebRequest -UseBasicParsing -Uri $urlSalud -Headers $cabecerasSalud -TimeoutSec 2
        $contenidoSalud = $respuestaSalud.Content | ConvertFrom-Json
        $saludable = $respuestaSalud.StatusCode -eq 200 -and $contenidoSalud.estado -eq "ok"
    }
    catch {
        $saludable = $false
    }
    if (-not $saludable) { Start-Sleep -Milliseconds 500 }
} while (-not $saludable -and (Get-Date) -lt $limiteSalud)
if (-not $saludable) {
    throw "El servicio se registró, pero /salud/ no respondió correctamente. Revisa logs\waitress.log."
}
# El servicio puede crear archivos entre el endurecimiento inicial y la primera
# respuesta saludable. Normaliza de nuevo las carpetas dinámicas para que esos
# archivos conserven el mismo propietario y las mismas ACE explícitas.
foreach ($directorioDinamico in @("runtime", "logs", "media")) {
    Protect-Path -Path (Join-Path $raiz $directorioDinamico) -LocalServiceAccess "Modify"
}
if ($Modo -eq "Actualizar" -and
    (Get-FileHash -LiteralPath $entorno -Algorithm SHA256).Hash -ne $envHashOriginal) {
    throw "El contenido de .env cambió después de iniciar el servicio."
}

Write-Host ""
Write-Host "Servicio Los Tocayos POS: operación $Modo completada e iniciada con Waitress." -ForegroundColor Green
Write-Host "Versión: $releaseVersion"
Write-Host "Sucursal: $SucursalClave"
Write-Host "Hosts permitidos: $AllowedHosts"
if ($Https) {
    Write-Host "Waitress escucha sólo en $ListenAddress y confía en el proxy $TrustedProxy. Publica HTTPS desde el proxy."
}
else {
    Write-Host "Puerto LAN: $Port (sólo perfil privado/subred local)"
}
if ($Modo -eq "Instalar") {
    Write-Host "La clave secreta quedó guardada en .env con permisos NTFS restringidos."
}
else {
    Write-Host "Identidad, secretos y configuración operativa de .env se conservaron sin cambios."
}
if ($Modo -eq "Instalar" -and $usaSqlite -and -not $SkipBackupTask) {
    Write-Host "Respaldo diario: tarea $nombreTareaRespaldo a las $BackupTime; retencion $BackupRetentionDays dias."
    Write-Host "Purgas físicas: tarea $nombreTareaPurgas cada 5 minutos, inactiva cuando no hay solicitudes."
}
}
catch {
    $falloOriginal = $_
    if ($arranqueIntentadoPorScript) {
        try {
            $servicioFallido = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
            if ($servicioFallido -and $servicioFallido.Status -ne "Stopped") {
                Stop-Service -Name $nombreServicio
                $servicioFallido.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
            }
            Write-Warning "El arranque o la comprobación de salud falló; el servicio quedó detenido."
        }
        catch {
            Write-Warning "Falló el arranque o la salud y tampoco fue posible confirmar el servicio detenido. Revísalo manualmente."
        }
    }
    if ($Modo -eq "Actualizar" -and $servicioDetenidoPorScript -and
        -not $runtimeModificado -and -not $migracionIniciada) {
        try {
            Start-Service -Name $nombreServicio
            (Get-Service -Name $nombreServicio).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
            Write-Warning "La actualización falló antes de iniciar migraciones; se volvió a iniciar el servicio existente."
        }
        catch {
            Write-Warning "La actualización falló antes de migrar y tampoco fue posible reanudar el servicio. Revisa el Visor de eventos y logs\waitress.log."
        }
    }
    elseif ($Modo -eq "Actualizar" -and ($runtimeModificado -or $migracionIniciada)) {
        Write-Warning "El runtime o la base ya pudieron cambiar; no se intentará un arranque ciego ni rollback automático."
    }
    if ($Modo -eq "Instalar") {
        try {
            $servicioParcial = Get-Service -Name $nombreServicio -ErrorAction SilentlyContinue
            if ($servicioParcial) {
                Assert-ServiceBelongsToProject -ServiceName $nombreServicio
                if ($servicioParcial.Status -ne "Stopped") {
                    Stop-Service -Name $nombreServicio -ErrorAction Stop
                    $servicioParcial.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
                }
                & sc.exe delete $nombreServicio | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "sc.exe no pudo retirar el servicio parcial."
                }
            }
        }
        catch {
            Write-Warning "No fue posible retirar por completo el servicio parcial; comprueba LosTocayosPOS antes de reintentar."
        }
        try {
            Remove-SqliteBackupTask
            Get-NetFirewallRule -DisplayName $nombreFirewall -ErrorAction SilentlyContinue |
                Remove-NetFirewallRule -ErrorAction Stop
        }
        catch {
            Write-Warning "No fue posible retirar por completo la tarea o regla de firewall parcial."
        }
        try {
            if ($envExistiaAntesInstalacion) {
                [IO.File]::WriteAllBytes($entorno, $envBytesAntesInstalacion)
                Protect-Path -Path $entorno -LocalServiceAccess "Read"
            }
            elseif (Test-Path -LiteralPath $entorno) {
                Remove-Item -LiteralPath $entorno -Force
            }
        }
        catch {
            Write-Warning "No fue posible restaurar .env después de la instalación fallida."
        }
        Write-Warning "Se revirtieron registro, tarea, firewall y configuración creados por la instalación. La base/runtime se conservaron para diagnóstico y un reintento con la misma identidad."
    }
    throw $falloOriginal
}
}
finally {
    if ($null -ne $respaldoMutex) {
        try { $respaldoMutex.ReleaseMutex() }
        catch { Write-Warning "No fue posible liberar normalmente el mutex de respaldo." }
        finally { $respaldoMutex.Dispose() }
    }
    if ($null -ne $envReadLock) {
        try { $envReadLock.Dispose() }
        catch { Write-Warning "No fue posible liberar inmediatamente el bloqueo de .env." }
    }
    if ($null -ne $mantenimientoMutex) {
        try { $mantenimientoMutex.ReleaseMutex() }
        catch { Write-Warning "No fue posible liberar normalmente el mutex de mantenimiento." }
        finally { $mantenimientoMutex.Dispose() }
    }
}
