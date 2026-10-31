# Privacy Policy (bản mẫu để nộp form audit)

> **Cách dùng:** form *YouTube API Services Audit and Quota Extension* yêu cầu
> một link privacy policy **công khai, truy cập được không cần đăng nhập**.
> File Markdown trong repo không tính. Hãy:
>
> 1. Thay toàn bộ placeholder `[...]` bằng thông tin thật.
> 2. Đưa lên một URL công khai — GitHub Pages, một trang trên website của bạn,
>    hoặc Notion/Google Sites ở chế độ public đều được.
> 3. Dán URL đó vào form audit và vào OAuth consent screen trong Google Cloud
>    (mục *App information → Privacy policy link*).
>
> Nội dung dưới đây mô tả đúng hệ thống này: một tool nội bộ, chỉ upload video
> của chính chủ kênh, không có người dùng bên thứ ba. Đừng thêm điều khoản mô
> tả việc bạn không làm — reviewer đối chiếu policy với demo thực tế.

---

## English

**Last updated: [NGÀY]**

### 1. Who we are

[TÊN BẠN / TÊN CÔNG TY] ("we") operates an internal video publishing tool
("the Tool") used solely to upload videos to YouTube channels that we own and
operate. The Tool is not offered to the public and has no external users.

Contact: **[EMAIL]**

### 2. What the Tool does

