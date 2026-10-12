$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$serverPath = Join-Path $workspace 'serve.py'
$pidPath = Join-Path $workspace 'logs\api-processes.json'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Output '没有本脚本启动的进程记录'; return }
$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
if ($record.script -ne $serverPath) { throw '进程记录不属于当前项目' }
foreach ($processId in $record.process_ids) {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if ($existing -and $existing.CommandLine -and $existing.CommandLine.Contains($serverPath)) {
        Stop-Process -Id $processId
    }
}
Remove-Item -LiteralPath $pidPath
Write-Output 'API 已停止；内存会话已清空'
