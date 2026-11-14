# Lấy analytics kênh tự động: `fetch_analytics.py`

Script kéo số liệu kênh từ **YouTube Analytics API** rồi ghi ra 2 file CSV trên
Google Drive, ngay cạnh `upload-history.csv`. Mở bằng Google Sheets là có
dashboard, xem được cả trên điện thoại.

Mặc định lấy cửa sổ **28 ngày tính đến hôm nay** — đúng khoảng mà YouTube Studio
mở sẵn ở chế độ nâng cao.

```powershell
cd C:\youtube-upload\scripts
python fetch_analytics.py --channel "Bare Skull"
```

## 1. Quota — không đụng vào quota upload

Analytics API có bucket quota **riêng**, không tính vào 10.000 units/ngày của
Data API đang dành cho `videos.insert`. Chạy script này hằng ngày không làm mất
video nào.

Ngoại lệ nhỏ: script gọi thêm `videos.list` để lấy tiêu đề video (Analytics API
chỉ trả về video ID). Tốn **1 unit cho mỗi 50 video** — không đáng kể so với
1600 units của một lần upload.

## 2. Hai cột Studio có mà API không có

| Cột trong Studio | Lấy tự động được? |
|---|---|
| Số lượt xem, Thời gian xem, Người đăng ký, Doanh thu ước tính | ✅ |
| **Số lượt hiển thị hình thu nhỏ** | ❌ |
| **Tỷ lệ nhấp vào hình thu nhỏ** | ❌ |

Hai cột impressions/CTR **không có trong bất kỳ API công khai nào** — cả
Analytics API lẫn Reporting API đều không trả về. Đó là số riêng của giao diện
Studio. Muốn có thì bấm nút tải xuống ở góc phải màn hình Studio, xuất tay.

Đừng dùng browser automation để cào hai cột này: dễ vỡ mỗi lần Google đổi giao
diện, và đụng vào tài khoản chính của kênh.

## 3. Thêm scope và đăng nhập lần đầu

Script dùng **token riêng** (`token-analytics.json`), không đè lên
`token-drive.json` và `token-youtube.json` đang chạy. Uploader không bị ảnh
hưởng, không phải đăng nhập lại.

Trước khi chạy lần đầu, thêm 2 scope vào consent screen (Google Cloud Console →
**Data Access** → *Manually add scopes*, xem `setup.md` §4.4):

```
https://www.googleapis.com/auth/yt-analytics.readonly
https://www.googleapis.com/auth/yt-analytics-monetary.readonly
```

Scope thứ hai chỉ để đọc `estimatedRevenue`. Không khai cũng chạy được — script
tự nhận lỗi 403 rồi query lại không kèm cột doanh thu (xem §6).

Chạy lần đầu trên VPS, script mở trình duyệt một lần để xin quyền. Đăng nhập
bằng đúng tài khoản sở hữu kênh.

```powershell
python fetch_analytics.py --channel "Bare Skull" --dry-run
```

`--dry-run` in CSV ra màn hình, không ghi gì lên Drive — dùng để thử.

## 4. Hai file CSV sinh ra

Cả hai nằm trong `YouTube-Upload/<kênh>/`.

### `analytics-daily.csv` — một dòng mỗi ngày, **gộp dần**

| Cột | Nội dung |
|---|---|
| `ngay` | `YYYY-MM-DD` |
| `luot_xem` | views |
| `phut_xem` | estimatedMinutesWatched |
| `tg_xem_tb_giay` | averageViewDuration |
| `ty_le_xem_phan_tram` | averageViewPercentage |
| `dang_ky_moi` / `dang_ky_huy` | subscribersGained / Lost |
| `thich`, `binh_luan`, `chia_se` | likes, comments, shares |
| `doanh_thu_usd` | estimatedRevenue (nếu có quyền) |

File này **không bị xoá đi viết lại**. Mỗi lần chạy, ngày nào nằm trong cửa sổ
28 ngày thì dòng cũ bị ghi đè, ngày cũ hơn giữ nguyên. Nhờ vậy sau vài tháng là
có lịch sử đầy đủ, dài hơn hẳn 28 ngày.

### `analytics-videos.csv` — một dòng mỗi video, **ghi đè mỗi lần**

