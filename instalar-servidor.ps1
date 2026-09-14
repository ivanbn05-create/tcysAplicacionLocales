<#
.SYNOPSIS
Instala por primera vez el servidor local e identifica explícitamente su sucursal.
.DESCRIPTION
No es un actualizador. Exige identidad y hosts concretos; la carga histórica de
Arboledas permanece desactivada salvo que se solicite con su switch específico.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SucursalClave,
    [Parameter(Mandatory = $true)][string]$SucursalNombre,
    [string]$SucursalId,
    [Parameter(Mandatory = $true)][string]$AllowedHosts,
    [string]$SecretKey,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(2, 64)][int]$Threads = 8,
    [string]$ListenAddress,
    [string]$TrustedProxy,
    [switch]$Https,
    [switch]$AllowInsecureHttpLan,
    [ValidateSet("archivo", "tcp")][string]$PrintBackend = "archivo",
    [string]$PrinterCajaHost,
    [string]$PrinterCocinaHost,
    [string]$PrinterBarraHost,
    [ValidateRange(1, 65535)][int]$PrinterPort = 9100,
    [switch]$SkipFirewall,
    [ValidatePattern("^(?:[01]\d|2[0-3]):[0-5]\d$")][string]$BackupTime = "03:15",
    [ValidateRange(1, 3650)][int]$BackupRetentionDays = 30,
    [switch]$SkipBackupTask,
    [switch]$AllowOnlineDependencies,
    [switch]$AllowUnverifiedDevelopmentTree,
    [switch]$InicializarDatosArboledas
)

$ErrorActionPreference = "Stop"
$motor = Join-Path $PSScriptRoot "instalar-servicio-lan.ps1"
$argumentos = @{
    Modo = "Instalar"
    SucursalClave = $SucursalClave
    SucursalNombre = $SucursalNombre
    AllowedHosts = $AllowedHosts
    Port = $Port
    Threads = $Threads
    Https = $Https
    AllowInsecureHttpLan = $AllowInsecureHttpLan
    PrintBackend = $PrintBackend
    PrinterPort = $PrinterPort
    SkipFirewall = $SkipFirewall
    BackupTime = $BackupTime
    BackupRetentionDays = $BackupRetentionDays
    SkipBackupTask = $SkipBackupTask
    AllowOnlineDependencies = $AllowOnlineDependencies
    AllowUnverifiedDevelopmentTree = $AllowUnverifiedDevelopmentTree
    InicializarDatosArboledas = $InicializarDatosArboledas
}
if (-not [string]::IsNullOrWhiteSpace($SucursalId)) { $argumentos.SucursalId = $SucursalId }
if (-not [string]::IsNullOrWhiteSpace($SecretKey)) { $argumentos.SecretKey = $SecretKey }
if (-not [string]::IsNullOrWhiteSpace($PrinterCajaHost)) { $argumentos.PrinterCajaHost = $PrinterCajaHost }
if (-not [string]::IsNullOrWhiteSpace($PrinterCocinaHost)) { $argumentos.PrinterCocinaHost = $PrinterCocinaHost }
if (-not [string]::IsNullOrWhiteSpace($PrinterBarraHost)) { $argumentos.PrinterBarraHost = $PrinterBarraHost }
if ($PSBoundParameters.ContainsKey("ListenAddress")) { $argumentos.ListenAddress = $ListenAddress }
if ($PSBoundParameters.ContainsKey("TrustedProxy")) { $argumentos.TrustedProxy = $TrustedProxy }

& $motor @argumentos
