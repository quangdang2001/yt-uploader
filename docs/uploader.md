# Chạy uploader bằng Python trên VPS

Bản thay thế cho n8n. Không cần Docker, không cần WSL 2, không cần domain hay
Cloudflare Tunnel — nên chạy được cả trên **Windows Server 2016**.

```
Máy render (của bạn)                    VPS
─────────────────────                   ───
push_to_drive.py                        youtube_uploader.py
   │                                       │
   └── Drive: <kênh>/pending/ ────────────►│ quét mỗi vài phút
                                           │ tải .mp4 về
                                           │ videos.insert (resumable)
                                           │ thumbnails.set
                                           └─► chuyển sang done/ hoặc failed/
```

Khác n8n ở hai điểm đáng giá:

- **Resume thật.** Rớt mạng giữa chừng thì upload tiếp từ chỗ đang dở, không
  mất 1600 quota như node YouTube của n8n.
- **Một lệnh API thay vì hai.** `publishAt` và `containsSyntheticMedia` gửi
  thẳng trong `videos.insert`, không cần bước `videos.update` riêng → tiết kiệm
  50 quota mỗi video và bớt một chỗ có thể hỏng.

---

## 1. Chuẩn bị VPS

Cài **Python 3.12 trở lên** từ [python.org](https://www.python.org/downloads/windows/).
Lúc cài nhớ tick **Add python.exe to PATH**.

> Python 3.14 hỗ trợ "Windows 10 and newer", mà Server 2016 dùng chung nhân với
> Windows 10 1607 nên vẫn nằm trong phạm vi. Nếu muốn chắc chắn thì chọn 3.12.

Cài thêm một trình duyệt (Chrome/Edge mới) — bước đăng nhập Google cần nó.

Copy thư mục `scripts/` của repo này lên VPS, ví dụ `C:\yt\scripts\`.

```powershell
cd C:\yt\scripts
pip install -r requirements.txt
```

## 2. Đưa OAuth client lên

Chép `client_secret.json` (loại **Desktop app**) vào `C:\yt\scripts\`.

Chỉ cần loại Desktop app. Client **Web application** dùng cho n8n giờ không cần
nữa — bạn có thể xoá nó trong Google Cloud Console, hoặc để đó cũng không sao.

## 3. Đăng nhập lần đầu

Chạy lệnh dưới đây **ngay trên VPS** (RDP vào rồi mở PowerShell):

```powershell
python youtube_uploader.py --channel ten-kenh --dry-run
```

Trình duyệt sẽ mở **hai lần**:

1. **Drive** — đăng nhập tài khoản chứa thư mục `YouTube-Upload`
2. **YouTube** — đăng nhập tài khoản sở hữu kênh, rồi **chọn đúng kênh**

Gặp màn *unverified app* thì bấm **Advanced → Go to ... (unsafe)**.

Hai tài khoản **không bắt buộc phải giống nhau**. Token lưu thành
`token-drive.json` và `token-youtube.json`, lần sau không hỏi lại.

> Đây là lý do tài khoản kênh chỉ tồn tại trên VPS: luồng Desktop app redirect
> về `localhost` của chính máy đang chạy, nên phải đăng nhập tại đây. Máy cá
> nhân của bạn không bao giờ chạm vào tài khoản kênh.

## 4. Chạy thử

Từ máy render, đẩy một video ngắn lên:

```bash
python push_to_drive.py --channel ten-kenh --video test.mp4 --title "Test uploader"
```

Rồi trên VPS:

```powershell
python youtube_uploader.py --channel ten-kenh --once --verbose
```

Mong đợi: video xuất hiện trong YouTube Studio ở trạng thái *Private*, và cả bộ
file nằm trong `done/`.

**Thử lỗi:** upload một file `.json` mà không có `.mp4` cùng tên → script báo
*"Khong tim thay ... .mp4"* và chuyển file sang `failed/`.

## 5. Chạy tự động

Tạo Scheduled Task chạy mỗi 5 phút:

```powershell
schtasks /Create /SC MINUTE /MO 5 /TN "YouTube Uploader" /RL HIGHEST /F /TR "python C:\yt\scripts\youtube_uploader.py --channel ten-kenh --once"
```

Kiểm tra và chạy thử ngay:

```powershell
schtasks /Run /TN "YouTube Uploader"
```

Cách này an toàn hơn chạy thường trú: nếu một lần chạy chết, lần sau tự nhặt
lại. Script tự chặn chạy chồng bằng lock file, nên Task Scheduler gọi trùng giờ
cũng không sao.

Mỗi lần chạy xử lý **1 video** (mặc định `--max-per-run 1`). Video 1GB mất hàng
chục phút, nên để 1 thì mỗi lần chạy ngắn, giữ lock ít, và hỏng giữa chừng thì
mất ít tiến độ. Muốn nhiều hơn thì `--max-per-run 3`. Giá trị phải ≥ 1.

**Đặt `PYTHONUTF8=1` cho user chạy task:**

```powershell
setx PYTHONUTF8 1
```

Qua Task Scheduler, stdout không còn là console nên Python dùng codepage hệ
thống. Vài dòng log có dấu tiếng Việt sẽ encode không được, logging in
`--- Logging error ---` rồi bỏ dòng đó. Không làm chết script và file
`logs/uploader.log` vẫn đủ (handler đó đã ghi UTF-8), nhưng đặt biến này thì
output trên màn hình cũng sạch.

Muốn chạy thường trú thì dùng `--watch 300` thay cho `--once`.

## 5b. Xoá file trên Drive thay vì chuyển sang `done/`

Mặc định, upload xong thì cả bộ file chuyển sang `done/`. Nếu bản gốc đã nằm
trên máy render và bạn không muốn Drive phình ra, thêm `--on-success`:

| Chế độ | Việc làm | Hồi phục |
|---|---|---|
| `move` (mặc định) | Chuyển sang `done/` | File vẫn nguyên |
| `trash` | Bỏ vào thùng rác Drive | **Được, trong 30 ngày** |
| `delete` | Xoá vĩnh viễn, không qua thùng rác | **Không** |

```powershell
schtasks /Create /SC MINUTE /MO 5 /TN "YouTube Uploader" /RL HIGHEST /F /TR "python C:\yt\scripts\youtube_uploader.py --channel ten-kenh --once --on-success trash"
```

Nên dùng `trash` hơn `delete`: vẫn dọn sạch `pending/`, mà upload sai metadata
hay sai kênh thì còn 30 ngày để lấy lại file từ thùng rác. `delete` chỉ nên
dùng khi dung lượng Drive thật sự căng.

**Chỉ áp dụng khi upload thành công hoàn toàn.** Hai trường hợp còn lại không
bị ảnh hưởng, dù bạn đặt chế độ nào:

- Upload thất bại → file sang `failed/`, **không bị xoá**
- Video đã lên nhưng bước sau lỗi (ví dụ thumbnail) → file sang `done/`, cũng
  **không bị xoá**, để bạn còn xem lại

Nên `done/` trở thành thư mục "cần để ý", không còn là nơi chứa mọi video đã
xong. Nếu trash/delete hỏng giữa chừng (mất mạng), file còn kẹt được chuyển
sang `done/` như phương án dự phòng.

## 6. Thông báo Telegram (tuỳ chọn)

Đặt hai biến môi trường cho user chạy task:

```powershell
setx TELEGRAM_BOT_TOKEN "123456:ABC..."
setx TELEGRAM_CHAT_ID "123456789"
```

Không đặt thì script bỏ qua, không lỗi.

---

## File script tạo ra

| File | Vai trò |
|---|---|
| `token-drive.json`, `token-youtube.json` | Token OAuth. **Bí mật** |
| `logs/uploader.log` | Log, tự xoay vòng 5MB × 5 file |
| `.uploader-state-<kênh>.json` | Nhớ video đã upload xong nhưng chưa kịp dọn file |
| `.uploader-<kênh>.lock` | Chặn chạy chồng |

Tất cả đã nằm trong `.gitignore`.

**File state quan trọng hơn vẻ ngoài của nó.** Tình huống thật: upload xong
(đã mất 1600 quota) rồi mất điện trước khi kịp chuyển file sang `done/`. Lần
chạy sau file vẫn nằm trong `pending/`; không có state thì script sẽ upload lần
hai, vừa trùng video vừa tốn thêm 1600 quota. Có state thì nó bỏ qua bước
upload và chỉ làm nốt phần còn lại.

## Sự cố thường gặp

| Hiện tượng | Xử lý |
|---|---|
| `Khong thay client_secret.json` | Chép file OAuth loại Desktop app vào cùng thư mục script |
| Đăng nhập xong 7 ngày sau hỏng | OAuth consent screen còn ở *Testing*. Phải Publish sang *In production* |
| `thumbnails.set` lỗi 403 | Kênh chưa xác minh số điện thoại. Vào youtube.com/verify. Video vẫn lên bình thường, chỉ thiếu thumbnail |
| `quotaExceeded` | Hết quota ngày (~5-6 video). Script dừng vòng lặp, **để nguyên file trong `pending/`** và báo còn bao nhiêu file. Lần chạy sau tự làm lại, không cần can thiệp. Quota reset 15:00 giờ VN |
| `publishAt` bị từ chối | Project chưa qua audit YouTube nên không được hẹn giờ. Bỏ `publishAt` hoặc nộp form audit |
| Script báo "Mot tien trinh khac dang chay" | Bình thường, lần chạy trước chưa xong. Lock quá 6 giờ thì tự bỏ qua |
| Video lên hai lần | Không nên xảy ra: upload xong là state ghi ngay, và chỉ xoá khi cả bộ file đã sang `done/`. Nếu vẫn gặp, kiểm tra `.uploader-state-<kênh>.json` có bị xoá tay giữa chừng không |
| Ổ đĩa đầy | Video tải tạm vào thư mục temp hệ thống. Dùng `--temp-dir D:\temp` để đổi chỗ |

Xem log:

```powershell
Get-Content C:\yt\scripts\logs\uploader.log -Tail 50 -Wait
```

---

## Chạy unit test

27 test, không gọi mạng và không tốn quota — chạy được cả trên máy cá nhân:

```bash
cd scripts
python -m unittest test_youtube_uploader -v
```

Test mock các hàm gọi mạng (`download_to`, `upload_video`, `move_file`...) nên
phần được kiểm tra thật là logic phân luồng `pending/` → `done/` | `failed/`
và chuyện giữ hay xoá state. Đáng chú ý nhất là hai nhóm:

- `TestQuota` — hết quota thì file phải **ở lại `pending/`**, không bị đẩy sang
  `failed/`, và vòng lặp phải dừng ngay chứ không thử tiếp các file còn lại.
- `TestState` — nếu `move_file` hỏng sau khi upload xong thì **phải giữ state**,
  để lần chạy sau biết video đã lên và không upload lần hai (trùng video, mất
  thêm 1600 quota).

Cả hai nhóm này tương ứng với lỗi thật đã sửa; chạy chúng trên code trước khi
sửa thì fail.
