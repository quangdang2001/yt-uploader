# n8n YouTube Auto-Upload

Trao đổi bằng tiếng Việt.

## Mục tiêu
Tự động upload video (≈1GB/video) lên YouTube theo flow:
Pipeline render → Google Drive → n8n (self-host) → YouTube Data API.

## Các quyết định đã chốt
- **BỎ n8n, chuyển sang script Python** (31/10/2026). Lý do: VPS hiện có là
  Windows Server 2016 — không chạy được Linux container, WSL 2 đòi build ≥18362
  mà 2016 chỉ có 14393. Script Python không cần Docker nên chạy thẳng được.
  - `scripts/youtube_uploader.py` chạy trên VPS, thay toàn bộ workflow n8n.
  - **Resume thật**: rớt mạng giữa chừng thì upload tiếp từ chỗ dở, không mất
    1600 quota như node YouTube của n8n.
  - **Một lệnh thay vì hai**: `publishAt` + `containsSyntheticMedia` gửi thẳng
    trong `videos.insert`, bỏ được bước `videos.update` (tiết kiệm 50 quota/video).
  - Chỉ cần OAuth client loại **Desktop app**. Client Web application không
    dùng nữa, cũng không cần domain / Cloudflare Tunnel.
  - Server 2016 hết hỗ trợ **13/01/2027** — script vẫn chạy nhưng OS hết vá.
    Cần kế hoạch chuyển trong vài tháng.
- **n8n self-host** — n8n Cloud không dùng được cho video 1GB (số liệu tra
  31/10/2026 từ docs chính thức):
  - **Starter 20€/mo: max execution duration 5 phút.** Một video phải tải 1GB từ
    Drive rồi đẩy 1GB lên YouTube; node YouTube lại gửi theo chunk 1 MiB, tức
    ~1024 request tuần tự. Không có cửa trong 5 phút.
  - **Pro 50€/mo: 40 phút, RAM 640MiB (Pro-1) hoặc 1280MiB (Pro-2)**, mà riêng
    n8n đã ăn ~180MiB.
  - Mấu chốt: **Cloud không cho đặt `N8N_DEFAULT_BINARY_DATA_MODE`**. Docs không
    hề nói Cloud lưu binary ở đâu. Không ép được `filesystem` thì không bảo đảm
    node YouTube đi nhánh stream — rơi vào nhánh buffer là ôm cả file vào RAM.
  - YouTube API **không** hỗ trợ upload từ URL, nên file buộc phải chảy qua n8n,
    không có đường vòng.
  - Kết luận: VPS Ubuntu 4GB (~5-12 USD/mo) vừa rẻ hơn Pro vừa chắc chắn chạy.
  - VPS Windows Server 2016 hiện có **không dùng được**: chỉ chạy Windows
    container trong khi image n8n là Linux, WSL 2 không hỗ trợ bản này
    (Microsoft chỉ hỗ trợ từ Server 2019), và OS hết hỗ trợ **13/01/2027**.
  - **Đang chạy tạm trên máy Windows cá nhân** bằng `docker-compose.local.yml` —
    không cần domain/tunnel vì workflow không có webhook nào.
  - Đích đến vẫn là VPS Ubuntu 24.04 (2–4GB RAM, ổ ≥40GB) với `docker-compose.yml`.
- **Cloudflare Tunnel** cho domain + HTTPS, không mở port ra Internet — chỉ cần khi lên VPS. Chạy local thì dùng `http://localhost:5678`, Google miễn trừ localhost khỏi yêu cầu HTTPS cho OAuth client Web application.
- **Chỉ dùng YouTube Data API chính thức** (`videos.insert`) qua OAuth của chủ kênh. Không browser automation, không tool bên thứ ba lạ.
- **Một Google Cloud project duy nhất**, không tạo nhiều project để lách quota (vi phạm ToS).
- Project chưa audit → video bị ép **private**. Cần nộp *YouTube API Services Audit and Quota Extension Form* để public/lên lịch tự động.
- Quota từ 01/06/2026: `videos.insert` có bucket riêng, mặc định 100 upload/ngày (cần kiểm tra lại trong Cloud Console).

