<#
.SYNOPSIS
  Update TheHive in place: back up data/, fetch the latest code, rebuild and restart.

.DESCRIPTION
  Works for a git checkout (git pull) or a plain folder copy (fresh clone into temp, then sync).
  Never touches .env, data/ or storage/. Keeps the last 10 backups in data\backups.

.PARAMETER Branch   Branch or tag to update to. Default: the current branch, else pre-prod.
.PARAMETER Tls      Also apply the TLS overlay (docker-compose.tls.yml).
.PARAMETER NoBuild  Pull a prebuilt image (docker-compose.ghcr.yml) instead of building locally.
#>
param(
  [string]$Branch = "",
  [switch]$Tls,
  [switch]$NoBuild
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$repo = "https://github.com/SyphenAI/TheHive.git"

Write-Host "== TheHive update ($root)" -ForegroundColor Yellow

# 1. backup
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bk = Join-Path $root "data\backups"
New-Item -ItemType Directory -Force $bk | Out-Null
if (Test-Path "$root\data\thehive.db") {
  Compress-Archive -Path "$root\data\thehive.db*", "$root\.env" -DestinationPath "$bk\pre-update-$stamp.zip" -Force
  Write-Host "   backup: data\backups\pre-update-$stamp.zip"
  Get-ChildItem $bk -Filter "pre-update-*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item -Force
}

# 2. fetch code
if (Test-Path "$root\.git") {
  if (-not $Branch) { $Branch = (git rev-parse --abbrev-ref HEAD).Trim() }
  git fetch --tags origin
  git checkout -q $Branch
  git pull --ff-only origin $Branch
} else {
  if (-not $Branch) { $Branch = "pre-prod" }
  $tmp = Join-Path $env:TEMP "thehive-update-$stamp"
  git clone -q --depth 1 -b $Branch $repo $tmp
  # Sync everything except local state. /MIR would delete; we only add/overwrite.
  robocopy $tmp $root /E /XD .git data storage node_modules .venv /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
  Remove-Item -Recurse -Force $tmp
}
$sha = ""
if (Test-Path "$root\.git") { $sha = (git rev-parse --short=12 HEAD).Trim() }
$ver = (Get-Content "$root\VERSION" -ErrorAction SilentlyContinue | Select-Object -First 1)
Write-Host "   code: $Branch $ver $sha"

# 3. rebuild + restart
$files = @("-f", "docker-compose.yml")
if ($NoBuild) { $files += @("-f", "docker-compose.ghcr.yml") }
if ($Tls)     { $files += @("-f", "docker-compose.tls.yml") }
$env:GIT_SHA = $sha
if ($NoBuild) { docker compose @files pull }
docker compose @files up -d --build --remove-orphans
Write-Host "== done. Check the Audit page for 'schema:' lines if the DB was migrated." -ForegroundColor Green
