# Run from any directory. Stop the owned process with stop-api.ps1.
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $workspace '.venv\Scripts\python.exe'
$serverPath = Join-Path $workspace 'serve.py'
$logDirectory = Join-Path $workspace 'logs'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先安装 .venv 和 requirements.txt' }
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$pidPath = Join-Path $logDirectory 'api-processes.json'
if (Test-Path -LiteralPath $pidPath) {
    $record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
    foreach ($processId in $record.process_ids) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        if ($existing -and $existing.CommandLine.Contains($serverPath)) {
            throw '本脚本启动的服务已经运行，先执行 stop-api.ps1'
        }
    }
}
$process = Start-Process -FilePath $pythonPath -ArgumentList @('-X', 'utf8', ('"' + $serverPath + '"')) `
    -WorkingDirectory $workspace -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDirectory 'server.stdout.log') `
    -RedirectStandardError (Join-Path $logDirectory 'server.stderr.log')
# Windows venv launchers can create a child interpreter.
Start-Sleep -Seconds 2
$children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($serverPath) })
$processIds = @($process.Id) + @($children | ForEach-Object { $_.ProcessId })
@{ process_ids = $processIds; script = $serverPath } | ConvertTo-Json | Set-Content -LiteralPath $pidPath
Write-Output "API 已启动，进程: $($processIds -join ', ')；请检查 /health 和 logs/cook_rag.jsonl"
