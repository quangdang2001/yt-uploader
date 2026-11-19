# Chay moi 2 gio bang Task Scheduler tren may chu kenh: keo du lieu roi phan tich.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m loop.fetch --channel "Bare Skull"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m loop.analyse --channel "Bare Skull"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit 0
