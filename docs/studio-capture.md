# Đọc số liệu YouTube Studio trên VPS (`studio_capture.py`)

`studio_capture.py` chạy trên VPS cạnh uploader. Mỗi lần chạy, nó mở YouTube Studio
trong Chrome thật bằng một profile đã đăng nhập sẵn, đọc JSON của chính Studio cho
từng video công khai đến hạn rồi ghi lên Drive, vào `YouTube-Upload/<kênh>/studio/`.
Sau đó nó chạy bộ thực thi đổi thumbnail (`actions/pending/`).

```
Task Scheduler (30 phút/lần, chỉ khi user đang đăng nhập)
  └─ studio_capture.py --once
       ├─ Data API: danh sách video công khai (bỏ Short, riêng tư, không công khai, hẹn giờ)
       ├─ đến hạn? < 72 h: 2 giờ/lần · còn lại: 1 lần/ngày từ 06:00 giờ VPS
       ├─ Chrome (C:\yt\studio-profile): tab Reach + Engagement, nghỉ 5–15 s giữa các video
       ├─ Drive studio/: snapshots/<ngày>.csv, raw/<ngày>/…json.gz, health.json, upload-history.csv
       └─ studio_actions: đổi thumbnail theo yêu cầu trong actions/pending/
```

Script chỉ đọc. Nó không bao giờ điền form đăng nhập và không lưu mật khẩu.

---

## 1. Cài đặt (một lần)

1. Cài **Google Chrome** bản thường trên VPS. Script dùng `channel="chrome"`, không dùng
   Chromium đi kèm Playwright, vì Google hay chặn đăng nhập trên Chromium đó.
2. Cài thư viện. Launcher `pip` trên VPS bị hỏng, nên gọi qua `python -m`:

   ```powershell
   cd C:\yt\yt-uploader\scripts
   python -m pip install -r requirements.txt
   ```

3. Tạo file cấu hình từ file mẫu (file thật đã nằm trong `.gitignore`):

   ```powershell
   copy studio_config.example.json studio_config.json
   ```

   | key | nghĩa | mặc định |
   |---|---|---|
   | `channel` | tên kênh, trùng tên folder trên Drive | `Bare Skull` |
   | `profile_dir` | profile Chrome đã đăng nhập | `C:\yt\studio-profile` |
   | `daily_hour` | giờ VPS bắt đầu lượt đọc hằng ngày cho video ≥ 72 h | `6` |

   Script tự đọc `studio_config.json` nằm cạnh nó. Cờ dòng lệnh (`--channel`, `--profile`)
   ghi đè giá trị trong config.

