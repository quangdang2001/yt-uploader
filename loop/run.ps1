# Chay moi 2 gio bang Task Scheduler tren may chu kenh: keo du lieu roi phan tich.
# studio_folder_id nam trong loop/config.json. Khong truyen --channel: may nay chi thay studio/,
# che do theo ten se tao folder rong sai.
# Buoc nao thoat khac 0: loop.notify_fail gui mot Telegram (toi da mot lan moi ngay moi buoc + ma),
# roi script thoat voi dung ma do. Huong dan: docs/loop-pc.md.
Set-Location (Split-Path -Parent $PSScriptRoot)
$config = Join-Path $PSScriptRoot "config.json"
if (-not (Test-Path $config)) {
    Write-Error "Thieu loop/config.json (chep tu config.example.json va dat studio_folder_id)."
    python -m loop.notify_fail config 2
    exit 2
}

python -m loop.fetch --config $config
$code = $LASTEXITCODE
if ($code -ne 0) {
    python -m loop.notify_fail fetch $code --config $config
    exit $code
}

python -m loop.analyse --config $config
$code = $LASTEXITCODE
if ($code -ne 0) {
    python -m loop.notify_fail analyse $code --config $config
    exit $code
}

python -m loop.ledger --config $config
$code = $LASTEXITCODE
if ($code -ne 0) {
    python -m loop.notify_fail ledger $code --config $config
    exit $code
}
exit 0