## Cấu trúc Drive
```
YouTube-Upload/<ten-kenh>/{pending, done, failed}/
```
Mỗi video gồm: `<id>.mp4`, `<id>.jpg` (thumbnail, tuỳ chọn), `<id>.json` (metadata).
Pipeline upload video + thumbnail **trước**, JSON **sau cùng** → trigger bắt theo file `.json`.

Schema JSON:
```json
{
  "title": "...",
  "description": "...",
  "tags": ["..."],
  "categoryId": "27",
  "privacyStatus": "private",
  "publishAt": "2026-11-05T12:00:00Z",
  "containsSyntheticMedia": true,
  "madeForKids": false
}
```
Chỉ `title` là bắt buộc. Có `publishAt` thì `privacyStatus` bị ép về
`private` (YouTube chỉ cho hẹn giờ video private). Chi tiết: `docs/setup.md` §8.

## Workflow n8n (đã viết)
`workflows/youtube-upload.json` — 20 node, import trực tiếp. **Một workflow cho
một kênh**: Google Drive Trigger chỉ theo dõi được một folder và không nhìn vào
subfolder. Thêm kênh = Duplicate workflow, sửa node `Cau hinh kenh` + folder ở
trigger + credential YouTube.

Luồng chính:
1. `Trigger Drive pending` — Drive Trigger, `fileCreated`, poll 1 phút
2. `Chi file JSON` — IF, chỉ đi tiếp với file `.json`
3. `Cau hinh kenh` — Set node giữ 4 giá trị cần sửa: tên kênh + 3 folder ID
4. `Tai JSON metadata` — HTTP Request `files/<id>?alt=media`, đọc dạng text
5. `Chuan hoa metadata` — Code: parse, validate, build sẵn object `status`
6. `Tim MP4 va JPG` — HTTP Request `files.list`, query theo tên gốc
7. `Ghep thong tin file` — Code: bắt buộc có `.mp4`, thumbnail tuỳ chọn
8. `Tai video tu Drive` — Drive download vào binary `video`
9. `Upload YouTube` — node YouTube, `binaryProperty: video`
10. `Cap nhat status video` — HTTP PUT `videos.update`
11. `Co thumbnail` → `Tai thumbnail` → `Dat thumbnail` (`thumbnails.set`)
12. `Danh sach file can chuyen` → `Chuyen sang done`

Nhánh lỗi: mọi node đều bật error output nối vào `Danh sach file loi` →
`Chuyen file loi` → `Thong bao loi` (Telegram, đang tắt) → `Bao loi`
(stopAndError, để execution được lưu lại là failed).
`workflows/youtube-upload-error.json` là error workflow riêng, chỉ để thông báo.

### Khác với kế hoạch ban đầu
- Node YouTube **có** hỗ trợ `publishAt`, nhưng `containsSyntheticMedia` thì
  không → vẫn cần bước `videos.update`.
- `videos.update` với `part=status` **ghi đè toàn bộ** object status: field nào
  không gửi sẽ bị reset về mặc định. Vì vậy `status` được build một lần ở node
  `Chuan hoa metadata` và dùng lại y nguyên.
- Node YouTube **không** nạp cả file 1GB vào RAM: khi binary ở `filesystem`
  mode nó stream từ đĩa theo chunk 1 MiB và dùng `uploadType=resumable`
  (`getBinaryStream(binaryData.id, UPLOAD_CHUNK_SIZE)`). Các báo lỗi OOM trên
  forum là từ chế độ memory.
- Lỗi xảy ra *sau* khi upload thành công → file được chuyển vào `done/`, không
  phải `failed/`, để tránh upload lại (trùng video + tốn thêm 1600 quota).
- Node YouTube **không retry được từng chunk**: vòng lặp chỉ bỏ qua HTTP 308,
  mọi lỗi khác ném ra ngay, và `offset` cộng vô điều kiện nên nó cũng không hỏi
  Google đã nhận tới đâu để resume. Một cú rớt mạng giữa chừng = mất cả lần
  upload, mà 1600 quota vẫn bị trừ. Vì vậy node `Upload YouTube` **cố ý không
  bật `retryOnFail`**: với quota chỉ đủ ~6 video/ngày, retry tự động có thể đốt
  sạch quota trong vài phút. Hỏng thì xử lý tay.

