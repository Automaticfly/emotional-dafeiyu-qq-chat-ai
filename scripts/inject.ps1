#Requires -Version 5.1
<#
大肥鱼层增量安装（Windows）

把插件、补丁、人格模板和配置示例注入到已有的 AstrBot + SnowLuma 部署。
不会修改 data\config、data\cmd_config.json、.env 和用户的 compose。

用法：
  powershell -ExecutionPolicy Bypass -File scripts\inject.ps1 -Path D:\dafeiyu
  powershell -ExecutionPolicy Bypass -File scripts\inject.ps1 -Path D:\dafeiyu -DryRun
#>
[CmdletBinding()]
param(
    [string]$Path = "",
    [switch]$DryRun,
    [switch]$NoRestart,
    [switch]$NoPatches,
    [switch]$WithDockerfile
)

$ErrorActionPreference = 'Continue'
$SourceRoot = Split-Path -Parent $PSScriptRoot

function Write-Info([string]$Message) { Write-Host "[信息] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[提示] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) { Write-Host "[错误] $Message" -ForegroundColor Red }

if (-not $Path) {
    if ((Test-Path '.\docker-compose.yml') -and (Test-Path '.\data')) {
        $Path = (Get-Location).Path
    } else {
        Write-Err '请用 -Path 指定已有的 AstrBot + SnowLuma 部署目录。'
        exit 1
    }
}
if (-not (Test-Path $Path)) {
    Write-Err "目录不存在：$Path"
    exit 1
}
$Target = (Resolve-Path $Path).Path
if (-not (Test-Path (Join-Path $Target 'docker-compose.yml'))) {
    Write-Err "目标目录没有 docker-compose.yml：$Target"
    exit 1
}

$ComposeFile = Join-Path $Target 'docker-compose.windows.yml'
if (-not (Test-Path $ComposeFile)) {
    $ComposeFile = Join-Path $Target 'docker-compose.yml'
}

$pluginRel = 'data\plugins'
$pluginsDir = Join-Path $Target 'plugins'
$dataPluginsDir = Join-Path $Target 'data\plugins'
if ((Test-Path $pluginsDir) -and -not (Test-Path $dataPluginsDir)) {
    $pluginRel = 'plugins'
} else {
    $composeText = Get-Content $ComposeFile -Raw -Encoding UTF8
    if ($composeText -match '\./plugins:/AstrBot/data/plugins') {
        $pluginRel = 'plugins'
    }
}
$pluginDir = Join-Path $Target $pluginRel

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $Target "backups\inject-$stamp"
$manifest = Join-Path $Target '.dafeiyu-layer.json'

Write-Info "源目录：$SourceRoot"
Write-Info "目标目录：$Target"
Write-Info "插件目录：$pluginRel"
Write-Info "备份目录：$backup"

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    & docker compose -f $ComposeFile @ComposeArgs 2>&1 | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}

function Backup-Item([string]$Rel) {
    $dest = Join-Path $Target $Rel
    if (-not (Test-Path $dest)) { return }
    if ($DryRun) {
        Write-Host "[dry-run] 备份 $Rel"
        return
    }
    $bak = Join-Path $backup $Rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bak) | Out-Null
    Copy-Item -Recurse -Force $dest $bak
}

function Replace-Dir([string]$Src, [string]$Rel) {
    if (-not (Test-Path $Src)) { return }
    Backup-Item $Rel
    if ($DryRun) {
        Write-Host "[dry-run] 替换目录 $Rel"
        return
    }
    $dest = Join-Path $Target $Rel
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -Recurse -Force $Src $dest
    Write-Info "已更新 $Rel"
}

function Replace-File([string]$Src, [string]$Rel) {
    if (-not (Test-Path $Src)) { return }
    Backup-Item $Rel
    if ($DryRun) {
        Write-Host "[dry-run] 替换文件 $Rel"
        return
    }
    $dest = Join-Path $Target $Rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -Force $Src $dest
    Write-Info "已更新 $Rel"
}

# ---------------------------------------------------------------- 注入
New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
$pluginNames = @()
foreach ($dir in Get-ChildItem (Join-Path $SourceRoot 'plugins') -Directory) {
    $pluginNames += $dir.Name
    Replace-Dir $dir.FullName (Join-Path $pluginRel $dir.Name)
}
if ($pluginNames.Count -eq 0) {
    Write-Err '源目录 plugins\ 为空。'
    exit 1
}

if (-not $NoPatches) {
    Replace-Dir (Join-Path $SourceRoot 'patches') 'patches'
}

Replace-File (Join-Path $SourceRoot 'persona\dafeiyu.md') 'persona\dafeiyu.md'

Get-ChildItem (Join-Path $SourceRoot 'config') -File |
    Where-Object { $_.Name -like '*.example.json' -or $_.Name -eq 'CONFIGURATION.md' } |
    ForEach-Object {
        Replace-File $_.FullName (Join-Path 'config' $_.Name)
    }

