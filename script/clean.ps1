# 強制使用 UTF-8 編碼，避免亂碼
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# === 定位專案根目錄（相對於腳本位置）===
$scriptPath = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$projectRootPath = Split-Path -Path $scriptPath -Parent
Set-Location -Path $projectRootPath

# === 刪除資料夾的通用函式 ===
function Remove-FolderIfExists($path) {
    if (Test-Path $path) {
        try {
            Remove-Item $path -Recurse -Force
            Write-Host "🗑️ 已刪除：$path"
        } catch {
            Write-Warning "⚠️ 無法刪除 $path：$($_.Exception.Message)"
        }
    }
}

# === 要刪除的頂層資料夾 ===
$foldersToDelete = @("venv", "build", "dist", "workspace")

Write-Host "🧹 開始清理專案環境..." -ForegroundColor Cyan

# 清除指定資料夾
$foldersToDelete | ForEach-Object { Remove-FolderIfExists $_ }

# 遞迴刪除所有 __pycache__ 資料夾
Get-ChildItem -Path . -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-FolderIfExists $_.FullName }

Write-Host "✅ 清理完成。" -ForegroundColor Green

pause