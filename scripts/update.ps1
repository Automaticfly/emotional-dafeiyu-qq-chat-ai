#Requires -Version 5.1
<#
大肥鱼 QQ 机器人 · Windows 更新脚本

用法（在项目根目录打开 PowerShell）：
  powershell -ExecutionPolicy Bypass -File scripts\update.ps1
  powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -Check
  powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -NoRestart

行为：
  - git clone 的目录：git fetch + 快进合并
  - 下载 zip 的目录：从 GitHub 下载最新压缩包，只替换代码和插件
  - 始终保留 data\ 和 .env
  - 旧文件备份到 backups\update-<时间>\
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$NoRestart,
    [switch]$Stash,
    [switch]$Yes
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ComposeFile = Join-Path $Root 'docker-compose.windows.yml'
$Repo = 'Automaticfly/emotional-dafeiyu-qq-chat-ai'
$Branch = 'main'
$StateFile = Join-Path $Root '.last_update_sha'
$ApiUrl = "https://api.github.com/repos/$Repo/commits/$Branch"
$ZipUrl = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"

function Write-Info([string]$Message) {
    Write-Host "[信息] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[提示] $Message" -ForegroundColor Yellow
}

function Write-Err([string]$Message) {
    Write-Host "[错误] $Message" -ForegroundColor Red
}

function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    $output = & $Command @Arguments 2>&1
    return [pscustomobject]@{
        Output = ($output | Out-String)
        Code   = $LASTEXITCODE
    }
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    & docker compose -f $ComposeFile @ComposeArgs 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArgs -join ' ') 执行失败"
    }
}

# ---------------------------------------------------------------- 权限检查
$probe = Join-Path $Root ".write-test-$PID"
try {
    [System.IO.File]::WriteAllText($probe, 'ok')
    Remove-Item $probe -Force
} catch {
    Write-Err "当前目录没有写权限：$Root"
    Write-Warn "请用管理员身份打开 PowerShell，或把项目放到有写权限的目录。"
    exit 1
}

if (-not (Test-Path $ComposeFile)) {
    Write-Err "没有找到 docker-compose.windows.yml，请在项目根目录运行。"
    exit 1
}

if (-not $Check -and -not $NoRestart -and -not $Yes) {
    $answer = Read-Host '将更新代码并重建、重启容器，data\ 和 .env 会保留。继续？[y/N]'
    if ($answer -notmatch '^(y|yes)$') {
        Write-Host '已取消'
        exit 0
    }
}

# ---------------------------------------------------------------- git 模式
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
$isGit = $false
if ($gitCommand) {
    $checkGit = Invoke-Native 'git' @('rev-parse', '--is-inside-work-tree')
    if ($checkGit.Code -eq 0 -and $checkGit.Output.Trim() -eq 'true') {
        $isGit = $true
    }
}

$updateSha = ''

