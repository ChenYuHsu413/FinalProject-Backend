<#
.SYNOPSIS
    Stand up a throwaway local PostgreSQL 16 for real-DB verification (DECISIONS D2.9).

.DESCRIPTION
    Downloads the official EnterpriseDB portable PostgreSQL 16 binaries (no
    installer, no admin/UAC), initdb's a trust-auth cluster under .localpg/
    (gitignored), and starts it on a non-default port so it never clashes with a
    system Postgres. Prints the DATABASE_URL to export.

    This exists because batch 2 shipped a migration that had never run against a
    real Postgres. "Local verification" now REQUIRES a real-PG `alembic upgrade
    head` + full pytest; this script makes that one command on Windows without
    Docker or admin rights.

.EXAMPLE
    pwsh scripts/local_pg.ps1            # download (first run), init, start
    pwsh scripts/local_pg.ps1 -Stop      # stop the cluster
#>
param(
    # 15432 sits outside the Hyper-V/WSL reserved TCP exclusion ranges that make
    # 5432/5433 fail to bind with "Permission denied". If bind still fails, check
    #   netsh int ipv4 show excludedportrange protocol=tcp
    # and pass a free -Port.
    [int]$Port = 15432,
    [string]$Db = "aiservo_test",
    [string]$Version = "16.6-1",
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$work = Join-Path $root ".localpg"
$pgdir = Join-Path $work "pgsql"           # EDB zip extracts to pgsql/
$bin = Join-Path $pgdir "bin"
$data = Join-Path $work "data"
$log = Join-Path $work "pg.log"

if ($Stop) {
    & "$bin\pg_ctl.exe" -D $data stop -m fast
    return
}

New-Item -ItemType Directory -Force $work | Out-Null

# 1. Download + extract portable binaries (once).
if (-not (Test-Path (Join-Path $bin "pg_ctl.exe"))) {
    $zip = Join-Path $work "pg16.zip"
    $url = "https://get.enterprisedb.com/postgresql/postgresql-$Version-windows-x64-binaries.zip"
    if (-not (Test-Path $zip)) {
        Write-Host "Downloading PostgreSQL $Version binaries..."
        Invoke-WebRequest -Uri $url -OutFile $zip
    }
    Write-Host "Extracting..."
    Expand-Archive -Path $zip -DestinationPath $work -Force
}

# 2. initdb a trust-auth cluster (once). Trust auth = no password for localhost;
#    the superuser 'postgres' is needed by the integration tests (ALTER TABLE
#    DISABLE TRIGGER during cleanup).
if (-not (Test-Path (Join-Path $data "PG_VERSION"))) {
    Write-Host "initdb..."
    & "$bin\initdb.exe" -D $data -U postgres --auth=trust -E UTF8 | Out-Null
}

# 3. Start (idempotent) and ensure the test database exists.
& "$bin\pg_ctl.exe" -D $data -o "-p $Port" -l $log -w start
& "$bin\createdb.exe" -p $Port -U postgres $Db 2>$null

Write-Host ""
Write-Host "PostgreSQL up on port $Port."
Write-Host "  DATABASE_URL=postgresql+asyncpg://postgres@localhost:$Port/$Db"
Write-Host "Stop with: pwsh scripts/local_pg.ps1 -Stop"
