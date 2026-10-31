<#
.SYNOPSIS
    Provision and deploy the Nutritional Insights app to Azure (Project Phase 2).

.DESCRIPTION
    Safe to re-run: every step creates the resource only if it is missing, then updates it.

      1. Resource group            diet-analysis-rg (tagged)
      2. Storage account           private "datasets" container + All_Diets.csv upload
      3. Function App              Python, Flex Consumption (or Linux Consumption), Application Insights
      4. App settings              DIETS_* environment variables, storage connection string
      5. Function code             zip deploy with remote build (pip install runs in Azure)
      6. Dashboard host            App Service web app (Free F1) or Static Web App (Free), hosts frontend/
      7. CORS + config.js          Function App only accepts browser calls from the dashboard's origin
      8. Smoke test                /api/health and /api/insights

    Needs only the Azure CLI (az login first); the Static Web App option also needs Node.js (npx).
    Works in Windows PowerShell 5.1, PowerShell 7, and the Azure Cloud Shell (pwsh).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File infra/deploy.ps1

.EXAMPLE
    ./infra/deploy.ps1 -Location eastus -FrontendHost staticwebapp -StaticWebAppLocation eastus2 -AllowLocalDev
#>
param(
    [string]$ResourceGroup = "diet-analysis-rg",
    [string]$Location = "canadacentral",
    # Azure for Students only allows a few regions (canadacentral, francecentral, ...), and none of them
    # offers Static Web Apps, so the dashboard defaults to a Free App Service web app in -Location.
    [ValidateSet("appservice", "staticwebapp")]
    [string]$FrontendHost = "appservice",
    # Static Web Apps run in a few regions only: westus2, centralus, eastus2, westeurope, eastasia
    [string]$StaticWebAppLocation = "westus2",
    [string]$Prefix = "dietinsights",
    [ValidateSet("flex", "consumption")]
    [string]$Plan = "flex",
    # requirements.txt pins azure-functions 2.x, which needs Python 3.13 or later
    [string]$PythonVersion = "3.13",
    # Also allow http://localhost:4280 to call the cloud API (local dashboard against cloud backend)
    [switch]$AllowLocalDev,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
$OnWindows = $env:OS -eq "Windows_NT"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

# Run az and stop on failure (native commands do not throw on their own).
# "Continue" inside the helpers: Windows PowerShell 5.1 would turn az warnings on stderr into errors.
function Invoke-AzCli {
    $ErrorActionPreference = "Continue"
    $output = & az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed with exit code $LASTEXITCODE" }
    return $output
}

# True when the az command succeeds, e.g. "show" on a resource that may not exist yet.
function Test-AzCli {
    $ErrorActionPreference = "Continue"
    & az @args -o none 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

# Zip a folder's contents. ZipFile writes "/" separators; Compress-Archive in PowerShell 5.1 writes "\",
# which breaks subfolders (css/, js/) once extracted on Linux.
function New-Zip([string]$Folder, [string]$ZipPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    [IO.Compression.ZipFile]::CreateFromDirectory($Folder, $ZipPath)
}

Write-Step "Checking tools and Azure sign-in"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI not found. Install it (winget install Microsoft.AzureCLI) or run this script in Azure Cloud Shell."
}
if (-not (Test-AzCli account show)) { throw "Not signed in. Run 'az login' first." }
$account = Invoke-AzCli account show -o json | ConvertFrom-Json
Write-Host "Subscription: $($account.name) ($($account.id))"

# Deterministic suffix (like Bicep's uniqueString) so re-runs reuse the same globally unique names.
$sha = [System.Security.Cryptography.SHA256]::Create()
$bytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("$($account.id)/$ResourceGroup"))
$suffix = (($bytes[0..2] | ForEach-Object { $_.ToString("x2") }) -join "")
$storageName = (("$Prefix" + "st" + $suffix) -replace "[^a-z0-9]", "")
if ($storageName.Length -gt 24) { $storageName = $storageName.Substring(0, 24) }
$functionName = "$Prefix-func-$suffix"
$webName = "$Prefix-web-$suffix"
$webPlanName = "$Prefix-webplan-$suffix"
$container = "datasets"
$blobName = "All_Diets.csv"
$tags = @("project=nutritional-insights", "phase=2", "course=CPSY300")

Write-Host "Resource group: $ResourceGroup ($Location)"
Write-Host "Storage:        $storageName"
Write-Host "Function App:   $functionName ($Plan, Python $PythonVersion)"
if ($FrontendHost -eq "appservice") {
    Write-Host "Dashboard:      $webName (App Service F1, $Location)"
} else {
    Write-Host "Dashboard:      $webName (Static Web App, $StaticWebAppLocation)"
}

