# Vòng phản hồi Studio trên máy chủ kênh (`loop/`)

VPS đọc YouTube Studio và ghi số lên Drive (`docs/studio-capture.md`). Máy này kéo số đó về,
ra phán quyết cho từng video, viết báo cáo và — khi đã bật — ghi yêu cầu đổi thumbnail để VPS
thực hiện. Mỗi 2 giờ một lần:

```
Task Scheduler (2 giờ/lần)
  └─ loop\run.ps1
       ├─ python -m loop.fetch     Drive studio/ -> loop\cache\
       ├─ python -m loop.analyse   phán quyết, báo cáo, Telegram, yêu cầu (khi live)
       └─ bước nào lỗi -> python -m loop.notify_fail <bước> <mã>  (một Telegram, rồi thoát đúng mã đó)
```

Máy này không bao giờ mở trình duyệt để đăng nhập, không bao giờ đụng vào video, chỉ ghi file
vào `studio/` trên Drive.

---

## 1. Cài đặt (một lần)

Repo nằm ở `D:\Youtube\n8n-upload` (bản clone của yt-uploader).

1. **Cấu hình.** Chép file mẫu (file thật đã nằm trong `.gitignore`):

   ```powershell
   cd D:\Youtube\n8n-upload
   copy loop\config.example.json loop\config.json
   ```

   | key | nghĩa |
   |---|---|
   | `studio_folder_id` | ID folder `YouTube-Upload/Bare Skull/studio/` trên Drive. Bắt buộc. |
   | `go_live` | Ngày bật đổi thumbnail thật, dạng `2026-12-01` (00:00 giờ `tz`). **Để `""` = chỉ cảnh báo mãi mãi**: báo cáo ghi "would swap", không ghi yêu cầu nào. |
   | `alert_only_days` | Số ngày vẫn chỉ cảnh báo sau `go_live` (mặc định 7). |
   | `release_manual` | Danh sách video_id đã đổi thumbnail bằng tay mà anh cho phép vòng lặp xử lý lại (mục 4). Mặc định `[]`. |
   | `tz` | Múi giờ cho ngày báo cáo và "một lần mỗi ngày" (mặc định `Asia/Ho_Chi_Minh`). |
   | `report_dir` | Nơi ghi báo cáo `YYYY-MM-DD.md`. |

2. **Telegram.** Đặt hai biến môi trường ở phạm vi **User** (không phải Machine), rồi đăng xuất
   và đăng nhập lại Windows để Task Scheduler thấy:

   ```powershell
   [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "<token của bot>", "User")
   [Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID", "<chat id của anh>", "User")
   ```

   Thiếu một trong hai thì vòng lặp vẫn chạy nhưng im lặng hoàn toàn.

3. **Token Drive.** Vòng lặp dùng `scripts\token-drive-link.json` (cùng token mà
   `push_to_pending.py` của repo dựng video dùng). Nó chỉ đọc token và tự làm mới trong bộ nhớ;
   nếu token hỏng hoặc bị thu hồi thì nó **không** mở trình duyệt: bước đó thoát mã 3, in lỗi rõ
   ràng và gửi một Telegram mỗi ngày. Đăng nhập lại (một lần, có trình duyệt):

   ```powershell
   cd D:\Youtube\n8n-upload\scripts
   python -c "from pathlib import Path; import push_to_drive as p; p.get_service(Path('client_secret.json'), Path('token-drive-link.json'))"
   ```

   (Đẩy một video bằng `push_to_pending.py` cũng đăng nhập lại token này.)

4. **Task Scheduler.** Mỗi 2 giờ:

   ```powershell
   schtasks /Create /TN "Studio loop" /SC HOURLY /MO 2 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\Youtube\n8n-upload\loop\run.ps1"
   ```

   Chạy thử một lần bằng tay: `powershell -NoProfile -ExecutionPolicy Bypass -File D:\Youtube\n8n-upload\loop\run.ps1`
   rồi xem `echo $LASTEXITCODE` (0 = ổn).

---

## 2. Mã thoát của `run.ps1`

| mã | nghĩa |
|---|---|
| 0 | ổn |
| 1 | lỗi bất ngờ (mạng, Drive). Xem output của lần chạy. |
| 2 | thiếu `loop\config.json` hoặc thiếu `studio_folder_id` |
| 3 | token Drive trên máy này cần đăng nhập lại (mục 1.3) |

---

## 3. Telegram nghĩa là gì

Vòng lặp không bao giờ bắn hàng loạt: lần chạy đầu tiên gửi **một** tin tổng hợp; mỗi loại phán
quyết của một video báo tối đa một lần mỗi 24 giờ và chỉ khi nó giữ nguyên 2 lần chạy liên tiếp;
lỗi health báo khi trạng thái đổi, cộng một lần nhắc mỗi ngày.

**Từ máy này (`loop/`):**

