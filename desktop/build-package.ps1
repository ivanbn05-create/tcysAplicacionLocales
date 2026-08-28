param(
    [string]$ServerUrl = "http://192.168.0.30:8000",
    [switch]$AllowInsecureHttp
)

$ErrorActionPreference = "Stop"
$desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$build = Join-Path $desktop "build"
$staging = Join-Path $build "cliente-windows"
$release = Join-Path $desktop "release"
$packageSources = Join-Path $desktop "package"
$zip = Join-Path $release "LosTocayosPOS-Cliente-Windows.zip"
$installer = Join-Path $release "Instalador-LosTocayosPOS.exe"
$installerSource = Join-Path $desktop "InstallerBootstrap.cs"
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

$ServerUrl = $ServerUrl.TrimEnd("/")
$uri = $null
if (-not [Uri]::TryCreate($ServerUrl, [UriKind]::Absolute, [ref]$uri) -or
    $uri.Scheme -notin @("http", "https") -or
    -not [string]::IsNullOrEmpty($uri.UserInfo) -or
    $uri.AbsolutePath -ne "/" -or
    -not [string]::IsNullOrEmpty($uri.Query) -or
    -not [string]::IsNullOrEmpty($uri.Fragment)) {
    throw "ServerUrl debe ser sólo el origen HTTP(S), sin credenciales, ruta, consulta ni fragmento."
}
if ($uri.Scheme -eq "http" -and -not $AllowInsecureHttp) {
    throw "Empaquetar un servidor HTTP requiere -AllowInsecureHttp; prefiere una URL HTTPS."
}

function Assert-ChildPath {
    param([string]$Parent, [string]$Child)

    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd("\") + "\"
    $childFull = [IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "La ruta $childFull quedó fuera de $parentFull."
    }
}

Assert-ChildPath -Parent $desktop -Child $build
Assert-ChildPath -Parent $desktop -Child $release
Assert-ChildPath -Parent $build -Child $staging

& (Join-Path $desktop "build.ps1")

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging, $release | Out-Null

Copy-Item -LiteralPath (Join-Path $desktop "dist\TocayosPOS.exe") -Destination $staging
Get-ChildItem -LiteralPath $packageSources -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $staging
}
Set-Content -LiteralPath (Join-Path $staging "servidor.txt") -Value $ServerUrl -Encoding ASCII

if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip -CompressionLevel Optimal

$resourceArguments = Get-ChildItem -LiteralPath $staging -File | ForEach-Object {
    "/resource:$($_.FullName),LosTocayos.Installer.Package.$($_.Name)"
}
& $compiler `
    /nologo `
    /target:winexe `
    /platform:anycpu `
    /reference:System.dll `
    /reference:System.Windows.Forms.dll `
    "/out:$installer" `
    $installerSource `
    $resourceArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installer)) {
    throw "La compilación del instalador falló."
}

$hashes = @($zip, $installer) | ForEach-Object {
    $hash = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($hash.Hash)  $([IO.Path]::GetFileName($_))"
}
Set-Content `
    -LiteralPath (Join-Path $release "SHA256SUMS.txt") `
    -Value $hashes `
    -Encoding ASCII

Write-Host ""
Write-Host "Paquete ZIP: $zip" -ForegroundColor Green
Write-Host "Instalador:   $installer" -ForegroundColor Green
Write-Host "Servidor:     $ServerUrl"
