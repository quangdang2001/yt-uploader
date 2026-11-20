# Chay moi 2 gio bang Task Scheduler tren may chu kenh: keo du lieu roi phan tich.
# studio_folder_id nam trong loop/config.json; khi co, hai buoc mo studio/ theo ID.
Set-Location (Split-Path -Parent $PSScriptRoot)
$config = Join-Path $PSScriptRoot "config.json"

python -m loop.fetch --channel "Bare Skull" --config $config
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m loop.analyse --channel "Bare Skull" --config $config
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit 0
