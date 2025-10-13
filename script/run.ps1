# 強制使用 UTF-8（避免中文亂碼）
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 防止 Windows 進入睡眠（不影響螢幕）
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PowerControl {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@ | Out-Null
# 用位移產生 0x80000000，再轉 UInt32；其餘旗標也顯式 UInt32
$ES_CONTINUOUS      = [uint32]([long]1 -shl 31)  # 0x80000000
$ES_SYSTEM_REQUIRED = [uint32]1                  # 0x00000001
[PowerControl]::SetThreadExecutionState([uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)) | Out-Null
Write-Host "已設定：防止自動睡眠（不含螢幕）。" -ForegroundColor Green

# === 導航路徑設定 ===
$scriptPath = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$projectRootPath = Split-Path -Path $scriptPath -Parent

# 切到 script 資料夾；UserDefinedToolkit 模組已經驗證過
Set-Location -Path $scriptPath
if (Test-Path .\UserDefinedToolkit) {
    Import-Module -Name .\UserDefinedToolkit -ErrorAction Stop
}
else {
    Write-Host "UserDefinedToolkit 載入失敗。" -ForegroundColor Red
    Exit 1
}

# ===（可選）確保以系統管理員身份執行 ===
# If (-Not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
#     Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
#     Write-Host "以管理員身份重新執行中..." -ForegroundColor Yellow
#     Exit
# }
# Write-Host "已以管理員身份執行。" -ForegroundColor Green

# === 切換回專案根目錄 ===
Set-Location -Path $projectRootPath

# === 路徑/檔案 ===
$venvPath       = "venv"
$activatePath   = Join-Path $venvPath "Scripts\Activate.ps1"
$pythonPath     = Join-Path $venvPath "Scripts\python.exe"
$logDir         = Join-Path $projectRootPath "log"
$pidFile        = Join-Path $logDir "server.pid"
$stdoutLogFile  = Join-Path $logDir "core_stdout.log"
$stderrLogFile  = Join-Path $logDir "core_stderr.log"

# 建立 log 目錄
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# === 虛擬環境建立與套件安裝 ===
if (Test-Path -Path $venvPath) {
    Write-Host "虛擬環境已存在，略過建立。"
    & $activatePath
} else {
    Write-Host "建立虛擬環境..."
    python -m venv $venvPath

    Write-Host "進入虛擬環境..."
    & $activatePath

    Write-Host "升級 pip / setuptools / wheel..."
    & $pythonPath -m pip install --upgrade pip setuptools wheel

    if (Test-Path ".\requirements.txt") {
        Write-Host "安裝 requirements.txt 中的套件..."
        & $pythonPath -m pip install --no-cache-dir -r requirements.txt
    } else {
        Write-Host "⚠ 找不到 requirements.txt，略過套件安裝。" -ForegroundColor Yellow
    }
}

# === 若已在跑 → 先嘗試關閉舊實例 ===
if (Test-Path $pidFile) {
    $oldPid = (Get-Content $pidFile) -as [int]
    if ($oldPid) {
        try {
            $p = Get-Process -Id $oldPid -ErrorAction Stop
            Write-Host "偵測到舊實例 PID=$oldPid，嘗試結束中..."
            Stop-ProcessTree -ProcessId $oldPid
            Start-Sleep -Seconds 1
            Write-Host "舊實例已結束。"
        } catch {
            Write-Host "pid 檔存在但程序不在：$oldPid。清理 pid 檔。" -ForegroundColor DarkGray
        }
    }
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

# === 背景啟動 SpyPrice API（uvicorn）背景常駐程序 ===
Write-Host "啟動 SpyPrice API（uvicorn）背景常駐程序..."

# 以 venv 的 python 執行 uvicorn：
# - 使用 -u（非緩衝輸出）讓 stdout/err 即時落檔
# - 使用 -m uvicorn 確保載入 venv 內模組
$uvicornArgs = @(
    "-u", "-m", "uvicorn",
    "backend.app.main:app",
    "--host", "localhost",
    "--port", "8000",
    "--log-level", "debug"
)

$proc = Start-Process -FilePath $pythonPath `
    -ArgumentList $uvicornArgs `
    -WorkingDirectory $projectRootPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLogFile `
    -RedirectStandardError  $stderrLogFile `
    -PassThru

Start-Sleep -Seconds 2

# 驗證是否仍在執行
try {
    $alive = Get-Process -Id $proc.Id -ErrorAction Stop
    # 記錄 PID
    Set-Content -Path $pidFile -Value $proc.Id -Encoding UTF8
    Write-Host "已啟動。PID=$($proc.Id)" -ForegroundColor Green
    Write-Host "日誌：`n  - $stdoutLogFile`n  - $stderrLogFile" -ForegroundColor DarkGray
} catch {
    Write-Host "啟動失敗，請查看日誌：" -ForegroundColor Red
    Write-Host "  - $stdoutLogFile`n  - $stderrLogFile"
    Exit 1
}

# 啟動成功後自動開啟瀏覽器
Start-Process "http://localhost:8000"

pause
