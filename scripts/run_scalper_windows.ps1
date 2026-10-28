<#
  Native Windows supervisor for the XAUUSD scalper (24/7 VPS).

  Mirrors scripts/run_scalper_demo_supervised.sh (Linux/Wine) for native
  Windows + native MetaTrader 5. It does NOT change the strategy or place
  orders; it only launches and restarts the existing Python runner.

  Safety:
    * The Python runner holds a single-instance lock (scalper.process_guard),
      so a Task Scheduler double-fire or reboot cannot start a second bot.
    * Relaunch has capped exponential backoff; a top-level STOP sentinel exits.
    * Demo-account verification / fail-closed logic live in the runner.

  Config via environment (set in the Task or a .env — NEVER commit secrets):
    SCALPER_PYTHON       full path to the venv python.exe
    SCALPER_MT5_PATH     full path to terminal64.exe (configurable terminal)
    SCALPER_PROJECT_DIR  repo root (contains python\, scripts\)
    SCALPER_TELEGRAM_BOT_TOKEN / SCALPER_TELEGRAM_CHAT_ID  (optional)

  Stop:  New-Item "<ProjectDir>\data\scalper-demo\SUPERVISOR_STOP"
#>
$ErrorActionPreference = "Stop"

$ProjectDir = if ($env:SCALPER_PROJECT_DIR) { $env:SCALPER_PROJECT_DIR } else { Split-Path -Parent $PSScriptRoot }
$Python     = if ($env:SCALPER_PYTHON) { $env:SCALPER_PYTHON } else { Join-Path $ProjectDir ".venv\Scripts\python.exe" }
$Runner     = Join-Path $ProjectDir "scripts\run_scalper_demo.py"
$LogDir     = Join-Path $ProjectDir "logs"
$StopFile   = Join-Path $ProjectDir "data\scalper-demo\SUPERVISOR_STOP"
$StaleBarExit = 42   # runner exit code requesting a full restart (frozen bar builder)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $StopFile) | Out-Null
if (Test-Path $StopFile) { Remove-Item $StopFile -Force }

if (-not (Test-Path $Python)) { throw "Python venv not found at $Python (set SCALPER_PYTHON)" }
if ($env:SCALPER_MT5_PATH -and -not (Test-Path $env:SCALPER_MT5_PATH)) {
    throw "MT5 terminal not found at $env:SCALPER_MT5_PATH (set SCALPER_MT5_PATH)"
}

$attempt = 0
while ($true) {
    if (Test-Path $StopFile) {
        "$([DateTime]::UtcNow.ToString('o')) supervisor: STOP sentinel found, exiting" |
            Tee-Object -FilePath (Join-Path $LogDir "scalper_win_supervisor.log") -Append
        break
    }
    $attempt++
    $out = Join-Path $LogDir ("scalper_win_run_{0}.out" -f $attempt)
    "$([DateTime]::UtcNow.ToString('o')) supervisor: launch attempt #$attempt" |
        Tee-Object -FilePath (Join-Path $LogDir "scalper_win_supervisor.log") -Append

    & $Python $Runner *>&1 | Tee-Object -FilePath $out
    $code = $LASTEXITCODE

    if (Test-Path $StopFile) { break }
    if ($code -eq $StaleBarExit) {
        $backoff = 5
    } else {
        $backoff = [Math]::Min(300, 5 * [Math]::Pow(2, [Math]::Min($attempt, 6)))
    }
    "$([DateTime]::UtcNow.ToString('o')) supervisor: runner exited code=$code; restarting in ${backoff}s" |
        Tee-Object -FilePath (Join-Path $LogDir "scalper_win_supervisor.log") -Append
    Start-Sleep -Seconds $backoff
}