## Template cộng đồng đã xem và loại (31/10/2026)
Template n8n #3906 *"Automate YouTube uploads with AI-generated metadata from
Google Drive"* — đọc JSON gốc qua `api.n8n.io/api/templates/workflows/3906`:

- Dùng **đúng cùng cơ chế**: `googleDrive` v3 `operation: download` → node
  `youTube` v1 `operation: upload`. Nghĩa là file vẫn chảy qua n8n y hệt, không
  giúp gì cho giới hạn của n8n Cloud.
- `Get Transcript` gọi **Apify** (actor `pintostudio~youtube-transcript-scraper`)
  để lấy phụ đề của video *vừa upload xong* — phụ đề tự động của YouTube thường
  mất hàng chục phút mới có, nên bước này rất dễ rỗng. Token Apify còn để trần
  trong một node Set.
- Upload với `title: "adadada"` rồi mới update metadata sau.
- `Delete File from Upload Folder` **xoá** file nguồn trên Drive thay vì chuyển
  sang `done/` — hỏng nửa chừng là mất file gốc.
- Không có nhánh lỗi, không thumbnail, không `publishAt`, không
  `containsSyntheticMedia`, hardcode `regionCode: DE` và `categoryId: 25`.
- Phần AI sinh title/description/tags là thứ pipeline của ta **đã tự có** trong
  file `.json` metadata.

Kết luận: không dùng. Vẫn giữ workflow tự viết.

## Hai lỗi đã sửa trong uploader (31/10/2026)
Cả hai chỉ phát tác khi chạy không người trông — đúng chế độ Scheduled Task —
nên không lộ ra khi chạy thử bằng tay. Mỗi lỗi có test riêng trong
`scripts/test_youtube_uploader.py`.

1. **Hết quota bị coi là lỗi của file.** `process_one` bắt `except Exception`,
   mà `HttpError` cũng là `Exception`, nên nhánh `except HttpError` xử lý
   `quotaExceeded` ở `run_once` **không bao giờ chạy**. Hậu quả: hết quota giữa
   phiên thì mọi file còn lại trong `pending/` lần lượt bị đẩy sang `failed/`
   dù không có gì sai, phải bê tay về.
   → Thêm `is_quota_error()` + exception `QuotaExceeded` kiểm tra **bên trong**
   `process_one`: để nguyên file trong `pending/` và dừng cả vòng lặp.
   Nhận diện `quotaExceeded`, `uploadLimitExceeded`, `dailyLimitExceeded`;
   **không** tính `rateLimitExceeded` vì đó là lỗi tạm thời đã có retry riêng.

2. **`state.clear()` chạy cả khi `move_file` thất bại.** Nhánh lỗi xoá state vô
   điều kiện khi đã có `video_id`, nhưng `move_file` có thể hỏng và chỉ được
   log rồi bỏ qua. Khi đó file vẫn trong `pending/` mà state đã mất → lần chạy
   sau **upload lại: trùng video + mất thêm 1600 quota**. Đúng cái tình huống
   class `State` được viết ra để chặn.
   → Thêm `move_all()` trả về danh sách file còn kẹt và tự bỏ khỏi list những
   cái đã chuyển xong (nên gọi lại không chuyển trùng). Chỉ `state.clear()` khi
   không còn file nào kẹt.

Kèm theo: bỏ dấu tiếng Việt ở 3 chuỗi log/notify. Qua Scheduled Task, stdout
không phải console nên Python dùng codepage hệ thống, `Đ` encode không được →
logging in `--- Logging error ---` rồi bỏ dòng. Không chết script (file log đã
ghi UTF-8) nhưng mất dòng trên màn hình. `docs/uploader.md` §5 thêm
`setx PYTHONUTF8 1`.

## Dọn file sau khi upload (`--on-success`, 01/11/2026)
Mặc định vẫn `move` (sang `done/`) để không đổi hành vi của task đang chạy.
Thêm `trash` (thùng rác Drive, hồi phục 30 ngày) và `delete` (xoá vĩnh viễn).

