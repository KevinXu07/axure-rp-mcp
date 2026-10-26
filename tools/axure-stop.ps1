# axure-stop.ps1 - stop the Axure instance that owns the reflection bridge.
#
# Only kills the pid recorded in the bridge discovery file (or the pid file written by
# axure-hook.ps1). Your own unrelated Axure windows are left alone.
param([switch]$All, [switch]$List)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$discovery = Join-Path $env:LOCALAPPDATA 'Axure\axure-bridge.json'
$pidFile = Join-Path $root 'bridge\.test-instance.pid'

$targets = @()
if (Test-Path $discovery) {
    $info = Get-Content $discovery -Raw | ConvertFrom-Json
    if ($info.pid) { $targets += [int]$info.pid }
}
if (Test-Path $pidFile) {
    $line = (Get-Content $pidFile | Select-Object -First 1)
    if ($line) { $targets += [int]$line }
}
$targets = $targets | Sort-Object -Unique

if ($List -or $All) {
    Get-Process | Where-Object { $_.ProcessName -like '*Axure*' } |
        Select-Object Id, StartTime, MainWindowTitle | Format-Table -AutoSize
}

if ($List) { return }

if ($All) {
    # dev convenience: every Axure except the very first (usually the user's own window)
    $procs = Get-Process | Where-Object { $_.ProcessName -like '*Axure*' } | Sort-Object StartTime
    $targets = $procs | Select-Object -Skip 1 -ExpandProperty Id
}

foreach ($procId in $targets) {
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) { Write-Output "stopping Axure pid=$procId"; Stop-Process -Id $procId -Force }
}
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800
Get-Process | Where-Object { $_.ProcessName -like '*Axure*' } |
    Select-Object Id, StartTime, MainWindowTitle | Format-Table -AutoSize