if ($WithDockerfile) {
    Replace-File (Join-Path $SourceRoot 'Dockerfile.astrbot') 'Dockerfile.astrbot'
}

# ---------------------------------------------------------------- 清单
if (-not $DryRun) {
    $version = 'unknown'
    $commit = 'unknown'
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $v = & git -C $SourceRoot describe --tags --always --dirty 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { $version = ($v | Out-String).Trim() }
        $c = & git -C $SourceRoot rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $c) { $commit = ($c | Out-String).Trim() }
    }
    $payload = @{
        layer = 'dafeiyu'
        version = $version
        commit = $commit
        installed_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
        target = $Target
        plugin_dir = $pluginRel
        plugins = $pluginNames
    }
    [System.IO.File]::WriteAllText(
        $manifest,
        ($payload | ConvertTo-Json -Depth 6) + "`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Info "已写入注入清单：$manifest"
}

# ---------------------------------------------------------------- 重启
if ($NoRestart) {
    Write-Warn '按 -NoRestart 跳过重启；配置在下次重启 AstrBot 后生效。'
    exit 0
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Warn '没有找到 docker，跳过重启。'
    exit 0
}

$services = & docker compose -f $ComposeFile config --services 2>&1
if (($services | Out-String) -notmatch '(^|\s)astrbot(\s|$)') {
    Write-Warn '没有在 compose 里找到 astrbot 服务，跳过自动重启。'
    exit 0
}

$needsBuild = $false
$dockerfile = Join-Path $Target 'Dockerfile.astrbot'
if (-not $NoPatches -and (Test-Path $dockerfile)) {
    if (Select-String -Path $dockerfile -Pattern 'COPY patches/' -SimpleMatch -Quiet) {
        $needsBuild = $true
    } else {
        Write-Warn '目标 Dockerfile 没有 COPY patches/，patches 已复制但不会自动生效。'
        Write-Warn '需要时加 -WithDockerfile，或手动把补丁 COPY 进去后重建。'
    }
}

function Test-Health {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:6185/' -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-Health {
    for ($i = 0; $i -lt 40; $i++) {
        if (Test-Health) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Start-AstrBot {
    if ($needsBuild) {
        Invoke-Compose build astrbot | Out-Null
        Invoke-Compose up -d --force-recreate astrbot | Out-Null
    } else {
        Invoke-Compose restart astrbot | Out-Null
    }
}

Write-Info '重启 AstrBot 使注入生效'
Start-AstrBot

if (Wait-Health) {
    Write-Info 'AstrBot 已恢复健康：http://127.0.0.1:6185/'
    exit 0
}

Write-Err 'AstrBot 启动后健康检查失败，开始回滚'
if (Test-Path $backup) {
    foreach ($name in $pluginNames) {
        $rel = Join-Path $pluginRel $name
        $bak = Join-Path $backup $rel
        $dest = Join-Path $Target $rel
        if (Test-Path $bak) {
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item -Recurse -Force $bak $dest
        } else {
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
        }
    }
    if (Test-Path (Join-Path $backup 'patches')) {
        $dest = Join-Path $Target 'patches'
        if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
        Copy-Item -Recurse -Force (Join-Path $backup 'patches') $dest
    }
    $personaBak = Join-Path $backup 'persona\dafeiyu.md'
    if (Test-Path $personaBak) {
        New-Item -ItemType Directory -Force -Path (Join-Path $Target 'persona') | Out-Null
        Copy-Item -Force $personaBak (Join-Path $Target 'persona\dafeiyu.md')
    }
    Get-ChildItem (Join-Path $SourceRoot 'config') -File |
        Where-Object { $_.Name -like '*.example.json' -or $_.Name -eq 'CONFIGURATION.md' } |
        ForEach-Object {
            $rel = Join-Path 'config' $_.Name
            $bak = Join-Path $backup $rel
            $dest = Join-Path $Target $rel
            if (Test-Path $bak) {
                Copy-Item -Force $bak $dest
            } elseif (Test-Path $dest) {
                Remove-Item -Force $dest
            }
        }
    $dockerfileBak = Join-Path $backup 'Dockerfile.astrbot'
    if (Test-Path $dockerfileBak) {
        Copy-Item -Force $dockerfileBak $dockerfile
    }
    Write-Warn "已恢复注入前的文件：$backup"
    Start-AstrBot
    if (Wait-Health) {
        Write-Warn '回滚后 AstrBot 已恢复，请查看日志排查注入失败原因。'
    } else {
        Write-Err '回滚后仍未恢复，请查看 docker compose logs astrbot。'
    }
} else {
    Write-Err '没有可用的备份目录，请手动恢复。'
}
exit 1
