<#
.SYNOPSIS
Recupera únicamente el acceso de Administradores y SYSTEM al árbol del servidor.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$motor = Join-Path $PSScriptRoot "instalar-servicio-lan.ps1"
& $motor -RepairPermissions