4. Token Drive và YouTube dùng chung với uploader (`token-drive.json`, `token-youtube.json`
   trong `scripts\`). Telegram cũng dùng chung hai biến môi trường `TELEGRAM_BOT_TOKEN` và
   `TELEGRAM_CHAT_ID` của uploader.

## 2. Đăng nhập Studio (một lần, qua RDP)

Đăng nhập ngay trên VPS để Google thấy lần đăng nhập đến từ đúng IP mà script sẽ dùng.

```powershell
cd C:\yt\yt-uploader\scripts
python studio_spike.py --profile C:\yt\studio-profile --login
```

Một cửa sổ Chrome sẽ mở ra. Hãy đăng nhập bằng tay; script chỉ chờ, tối đa 15 phút. Lần
đầu có thể điện thoại sẽ hỏi "verify it's you".

**Nếu Google chặn đăng nhập trong cửa sổ Playwright** ("this browser may not be secure"):
đóng cửa sổ đó, mở Chrome thường với cùng profile, đăng nhập bằng tay, rồi đóng Chrome:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir=C:\yt\studio-profile https://studio.youtube.com
```

Kiểm tra bằng một lần chạy thử. Lần này không ghi gì lên Drive và không ghi state:

```powershell
python studio_capture.py --channel "Bare Skull" --dry-run --once --no-jitter --no-actions
```

Lần chạy phải in ra dòng tiêu đề CSV và một dòng cho mỗi video đến hạn, rồi thoát với mã 0.
Thêm `--only <video_id>` để chỉ đọc một video, bỏ qua lịch.

## 3. RDP: ngắt kết nối, không bao giờ sign out

Chrome chạy có cửa sổ, trong phiên RDP của chủ kênh. Khi xong việc, hãy **đóng cửa sổ RDP
(Disconnect)**. **Không bấm Sign out**: sign out sẽ dừng mọi lần chạy, vì task chỉ chạy khi
user đang đăng nhập.

## 4. Task Scheduler

Chạy mỗi 30 phút, chỉ khi user đang đăng nhập (`/IT`). Gõ lệnh này trong **Command Prompt
(cmd)**, không gõ trong PowerShell, vì PowerShell 5.1 hiểu sai dấu `\"`:

```bat
schtasks /Create /SC MINUTE /MO 30 /TN "Studio Capture" /IT /F /TR "powershell -NoProfile -ExecutionPolicy Bypass -Command \"Set-Location C:\yt\yt-uploader\scripts; python studio_capture.py --channel 'Bare Skull' --once; exit $LASTEXITCODE\""
```

Lệnh trên đúng với cấu hình
`powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location C:\yt\yt-uploader\scripts; python studio_capture.py --channel 'Bare Skull' --once; exit $LASTEXITCODE"`,
chạy mỗi 30 phút với "Run only when user is logged on". Đoạn `exit $LASTEXITCODE` giữ lại
mã thoát của script, nhờ đó cột "Last Run Result" của Task Scheduler hiện 0, 1, 2, 3 hoặc 4.

Chạy ngay một lần để thử: `schtasks /Run /TN "Studio Capture"`.

**Xoá các task StudioSpike một lần** sau khi đã lên lịch cho bộ đọc này, vì chúng dùng
chung profile Chrome và hai Chrome không mở được cùng một profile:

```powershell
schtasks /Query /FO LIST | findstr /I StudioSpike
schtasks /Delete /TN "<tên task StudioSpike>" /F
```

Không cần task riêng cho `studio_actions.py`, vì mỗi lần chạy `studio_capture.py` đã gọi bộ
thực thi một lần. Nếu vẫn chạy `studio_actions.py --watch` riêng thì hai bên dùng chung lock
`.studio-actions-<kênh>.lock`; bên đến sau sẽ bỏ qua lượt của mình.

### Lịch và nhịp

- Mỗi lần chạy chờ ngẫu nhiên 0–10 phút trước khi bắt đầu (`--no-jitter` để bỏ chờ).
- Mỗi lúc chỉ mở một trang video, và nghỉ 5–15 s giữa hai video.
- Video công khai dưới 72 giờ được đọc khoảng 2 giờ/lần. Tuổi video tính từ lúc nó thành
  công khai.
- Video cũ hơn được đọc mỗi ngày một lần, từ `daily_hour` giờ VPS.
- Sau 35 phút kể từ lúc khởi động (tính cả thời gian chờ jitter), script không mở video mới;
  phần còn lại để lần chạy sau. Nếu cả lần chạy vượt 50 phút, watchdog sẽ dừng nó và gỡ lock
  của nó.
- Nếu uploader đang chạy (`.uploader-<kênh>.lock` còn mới), lần này bỏ qua hoàn toàn và
  thoát 0, vì upload được ưu tiên. Nếu giữa chừng có lock uploader đã giữ quá 60 s, script
  dừng mở video mới; các dòng đã đọc vẫn được ghi. Một lượt quét `pending/` vài giây của
  uploader thì không làm script dừng.

## 5. Trạng thái `studio/health.json`

| `status` | nghĩa | làm gì |
|---|---|---|
| `PASS` | mọi video đến hạn đều đọc được (hoặc không có video nào đến hạn) | không cần làm gì |
| `FAIL read` | ít nhất một video không đọc được (Studio không trả số, trang không mở, hoặc Chrome không khởi động). `detail` ghi tên từng video và lý do | không cần làm gì nếu chỉ một lần; video lỗi sẽ được đọc lại ở lần sau. Không bao giờ ghi số 0 thay cho lần đọc lỗi |
| `FAIL auth` | Studio hiện trang đăng nhập, CAPTCHA hoặc "verify it's you". Nếu `detail` bắt đầu bằng `token:` thì token Google API của uploader hỏng | Studio: làm lại mục 2. Token: chạy uploader một lần bằng tay (`python youtube_uploader.py --channel "Bare Skull" --once`) để đăng nhập lại |
| `FAIL api` | Data API hoặc Drive báo lỗi (quota, mạng, quyền). `detail` ghi loại lỗi | thường tự hết; nếu kéo dài, xem log |

`videos_read` và `videos_failed` là số video đọc được và đọc lỗi trong lần chạy gần nhất.

Sau một lần `FAIL auth` do Studio, các lần chạy sau **không mở Studio nữa** (thoát 3, không
gửi lại Telegram) cho tới khi profile Chrome thay đổi, tức là có người đã mở profile để đăng
nhập lại. Trong thời gian chờ, script vẫn chép `upload-history.csv` và vẫn chạy bộ thực thi
đổi thumbnail, vì hai việc này chỉ cần token API, không cần Studio. Lần chạy kế tiếp sau khi
profile thay đổi sẽ tự đọc lại.

## 6. Tin nhắn Telegram

| tin nhắn | nghĩa |
|---|---|
| `Studio sign-in needed on the VPS: run studio_spike.py --login` | phiên Google hết hạn hoặc bị thử thách; làm lại mục 2 |
| `Google API token on the VPS needs re-auth: run the uploader once by hand` | token Drive/YouTube không refresh được. Script không bao giờ tự mở trang đăng nhập OAuth và không ghi lại file token. Chạy uploader một lần bằng tay qua RDP. Tối đa một tin mỗi ngày |
| `[Bare Skull] Studio read failed twice in a row for N video(s): <id> (<lý do>); …` | mỗi lần chạy có tối đa một tin, liệt kê các video vừa lỗi lần thứ hai liên tiếp. Nếu kéo dài, có thể Studio đã đổi giao diện, nên xem `studio/raw/`. Video lỗi được thử lại sau đúng chu kỳ bình thường, tính từ lần thử cuối. Ba video lỗi liên tiếp, hoặc Chrome bị đóng, thì lần chạy dừng sớm |
| `[Bare Skull] Studio capture: Chrome did not start twice in a row: …` | Chrome không mở được profile hai lần liên tiếp, thường do một cửa sổ Chrome khác đang giữ `C:\yt\studio-profile`; đóng cửa sổ đó |
| `[Bare Skull] Swap executor error: …` | bộ thực thi đổi thumbnail lỗi; việc đọc số vẫn xong bình thường |
| `Swap <id> -> alt1: ok` / `FAILED …` / `quota exceeded …` | kết quả từng yêu cầu đổi thumbnail (từ `studio_actions`) |
| `Studio capture: watchdog after 50 min, run killed` | lần chạy treo quá 50 phút và đã bị dừng; nếu Chrome còn mở thì đóng nó |
| `[Bare Skull] Studio capture ERROR: …` | Data API hoặc Drive lỗi (`FAIL api`), hoặc lỗi khác. Tối đa một tin cho mỗi loại lỗi mỗi ngày UTC. Xem log |

## 7. Mã thoát và file

| mã | nghĩa |
|---|---|
| 0 | xong (kể cả khi bỏ qua vì uploader đang chạy, hoặc không có video nào đến hạn) |
| 1 | lỗi Data API / Drive (`FAIL api`), `--only` chỉ vào video không công khai, hoặc watchdog |
| 2 | một `studio_capture` khác đang chạy (`.studio-capture-<kênh>.lock`) |
| 3 | đăng nhập Studio, thử thách, hoặc token Google hỏng (`FAIL auth`) |
| 4 | không mở được trang của ít nhất một video, hoặc Chrome không khởi động |

File cục bộ trong `scripts\` (đều đã nằm trong `.gitignore`):

- `.studio-capture-<kênh>.lock`: lock của lần chạy;
- `.studio-state-<kênh>.json`: lần đọc cuối của mỗi video, số lần lỗi liên tiếp, và cờ
  `auth_blocked`. Xoá file này thì mọi video sẽ đến hạn ngay ở lần sau;
- `logs\studio_capture.log`: log xoay vòng.
