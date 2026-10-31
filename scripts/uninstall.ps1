#Requires -Version 5.1
<#
大肥鱼 QQ 机器人 · Windows 卸载脚本

用法（在项目根目录打开 PowerShell）：
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Purge
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -RemoveImages

默认：停止并删除容器，保留 data\ 和 .env。
-Purge：连 data\、.env 一起删除（聊天记录、记忆、相册、登录态）。
-RemoveImages：同时删除项目镜像。
#>
[CmdletBinding()]
param(
    [switch]$Purge,
    [switch]$RemoveImages,
    [switch]$Yes
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ComposeFile = Join-Path $Root 'docker-compose.windows.yml'
if (-not (Test-Path $ComposeFile)) {
    $ComposeFile = Join-Path $Root 'docker-compose.yml'
}
if (-not (Test-Path $ComposeFile)) {
    Write-Host '[错误] 没有找到 docker-compose 文件，请在项目根目录运行。' -ForegroundColor Red
    exit 1
}

if ($Purge) {
    Write-Host '将停止并删除容器，并永久删除：' -ForegroundColor Yellow
    Write-Host '  data\     聊天记录、长期记忆、涟漪池、相册、登录态'
    Write-Host '  .env      VNC 密码等本地配置'
} else {
    Write-Host '将停止并删除 AstrBot / SnowLuma 容器，保留 data\ 和 .env。'
}

if (-not $Yes) {
    $answer = Read-Host '继续？[y/N]'
    if ($answer -notmatch '^(y|yes)$') {
        Write-Host '已取消'
        exit 0
    }
}

$dockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker info *> $null
    $dockerOk = ($LASTEXITCODE -eq 0)
}

if ($dockerOk) {
    & docker compose -f $ComposeFile down --remove-orphans 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[提示] 容器清理没有完全成功，可以稍后手动重试。' -ForegroundColor Yellow
    }
} else {
    Write-Host '[提示] Docker Desktop 未启动或无法连接，跳过容器清理。' -ForegroundColor Yellow
    Write-Host '       如需清理容器，请先启动 Docker Desktop 再运行本脚本。' -ForegroundColor Yellow
}

if ($RemoveImages -and $dockerOk) {
    & docker rmi dafeiyu-astrbot:latest *> $null
    & docker rmi motricseven7/snowluma:latest *> $null
    Write-Host '已尝试删除项目镜像。'
}

if ($Purge) {
    try {
        foreach ($item in @('data', '.env', 'update.log', '.last_update_sha')) {
            $target = Join-Path $Root $item
            if (Test-Path $target) {
                Remove-Item -Recurse -Force $target -ErrorAction Stop
            }
        }
        Write-Host '已删除 data\、.env 和更新记录。'
    } catch {
        Write-Host "[错误] 删除失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host '请确认容器已停止，或用管理员身份重新运行。' -ForegroundColor Yellow
        exit 1
    }
    $backupDir = Join-Path $Root 'backups'
    if (Test-Path $backupDir) {
        Write-Host "更新备份仍保留在：$backupDir（确认不需要后可手动删除）"
    }
}

Write-Host ''
Write-Host '容器已卸载。'
Write-Host "程序文件仍在：$Root"
Write-Host '要彻底删除，请在资源管理器中删除这个目录。'
