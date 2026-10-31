<#
.SYNOPSIS
    Delete everything deploy.ps1 created by removing the resource group (stops all charges).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File infra/teardown.ps1
#>
param(
    [string]$ResourceGroup = "diet-analysis-rg",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $Force) {
    $answer = Read-Host "Delete resource group '$ResourceGroup' and every resource in it? Type the name to confirm"
    if ($answer -ne $ResourceGroup) {
        Write-Host "Cancelled."
        exit 1
    }
}
& az group delete --name $ResourceGroup --yes --no-wait
if ($LASTEXITCODE -ne 0) { throw "az group delete failed" }
Write-Host "Deletion of '$ResourceGroup' started; it finishes in the background (check the portal)."
