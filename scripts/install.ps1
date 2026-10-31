#Requires -Version 5.1
<#
大肥鱼 QQ 机器人 · Windows 一键安装脚本

用法（在项目根目录打开 PowerShell）：
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1

前置条件：
  - Windows 10/11
  - Docker Desktop（WSL2 后端）已安装并启动
  - 建议 8GB 以上内存

脚本会：
  1. 检查 Docker Desktop 和目录权限
  2. 创建 data\ 等运行目录
  3. 生成 .env（没改 VNC 占位密码时自动生成随机密码）
  4. 复制表情包和 SnowLuma OneBot 配置
  5. 构建并启动 AstrBot + SnowLuma
  6. 把初始账号密码保存到 data\INITIAL_CREDENTIALS.txt
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ComposeFile = Join-Path $Root 'docker-compose.windows.yml'
$CredFile = Join-Path $Root 'data\INITIAL_CREDENTIALS.txt'

function Write-Info([string]$Message) {
    Write-Host "[信息] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[提示] $Message" -ForegroundColor Yellow
}

function Write-Err([string]$Message) {
    Write-Host "[错误] $Message" -ForegroundColor Red
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
    Write-Warn "请把项目放到有写权限的目录，或右键 PowerShell 选择“以管理员身份运行”。"
    exit 1
}

if (-not (Test-Path $ComposeFile)) {
    Write-Err "没有找到 docker-compose.windows.yml，请在项目根目录运行。"
    exit 1
}

# ---------------------------------------------------------------- Docker 检查
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "没有找到 docker 命令。"
    Write-Warn "请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/"
    exit 1
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Err "无法连接 Docker Desktop。"
    Write-Warn "请先启动 Docker Desktop，等右下角鲸鱼图标变绿后再试。"
    Write-Warn "如果仍然失败：Settings → Resources → WSL Integration 打开 Ubuntu。"
    Write-Warn "或者把当前用户加入 docker-users 组（需要管理员）："
    Write-Warn "  net localgroup docker-users `$env:USERNAME /add"
    exit 1
}

& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Err "没有找到 docker compose 插件，请更新 Docker Desktop。"
    exit 1
}

# ---------------------------------------------------------------- 1. 目录
Write-Info "第 1 步：创建运行目录"
$directories = @(
    'data\config',
    'data\plugin_data',
    'data\logs',
    'data\temp',
    'data\attachments',
    'data\snowluma\config',
    'data\snowluma-qq-config',
    'data\snowluma-qq-data'
)
foreach ($dir in $directories) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $dir) | Out-Null
}
Write-Info "已创建 data\ 目录"

# 表情包素材只在第一次安装时复制。
$stickerTarget = Join-Path $Root 'data\plugin_data\astrbot_plugin_dafeiyu_social\stickers'
New-Item -ItemType Directory -Force -Path $stickerTarget | Out-Null
$stickerSource = Join-Path $Root 'assets\stickers\dafeiyu'
if (-not (Test-Path (Join-Path $stickerTarget 'dafeiyu'))) {
    Copy-Item -Recurse -Force $stickerSource (Join-Path $stickerTarget 'dafeiyu')
    Write-Info "已复制表情包素材"
}

# ---------------------------------------------------------------- 2. .env
Write-Info "第 2 步：准备 .env 配置"
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root '.env.example') $EnvFile
    Write-Info "已生成 .env"
}

$envText = [System.IO.File]::ReadAllText($EnvFile)
if ($envText -match '(?m)^VNC_PASSWORD=change-this-vnc-password\s*$') {
    $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.ToCharArray()
    $vncGenerated = -join (1..16 | ForEach-Object { $chars | Get-Random })
    $envText = [regex]::Replace($envText, '(?m)^VNC_PASSWORD=.*$', "VNC_PASSWORD=$vncGenerated")
    [System.IO.File]::WriteAllText($EnvFile, $envText, (New-Object System.Text.UTF8Encoding($false)))
    Write-Info "已自动生成 noVNC 密码并写入 .env"
}
$vncPassword = ''
if ($envText -match '(?m)^VNC_PASSWORD=(.*)$') {
    $vncPassword = $Matches[1].Trim()
}

# ---------------------------------------------------------------- 3. OneBot
Write-Info "第 3 步：准备 SnowLuma OneBot 配置"
$onebotFile = Join-Path $Root 'data\snowluma\config\onebot.json'
if (-not (Test-Path $onebotFile)) {
    Copy-Item (Join-Path $Root 'config\snowluma-onebot.example.json') $onebotFile
    Write-Info "已生成 data\snowluma\config\onebot.json"
    Write-Warn "里面的 accessToken 还是占位值，需要和 AstrBot 平台 Token 保持一致。"
}

# ---------------------------------------------------------------- 4. 构建
Write-Info "第 4 步：构建镜像（第一次会比较慢，请耐心等待）"
Invoke-Compose build

Write-Info "第 5 步：启动容器"
Invoke-Compose up -d

