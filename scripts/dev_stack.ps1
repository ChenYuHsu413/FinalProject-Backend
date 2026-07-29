<#
.SYNOPSIS
    One-key DEV/DEMO stack: test PostgreSQL + migrated schema + backend API with a
    local service token. FOR DEVELOPMENT, REHEARSAL AND DEMO ONLY — NOT A
    DEPLOYMENT. Real deployment is docs/DEPLOYMENT.md + deploy/.

.DESCRIPTION
    Wraps the pieces used to do a *real* end-to-end verification on a dev box into
    a single start/stop:

      1. brings up the throwaway local Postgres (scripts/local_pg.ps1),
      2. generates (once) a random service token under .localdev/ (gitignored),
      3. runs `alembic upgrade head` against that DB,
      4. starts `uvicorn app.main:app` in the background with SERVICE_TOKEN +
         DATABASE_URL set, and waits for /api/v1/health,
      5. prints the API base + token so you can drive the governance API by hand.

    The token, uvicorn pid and log live in .localdev/ and never enter git. The
    token is a LOCAL DEV SECRET only — it is not the production service token.

    Idempotent: re-running -Start reuses the existing token and DB and restarts
    uvicorn. Tear everything down with -Stop.

.EXAMPLE
    pwsh scripts/dev_stack.ps1            # start pg + migrate + start API
    pwsh scripts/dev_stack.ps1 -Stop      # stop API (and the local Postgres)
    pwsh scripts/dev_stack.ps1 -Stop -KeepDb   # stop API, leave Postgres running
#>
param(
    [int]$Port = 8000,          # API port
    [int]$PgPort = 15432,       # must match scripts/local_pg.ps1 default
    [string]$Db = "aiservo_test",
    [switch]$Stop,
    [switch]$KeepDb             # with -Stop: leave the local Postgres up
)

$ErrorActionPreference = "Stop"
$root       = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPy     = Join-Path $root ".venv\Scripts\python.exe"
$pgScript   = Join-Path $PSScriptRoot "local_pg.ps1"
$devDir     = Join-Path $root ".localdev"
$tokenFile  = Join-Path $devDir "service_token.txt"
$pidFile    = Join-Path $devDir "uvicorn.pid"
$logFile    = Join-Path $devDir "uvicorn.log"
$apiBase    = "http://127.0.0.1:$Port/api/v1"
$databaseUrl = "postgresql+asyncpg://postgres@localhost:$PgPort/$Db"

function Stop-Api {
    if (Test-Path $pidFile) {
        $procId = (Get-Content $pidFile -Raw).Trim()
        if ($procId) {
            try { Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop; Write-Host "uvicorn (pid $procId) stopped." }
            catch { Write-Host "uvicorn pid $procId not running." }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "No uvicorn pid file; nothing to stop."
    }
}

# ---- Stop path -------------------------------------------------------------
if ($Stop) {
    Stop-Api
    if ($KeepDb) {
        Write-Host "Left local Postgres running (--KeepDb)."
    } else {
        & pwsh -NoProfile -File $pgScript -Stop -Port $PgPort
    }
    return
}

# ---- Start path ------------------------------------------------------------
if (-not (Test-Path $venvPy)) { throw "venv python not found at $venvPy — create the .venv first." }
New-Item -ItemType Directory -Force $devDir | Out-Null

# 1. Local Postgres (idempotent — downloads/inits on first ever run).
Write-Host "==> Local Postgres on port $PgPort ..."
& pwsh -NoProfile -File $pgScript -Port $PgPort -Db $Db

# 2. Service token (generate once, then reuse). LOCAL DEV SECRET — gitignored.
if (-not (Test-Path $tokenFile)) {
    $tok = -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
    Set-Content -Path $tokenFile -Value $tok -NoNewline
    Write-Host "==> Generated new dev service token -> $tokenFile"
} else {
    $tok = (Get-Content $tokenFile -Raw).Trim()
    Write-Host "==> Reusing dev service token from $tokenFile"
}

# Environment for both alembic and uvicorn (this process + the child).
$env:DATABASE_URL  = $databaseUrl
$env:SERVICE_TOKEN = $tok
$env:APP_ENV       = "dev"
$env:MOCK_MODE     = "true"

# 3. Migrate.
Write-Host "==> alembic upgrade head ..."
& $venvPy -m alembic upgrade head | Out-Null

# 4. (Re)start uvicorn in the background.
Stop-Api  # kill a previous instance if any
Write-Host "==> Starting uvicorn on port $Port ..."
$p = Start-Process -FilePath $venvPy `
    -ArgumentList @("-m","uvicorn","app.main:app","--host","127.0.0.1","--port","$Port","--log-level","warning") `
    -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err" `
    -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $p.Id -NoNewline

# 5. Wait for health.
$up = $false
for ($i = 0; $i -lt 25; $i++) {
    try { Invoke-RestMethod "$apiBase/health" -TimeoutSec 2 | Out-Null; $up = $true; break }
    catch { Start-Sleep -Milliseconds 700 }
}

Write-Host ""
if ($up) {
    Write-Host "DEV/DEMO stack up (NOT a deployment)."
    Write-Host "  API base     : $apiBase"
    Write-Host "  DATABASE_URL : $databaseUrl"
    Write-Host "  Service token: (in $tokenFile)"
    Write-Host ""
    Write-Host "Drive it, e.g.:"
    Write-Host '  $env:API = "' + $apiBase + '"'
    Write-Host '  $tok = Get-Content "' + $tokenFile + '" -Raw'
    Write-Host '  $h = @{ Authorization="Bearer $tok"; "X-User-ID"="svc-test"; "X-User-Role"="engineer"; "X-Correlation-ID"=[guid]::NewGuid().ToString() }'
    Write-Host '  (Invoke-RestMethod "$env:API/alarms" -Headers $h).alarms   # note: key is .alarms, not .items'
    Write-Host ""
    Write-Host "Stop with: pwsh scripts/dev_stack.ps1 -Stop"
} else {
    Write-Host "API did not become healthy in time. Last log lines:"
    if (Test-Path $logFile) { Get-Content $logFile -Tail 20 }
    if (Test-Path "$logFile.err") { Get-Content "$logFile.err" -Tail 20 }
    exit 1
}