Lưu ý: `CLAUDE.md` từng ghi việc xoá file nguồn là lý do **loại** template n8n
#3906 — "hỏng nửa chừng là mất file gốc". Điều đó vẫn đúng khi Drive là bản duy
nhất. Giờ bản gốc nằm trên máy render nên Drive chỉ là đường vận chuyển, xoá
được. Vì vậy mặc định là `trash` chứ không `delete` khi bật tính năng này.

Chỉ áp dụng cho **upload thành công hoàn toàn**:
- Thất bại → `failed/`, không xoá
- Video đã lên nhưng bước sau lỗi → `done/`, không xoá
- Trash/delete hỏng giữa chừng → file kẹt được chuyển sang `done/` dự phòng,
  nên không còn ở `pending/` và không có nguy cơ upload lại

## `--max-per-run` (01/11/2026)
Mặc định đổi từ 5 → **1 video mỗi lần chạy**. Video 1GB mất hàng chục phút nên
mỗi lần chạy ngắn thì giữ lock ít, hỏng giữa chừng mất ít tiến độ.

Kèm theo sửa một chỗ nhận giá trị lạ: `items[:n]` với `n = 0` thì im lặng không
làm gì, còn `n` âm thì **cắt từ đầu đến gần cuối** — `--max-per-run -1` xử lý
9/10 video, ngược hẳn ý nghĩa "giới hạn". Giờ parser bắt buộc ≥ 1.

## Theo dõi: Telegram + CSV trên Drive (01/11/2026)
**Thông báo** — `notify()` qua Telegram đã có từ đầu, chỉ cần 2 biến môi
trường. Thêm tình huống thứ 4: **uploader chết hẳn**. Không có nó thì token hết
hạn hay Drive đổi quyền là im lặng hoàn toàn, không ai biết.

**Dashboard** — `YouTube-Upload/<kênh>/upload-history.csv`, mỗi video một dòng,
8 cột. Mở bằng Google Sheets để lọc. Dùng đúng scope `drive` đang có nên không
phải đăng nhập lại. Drive không có API append nên phải tải về - nối - đẩy lên;
file nhỏ nên chấp nhận được.

Trạng thái: `OK` | `LOI` | `OK-LOI-SAU` (video đã lên, bước sau lỗi).

**`log_history()` luôn nuốt lỗi.** Ghi sổ thất bại không được làm hỏng kết quả
upload — để lỗi thoát ra ở nhánh thành công thì một video đã lên YouTube bị coi
là thất bại, đắt hơn nhiều so với một dòng CSV bị thiếu. Có test riêng cho
chuyện này.

## Quota
`videos.insert` = 1600 units, `videos.update` = 50, `thumbnails.set` = 50.
Quota mặc định 10.000 units/ngày → **~5-6 video/ngày cho cả project**. Phải
kiểm tra số thực tại Cloud Console → IAM & Admin → Quotas.

## Đã kiểm thử (31/10/2026, n8n 2.45.7 local qua Docker)
Chạy trên máy Windows, container `n8n-test` port 5679, chưa nối credential thật.

- **Import**: cả 2 workflow import thành công bằng `n8n import:workflow`.
- **Node type + typeVersion**: đọc trực tiếp từ class node trong container, tất cả
  đều được hỗ trợ — if 2.2 (max 2.4), set 3.4 (max 3.5), httpRequest 4.2 (max 4.5),
  googleDrive 3, youTube 1, code 2, telegram 1.2, stopAndError 1.
  ⚠ Đừng dùng `dist/types/nodes.json` để kiểm tra: với node nhiều version, file này
  chỉ chứa description của version gốc (báo googleDrive chỉ có [1,2]).
- **Tên tham số**: đối chiếu với description thật của từng typeVersion — khớp hết.
  `pollTimes` không có trong description của Drive Trigger vì n8n core tự chèn cho
  node polling, default đúng là `{item:[{mode:"everyMinute"}]}`.
- **Logic Code node**: 33 unit test (Node.js) pass — validate metadata, ghép file,
  cắt title/tags, escape dấu nháy, phân luồng done/failed.
