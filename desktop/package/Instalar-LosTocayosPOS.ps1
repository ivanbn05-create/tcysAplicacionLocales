param(
    [string]$ServerUrl,
    [string]$InstallRoot,
    [switch]$SkipIntegration,
    [switch]$AllowInsecureHttp
)

$ErrorActionPreference = "Stop"
$nombre = "Los Tocayos POS"
$instalacion = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    Join-Path $env:LOCALAPPDATA "Programs\LosTocayosPOS"
}
else {
    [IO.Path]::GetFullPath($InstallRoot)
}
$ejecutableOrigen = Join-Path $PSScriptRoot "TocayosPOS.exe"
$configuracionDestino = Join-Path $env:LOCALAPPDATA "LosTocayosPOS/servidor.txt"
$configuracionAnterior = Join-Path $instalacion "servidor.txt"

function Test-ServerUrl {
    param([string]$Value)

    $uri = $null
    return [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -and
        $uri.Scheme -in @("http", "https") -and
        [string]::IsNullOrEmpty($uri.UserInfo) -and
        $uri.AbsolutePath -eq "/" -and
        [string]::IsNullOrEmpty($uri.Query) -and
        [string]::IsNullOrEmpty($uri.Fragment)
}

function Test-ServerConnection {
    param([string]$Value)

    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri ($Value.TrimEnd("/") + "/salud/") `
            -TimeoutSec 3
        $contenido = $response.Content | ConvertFrom-Json
        return $response.StatusCode -eq 200 -and $contenido.estado -eq "ok"
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $ejecutableOrigen)) {
    throw "El paquete está incompleto: no se encontró TocayosPOS.exe."
}

$predeterminado = ""
if (Test-Path -LiteralPath $configuracionDestino) {
    $predeterminado = (Get-Content -LiteralPath $configuracionDestino -Raw).Trim()
}
elseif (Test-Path -LiteralPath $configuracionAnterior) {
    $predeterminado = (Get-Content -LiteralPath $configuracionAnterior -Raw).Trim()
}

if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
    Write-Host ""
    Write-Host "Instalación de $nombre" -ForegroundColor Yellow
    Write-Host "La dirección debe apuntar a la computadora principal del local."
    $respuesta = Read-Host "Dirección HTTPS del Edge [$predeterminado]"
    $ServerUrl = if ([string]::IsNullOrWhiteSpace($respuesta)) {
        $predeterminado
    }
    else {
        $respuesta.Trim()
    }
}

$ServerUrl = $ServerUrl.TrimEnd("/")
if (-not (Test-ServerUrl -Value $ServerUrl)) {
    Write-Host "La dirección debe ser sólo el origen HTTP(S), sin credenciales, ruta, consulta ni fragmento." -ForegroundColor Red
    exit 2
}

$uriServidor = [Uri]$ServerUrl
if ($uriServidor.Scheme -eq "http" -and -not $AllowInsecureHttp) {
    Write-Host ""
    Write-Host "ADVERTENCIA: HTTP permite leer o alterar sesiones y pedidos desde la LAN." -ForegroundColor Red
    $aceptacion = Read-Host "Escribe exactamente HTTP LAN para aceptar este riesgo"
    if ($aceptacion -cne "HTTP LAN") {
        Write-Host "Instalación cancelada. Configura primero una dirección HTTPS." -ForegroundColor Yellow
        exit 4
    }
}

Write-Host "Comprobando $ServerUrl ..."
$conectado = Test-ServerConnection -Value $ServerUrl
if ($conectado) {
    Write-Host "Servidor encontrado en la red." -ForegroundColor Green
}
else {
    Write-Host "No fue posible conectar con el servidor en este momento." -ForegroundColor Yellow
    Write-Host "Puede instalarse ahora y corregirse después desde Configurar servidor."
    $continuar = Read-Host "¿Continuar con la instalación? [S/n]"
    if ($continuar -match "^[Nn]") {
        exit 3
    }
}

New-Item -ItemType Directory -Force -Path $instalacion | Out-Null

$archivos = @(
    "TocayosPOS.exe",
    "Configurar-Servidor.ps1",
    "Desinstalar-LosTocayosPOS.ps1",
    "LEEME.txt"
)
foreach ($archivo in $archivos) {
    $origen = Join-Path $PSScriptRoot $archivo
    if (Test-Path -LiteralPath $origen) {
        Copy-Item -LiteralPath $origen -Destination (Join-Path $instalacion $archivo) -Force
    }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $configuracionDestino) | Out-Null
Set-Content -LiteralPath $configuracionDestino -Value $ServerUrl -Encoding ASCII

if ($SkipIntegration) {
    Write-Host "$nombre quedó copiado correctamente en $instalacion." -ForegroundColor Green
    exit 0
}

$wsh = New-Object -ComObject WScript.Shell
$escritorio = [Environment]::GetFolderPath("Desktop")
$menuInicio = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Los Tocayos POS"
New-Item -ItemType Directory -Force -Path $menuInicio | Out-Null

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments,
        [string]$Description
    )

    $shortcut = $wsh.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $instalacion
    $shortcut.Description = $Description
    $shortcut.IconLocation = "$Target,0"
    $shortcut.Save()
}

$ejecutableDestino = Join-Path $instalacion "TocayosPOS.exe"
New-Shortcut `
    -Path (Join-Path $escritorio "Los Tocayos POS.lnk") `
    -Target $ejecutableDestino `
    -Arguments "" `
    -Description "Punto de venta Los Tocayos"
New-Shortcut `
    -Path (Join-Path $menuInicio "Los Tocayos POS.lnk") `
    -Target $ejecutableDestino `
    -Arguments "" `
    -Description "Punto de venta Los Tocayos"
New-Shortcut `
    -Path (Join-Path $menuInicio "Los Tocayos POS - Tableta.lnk") `
    -Target $ejecutableDestino `
    -Arguments "--tableta" `
    -Description "Interfaz táctil de Los Tocayos"

$systemDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::System)
$powershell = Join-Path $systemDirectory "WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
    throw "No se encontró Windows PowerShell en la ruta del sistema."
}
New-Shortcut `
    -Path (Join-Path $menuInicio "Configurar servidor.lnk") `
    -Target $powershell `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $instalacion "Configurar-Servidor.ps1") + '"') `
    -Description "Cambiar y comprobar el servidor de la sucursal"
New-Shortcut `
    -Path (Join-Path $menuInicio "Desinstalar Los Tocayos POS.lnk") `
    -Target $powershell `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $instalacion "Desinstalar-LosTocayosPOS.ps1") + '"') `
    -Description "Desinstalar Los Tocayos POS"

$registro = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LosTocayosPOS"
New-Item -Path $registro -Force | Out-Null
Set-ItemProperty -Path $registro -Name DisplayName -Value $nombre
$displayVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($ejecutableDestino).FileVersion
if ([string]::IsNullOrWhiteSpace($displayVersion)) {
    throw "TocayosPOS.exe no contiene una versión de archivo válida."
}
Set-ItemProperty -Path $registro -Name DisplayVersion -Value $displayVersion
Set-ItemProperty -Path $registro -Name Publisher -Value "Los Tocayos"
Set-ItemProperty -Path $registro -Name InstallLocation -Value $instalacion
Set-ItemProperty -Path $registro -Name DisplayIcon -Value $ejecutableDestino
Set-ItemProperty `
    -Path $registro `
    -Name UninstallString `
    -Value ('"' + $powershell + '" -NoProfile -ExecutionPolicy Bypass -File "' +
        (Join-Path $instalacion "Desinstalar-LosTocayosPOS.ps1") + '"')
Set-ItemProperty -Path $registro -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $registro -Name NoRepair -Value 1 -Type DWord

Write-Host ""
Write-Host "$nombre quedó instalado correctamente." -ForegroundColor Green
Write-Host "Servidor configurado: $ServerUrl"
Write-Host "Acceso directo: $(Join-Path $escritorio 'Los Tocayos POS.lnk')"
exit 0
