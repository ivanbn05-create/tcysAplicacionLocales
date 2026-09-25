param(
    [string]$GradleExecutable,
    [string]$SdkRoot
)

$ErrorActionPreference = "Stop"
$project = [IO.Path]::GetFullPath($PSScriptRoot)
$repository = [IO.Path]::GetFullPath((Join-Path $project ".."))
$keystore = $env:TOCAYOS_ANDROID_KEYSTORE
$alias = $env:TOCAYOS_ANDROID_ALIAS
if ([string]::IsNullOrWhiteSpace($keystore) -or
    [string]::IsNullOrWhiteSpace($alias) -or
    [string]::IsNullOrWhiteSpace($env:TOCAYOS_ANDROID_STORE_PASSWORD) -or
    [string]::IsNullOrWhiteSpace($env:TOCAYOS_ANDROID_KEY_PASSWORD)) {
    throw "Define TOCAYOS_ANDROID_KEYSTORE, TOCAYOS_ANDROID_ALIAS y las dos contraseñas en el entorno."
}
$keystoreFull = [IO.Path]::GetFullPath($keystore)
if (-not (Test-Path -LiteralPath $keystoreFull -PathType Leaf)) {
    throw "No se encontró el keystore externo."
}
$repositoryPrefix = $repository.TrimEnd("\") + "\"
if ($keystoreFull.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "El keystore debe residir fuera del repositorio."
}
$env:TOCAYOS_ANDROID_KEYSTORE = $keystoreFull

if (-not [string]::IsNullOrWhiteSpace($SdkRoot)) {
    $env:ANDROID_SDK_ROOT = [IO.Path]::GetFullPath($SdkRoot)
}
if ([string]::IsNullOrWhiteSpace($env:ANDROID_SDK_ROOT) -and
    [string]::IsNullOrWhiteSpace($env:ANDROID_HOME)) {
    throw "Configura ANDROID_SDK_ROOT o pasa -SdkRoot."
}
if ([string]::IsNullOrWhiteSpace($GradleExecutable)) {
    $wrapper = Join-Path $project "gradlew.bat"
    if (Test-Path -LiteralPath $wrapper) {
        $GradleExecutable = $wrapper
    }
    else {
        $command = Get-Command gradle -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            throw "No hay Gradle disponible; instala Gradle 8.11.1 o añade el wrapper."
        }
        $GradleExecutable = $command.Source
    }
}
Push-Location $project
try {
    & $GradleExecutable --no-daemon :app:assembleRelease
    if ($LASTEXITCODE -ne 0) {
        throw "La compilación Android falló."
    }
}
finally {
    Pop-Location
}

$original = Join-Path $project "app/build/outputs/apk/release/app-release.apk"
if (-not (Test-Path -LiteralPath $original -PathType Leaf)) {
    throw "Gradle no generó un APK release firmado."
}
$version = (Get-Content -LiteralPath (Join-Path $repository "VERSION") -Raw).Trim()
$release = Join-Path $project "release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
$destination = Join-Path $release "LosTocayosPOS-$version.apk"
Copy-Item -LiteralPath $original -Destination $destination -Force

$sdk = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { $env:ANDROID_HOME }
$apksigner = Join-Path $sdk "build-tools/35.0.0/apksigner.bat"
if (-not (Test-Path -LiteralPath $apksigner -PathType Leaf)) {
    throw "Falta apksigner de Android Build Tools 35.0.0."
}
& $apksigner verify --verbose --print-certs $destination
if ($LASTEXITCODE -ne 0) {
    throw "La verificación de firma Android falló."
}
$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
Set-Content -LiteralPath (Join-Path $release "SHA256SUMS.txt") -Value "$hash  $([IO.Path]::GetFileName($destination))" -Encoding ASCII
Write-Host "APK firmado: $destination" -ForegroundColor Green
Write-Host "SHA-256: $hash"
