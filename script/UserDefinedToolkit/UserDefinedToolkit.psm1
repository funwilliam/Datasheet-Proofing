# 設定編碼為UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$moduleName = 'UserDefinedToolkit'
Write-Host "模組 $($moduleName) 已設定編碼為UTF8" -ForegroundColor DarkGray

# 定義遞歸函數以停止進程及其子進程
function Stop-ProcessTree {
    param([int]$ProcessId)

    if ($ProcessId -in 0,4) {  # 跳過系統/Idle
        Write-Host "跳過 PID=$ProcessId" -ForegroundColor DarkGray
        return
    }

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Host "進程不存在或已結束：PID=$ProcessId" -ForegroundColor DarkGray
        return
    }

    Write-Host ("找到進程: {0} (PID: {1}, Parent PID: {2})" -f $proc.Name, $ProcessId, $proc.ParentProcessId) -ForegroundColor DarkGray

    # 以 WMI Filter 尋找子進程，避免全表 Where-Object
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($children) {
        $ids = ($children | Select-Object -ExpandProperty ProcessId)
        Write-Host "進程 PID=$ProcessId 有子進程: $($ids -join ', ')" -ForegroundColor DarkGray
        foreach ($c in $children) { Stop-ProcessTree -ProcessId $c.ProcessId }
    } else {
        Write-Host "進程 PID=$ProcessId 沒有子進程" -ForegroundColor DarkGray
    }

    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Write-Host "成功: 已終止 PID=$ProcessId" -ForegroundColor Green
    } catch {
        Write-Host "失敗: 無法停止 PID=$ProcessId。$($_.Exception.Message)" -ForegroundColor Red
    }
}

# 添加防火牆規則的函數
function Add-FirewallRule {
    param(
        [string]$ruleName,
        [int]$port,
        [ValidateSet("TCP","UDP")][string]$protocol,
        [string]$profile = "Domain,Private",
        [string]$remoteIp = "LocalSubnet"
    )
    try {
        $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "防火牆規則已存在: $ruleName" -ForegroundColor Yellow
            return
        }
        New-NetFirewallRule -DisplayName $ruleName `
            -Direction Inbound -Action Allow `
            -Protocol $protocol -LocalPort $port `
            -Profile $profile -RemoteAddress $remoteIp | Out-Null
        Write-Host "防火牆規則已添加: $ruleName (Port=$port, Protocol=$protocol, Profile=$profile, Remote=$remoteIp)" -ForegroundColor Green
    } catch {
        Write-Host "錯誤：無法添加規則 $ruleName。$($_.Exception.Message)" -ForegroundColor Red
    }
}

# 定義刪除防火牆規則的函數
function Remove-FirewallRule {
    param([string]$ruleName)
    try {
        $rules = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        if ($rules) {
            $rules | Remove-NetFirewallRule
            Write-Host "防火牆規則已刪除: $ruleName" -ForegroundColor Green
        } else {
            Write-Host "防火牆規則不存在: $ruleName" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "錯誤：無法刪除規則 $ruleName。$($_.Exception.Message)" -ForegroundColor Red
    }
}