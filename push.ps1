# Estrella Car APK Packer - GitHub release script
# Usage in PowerShell:
#   $env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"
#   .\push.ps1
# Optional:
#   .\push.ps1 -RepoName "estrella-car-apk-packer" -Private:$false

param(
    [string]$RepoName = "estrella-car-apk-packer",
    [switch]$Private = $false
)

$ErrorActionPreference = "Stop"
$token = $env:GH_TOKEN
if (-not $token) { throw "Set GH_TOKEN first. Example: `$env:GH_TOKEN='ghp_xxx'" }

$headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github+json"
    "User-Agent"    = "estrella-publish"
}

$me = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/user"
$owner = $me.login
Write-Host "GitHub user: $owner"

$exists = $true
try {
    Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/$owner/$RepoName" | Out-Null
} catch {
    $exists = $false
}
if (-not $exists) {
    $body = @{
        name        = $RepoName
        description = "AI-agent Skill: per-vehicle CarPlay / Android head-unit APK compatibility analysis, fix matching and local APK packaging, with a self-evolving knowledge base."
        private     = [bool]$Private
        auto_init   = $false
    } | ConvertTo-Json
    Write-Host "Creating repo $RepoName ..."
    Invoke-RestMethod -Method Post -Headers $headers -Uri "https://api.github.com/user/repos" -Body $body -ContentType "application/json" | Out-Null
} else {
    Write-Host "Repo $RepoName already exists, pushing to it."
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot
git rev-parse --is-inside-work-tree | Out-Null

$remote = "https://$($owner):$token@github.com/$owner/$RepoName.git"
git remote remove origin 2>$null | Out-Null
git remote add origin $remote
git branch -M main
Write-Host "Pushing to https://github.com/$owner/$RepoName ..."
git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "git push failed (proxy / TLS). Falling back to REST API push ..." -ForegroundColor Yellow
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $py) { throw "Neither git nor python is available for pushing." }
    & $py.Name (Join-Path $repoRoot "api_push.py")
    if ($LASTEXITCODE -ne 0) { throw "REST API push failed too." }
}

git remote set-url origin "https://github.com/$owner/$RepoName.git"
Write-Host ""
Write-Host "Done: https://github.com/$owner/$RepoName" -ForegroundColor Green
Write-Host "Reminder: revoke the token at https://github.com/settings/tokens" -ForegroundColor Yellow
