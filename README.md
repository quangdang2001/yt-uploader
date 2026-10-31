# yt-uploader

Tự động upload video lên YouTube: pipeline render → Google Drive → script Python trên VPS → YouTube Data API.

## Thành phần

| File | Chạy ở đâu | Vai trò |
|---|---|---|
| `scripts/push_to_drive.py` | máy render | Đẩy video + thumbnail + metadata lên Drive đúng thứ tự |
| `scripts/youtube_uploader.py` | VPS | Quét `pending/`, upload resumable lên YouTube, chuyển file sang `done/`/`failed/` |

## Tài liệu

- `docs/uploader.md` — triển khai uploader trên VPS
- `docs/setup.md` — cấu hình Google Cloud (§4) và quota/audit (§10)
- `CLAUDE.md` — bối cảnh, các quyết định đã chốt và lý do

## Bắt đầu

```bash
pip install -r scripts/requirements.txt
```

Xem `docs/uploader.md`.

> `docker-compose*.yml` và `workflows/` là phương án n8n cũ, giữ lại để tham khảo, không còn dùng.