# ---------------------------------------------------------------- 5. 初始账号
function Get-ComposeLog {
    param([string]$Service)
    $raw = & docker compose -f $ComposeFile logs --no-color --no-log-prefix $Service 2>&1 | Out-String
    $esc = [char]27
    return ($raw -replace "$esc\[[0-9;]*[mK]", "")
}

$abUser = ''
$abPass = ''
$slUser = ''
$slPass = ''

Write-Info "正在等待面板启动并收集初始账号密码（最多 2 分钟）"
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if (-not $abPass) {
        $log = Get-ComposeLog 'astrbot'
        if ($log -match 'Initial username:\s*(\S+)') { $abUser = $Matches[1] }
        if ($log -match 'Initial password:\s*(\S+)') { $abPass = $Matches[1] }
    }
    if (-not $slPass) {
        $log = Get-ComposeLog 'snowluma'
        if ($log -match 'initial credentials:\s*user=(\S+)\s+password=(\S+)') {
            $slUser = $Matches[1]
            $slPass = $Matches[2]
        }
    }
    if ($abPass -and $slPass) { break }
    Start-Sleep -Seconds 3
}

if (-not $abUser) { $abUser = 'astrbot' }
if (-not $slUser) { $slUser = 'admin' }
if (-not $abPass) { $abPass = '未捕获' }
if (-not $slPass) { $slPass = '未捕获' }
if (-not $vncPassword) { $vncPassword = '见 .env 的 VNC_PASSWORD' }

if (Test-Path $CredFile) {
    Write-Warn "已存在初始登录文件，保留原内容：$CredFile"
} else {
    $credText = @"
# 大肥鱼 QQ 机器人 · 初始登录信息
# 生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
# 登录后请立即修改密码，并删除本文件。

[AstrBot 面板]
地址：http://127.0.0.1:6185
用户名：$abUser
密码：$abPass

[SnowLuma 面板]
地址：http://127.0.0.1:5099
用户名：$slUser
密码：$slPass

[noVNC 远程桌面]
地址：http://127.0.0.1:6081/vnc.html?autoconnect=1
密码：$vncPassword

[绑定主人]
1. 机器人账号请使用 QQ 小号登录
2. 用你的大号先加机器人小号为好友
3. 用大号私聊机器人，发送：/认主 你的QQ
4. 设置换主密码：/认主 密码 你的密码
5. 绑定后 AstrBot 会自动重启一次

[下一步]
1. 登录 AstrBot，配置模型 Provider 和 OneBot 平台
2. 登录 SnowLuma，确认 QQ 进程在线
3. 打开 noVNC，用手机 QQ 扫码登录
4. 让 AstrBot 平台 Token 与 SnowLuma 的 accessToken 保持一致

[忘记密码时]
AstrBot：
  docker exec -it astrbot astrbot conf set dashboard.password "新密码"
  docker compose -f docker-compose.windows.yml restart astrbot
SnowLuma：
  docker compose -f docker-compose.windows.yml stop snowluma
  Rename-Item data\snowluma\config\webui.json webui.json.bak
  docker compose -f docker-compose.windows.yml start snowluma
  docker compose -f docker-compose.windows.yml logs snowluma | Select-String "initial credentials"

[说明]
端口只绑定 127.0.0.1，本机访问即可。
电脑睡眠或关机后机器人会下线。
"@
    [System.IO.File]::WriteAllText($CredFile, $credText, (New-Object System.Text.UTF8Encoding($false)))
    try {
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $acl = Get-Acl -Path $CredFile
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($me, 'FullControl', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl -Path $CredFile -AclObject $acl
    } catch {
        Write-Warn "无法收紧初始密码文件权限，请登录后尽快删除该文件。"
    }
}

# ---------------------------------------------------------------- 6. 完成
Write-Host ''
Write-Info "安装完成"
Write-Host "  AstrBot 面板：http://127.0.0.1:6185"
Write-Host "  SnowLuma 面板：http://127.0.0.1:5099"
Write-Host "  noVNC 远程桌面：http://127.0.0.1:6081/vnc.html?autoconnect=1"
Write-Host ''
Write-Warn "初始账号密码已保存到：data\INITIAL_CREDENTIALS.txt"
Write-Warn "登录后请立即修改两个面板的密码，并删除这个文件。"
Write-Host ''
Write-Host "首次使用顺序："
Write-Host "  1. AstrBot 登录 → 配置模型 Provider"
Write-Host "  2. SnowLuma 登录 → 确认 QQ 进程"
Write-Host "  3. noVNC 扫码登录 QQ（建议用小号）"
Write-Host "  4. AstrBot 新建 OneBot v11 平台，Token 与 SnowLuma 一致"
Write-Host "  5. 用大号私聊机器人，发送 /认主 你的QQ"
Write-Host "  6. 设置换主密码：/认主 密码 你的密码"
Write-Host ''
Write-Host "机器人账号建议使用 QQ 小号；大号只用来认主和聊天。"
Write-Host ''
Write-Host "更新：powershell -ExecutionPolicy Bypass -File scripts\update.ps1"
Write-Host "卸载：powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1"
Write-Host ''
Write-Host "遇到问题可以让 AI 协助：把报错和系统环境发给它，不要发真实密钥。"