| tin | nghĩa | làm gì |
|---|---|---|
| `Studio loop started: N videos, X KEEP, Y CHANGE_TITLE …` | Lần chạy đầu tiên: tổng hợp thay cho từng video. | Đọc báo cáo hôm nay. |
| `<base> (<id>): SWAP - would swap to alt1: …` | CTR dưới ngưỡng, đang chỉ cảnh báo nên không đổi gì. | Không cần. |
| `<base> (<id>): SWAP - swap to alt1: …` | Live: đã ghi yêu cầu, VPS sẽ đặt alt1. | Không cần; VPS báo khi xong. |
| `… REVERT - revert to main: …` | Bản đang chạy kém rõ rệt so với bản tốt nhất; đặt lại bản tốt nhất. Sau đó video này không đổi thêm nữa. | Không cần. |
| `… CHANGE_TITLE - … no swap left` | Đã thử hết thumbnail mà CTR vẫn thấp. | Anh quyết định đổi tiêu đề. |
| `… NOT_PACKAGING - …` | CTR ổn nhưng số lần hiển thị thấp hơn nhiều so với video khác cùng tuổi. | Vấn đề không ở thumbnail. |
| `… OPENING - …` | CTR ổn nhưng giữ chân 30 giây kém. | Báo phía kịch bản. |
| `… PAUSED - manual thumbnail change, paused until …` | Thumbnail đổi mà không có yêu cầu nào: anh đổi tay. Dừng 7 ngày. | Không cần. |
| `… (manual - alert only)` ở cuối | Video đang chạy thumbnail anh đổi tay: chỉ báo cáo, **không bao giờ** ghi yêu cầu, kể cả sau 7 ngày. | Muốn vòng lặp xử lý lại: mục 4. |
| `Studio loop health: FAIL auth …` | VPS cần đăng nhập lại Studio. | Làm theo `docs/studio-capture.md`. |
| `Studio loop health: FAIL read / FAIL api / missing / corrupt …` | VPS đọc lỗi hoặc chưa ghi health. `FAIL read` chỉ báo khi giữ 2 lần phân tích liên tiếp (một video đọc lỗi thoáng qua không báo); `PASS (was FAIL read)` chỉ gửi nếu đã gửi cảnh báo. | Xem chi tiết; nhắc lại mỗi ngày nếu còn. |
| `Studio loop health: PASS (was …)` | Đã hết lỗi. | Không cần. |
| `Studio loop: no fresh data from the VPS since <time>` | `health.json` cũ hơn 3 giờ hoặc snapshot mới nhất cũ hơn 26 giờ: VPS không chạy hoặc không đọc được gì. Cùng dòng này ở đầu báo cáo. | Kiểm tra VPS (RDP còn kết nối, Task Scheduler). |
| `Studio loop on this PC: step fetch failed with exit code N - …` | Một bước trên máy này lỗi (mã ở mục 2). | Theo gợi ý trong tin. |

**Từ VPS** (cùng bot): `Swap <id> -> alt1: ok` (đã đặt), `… FAILED …` (không đặt được, yêu cầu
sang `failed/`), `… set OK but request file not moved …` (đã đặt, chỉ chưa chuyển file; lần sau
chỉ chuyển lại, không đặt lại), `Studio sign-in needed on the VPS …`. Yêu cầu cũ hơn 24 giờ thì
VPS không thực hiện, chuyển sang `failed/` với "stale request".

---

## 4. Thả một video anh đã đổi thumbnail bằng tay

Khi anh đổi thumbnail trên Studio, vòng lặp nhận ra (`manual`) và từ đó chỉ báo cáo cho video đó.
Muốn vòng lặp xử lý lại như bình thường:

1. Thêm video_id vào `release_manual` trong `loop\config.json`:

   ```json
   "release_manual": ["dQw4w9WgXcQ"]
   ```

2. Lần chạy kế tiếp ghi lại **thời điểm thả** (trong `loop\cache\.last_verdicts.json`). Từ snapshot
   đầu tiên sau thời điểm đó, video được coi như `main`, số liệu tính lại từ đầu; thumbnail anh
   đang để là điểm xuất phát.
3. Một lần thả chỉ áp cho lần đổi tay đã có. Nếu anh đổi tay **lần nữa**, video lại thành
   `manual`. Muốn thả lần nữa: xoá id khỏi danh sách, chờ một lần chạy, rồi thêm lại.

Lưu ý: sau khi thả, "main" vẫn là file `done/<base>.jpg` trên Drive. Nếu vòng lặp đổi sang alt
rồi REVERT về main, nó đặt lại file gốc đó, không phải thumbnail anh đổi tay.

---

## 5. Changing a thumbnail by hand (đổi thumbnail bằng tay)

- **Vòng lặp làm gì khi anh đổi tay.** Hash thumbnail đổi mà không có action nào giải thích: video thành
  `manual`, dừng 7 ngày, chỉ báo cáo (`PAUSED - manual thumbnail change …`), không bao giờ ghi yêu cầu cho
  video đó. Muốn xử lý lại: thêm id vào `release_manual` (mục 4).
- **Ngay sau tin `Swap … ok`.** Cửa sổ chờ sau một swap kết thúc ở mốc MUỘN HƠN của: 3 snapshot có hash
  sau action, hoặc 24 giờ sau action (CDN cần thời gian), và đóng sớm ở lần đổi đầu tiên được xác nhận
  (2 snapshot liên tiếp). Hash mới thứ nhất trong cửa sổ là của swap. Nếu anh đổi tay ngay sau tin
  `ok`, lần đổi của anh chỉ được nhận là `manual` SAU KHI swap đã được xác nhận (hoặc khi hai hash mới
  khác nhau cùng xuất hiện trong cửa sổ); có thể chậm vài snapshot, tối đa tới khi cửa sổ đóng.
- **VPS tự kiểm tra trước khi đặt.** Mỗi yêu cầu mang `expect_hash` (hash thumbnail mới nhất lúc phân
  tích). Nếu lúc đặt thumbnail trên YouTube đã khác, VPS không đặt và chuyển yêu cầu sang `failed/` với
  "thumbnail changed since request" (anh vừa đổi tay thì swap không đè lên). Không lấy được hash thì vẫn
  đặt: đây là rào chắn, không phải cổng.
- Khi dữ liệu cũ (health > 3 giờ hoặc snapshot > 26 giờ), phân tích không ghi yêu cầu nào; phán quyết và
  báo cáo vẫn ra, kèm "(stale data - no request)".