Top 200 video theo lượt xem trong cửa sổ, kèm `title`, `ngay_dang`, và
`tu_ngay`/`den_ngay` để biết số liệu thuộc khoảng nào. Đây là ảnh chụp của cửa
sổ hiện tại nên ghi đè toàn bộ, không gộp.

## 5. Số liệu trễ 2–3 ngày

YouTube chốt số chậm, doanh thu còn chậm hơn. Vì vậy script **lấy lại toàn bộ
28 ngày mỗi lần chạy** chứ không chỉ lấy hôm qua: số của mấy ngày gần nhất sẽ tự
được sửa lại ở lần chạy sau khi Google chốt xong.

Đừng coi số của 2 ngày gần nhất là số cuối cùng.

## 6. Kênh chưa bật kiếm tiền

Nếu kênh chưa monetize hoặc thiếu scope monetary, API trả **403** cho query có
`estimatedRevenue`. Script bắt riêng trường hợp này, ghi cảnh báo vào log rồi
query lại không kèm cột doanh thu. Mất một cột còn hơn mất cả báo cáo.

## 7. Các tuỳ chọn khác

```powershell
# 90 ngày
python fetch_analytics.py --channel "Bare Skull" --days 90

# Một khoảng cụ thể (--start-date đè lên --days)
python fetch_analytics.py --channel "Bare Skull" `
    --start-date 2026-01-01 --end-date 2026-01-31

# Giữ thêm một bản CSV trên ổ đĩa VPS
python fetch_analytics.py --channel "Bare Skull" --out-dir C:\youtube-upload\reports
```

`--days` bắt buộc ≥ 1. Ngày phải đúng dạng `YYYY-MM-DD`.

**Backfill lịch sử:** chạy một lần với `--start-date` từ ngày tạo kênh. Dữ liệu
cũ được gộp vào `analytics-daily.csv` như bình thường.

## 8. Chạy tự động hằng ngày

Thêm một Scheduled Task thứ hai, độc lập với task upload. Chạy lúc rảnh, ví dụ
6 giờ sáng:

```powershell
schtasks /create /tn "YouTube Analytics" /sc daily /st 06:00 ^
  /tr "C:\Python313\python.exe C:\youtube-upload\scripts\fetch_analytics.py --channel \"Bare Skull\"" ^
  /ru "%USERNAME%" /rl LIMITED
```

Nhớ `setx PYTHONUTF8 1` như uploader — qua Scheduled Task, stdout không phải
console nên Python dùng codepage hệ thống và nuốt mất dòng log có dấu.

Script có lock riêng (`.analytics-<kênh>.lock`) nên hai lần chạy chồng lên nhau
thì lần sau tự thoát.

## 9. Báo lỗi

Dùng chung Telegram với uploader (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
Script chết hẳn — token hết hạn, mất quyền Drive, API đổi — thì gửi một tin
`[<kênh>] Analytics LOI: ...`. Không có nó thì hỏng là im lặng hoàn toàn.

## 10. File script tạo ra

| File | Vai trò |
|---|---|
| `token-analytics.json` | Token OAuth analytics. **Bí mật** |
| `logs/analytics.log` | Log, tự xoay vòng 5MB × 5 file |
| `.analytics-<kênh>.lock` | Chặn chạy chồng |

Đã nằm trong `.gitignore`.

## 11. Unit test

```bash
cd scripts
python -m unittest test_fetch_analytics -v
```

31 test, mock hết phần gọi mạng. Trọng tâm: cửa sổ 28 ngày tính đúng, gộp CSV
giữ được ngày cũ và ghi đè ngày mới, nhánh 403 doanh thu, và việc lấy title
hỏng thì báo cáo vẫn ra.

## 12. Nếu sau này cần đầy đủ hơn: Reporting API

`fetch_analytics.py` dùng **Analytics API** (`reports.query`) — query theo yêu
cầu, trả JSON ngay, đủ cho nhu cầu hiện tại.

Có một đường khác là **YouTube Reporting API**: tạo `job` một lần, Google tự
sinh file CSV đầy đủ mỗi ngày, giữ 60 ngày, có backfill lịch sử. Hợp khi cần
kho dữ liệu lớn hoặc nhiều chiều (traffic source, thiết bị, nhân khẩu học).
Nặng tay hơn để bắt đầu, và **vẫn không có impressions/CTR**.
