# 強制使用 UTF-8（避免中文亂碼）
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# === 恢復系統睡眠（先載入 API，結尾會執行恢復） ===
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PowerControl {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@ | Out-Null
# 先用 long 做位移，再轉 UInt32（避免 0x80000000 被當成 Int32 負數）
$ES_CONTINUOUS = [uint32]([long]1 -shl 31)

# 目錄導航
$scriptPath = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$projectRootPath = Split-Path -Path $scriptPath -Parent

# 載入模組
Set-Location -Path $scriptPath
Import-Module -Name .\UserDefinedToolkit

# 切換到專案根目錄
Set-Location -Path $projectRootPath

# 檢查伺服器是否正在運行，停止運行伺服器
$isRunning = $false
$pidFile = ".\log\server.pid"
if (Test-Path $pidFile) {
    $serverPid = Get-Content $pidFile
    try {
        $process = Get-Process -Id $serverPid -ErrorAction Stop
        if ($process) {
            Write-Host "伺服器主程序的PID: $($serverPid), 正在嘗試關閉..."
            try {
                Stop-ProcessTree -ProcessId $serverPid
                $isRunning = $true
                Remove-Item -Path $pidFile -Force
                Write-Host "伺服器主程序及其子進程已結束。" -ForegroundColor Green
            }
            catch {
                Write-Host "錯誤: 結束伺服器主程序及其子進程時發生錯誤。詳細錯誤信息：$_" -ForegroundColor Red
            }
        }
        else {
            Remove-Item -Path $pidFile -Force
            Write-Host "警告: 雖然檔案 server.pid 存在，但該 PID 不在執行列表，刪除 server.pid。" -ForegroundColor Yellow
        }
    }
    catch {
        Remove-Item -Path $pidFile -Force
        Write-Host "警告: 雖然檔案 server.pid 存在，但該 PID 不在執行列表，刪除 server.pid。" -ForegroundColor Yellow
    }
}
else {
    Write-Host "警告: 沒有找到 PID 文件，伺服器可能未在運行。" -ForegroundColor Yellow
}

# （可選）刪除防火牆規則
# if ($isRunning) {
#     try {
#         Write-Host "正在刪除防火牆規則..."
#         Remove-FirewallRule -ruleName "AllowMinmaxApiServerHTTP"
#         Remove-FirewallRule -ruleName "AllowMinmaxApiServerHTTPS"
#     }
#     catch {
#         Write-Host "刪除防火牆規則時發生錯誤。" -ForegroundColor Red
#     }
# } else {
#     Write-Host "警告: 伺服器未運行or關閉時發生錯誤，因此未執行刪除防火牆規則: AllowMinmaxApiServerHTTP, AllowMinmaxApiServerHTTPS" -ForegroundColor Yellow
# }

# === 恢復系統睡眠設定（冪等、安全） ===
[PowerControl]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null
Write-Host "已恢復系統自動睡眠設定（不影響螢幕邏輯）。" -ForegroundColor DarkGray

Pause