The Tool automates publishing of our own video content. A rendering pipeline on
our own computer places a video file, an optional thumbnail image and a
metadata file into our own Google Drive. A self-hosted
[n8n](https://n8n.io) instance running on our own server detects the new files
and uploads the video to our own YouTube channel through the official YouTube
Data API v3.

### 3. Data we access

The Tool accesses data only from Google accounts that we own, after we grant
access through Google's OAuth consent flow:

| Scope | Why it is needed |
|---|---|
| `https://www.googleapis.com/auth/drive` | Read the video, thumbnail and metadata files our pipeline wrote, and move them to a different folder after upload |
| `https://www.googleapis.com/auth/youtube.upload` | Upload the video file to our channel |
| `https://www.googleapis.com/auth/youtube` | Set the video's privacy status, scheduled publish time, synthetic-media declaration and thumbnail |

The Tool does not request access to, and does not read, any other Google
service, and does not access any account other than our own.

### 4. Data we store

- **OAuth tokens** for our own Google accounts, stored encrypted on our own
  server (n8n credential encryption) and in a local token file on our own
  rendering machine. They are never transmitted to any third party.
- **Video files and metadata**, temporarily, while an upload is in progress.
  Video files are deleted from the server once the upload completes.
- **Execution logs** of failed uploads, retained for at most 72 hours, for
  troubleshooting. Successful runs are not stored.

We do not collect personal data from any person other than ourselves. We do not
collect data about viewers of our videos beyond the aggregate statistics YouTube
itself provides to channel owners.

### 5. Sharing

We do not sell, rent or share any data obtained through the YouTube API or
Google Drive API with any third party. No data obtained through these APIs is
used for advertising, profiling, or training machine-learning models.

### 6. Security

The Tool runs on a server with no inbound ports exposed to the internet; access
is through an authenticated Cloudflare Tunnel over HTTPS. Credentials are
encrypted at rest. Access is limited to [SỐ NGƯỜI, ví dụ "the owner only"].

### 7. Retention and deletion

OAuth tokens are kept until we revoke them. Temporary video files are deleted
after each upload. To revoke the Tool's access at any time, we remove its
permission at [Google Account permissions](https://myaccount.google.com/permissions),
which immediately and permanently ends its ability to access any data.

### 8. YouTube API Services

The Tool uses YouTube API Services. By using it we are bound by the
[YouTube Terms of Service](https://www.youtube.com/t/terms) and the
[Google Privacy Policy](https://policies.google.com/privacy).

### 9. Changes

If this policy changes, the updated version will be published at this same URL
with a new "Last updated" date.

---

## Tiếng Việt

**Cập nhật lần cuối: [NGÀY]**

### 1. Chúng tôi là ai

[TÊN BẠN / TÊN CÔNG TY] ("chúng tôi") vận hành một công cụ đăng video nội bộ
("Công cụ"), chỉ dùng để upload video lên các kênh YouTube do chúng tôi sở hữu.
Công cụ không mở cho người ngoài sử dụng và không có người dùng bên thứ ba.

Liên hệ: **[EMAIL]**

### 2. Công cụ làm gì

Công cụ tự động hoá việc đăng video của chính chúng tôi. Pipeline render trên
máy của chúng tôi đặt file video, ảnh thumbnail (tuỳ chọn) và file metadata vào
Google Drive của chúng tôi. Một instance n8n self-host trên server của chúng
tôi phát hiện file mới và upload video lên kênh YouTube của chúng tôi thông qua
YouTube Data API v3 chính thức.

### 3. Dữ liệu Công cụ truy cập

Chỉ truy cập dữ liệu trong các tài khoản Google do chúng tôi sở hữu, sau khi
chúng tôi tự cấp quyền qua luồng OAuth của Google:

| Scope | Lý do cần |
|---|---|
| `.../auth/drive` | Đọc file video, thumbnail, metadata do pipeline ghi ra; chuyển file sang folder khác sau khi upload |
| `.../auth/youtube.upload` | Upload file video lên kênh |
| `.../auth/youtube` | Đặt trạng thái riêng tư, giờ đăng, khai báo nội dung AI, và thumbnail |

Công cụ không yêu cầu quyền với bất kỳ dịch vụ Google nào khác, và không truy
cập tài khoản nào ngoài tài khoản của chúng tôi.

### 4. Dữ liệu Công cụ lưu

- **Token OAuth** của tài khoản Google của chúng tôi, lưu dạng mã hoá trên
  server của chúng tôi và trong file token trên máy render. Không gửi cho bất
  kỳ bên thứ ba nào.
- **File video và metadata**, lưu tạm trong lúc upload. File video bị xoá khỏi
  server ngay sau khi upload xong.
- **Log execution lỗi**, giữ tối đa 72 giờ để xử lý sự cố. Lần chạy thành công
  không được lưu.

Chúng tôi không thu thập dữ liệu cá nhân của bất kỳ ai khác ngoài chính mình,
và không thu thập dữ liệu về người xem ngoài số liệu tổng hợp mà YouTube cung
cấp cho chủ kênh.

### 5. Chia sẻ dữ liệu

Chúng tôi không bán, cho thuê hay chia sẻ dữ liệu lấy từ YouTube API / Google
Drive API cho bất kỳ bên thứ ba nào. Dữ liệu từ các API này không được dùng cho
quảng cáo, lập hồ sơ người dùng, hay huấn luyện model.

### 6. Bảo mật

Công cụ chạy trên server không mở port nào ra Internet; truy cập qua Cloudflare
Tunnel có xác thực, dùng HTTPS. Credential được mã hoá khi lưu. Số người có
quyền truy cập: [SỐ NGƯỜI].

### 7. Lưu trữ và xoá

Token OAuth được giữ cho đến khi chúng tôi thu hồi. File video tạm bị xoá sau
mỗi lần upload. Có thể thu hồi quyền của Công cụ bất cứ lúc nào tại
[Google Account permissions](https://myaccount.google.com/permissions) — khi đó
Công cụ mất hoàn toàn khả năng truy cập dữ liệu.

### 8. YouTube API Services

Công cụ sử dụng YouTube API Services. Khi sử dụng, chúng tôi tuân thủ
[Điều khoản dịch vụ YouTube](https://www.youtube.com/t/terms) và
[Chính sách bảo mật của Google](https://policies.google.com/privacy).

### 9. Thay đổi

Nếu chính sách này thay đổi, bản cập nhật sẽ được đăng tại cùng URL này kèm
ngày "Cập nhật lần cuối" mới.
