# Developer helper: copies the Household add-on to a Home Assistant box's /addons share
# (Samba share add-on) so it can be installed as a local add-on.
# Usage: .\deploy.ps1   (reads HA_HOST / SAMBA_USERNAME / SAMBA_PASSWORD from .env)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) { throw "Missing .env - copy .env.example to .env and fill it in." }
$cfg = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$') { $cfg[$Matches[1]] = $Matches[2].Trim('"') }
}
foreach ($k in 'HA_HOST', 'SAMBA_USERNAME', 'SAMBA_PASSWORD') {
    if (-not $cfg[$k]) { throw "$k is empty in .env" }
}

$unc = "\\$($cfg.HA_HOST)\addons"
if (-not (Test-Path $unc)) {
    net use $unc $cfg.SAMBA_PASSWORD /user:$($cfg.SAMBA_USERNAME) /persistent:no | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not connect to $unc" }
}

$dest = "$unc\household"
robocopy (Join-Path $root 'household') $dest /MIR /XD __pycache__ /NFL /NDL /NJH /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0  # robocopy uses 1-7 for success
Write-Host "Deployed to $dest. In HA: Settings > Add-ons > Add-on Store > (menu) Check for updates, then update Household."