- **Chạy thật trong n8n** (`n8n execute`, 3 workflow test dùng chính jsCode của
  workflow thật, node cần credential được thay bằng mock):
  - đường thành công: 3 file đều về `done/`, videoUrl đúng
  - lỗi sau khi upload: về `done/` kèm thông báo "Video DA len YouTube"
  - lỗi sớm khi các node phía trên **chưa từng chạy**: `try/catch` trong Code node
    **có** bắt được lỗi của `$('Node')` → nhánh lỗi không sụp, vẫn chuyển được file
    `.json` sang `failed/`. Đây là giả định quan trọng nhất và nó đúng.
  - 12 expression trên node thường resolve đúng, gồm `jsonBody` của `videos.update`
    (object literal lồng trong `{{ }}`) ra đủ 7 field của `status`.

**Chưa test được** (cần credential thật): gọi Google Drive API, `videos.insert`,
`videos.update`, `thumbnails.set`, Drive Trigger poll thật, và upload file 1GB.

Lệnh dọn container test:
```bash
docker rm -f n8n-test && docker volume rm n8n_test_data
```

## Cấu hình n8n (đã có trong docker-compose.yml)
- `N8N_DEFAULT_BINARY_DATA_MODE=filesystem`
- `N8N_CONCURRENCY_PRODUCTION_LIMIT=1`
- Pruning bật, không lưu execution thành công
- OAuth redirect URI: `https://<N8N_DOMAIN>/rest/oauth2-credential/callback`
- Google OAuth app nên chuyển sang "In production" để refresh token không hết hạn sau 7 ngày

## Files trong thư mục
**Đang dùng:**
- `scripts/youtube_uploader.py` — chạy trên VPS, quét Drive và upload YouTube
- `scripts/test_youtube_uploader.py` — 58 unit test, mock hết phần gọi mạng
- `scripts/push_to_drive.py` — chạy trên máy render, đẩy video/thumb/metadata lên Drive
- `scripts/requirements.txt`
- `docs/uploader.md` — triển khai uploader trên VPS Windows
- `docs/upload-video.md` — hướng dẫn phía máy render: đẩy 3 file lên `pending/`
- `docs/setup.md` — vẫn dùng **§4 Google Cloud** và **§10 quota/audit**; §0-3 và §5-7 là phần n8n, không còn cần
- `docs/public-site/` — trang privacy policy, đã đăng tại quangdang2001.github.io/video-analytics-policy
- `docs/privacy-policy.md` — bản Markdown gốc của trang trên

**Giữ lại nhưng không còn dùng** (phòng khi quay lại n8n):
- `docker-compose.yml`, `docker-compose.local.yml`, `.env.example`
- `workflows/youtube-upload.json`, `workflows/youtube-upload-error.json`

## Việc tiếp theo
Đã xong:
- [x] Google Cloud: bật API, khai 8 scope, publish consent screen, tạo 2 OAuth client
- [x] Privacy policy đã đăng công khai (GitHub Pages)
- [x] `scripts/push_to_drive.py` — phía pipeline
- [x] `scripts/youtube_uploader.py` — phía VPS
- [x] 58 unit test cho uploader, đã commit (`python -m unittest test_youtube_uploader`)
- [x] Sửa 2 lỗi phát hiện khi review (31/10/2026) — xem mục dưới
- [x] `docs/uploader.md`

Còn lại:
- [ ] Copy `scripts/` lên VPS, cài Python + requirements — `docs/uploader.md` §1-2
- [ ] Đăng nhập lần đầu trên VPS (Drive + YouTube) — §3
- [ ] Chạy thử 1 video ngắn, thử cả nhánh lỗi — §4
- [ ] Tạo Scheduled Task — §5 (nhớ `setx PYTHONUTF8 1`)
- [ ] Nộp form audit YouTube để đăng public/hẹn giờ — `docs/setup.md` §10
- [ ] Kế hoạch thay VPS trước 13/01/2027 (Server 2016 hết hỗ trợ)

## Lưu ý chính sách nội dung
Upload tự động hợp lệ; rủi ro nằm ở nội dung: khai báo synthetic content khi dùng AI trông như thật, tránh nội dung lặp lại/sản xuất hàng loạt (YPP), bản quyền nhạc/hình, tần suất đăng hợp lý.
