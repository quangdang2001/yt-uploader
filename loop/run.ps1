# Chay moi 2 gio bang Task Scheduler tren may chu kenh: keo du lieu roi phan tich.
# studio_folder_id nam trong loop/config.json. Khong truyen --channel: may nay chi thay studio/,
# che do theo ten se tao folder rong sai.
Set-Location (Split-Path -Parent $PSScriptRoot)
$config = Join-Path $PSScriptRoot "config.json"
if (-not (Test-Path $config)) {
    Write-Error "Thieu loop/config.json (chep tu config.example.json va dat studio_folder_id)."
    exit 2
}

python -m loop.fetch --config $config
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m loop.analyse --config $config
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit 0