Write-Step "Registering resource providers (first run on a new subscription can take a few minutes)"
foreach ($ns in "Microsoft.Web", "Microsoft.Storage", "Microsoft.Insights", "Microsoft.OperationalInsights") {
    $state = Invoke-AzCli provider show --namespace $ns --query registrationState -o tsv
    if ($state -ne "Registered") {
        Write-Host "registering $ns"
        Invoke-AzCli provider register --namespace $ns --wait | Out-Null
    }
}

Write-Step "1. Resource group"
Invoke-AzCli group create --name $ResourceGroup --location $Location --tags @tags -o none

Write-Step "2. Storage account, private container, dataset upload"
if (-not (Test-AzCli storage account show --name $storageName --resource-group $ResourceGroup)) {
    Invoke-AzCli storage account create --name $storageName --resource-group $ResourceGroup --location $Location `
        --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --https-only true `
        --allow-blob-public-access false --tags @tags -o none
}
$connection = Invoke-AzCli storage account show-connection-string --name $storageName --resource-group $ResourceGroup --query connectionString -o tsv
Invoke-AzCli storage container create --name $container --connection-string $connection --public-access off -o none
Invoke-AzCli storage blob upload --container-name $container --name $blobName --file (Join-Path $Root "data/All_Diets.csv") `
    --content-type "text/csv" --overwrite true --connection-string $connection -o none
Write-Host "uploaded data/All_Diets.csv -> $storageName/$container/$blobName"

Write-Step "3. Function App (Application Insights is created with it)"
if (-not (Test-AzCli functionapp show --name $functionName --resource-group $ResourceGroup)) {
    if ($Plan -eq "flex") {
        Invoke-AzCli functionapp create --name $functionName --resource-group $ResourceGroup --storage-account $storageName `
            --flexconsumption-location $Location --runtime python --runtime-version $PythonVersion --tags @tags -o none
    } else {
        Invoke-AzCli functionapp create --name $functionName --resource-group $ResourceGroup --storage-account $storageName `
            --consumption-plan-location $Location --os-type Linux --functions-version 4 `
            --runtime python --runtime-version $PythonVersion --tags @tags -o none
    }
}
if ($Plan -eq "flex") {
    # an app created by an earlier run may be on another Python version
    $current = Invoke-AzCli functionapp show --name $functionName --resource-group $ResourceGroup `
        --query "properties.functionAppConfig.runtime.version" -o tsv
    if ($current -ne $PythonVersion) {
        Write-Host "updating runtime: Python $current -> $PythonVersion"
        Invoke-AzCli functionapp runtime config set --name $functionName --resource-group $ResourceGroup `
            --runtime-version $PythonVersion -o none
    }
}
$functionHost = Invoke-AzCli functionapp show --name $functionName --resource-group $ResourceGroup --query "defaultHostName || properties.defaultHostName" -o tsv
$apiBase = "https://$functionHost/api"

Write-Step "4. App settings (environment variables read by data_source.py)"
# -o none keeps the connection string out of the console output
Invoke-AzCli functionapp config appsettings set --name $functionName --resource-group $ResourceGroup -o none --settings `
    "DIETS_STORAGE_CONNECTION=$connection" "DIETS_CONTAINER=$container" "DIETS_BLOB=$blobName" "DIETS_CACHE_SECONDS=300"

Write-Step "5. Deploying function code (remote build installs requirements.txt in Azure)"
$staging = Join-Path ([IO.Path]::GetTempPath()) "diet-func-$suffix"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null
foreach ($file in "function_app.py", "analysis.py", "data_source.py", "host.json", "requirements.txt") {
    Copy-Item (Join-Path $Root "backend/$file") $staging
}
$zip = "$staging.zip"
New-Zip $staging $zip
Invoke-AzCli functionapp deployment source config-zip --name $functionName --resource-group $ResourceGroup `
    --src $zip --build-remote true --timeout 900 -o none
Remove-Item $staging, $zip -Recurse -Force

Write-Host "waiting for the functions to come online"
$healthy = $false
for ($i = 0; $i -lt 30 -and -not $healthy; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "$apiBase/health" -TimeoutSec 60
        $healthy = $health.blobReachable
    } catch {
        Start-Sleep -Seconds 10
    }
}
if (-not $healthy) { throw "$apiBase/health did not report a reachable blob. Check the Function App logs in the portal." }
Write-Host "health ok: blob last modified $($health.blobLastModified)"

