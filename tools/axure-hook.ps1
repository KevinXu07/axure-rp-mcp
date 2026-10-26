# axure-hook.ps1 - build the injection bridge and launch a hooked Axure instance for testing.
#
# The user's own Axure window is never touched: we only ever stop the instance we started
# (tracked by pid in $PidFile).
param(
    [switch]$Kill,
    [switch]$NoBuild,
    [int]$Port = 41999,
    [int]$WaitSeconds = 20,
    [string]$OpenFile
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$exe = 'D:\Program Files (x86)\Axure\Axure RP 11\AxureRP11.exe'
$proj = Join-Path $root 'bridge\AxureBridge.csproj'
$dll = Join-Path $root 'bridge\bin\Debug\net10.0\AxureBridge.dll'
$pidFile = Join-Path $root 'bridge\.test-instance.pid'
$log = Join-Path $env:TEMP 'axure-bridge.log'

function Stop-TestInstance {
    if (Test-Path $pidFile) {
        $oldPid = Get-Content $pidFile | Select-Object -First 1
        if ($oldPid) {
            $p = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
            if ($p) { Write-Output "stopping test instance pid=$oldPid"; Stop-Process -Id $p.Id -Force; Start-Sleep -Milliseconds 800 }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

Stop-TestInstance
if ($Kill) { return }

if (-not $NoBuild) {
    & 'C:\Program Files\dotnet\dotnet.exe' build $proj -v q --nologo 2>&1 | Select-String 'error|已成功' | Select-Object -First 6
    if (-not (Test-Path $dll)) { throw "build did not produce $dll" }
}

Remove-Item $log -Force -ErrorAction SilentlyContinue
$env:DOTNET_STARTUP_HOOKS = $dll
$env:AXURE_BRIDGE_PORT = "$Port"

$args = @()
if ($OpenFile) { $args = @($OpenFile) }
$p = Start-Process -FilePath $exe -ArgumentList $args -PassThru
$p.Id | Set-Content $pidFile
Write-Output "launched pid=$($p.Id) hook=$dll port=$Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $live = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($live) {
        Write-Output "bridge listening on :$Port (pid $($live[0].OwningProcess))"
        Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 8
        return
    }
    if (-not (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)) { Write-Output "process exited early"; break }
}

Write-Output "=== no listener after $WaitSeconds s; log tail ==="
Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 20
