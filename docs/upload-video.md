# Đẩy video lên Drive để VPS tự upload

Hướng dẫn cho **máy render** (máy có file video). Việc của máy này chỉ là đặt
đúng 3 file vào folder `pending/` trên Drive. VPS tự làm phần còn lại.

```
Máy này                          Google Drive                    VPS
───────                          ────────────                    ───
lever_price_review.mp4  ──┐
lever_price_review.jpg  ──┼────► YouTube-Upload/
lever_price_review.json ──┘        <ten-kenh>/
                                     pending/  ◄── quét mỗi 5 phút
                                     done/     ◄── xong thì file về đây
                                     failed/   ◄── lỗi thì về đây
```

Folder `pending/`: mở Drive → `YouTube-Upload/<ten-kenh>/pending/`, hoặc dùng
link riêng của bạn.

> Link folder không để trong repo vì repo này public. Lấy link của bạn bằng
> cách mở folder `pending/` trên Drive rồi copy URL trên thanh địa chỉ, dán vào
> đây để lần sau mở nhanh.

> **Máy này không bao giờ chạm vào tài khoản kênh YouTube.** Nó chỉ cần quyền
> Drive. Token của kênh chỉ tồn tại trên VPS. Nếu máy này bị nhiễm gì, kênh
> không bị ảnh hưởng.

---

## Thông số của bạn

Điền một lần rồi dùng xuyên suốt tài liệu:

| Giá trị | Của bạn |
|---|---|
| Tên kênh (= tên folder trên Drive) | `ten-kenh` ← **sửa lại** |
| Thư mục chứa `scripts/` trên máy này | `C:\yt\yt-uploader\scripts` |

---

## Phần 1 — Chuẩn bị máy này (làm một lần)

Bỏ qua phần này nếu bạn chỉ kéo tay file vào Drive (xem Cách B ở Phần 3).

### 1.1 Cài Python