$webHost = $null
if (-not $SkipFrontend) {
    if ($FrontendHost -eq "appservice") {
        Write-Step "6. App Service web app for the dashboard (Free F1, Windows: IIS serves the static files)"
        if (-not (Test-AzCli appservice plan show --name $webPlanName --resource-group $ResourceGroup)) {
            Invoke-AzCli appservice plan create --name $webPlanName --resource-group $ResourceGroup --location $Location `
                --sku F1 --is-linux false --tags @tags -o none
        }
        if (-not (Test-AzCli webapp show --name $webName --resource-group $ResourceGroup)) {
            Invoke-AzCli webapp create --name $webName --resource-group $ResourceGroup --plan $webPlanName --tags @tags -o none
        }
        Invoke-AzCli webapp update --name $webName --resource-group $ResourceGroup --https-only true -o none
        Invoke-AzCli webapp config set --name $webName --resource-group $ResourceGroup --ftps-state Disabled `
            --min-tls-version 1.2 --http20-enabled true -o none
        $webHost = Invoke-AzCli webapp show --name $webName --resource-group $ResourceGroup --query "defaultHostName || properties.defaultHostName" -o tsv
    } else {
        Write-Step "6. Static Web App"
        if (-not (Test-AzCli staticwebapp show --name $webName --resource-group $ResourceGroup)) {
            Invoke-AzCli staticwebapp create --name $webName --resource-group $ResourceGroup --location $StaticWebAppLocation `
                --sku Free --tags @tags -o none
        }
        $webHost = Invoke-AzCli staticwebapp show --name $webName --resource-group $ResourceGroup --query defaultHostname -o tsv
    }

    Write-Step "7. CORS and frontend config"
    $origins = @("https://$webHost")
    if ($AllowLocalDev) { $origins += "http://localhost:4280", "http://127.0.0.1:4280" }
    $existing = @(Invoke-AzCli functionapp cors show --name $functionName --resource-group $ResourceGroup --query allowedOrigins -o tsv)
    $missing = @($origins | Where-Object { $existing -notcontains $_ })
    if ($missing.Count -gt 0) {
        Invoke-AzCli functionapp cors add --name $functionName --resource-group $ResourceGroup --allowed-origins @missing -o none
    }
    $config = @"
// Base URL of the Azure Function API (must end in /api).
// Written by infra/deploy.ps1 on $(Get-Date -Format "yyyy-MM-dd HH:mm").
// For a quick test against another backend, add ?api=<url> to the page address.
window.APP_CONFIG = {
    apiBaseUrl: "$apiBase"
};
"@
    [IO.File]::WriteAllText((Join-Path $Root "frontend/js/config.js"), $config, (New-Object Text.UTF8Encoding($false)))

    if ($FrontendHost -eq "appservice") {
        Write-Step "Deploying frontend/ to the App Service web app"
        $siteZip = Join-Path ([IO.Path]::GetTempPath()) "diet-web-$suffix.zip"
        New-Zip (Join-Path $Root "frontend") $siteZip
        Invoke-AzCli webapp deploy --name $webName --resource-group $ResourceGroup --src-path $siteZip --type zip `
            --clean true -o none
        Remove-Item $siteZip -Force
    } else {
        Write-Step "Deploying frontend/ to the Static Web App"
        $env:SWA_CLI_DEPLOYMENT_TOKEN = Invoke-AzCli staticwebapp secrets list --name $webName --resource-group $ResourceGroup --query properties.apiKey -o tsv
        $npx = "npx"
        if ($OnWindows) { $npx = "npx.cmd" }
        & $npx --yes "@azure/static-web-apps-cli@2.0.10" deploy (Join-Path $Root "frontend") --env production
        $deployExit = $LASTEXITCODE
        Remove-Item Env:SWA_CLI_DEPLOYMENT_TOKEN
        if ($deployExit -ne 0) { throw "Static Web App deployment failed" }
    }
}

Write-Step "8. Smoke test"
$insights = Invoke-RestMethod -Uri "$apiBase/insights" -TimeoutSec 120
Write-Host ("insights: {0} recipes, executed in {1} ms, cache {2}" -f $insights.data.summary.recipes, $insights.meta.executionTimeMs, $insights.meta.cache)

$outputs = [ordered]@{
    deployedAt       = (Get-Date).ToUniversalTime().ToString("o")
    subscription     = $account.name
    resourceGroup    = $ResourceGroup
    location         = $Location
    storageAccount   = $storageName
    datasetBlob      = "$container/$blobName"
    functionApp      = $functionName
    functionPlan     = $Plan
    functionApiUrl   = $apiBase
    healthUrl        = "$apiBase/health"
    insightsUrl      = "$apiBase/insights"
    frontendHost     = $FrontendHost
    dashboardApp     = $webName
    dashboardUrl     = $(if ($webHost) { "https://$webHost" } else { $null })
}
$outputs | ConvertTo-Json | Set-Content -Path (Join-Path $PSScriptRoot "deployment-outputs.json") -Encoding ascii

Write-Step "Done"
Write-Host "Function API:  $apiBase"
if ($webHost) { Write-Host "Dashboard:     https://$webHost" }
Write-Host "Saved to infra/deployment-outputs.json"
