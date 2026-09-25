param(
    [Parameter(Mandatory = $true)][string]$PrivateKeyPath,
    [Parameter(Mandatory = $true)][string]$TrustStorePath
)

$ErrorActionPreference = "Stop"
$desktop = Split-Path -Parent $MyInvocation.MyCommand.Path
$repository = [IO.Path]::GetFullPath((Join-Path $desktop ".."))
$build = Join-Path $desktop "build"
$staging = Join-Path $build "cliente-windows"
$release = Join-Path $desktop "release"
$packageSources = Join-Path $desktop "package"
$zip = Join-Path $release "LosTocayosPOS-Cliente-Windows.zip"
$manifestPath = Join-Path $release "client-manifest.json"
$checksumPath = Join-Path $release "SHA256SUMS.txt"
$signaturePath = Join-Path $release "release-signature.json"
$installer = Join-Path $release "Instalador-LosTocayosPOS.exe"
$installerSource = Join-Path $desktop "InstallerBootstrap.cs"
$compiler = Join-Path $env:WINDIR "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
$signer = Join-Path $repository "herramientas/release_firma.ps1"
$version = (Get-Content -LiteralPath (Join-Path $repository "VERSION") -Raw).Trim()
$utf8 = New-Object Text.UTF8Encoding($false)

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
foreach ($output in @($zip, $manifestPath, $checksumPath, $signaturePath, $installer)) {
    Assert-ChildPath -Parent $release -Child $output
}
$privateFull = [IO.Path]::GetFullPath($PrivateKeyPath)
$trustFull = [IO.Path]::GetFullPath($TrustStorePath)
$repositoryPrefix = $repository.TrimEnd("\") + "\"
if ($privateFull.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $trustFull.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Clave privada y trust store deben estar fuera del repositorio."
}
if (-not (Test-Path -LiteralPath $privateFull -PathType Leaf) -or
    -not (Test-Path -LiteralPath $trustFull -PathType Leaf)) {
    throw "Falta clave privada o trust store externo."
}

& (Join-Path $desktop "build.ps1")
if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging, $release | Out-Null
Copy-Item -LiteralPath (Join-Path $desktop "dist/TocayosPOS.exe") -Destination $staging
Get-ChildItem -LiteralPath $packageSources -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $staging
}
$files = @(Get-ChildItem -LiteralPath $staging -File | Sort-Object Name)
$manifest = [ordered]@{
    schema_version = 1
    product = "LosTocayosPOS-Cliente-Windows"
    release_version = $version
    files = @($files | ForEach-Object {
        [ordered]@{
            path = $_.Name
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
}
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6 -Compress) + [char]10, $utf8)

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
$archive = [IO.Compression.ZipFile]::Open($zip, [IO.Compression.ZipArchiveMode]::Create)
try {
    $fixedTime = New-Object DateTimeOffset(2020, 1, 1, 0, 0, 0, ([TimeSpan]::Zero))
    foreach ($file in $files) {
        $entry = $archive.CreateEntry($file.Name, [IO.Compression.CompressionLevel]::Optimal)
        $entry.LastWriteTime = $fixedTime
        $source = [IO.File]::OpenRead($file.FullName)
        $target = $entry.Open()
        try { $source.CopyTo($target) }
        finally {
            $target.Dispose()
            $source.Dispose()
        }
    }
}
finally { $archive.Dispose() }

$archiveHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumText = "$archiveHash  $([IO.Path]::GetFileName($zip))" + [char]10 +
    "$manifestHash  $([IO.Path]::GetFileName($manifestPath))" + [char]10
[IO.File]::WriteAllText($checksumPath, $checksumText, $utf8)
if (Test-Path -LiteralPath $signaturePath) {
    Remove-Item -LiteralPath $signaturePath -Force
}
& $signer -Mode Sign -ArchivePath $zip -ManifestPath $manifestPath -ChecksumPath $checksumPath -SignaturePath $signaturePath -PrivateKeyPath $privateFull
if (-not (Test-Path -LiteralPath $signaturePath -PathType Leaf)) {
    throw "La firma del paquete Windows no se generó."
}
& $signer -Mode Verify -ArchivePath $zip -ManifestPath $manifestPath -ChecksumPath $checksumPath -SignaturePath $signaturePath -TrustStorePath $trustFull -ExpectedVersion $version

$resources = @(
    "/resource:$zip,LosTocayos.Installer.Package.payload.zip",
    "/resource:$manifestPath,LosTocayos.Installer.Package.client-manifest.json",
    "/resource:$checksumPath,LosTocayos.Installer.Package.SHA256SUMS.txt",
    "/resource:$signaturePath,LosTocayos.Installer.Package.release-signature.json"
)
$arguments = @(
    "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+", "/debug-",
    "/reference:System.dll", "/reference:System.Windows.Forms.dll",
    "/reference:System.Web.Extensions.dll", "/reference:System.IO.Compression.dll",
    "/out:$installer", $installerSource, (Join-Path $build "Version.generated.cs")
) + $resources
& $compiler @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "La compilación del instalador verificador falló."
}
$artifactHashes = @($zip, $installer, $manifestPath, $checksumPath, $signaturePath) |
    ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $([IO.Path]::GetFileName($_))"
    }
[IO.File]::WriteAllText(
    (Join-Path $release "ARTIFACT_SHA256SUMS.txt"),
    (($artifactHashes -join [char]10) + [char]10),
    $utf8
)
Write-Host "Paquete cliente: $zip" -ForegroundColor Green
Write-Host "Instalador verificador: $installer" -ForegroundColor Green
Write-Host "Firma: $signaturePath"
Write-Host "El Edge se configura durante la instalación en cada terminal."