Tải Python 3.12+ từ [python.org](https://www.python.org/downloads/windows/), lúc
cài tick **Add python.exe to PATH**.

### 1.2 Lấy script

```powershell
git clone https://github.com/quangdang2001/yt-uploader.git C:\yt
```

### 1.3 Chép `client_secret.json`

Vào `C:\yt\yt-uploader\scripts\`, loại **Desktop app**, đúng tên
`client_secret.json`. Google tải về với tên dài
`client_secret_xxx.apps.googleusercontent.com.json` — phải đổi tên.

File này **không có trong repo** (nằm trong `.gitignore`).

### 1.4 Cài thư viện

```powershell
cd C:\yt\yt-uploader\scripts
```

```powershell
python -m pip install -r requirements.txt
```

> Dùng `python -m pip` thay vì `pip` để tránh lỗi
> `Fatal error in launcher` khi Python được cài lại sang chỗ khác.

### 1.5 Đăng nhập Drive

```powershell
python push_to_drive.py --channel ten-kenh --video C:\bat-ky-file.mp4 --title "x" --dry-run
```

Lệnh này chỉ để kích hoạt đăng nhập — `--dry-run` nên không upload gì. Trình
duyệt mở **một lần** cho Drive (không hỏi YouTube). Chọn tài khoản chứa thư mục
`YouTube-Upload`. Gặp màn *unverified app* → **Advanced → Go to ... (unsafe)**.

Xong thì có `token-drive.json`, lần sau không hỏi lại.

---

## Phần 2 — Chuẩn bị 3 file cho mỗi video

Ba file **cùng tên gốc**, đặt cạnh nhau:

| File | Bắt buộc | Ghi chú |
|---|---|---|
| `<ten-goc>.mp4` | ✅ | Video |
| `<ten-goc>.jpg` | ❌ | Thumbnail. Cũng nhận `.jpeg`, `.png` |
| `<ten-goc>.json` | ✅ | Metadata |

### Quy tắc đặt tên

- Dùng **ASCII**, không dấu tiếng Việt. Ký tự ngoài `A-Za-z0-9._-` sẽ bị đổi
  thành `-`.
- Phần mở rộng **chữ thường**. `.JPG` chữ hoa sẽ không được nhận, và không có
  báo lỗi rõ ràng — video vẫn lên, chỉ là thiếu thumbnail.
- Tên gốc phải khớp **từng ký tự**, kể cả hoa/thường, giữa 3 file.
- **Không** để cả `.jpg` lẫn `.png` cùng tên: script lấy một trong hai tuỳ thứ
  tự Drive trả về, không đoán được cái nào.

### File metadata `.json`

```json
{
  "title": "Tiêu đề video",
  "description": "Mô tả nhiều dòng.\nXuống dòng dùng \\n.",
  "tags": ["tu-khoa-1", "tu-khoa-2"],
  "categoryId": "22",
  "privacyStatus": "private",
  "containsSyntheticMedia": true,
  "madeForKids": false
}
```

Chỉ `title` là bắt buộc, còn lại có mặc định.

| Field | Giới hạn / giá trị |
|---|---|
| `title` | Tối đa **100** ký tự |
| `description` | Tối đa **5000** ký tự |
| `tags` | Tổng độ dài nối bằng dấu phẩy tối đa **480**. Nhận mảng hoặc chuỗi `"a,b,c"` |
| `categoryId` | Chuỗi. `22` People & Blogs, `27` Education, `24` Entertainment, `28` Science & Tech, `10` Music |
| `privacyStatus` | `private` \| `unlisted` \| `public`. Mặc định `private` |
| `publishAt` | Hẹn giờ, RFC3339 **phải có múi giờ**: `2026-11-05T12:00:00Z` hoặc `2026-11-05T19:00:00+07:00` |
| `containsSyntheticMedia` | `true` khi nội dung AI trông như thật |
| `madeForKids` | Mặc định `false` |

**Có `publishAt` thì `privacyStatus` bị ép về `private`** — YouTube chỉ cho hẹn
giờ video đang private.

### Thumbnail

1280×720 (16:9), **dưới 2MB**. Cần kênh đã xác minh số điện thoại tại
[youtube.com/verify](https://www.youtube.com/verify). Chưa xác minh thì
`thumbnails.set` trả 403 — **video vẫn lên bình thường**, chỉ thiếu thumbnail.

---

## Phần 3 — Đẩy lên Drive

### Cách A — dùng script (khuyến nghị)

Script tự validate metadata, đặt tên đồng bộ và upload **đúng thứ tự**.

**Bước 1. Kiểm tra trước, không upload gì, không tốn quota:**

```powershell
python push_to_drive.py --channel ten-kenh --video "D:\duong-dan\v001.mp4" --thumb "D:\duong-dan\v001.jpg" --meta "D:\duong-dan\v001.json" --dry-run
```

Nó bắt lỗi title quá 100 ký tự, `publishAt` thiếu múi giờ, tags quá dài... ngay
lập tức. Rất nên chạy bước này với mọi video dài — thay vì phát hiện lỗi ở phút
thứ 20 của lần upload thật.

**Bước 2. Đẩy lên thật** — bỏ `--dry-run`:

```powershell
python push_to_drive.py --channel ten-kenh --video "D:\duong-dan\v001.mp4" --thumb "D:\duong-dan\v001.jpg" --meta "D:\duong-dan\v001.json"
```

Không có file `.json` sẵn thì khai metadata ngay trên dòng lệnh, script tự sinh:

```powershell
python push_to_drive.py --channel ten-kenh --video "D:\duong-dan\v001.mp4" --title "Tiêu đề video" --description-file "D:\duong-dan\v001.txt" --tags "tu-khoa-1,tu-khoa-2" --category 27 --synthetic
```

Các cờ dòng lệnh **ghi đè** giá trị trong `--meta`, nên dùng kết hợp được.

Cờ hữu ích:

| Cờ | Tác dụng |
|---|---|
| `--dry-run` | Chỉ validate, không upload |
| `--id v001` | Đặt tên gốc thủ công, thay vì suy từ tên file video |
| `--overwrite` | Xoá bộ file cũ cùng tên trong `pending/` |
| `--publish-at "2026-11-05T19:00:00+07:00"` | Hẹn giờ |
| `--synthetic` | Khai nội dung AI |
| `--privacy unlisted` | Đổi privacy |

Nếu `pending/` đã có file cùng tên, script **dừng lại** thay vì tạo bộ trùng.
Dùng `--id` khác hoặc thêm `--overwrite`.

### Cách B — kéo tay vào Drive

Mở folder `YouTube-Upload/<ten-kenh>/pending/` trên Drive rồi:

1. Kéo `v001.mp4` vào — **đợi upload xong 100%**
2. Kéo `v001.jpg` vào (nếu có) — đợi xong
3. Kéo `v001.json` vào **sau cùng**

> ### Quy tắc quan trọng nhất của cả tài liệu này
>
> **File `.json` phải lên sau cùng.** Nó chính là tín hiệu trigger: VPS thấy
> `v001.json` thì mới đi tìm `v001.mp4`.
>
> Nếu `.json` lên trước khi `.mp4` xong, VPS sẽ không tìm thấy video, báo
> `Khong tim thay v001.mp4` và **đẩy cả bộ sang `failed/`**. Với file 1GB thì
> khoảng trống này là vài phút — đây là lỗi dễ gặp nhất khi kéo tay cả 3 file
> một lúc.
>
> Dùng Cách A thì không phải lo, script tự xếp thứ tự.

---

## Phần 4 — Sau khi đẩy lên

Không cần làm gì thêm. VPS chạy Scheduled Task mỗi **5 phút**, tự nhặt và
upload.

| Kết quả | Dấu hiệu |
|---|---|
| Thành công | Cả 3 file rời khỏi `pending/`, video hiện trong YouTube Studio |
| Thất bại | Cả 3 file chuyển sang `failed/` |

Thành công thì file đi đâu tuỳ chế độ `--on-success` của task trên VPS: sang
`done/` (mặc định), vào thùng rác Drive (`trash`), hoặc xoá vĩnh viễn
(`delete`). Chi tiết: `docs/uploader.md` §5b.

File vào `failed/` thì **không bao giờ bị xoá**, bất kể chế độ nào.

Vài điểm về cơ chế:

- **Độ trễ tối đa 5 phút** (bằng chu kỳ task).
- **1 video mỗi lần chạy** (mặc định), xử lý cũ nhất trước. Đẩy 10 video vào
  `pending/` thì chúng lên dần, mỗi 5 phút một cái.
- **Không chạy chồng**: video 1GB upload 20 phút thì các lần task gọi chen vào
  tự thoát ngay, không sinh tiến trình trùng.
- **Hết quota thì file ở lại `pending/`**, không bị đẩy sang `failed/`, và lần
  chạy sau tự làm lại. Không cần can thiệp.

### Xem log trên VPS

```powershell
Get-Content C:\yt\yt-uploader\scripts\logs\uploader.log -Tail 50 -Wait
```

### Chạy ngay, không đợi 5 phút

Trên VPS:

```powershell
cd C:\yt\yt-uploader\scripts
```

```powershell
python youtube_uploader.py --channel ten-kenh --once --verbose
```

---

## Giới hạn cần nhớ

**Quota: mỗi video tốn 1600 units, tổng 10.000/ngày → ~6 video/ngày** cho cả
project (nhiều kênh dùng chung). Đẩy 20 video vào `pending/` một lúc cũng không
sao — VPS làm hết quota rồi để dành phần còn lại cho hôm sau, không mất file.
Quota reset khoảng **15:00 giờ VN**.

**Project chưa qua audit YouTube thì `public` và `publishAt` không có tác dụng**
— YouTube ép mọi video về private. Đây là giới hạn phía Google, không phải lỗi
script. `title` / `description` / `tags` / `thumbnail` vẫn chạy đủ; chỉ riêng
chuyện công khai là phải vào Studio bật tay, cho tới khi nộp xong *YouTube API
Services Audit and Quota Extension Form* (xem `docs/setup.md` §10).

---

## Sự cố thường gặp

| Hiện tượng | Nguyên nhân / xử lý |
|---|---|
| File về `failed/`, log báo `Khong tim thay ....mp4` | `.json` lên trước khi `.mp4` xong. Kéo cả bộ từ `failed/` về `pending/`, lần này để `.json` sau cùng |
| Video lên nhưng không có thumbnail | Tên file sai hoa/thường, phần mở rộng chữ hoa (`.JPG`), ảnh quá 2MB, hoặc kênh chưa xác minh SĐT |
| Video lên mà vẫn private dù khai `public` | Project chưa audit. Bật tay trong Studio |
| `Thieu title` khi chạy `push_to_drive.py` | File `.json` thiếu `title`, hoặc chưa truyền `--title` |
| `... da co trong pending/` | Đã có bộ file cùng tên. Dùng `--id` khác hoặc `--overwrite` |
| `publishAt thieu mui gio` | Phải viết `2026-11-05T12:00:00Z` hoặc `...+07:00` |
| `Fatal error in launcher` khi gọi `pip` | Dùng `python -m pip` thay cho `pip` |
| Không thấy gì xảy ra sau 5 phút | Kiểm tra Scheduled Task trên VPS còn bật, và xem log |

---

## Checklist nhanh mỗi video

- [ ] 3 file cùng tên gốc, ASCII không dấu
- [ ] Phần mở rộng chữ thường
- [ ] `title` không quá 100 ký tự
- [ ] Thumbnail 1280×720, dưới 2MB
- [ ] Khai `containsSyntheticMedia` nếu nội dung AI trông như thật
- [ ] Chạy `--dry-run` trước khi đẩy video dài
- [ ] `.json` lên **sau cùng**