if ($isGit) {
    Write-Info '检测到 git 仓库，使用 git 更新'
    $fetch = Invoke-Native 'git' @('fetch', '--prune', 'origin', $Branch)
    if ($fetch.Code -ne 0) {
        Write-Err "git fetch 失败：$($fetch.Output.Trim())"
        exit 1
    }
    $localSha = (Invoke-Native 'git' @('rev-parse', 'HEAD')).Output.Trim()
    $remoteSha = (Invoke-Native 'git' @('rev-parse', 'FETCH_HEAD')).Output.Trim()
    if ($localSha -eq $remoteSha) {
        Write-Info "已经是最新版本（$($localSha.Substring(0, 7))）"
        exit 0
    }
    Write-Info "发现新版本：$($localSha.Substring(0, 7)) -> $($remoteSha.Substring(0, 7))"
    $log = (Invoke-Native 'git' @('--no-pager', 'log', '--oneline', 'HEAD..FETCH_HEAD')).Output
    Write-Host $log

    if ($Check) {
        Write-Info '运行 scripts\update.ps1 进行更新'
        exit 0
    }

    $status = (Invoke-Native 'git' @('status', '--porcelain', '--untracked-files=no')).Output
    if ($status.Trim()) {
        if ($Stash) {
            $stash = Invoke-Native 'git' @('stash', 'push', '-u', '-m', "update-stash-$(Get-Date -Format 'yyyyMMdd-HHmmss')")
            if ($stash.Code -ne 0) {
                Write-Err "git stash 失败：$($stash.Output.Trim())"
                exit 1
            }
            Write-Info '本地改动已保存到 git stash'
        } else {
            Write-Err '工作区有未提交的改动；先提交或加 -Stash'
            exit 1
        }
    }

    $merge = Invoke-Native 'git' @('merge', '--ff-only', 'FETCH_HEAD')
    if ($merge.Code -ne 0) {
        Write-Err "无法快进合并，请手动处理分叉：$($merge.Output.Trim())"
        exit 1
    }
    $updateSha = $remoteSha

    if ((Test-Path (Join-Path $Root 'plugins')) -and (Test-Path (Join-Path $Root 'data\plugins'))) {
        Get-ChildItem (Join-Path $Root 'plugins') -Directory | ForEach-Object {
            $target = Join-Path $Root "data\plugins\$($_.Name)"
            if (Test-Path $target) {
                Remove-Item -Recurse -Force $target
                Copy-Item -Recurse -Force $_.FullName $target
            }
        }
        Write-Info '已同步 plugins\ 到 data\plugins\'
    }
} else {
    # ------------------------------------------------------------ 压缩包模式
    Write-Info '没有 .git，使用 GitHub 压缩包同步'

    $remoteSha = ''
    try {
        $info = Invoke-RestMethod -Uri $ApiUrl -Headers @{ 'User-Agent' = 'dafeiyu-updater' } -TimeoutSec 20
        $remoteSha = [string]$info.sha
    } catch {
        Write-Warn "无法查询远端提交：$($_.Exception.Message)"
    }

    $lastSha = ''
    if (Test-Path $StateFile) {
        $lastSha = (Get-Content $StateFile -Raw).Trim()
    }
    if ($remoteSha -and $remoteSha -eq $lastSha) {
        Write-Info "已经是最新版本（$($remoteSha.Substring(0, 7))）"
        exit 0
    }
    if ($remoteSha) {
        $shortLast = if ($lastSha) { $lastSha.Substring(0, [Math]::Min(7, $lastSha.Length)) } else { '' }
        Write-Info "远端版本：$($remoteSha.Substring(0, 7))（本地记录：$shortLast）"
    }

    if ($Check) {
        if ($remoteSha) {
            Write-Info '有可用更新'
        } else {
            Write-Warn '无法确认远端版本，建议直接运行 scripts\update.ps1'
        }
        exit 0
    }

    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("dafeiyu-update-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $zip = Join-Path $temp 'repo.zip'
        Write-Info "下载 $ZipUrl"
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $temp -Force
        $src = Join-Path $temp "emotional-dafeiyu-qq-chat-ai-$Branch"
        if (-not (Test-Path (Join-Path $src 'docker-compose.yml'))) {
            throw '压缩包结构不正确'
        }

        $backup = Join-Path $Root ("backups\update-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Force -Path $backup | Out-Null

        function Backup-Item([string]$Rel) {
            $dest = Join-Path $Root $Rel
            if (-not (Test-Path $dest)) { return }
            $bak = Join-Path $backup $Rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bak) | Out-Null
            Copy-Item -Recurse -Force $dest $bak
        }

        function Replace-Path([string]$Rel) {
            $srcPath = Join-Path $src $Rel
            if (-not (Test-Path $srcPath)) { return }
            $dest = Join-Path $Root $Rel
            Backup-Item $Rel
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item -Recurse -Force $srcPath $dest
            Write-Info "更新 $Rel"
        }

        $topFiles = @(
            'Dockerfile.astrbot', '.dockerignore', '.gitattributes', '.gitignore',
            'README.md', 'ARCHITECTURE.md', 'COMMANDS.md', 'CHANGELOG.md',
            'DISCLAIMER.md', 'LICENSE', 'PRIVACY.md', 'THIRD_PARTY_NOTICES.md',
            'RELEASING.md', 'docker-compose.windows.yml'
        )
        foreach ($file in $topFiles) {
            Replace-Path $file
        }

        foreach ($dir in @('patches', 'config', 'persona', 'assets', 'docs')) {
            Replace-Path $dir
        }

        # 正在运行的 update.ps1 写成 .new，避免文件被占用导致更新中断。
        $scriptSource = Join-Path $src 'scripts'
        if (Test-Path $scriptSource) {
            $scriptDest = Join-Path $Root 'scripts'
            New-Item -ItemType Directory -Force -Path $scriptDest | Out-Null
            Get-ChildItem $scriptSource -File | ForEach-Object {
                $destFile = Join-Path $scriptDest $_.Name
                if ($_.Name -eq (Split-Path -Leaf $PSCommandPath)) {
                    Copy-Item $_.FullName "$destFile.new" -Force
                    Write-Warn "当前脚本已写入 $($_.Name).new，下次手动替换"
                } else {
                    Copy-Item $_.FullName $destFile -Force
                }
            }
            Write-Info '更新 scripts'
        }

        $pluginRel = if (Test-Path (Join-Path $Root 'plugins')) { 'plugins' } else { 'data\plugins' }
        Get-ChildItem (Join-Path $src 'plugins') -Directory | ForEach-Object {
            Replace-Path (Join-Path $pluginRel $_.Name)
        }
        Write-Info "插件目录：$pluginRel"

        if ($remoteSha) {
            [System.IO.File]::WriteAllText($StateFile, $remoteSha + "`n", (New-Object System.Text.UTF8Encoding($false)))
        }
        $updateSha = if ($remoteSha) { $remoteSha } else { 'unknown' }
        Write-Info "代码已更新，旧文件备份在 $backup"
    } finally {
        Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
    }
}

if ($NoRestart) {
    Write-Info "按 -NoRestart 跳过重建和重启，当前版本：$($updateSha.Substring(0, [Math]::Min(7, $updateSha.Length)))"
    exit 0
}

# ---------------------------------------------------------------- 重启
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Err '无法连接 Docker Desktop，代码已更新但容器没有重启。'
    Write-Warn '请启动 Docker Desktop 后运行：docker compose -f docker-compose.windows.yml up -d --build'
    exit 1
}

Write-Info '重建镜像并启动容器'
Invoke-Compose build
Invoke-Compose up -d
Invoke-Compose ps
Write-Info "更新完成：$($updateSha.Substring(0, [Math]::Min(7, $updateSha.Length)))"
