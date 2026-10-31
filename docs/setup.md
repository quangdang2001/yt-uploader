# Dựng hệ thống từ đầu

Làm tuần tự từ bước 1. Mỗi bước có cách kiểm tra "xong chưa" ở cuối.

Luồng cuối cùng:

```
pipeline render (máy bạn)
   └─ push_to_drive.py ──► Drive: YouTube-Upload/<kênh>/pending/
                                      │  (video → thumb → .json, .json đi cuối)
                                      ▼
                           n8n (VPS) bắt file .json mới
                                      │
                                      ├─ tải video từ Drive xuống đĩa
                                      ├─ videos.insert (stream, resumable)
                                      ├─ videos.update (publishAt, synthetic)
                                      ├─ thumbnails.set
                                      └─ move cả bộ file sang done/ hoặc failed/
```

---

## Chuẩn bị trước khi bắt đầu

Gom đủ những thứ này trước, đỡ phải dừng giữa chừng:

| Cần có | Ghi chú |
|---|---|
| **Tài khoản Google sở hữu kênh YouTube** | Phải là tài khoản quản lý được kênh. Nếu kênh thuộc Brand Account, bạn phải đăng nhập đúng tài khoản có quyền |
| **Kênh đã xác minh số điện thoại** | youtube.com/verify. Không xác minh thì `thumbnails.set` trả 403 |
| **Cùng một tài khoản Google cho cả Drive và YouTube** | Đơn giản nhất. Khác tài khoản vẫn được, miễn là tài khoản Drive thấy được folder |
| **Một video ngắn để test** | 10–50MB, không phải 1GB. Test nhanh và không phí quota chờ đợi |
| **Tên kênh dạng slug** | Ví dụ `kenh-review`. Dùng làm tên folder trên Drive |
| **Python + pip trên máy render** | Cho `scripts/push_to_drive.py` |

Ba giá trị bạn sẽ phải tự ghi lại trong quá trình làm (dán vào node `Cau hinh kenh`):

```
pendingFolderId = ____________________
doneFolderId    = ____________________
failedFolderId  = ____________________
```

---

## 0. Chạy tạm trên máy Windows (không cần VPS)

Nếu chưa có VPS Linux, chạy ngay trên máy của bạn được. Workflow này **không
dùng webhook** — Google Drive Trigger là polling, nó tự gọi ra Google chứ không
cần ai gọi vào — nên n8n không cần truy cập được từ Internet. Bỏ qua luôn
Cloudflare Tunnel và domain.

```bash
copy .env.example .env
```

Trong `.env` chỉ cần điền **một** dòng:

```
N8N_ENCRYPTION_KEY=<chuỗi hex 64 ký tự>
```

Sinh key (PowerShell):

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
```

Hai dòng `N8N_DOMAIN` và `CLOUDFLARE_TUNNEL_TOKEN` cứ để trống, bản local không
đụng tới. Rồi chạy:

```bash
docker compose -f docker-compose.local.yml up -d
```

Mở `http://localhost:5678` → tạo tài khoản owner.

> **Đặt `N8N_ENCRYPTION_KEY` ngay bây giờ, đừng bỏ trống.** Không đặt thì n8n tự
> sinh key và giấu trong volume. Sau này chuyển lên VPS mà không có đúng key cũ
> thì toàn bộ credential OAuth đã lưu thành rác, phải Connect lại từng kênh.

### Bước nào còn áp dụng

| Bước | Khi chạy local |
|---|---|
| §1–3 VPS, Cloudflare, compose | **Bỏ qua** |
| §4 Google Cloud | Vẫn làm, chỉ khác redirect URI (xem §4.3) |
| §5 Credential, §6 folder Drive, §7 import workflow | Y nguyên |
| §8 script pipeline | Y nguyên, mà giờ chạy cùng máy nên tiện hơn |
| §9 chạy thử, §10 quota, §11 sự cố | Y nguyên |

### Những điều phải chấp nhận

- **Máy ngủ là không upload.** Trigger poll mỗi phút; máy sleep thì chẳng có gì
  chạy. Tin tốt: trigger lọc theo `createdTime > lần kiểm tra cuối`, nên khi máy
  thức dậy nó vẫn bắt được các file tạo trong lúc ngủ — **trễ chứ không mất**.
  Muốn chạy đều: Settings → Power → Screen and sleep → *Never*, và bật
  *Start Docker Desktop when you sign in*.
- **Băng thông upload nhà bạn.** Mỗi video đi 1GB từ Drive xuống máy rồi 1GB từ
  máy lên YouTube — 2GB lưu lượng mỗi video.
- **Ổ đĩa.** Volume `n8n_data` giữ video tạm trong lúc xử lý. Chừa sẵn ~5GB.
- **Credential nằm trên máy cá nhân**, mã hoá bằng `N8N_ENCRYPTION_KEY`.

### Khi chuyển lên VPS sau này

Dùng lại **đúng** `N8N_ENCRYPTION_KEY` cũ, copy volume `n8n_data` sang, đổi sang
`docker-compose.yml` (bản có Cloudflare), rồi thêm redirect URI https vào OAuth
client. Không copy volume cũng được — chỉ là phải Connect lại các credential.

---

## 1. VPS

**Cấu hình tối thiểu:** Ubuntu 24.04, 2 vCPU, **4GB RAM**, SSD ≥ 40GB.

2GB RAM vẫn chạy được vì node YouTube của n8n stream video từ đĩa theo chunk
1 MiB chứ không nạp cả file vào RAM, nhưng 4GB thì không phải lo. Ổ đĩa quan
trọng hơn RAM: mỗi video 1GB được ghi tạm vào volume `n8n_data` trong lúc xử lý.

```bash
# swap 2GB cho chắc
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Cài Docker theo repo chính thức:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # đăng xuất rồi vào lại
```

✅ Kiểm tra: `docker run --rm hello-world` chạy được.

---

## 2. Cloudflare Tunnel

Cần một domain đã trỏ nameserver về Cloudflare.

1. Cloudflare Zero Trust → **Networks → Tunnels → Create a tunnel** → chọn
   **Cloudflared** → đặt tên → chọn môi trường **Docker** → copy token trong
   lệnh mẫu (đoạn dài sau `--token`).
2. Trong tunnel vừa tạo, tab **Public Hostnames** → **Add a public hostname**:
   - Subdomain: `n8n`, Domain: `tenmien.com`
   - Type: `HTTP`, URL: **`n8n:5678`**

URL phải là `n8n:5678` (tên container), không phải `localhost:5678` —
cloudflared nằm cùng network Docker với n8n nên gọi nhau bằng tên service.

✅ Kiểm tra: xong bước 3 thì mở `https://n8n.tenmien.com` ra được n8n.

---

## 3. Chạy n8n

```bash
git clone <repo của bạn> n8n-upload && cd n8n-upload   # hoặc copy 2 file lên
cp .env.example .env
nano .env
```

Điền:

| Biến | Lấy ở đâu |
|---|---|
| `N8N_DOMAIN` | `n8n.tenmien.com` (đúng hostname ở bước 2) |
| `N8N_ENCRYPTION_KEY` | `openssl rand -hex 32` |
| `CLOUDFLARE_TUNNEL_TOKEN` | token ở bước 2 |
| `N8N_MAX_HEAP_MB` | `1536` cho VPS 2GB, `2560` cho 4GB |

> **Giữ `N8N_ENCRYPTION_KEY` thật kỹ.** Mất key là mất toàn bộ credential
> OAuth đã lưu, phải kết nối lại tất cả kênh.

```bash
docker compose up -d
docker compose logs -f n8n
```

Mở `https://n8n.tenmien.com` → tạo tài khoản owner.

✅ Kiểm tra: đăng nhập được vào n8n qua HTTPS, không cần mở port nào trên VPS.

---

## 4. Google Cloud

**Một project duy nhất cho tất cả kênh.** Tạo nhiều project để lách quota là
vi phạm ToS và sẽ bị khoá cả project.

> Google đã đổi giao diện: mục *OAuth consent screen* cũ nay nằm trong
> **Google Auth Platform**, gồm 6 trang con — Overview, Branding, **Audience**,
> **Clients**, **Data Access**, Verification Center. Hướng dẫn dưới đây theo
> giao diện mới.

### 4.1 Tạo project và bật API

1. [console.cloud.google.com](https://console.cloud.google.com) → tạo project mới
2. **APIs & Services → Library** → tìm và bật từng cái:
   - **YouTube Data API v3**
   - **Google Drive API**

✅ Kiểm tra: **APIs & Services → Enabled APIs & services** thấy đủ 2 API.

### 4.2 Branding

Menu → **Google Auth Platform**. Nếu hiện *Google Auth platform not configured
yet* thì bấm **Get Started**.

Trang **Branding** có nhiều ô, nhưng với tool nội bộ chỉ cần **3 ô**:

| Ô | Điền gì |
|---|---|
| **App name** | Tên bất kỳ, ví dụ `n8n YouTube Uploader`. Tên này hiện trên màn hình xin quyền |
| **User support email** | Email của bạn (dropdown) |
| **Developer contact information** | Email của bạn, ở cuối trang |

**Để trống tất cả phần còn lại:**

| Ô | Vì sao để trống |
|---|---|
| **App logo** | ⚠️ Quan trọng. Chính trang đó ghi: *"After you upload a logo, you will need to submit your app for verification unless the app is configured for internal use only or has a publishing status of Testing"*. Bạn sắp Publish sang *In production*, nên upload logo là tự ép mình vào quy trình verification. **Đừng upload gì cả** |
| **Application terms of service link** | Không cần |

### 4.2b App domain — bắt buộc nếu muốn Publish

Vì workflow này xin scope *sensitive* (YouTube) và *restricted*
(`auth/drive`), Google **không cho bấm Publish app** cho tới khi khai đủ:

| Ô | Ràng buộc |
|---|---|
| **Application home page** | Một URL công khai, không cần đăng nhập |
| **Application privacy policy link** | Phải **cùng domain** với home page nhưng là **URL khác** — Google từ chối nếu hai link trùng nhau |
| **Authorized domains** | Phải chứa *top private domain* của hai link trên |

Repo này có sẵn 2 trang tĩnh để đăng: `docs/public-site/index.html` và
`docs/public-site/privacy.html`. Cách nhanh nhất là GitHub Pages:

1. Tạo repo **public** trên GitHub, ví dụ `video-analytics-policy`
2. Upload 2 file trong `docs/public-site/` vào nhánh `main` (ngay thư mục gốc repo)
3. **Settings → Pages → Source: Deploy from a branch →** `main` **/ (root)** → **Save**
4. Chờ khoảng 1 phút, mở thử `https://<tên-github>.github.io/<tên-repo>/`
5. Điền vào Branding:
   - Application home page: `https://<tên-github>.github.io/<tên-repo>/`
   - Application privacy policy link: `https://<tên-github>.github.io/<tên-repo>/privacy.html`
   - **Authorized domains → Add domain:** `<tên-github>.github.io`
6. **Save**, rồi sang Audience bấm **Publish app**

> Nhớ sửa tên và email trong 2 file HTML trước khi đăng — mặc định tôi điền theo
> App name và email hỗ trợ bạn đã đặt ở consent screen. Email này dù sao cũng
> hiện công khai trên màn hình xin quyền.

> `github.io` nằm trong Public Suffix List nên top private domain là
> `<tên-github>.github.io` — khai đúng chuỗi đó. Google chỉ bắt xác minh quyền sở
> hữu domain qua Search Console khi bạn **nộp hồ sơ verification**, còn publish ở
> trạng thái unverified thì không cần.

### 4.3 Audience → chọn External

Trang **Audience** → User type **External**.
(*Internal* chỉ có nếu bạn dùng Google Workspace có Organization.)

### 4.4 Data Access → thêm scope

Trang **Data Access** → bấm **Add or remove scopes**. Một panel trượt ra từ
bên phải. Ô để dán nằm **ở đáy panel**, không phải trên trang chính:

1. Trong panel, **kéo xuống cuối** → mục **Manually add scopes**
2. Dán cả danh sách vào ô text (nhiều scope thì cách nhau bằng **dấu phẩy hoặc
   xuống dòng**)
3. Bấm **Add to table** → scope hiện lên bảng phía trên, đã tick sẵn
4. Bấm **Update** ở cuối panel
5. Panel đóng lại → bấm **Save** trên trang Data Access

> Phải dùng ô nhập tay vì bảng scope **chỉ liệt kê scope của những API đã bật**,
> mà kể cả bật đủ 2 API rồi vẫn không hiện hết scope của YouTube. Bảng lúc đầu
> hiện "No rows to display" là bình thường.

Sau khi Save, 8 scope sẽ được xếp vào 3 bảng *non-sensitive* / *sensitive* /
*restricted* tuỳ mức nhạy cảm Google gán cho từng cái.


Credential n8n xin **8 scope** (số này tôi đọc từ source credential trong
container, không phải đoán):

YouTube — 5 scope:

```
https://www.googleapis.com/auth/youtube
https://www.googleapis.com/auth/youtube.force-ssl
https://www.googleapis.com/auth/youtube.upload
https://www.googleapis.com/auth/youtubepartner
https://www.googleapis.com/auth/youtubepartner-channel-audit
```

Drive — 3 scope:

```
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/drive.appdata
https://www.googleapis.com/auth/drive.photos.readonly
```

> **Muốn xin ít scope hơn?** Mỗi credential trong n8n có ô **Custom Scopes**.
> Workflow này thực tế chỉ cần `youtube.upload`, `youtube` và `drive` — ít scope
> thì hồ sơ audit đỡ phải giải trình, nhất là `youtubepartner-channel-audit`
> nghe rất nặng so với việc bạn đang làm. Nhưng n8n cảnh báo *"node may not
> function correctly"* khi đổi. Không muốn mạo hiểm thì cứ khai đủ 8 — app chỉ
> mình bạn dùng nên không ảnh hưởng gì.

> ⚠️ **Thêm scope TRƯỚC khi tạo OAuth client ở §4.6.** Google chặn một số scope
> *restricted* đối với project có redirect URI không phải HTTPS — mà §4.6 bảo
> bạn thêm `http://localhost:5678/...`. Nguyên văn tài liệu Google: *"usage of
> these scopes is restricted to projects using HTTPS URLs only. To enable the
> scopes, edit your OAuth clients and remove non-HTTPS URLs"*.
>
> Làm đúng thứ tự 4.4 → 4.6 là không gặp vấn đề. Lỡ tạo client trước rồi mà có
> scope không thêm được (hay gặp nhất là `auth/drive` — Google xếp quyền Drive
> đầy đủ vào nhóm restricted): vào **Clients**, tạm xoá dòng
> `http://localhost...`, thêm scope xong rồi quay lại thêm URI đó vào.

### 4.5 Publish app

Trang **Audience** → mục **Publishing status** → bấm **Publish app** → chuyển
sang *In production*.

> **Nút Publish app bị mờ?** Dưới nút sẽ có dòng *"To publish your app, you must
> complete your configuration on the Branding page"*. Nghĩa là §4.2 chưa xong —
> quay lại trang **Branding**, điền đủ 3 ô bắt buộc (App name, User support
> email, Developer contact information) và nhớ bấm **Save**. Quay lại Audience
> thì nút sáng.

> Trang Audience còn 2 thứ **đừng đụng vào**:
> - **Make internal** — chỉ dành cho Google Workspace có Organization, bấm vào
>   là hỏng cấu hình. User type **External** đã đúng rồi.
> - **Test users** — không cần thêm ai. Test user chỉ có ý nghĩa khi ở trạng
>   thái *Testing*, mà bạn sắp Publish khỏi trạng thái đó.

> **Bắt buộc làm bước này.** Để ở *Testing* thì theo đúng tài liệu Google:
> *"Authorizations by a test user will expire seven days from the time of
> consent"* — refresh token hết hạn theo. Cứ một tuần là workflow chết và phải
> bấm Connect lại từng credential.

Publish mà chưa verify vẫn dùng được: lúc đăng nhập Google hiện cảnh báo
*Unverified app* → bấm **Advanced → Go to ... (unsafe)**. Giới hạn 100 người
dùng trọn đời project, bạn chỉ có 1 nên không chạm tới.

### 4.6 Clients → tạo 2 OAuth client

Trang **Clients** → **Create Client**.

**Client 1 — cho n8n**

- Application type: **Web application**
- Name: `n8n`
- **Authorized redirect URIs → Add URI**, thêm **cả hai**:

```
http://localhost:5678/rest/oauth2-credential/callback
https://n8n.tenmien.com/rest/oauth2-credential/callback
```

Một client nhận nhiều URI, nên thêm sẵn cái `https` để sau này lên VPS khỏi
sửa. Google miễn trừ `localhost` khỏi yêu cầu HTTPS — ngoại lệ chính thức.

- ⚠️ **Ghi lại Client ID và Client Secret ngay tại đây.** Secret chỉ hiện
  **một lần** lúc tạo; sau đó Google chỉ giữ bản hash, không xem lại được.

**Client 2 — cho script pipeline**

- Application type: **Desktop app**
- Name: `pipeline`
- Không cần URI nào
- Tải file JSON về, lưu thành `scripts/client_secret.json`

> Mở file ra kiểm tra: JSON của **Desktop app** bắt đầu bằng `{"installed": {...`.
> Nếu thấy `{"web": {...` là bạn tải nhầm file của client `n8n` — script sẽ
> không chạy được vì luồng OAuth khác nhau.

> Ô **Use this client for an AI-powered agent**: để trống. Không sửa được sau
> khi đã tạo client.

> Redirect URI vừa thêm có thể mất **5 phút đến vài giờ** mới có hiệu lực. Gặp
> `redirect_uri_mismatch` ngay sau khi tạo thì soát lại từng ký tự (kể cả dấu
> `/` cuối) rồi chờ một lát thử lại.

✅ Kiểm tra: trang **Clients** có 2 dòng; **Audience** ở trạng thái
*In production*; đã lưu Client ID + Secret của client `n8n`; đã có file
`scripts/client_secret.json`.

---

## 5. Credential trong n8n

n8n → **Credentials → Add credential**. Dùng **Client ID / Client Secret của
client `n8n`** (Web application) cho cả hai loại dưới đây.

1. **Google Drive OAuth2 API** → dán client id/secret → **Sign in with Google**
   → chọn tài khoản Google chứa folder Drive.
2. **YouTube OAuth2 API** → dán client id/secret → **Sign in with Google**
   → **chọn đúng kênh** cần upload.

> **Mỗi kênh một credential YouTube riêng**, đặt tên rõ ràng
> (`YouTube - tên-kênh`). Khi đăng nhập, Google hỏi chọn kênh — chọn sai là
> video lên sai kênh.

✅ Kiểm tra: mở credential, nút hiện "Connected" / có dấu xanh.

---

## 6. Cấu trúc folder Drive

```
YouTube-Upload/
└── ten-kenh/
    ├── pending/   ← pipeline đẩy file vào đây
    ├── done/      ← n8n chuyển sang sau khi upload xong
    └── failed/
```

Script `push_to_drive.py` tự tạo các folder này ở lần chạy đầu, hoặc tạo tay
trong Drive.

**Lấy folder ID:** mở folder trong Drive, ID là đoạn cuối URL:
`https://drive.google.com/drive/folders/`**`1AbC...xyz`**

Cần 3 ID: `pending`, `done`, `failed`.

✅ Kiểm tra: tài khoản Google đã nối vào credential Drive ở bước 5 nhìn thấy
được 3 folder này.

---

## 7. Import workflow

### 7.1 Workflow chính

n8n → **Workflows → ⋯ → Import from File** → `workflows/youtube-upload.json`

Sửa 3 chỗ:

1. **Node `Cau hinh kenh`** — điền 4 giá trị:
   - `channelName`: tên kênh (chỉ để ghi log/thông báo)
   - `pendingFolderId`, `doneFolderId`, `failedFolderId`: ID ở bước 6
2. **Node `Trigger Drive pending`** — chọn credential Drive, rồi ở *Folder*
   chuyển sang **By ID** và dán đúng `pendingFolderId`.
3. **Credential cho từng node** — mở lần lượt và chọn:
   - `Tai JSON metadata`, `Tim MP4 va JPG`, `Tai video tu Drive`,
     `Tai thumbnail`, `Chuyen sang done`, `Chuyen file loi` → credential **Drive**
   - `Upload YouTube`, `Cap nhat status video`, `Dat thumbnail`
     → credential **YouTube của kênh đó**

Đổi tên workflow thành `YouTube Upload - <tên kênh thật>`.

### 7.2 Workflow thông báo lỗi

Import `workflows/youtube-upload-error.json`. Rồi ở workflow chính:
**⋯ → Settings → Error Workflow** → chọn `YouTube Upload - thong bao loi`.

Node Telegram đang **tắt**. Muốn nhận thông báo: tạo bot qua
[@BotFather](https://t.me/BotFather), thêm credential Telegram trong n8n, điền
`chatId`, rồi bật node (chuột phải → Enable) ở **cả hai** workflow.

Chưa bật Telegram vẫn dùng được: mọi lần lỗi đều hiện trong
**Executions** vì workflow kết thúc bằng node `Bao loi`.

### 7.3 Kích hoạt

Bật toggle **Active** ở góc trên phải. Trigger Drive poll mỗi phút.

> ⚠️ **Phải Active TRƯỚC khi đẩy video lên Drive.** Lần poll đầu tiên lấy mốc
> thời gian là đúng lúc bạn bấm Active (`startDate = lastTimeChecked || now`),
> rồi chỉ tìm file có `createdTime > mốc đó`. File nào đã nằm sẵn trong
> `pending/` từ trước sẽ **không bao giờ** kích hoạt workflow — nó nằm im ở đó
> và bạn sẽ tưởng workflow hỏng.
>
> Lỡ đẩy trước rồi: xoá file `.json` trên Drive rồi upload lại chính nó (giữ
> nguyên `.mp4`), vì `.json` mới là thứ trigger bắt.

### Thêm kênh thứ hai

Google Drive Trigger chỉ theo dõi **một** folder và **không** nhìn vào
subfolder. Nên mỗi kênh là một workflow riêng:

**⋯ → Duplicate** → sửa node `Cau hinh kenh`, sửa folder ở trigger, đổi
credential YouTube sang kênh mới.

✅ Kiểm tra: workflow Active, không node nào còn hình tam giác cảnh báo.

---

## 8. Script phía pipeline

Trên máy render:

```bash
pip install -r scripts/requirements.txt
```

Đặt `client_secret.json` (client *Desktop app* ở bước 4.3) vào `scripts/`.

Kiểm tra metadata trước khi đẩy gì lên:

```bash
python scripts/push_to_drive.py --channel ten-kenh --video out/v001.mp4 --title "Thử" --dry-run
```

Đẩy thật:

```bash
python scripts/push_to_drive.py \
  --channel ten-kenh \
  --video out/v001.mp4 \
  --thumb out/v001.jpg \
  --title "Tiêu đề video" \
  --description-file out/v001.txt \
  --tags "tag1,tag2" \
  --category 27 \
  --publish-at 2026-11-05T19:00:00+07:00 \
  --synthetic
```

Lần chạy đầu mở browser để đăng nhập Google, sau đó token lưu ở
`scripts/token-drive.json` và không hỏi lại.

Hoặc đưa sẵn một file metadata JSON:

```bash
python scripts/push_to_drive.py --channel ten-kenh --video out/v001.mp4 --meta out/v001.json
```

### Schema file metadata

```json
{
  "title": "Tiêu đề, tối đa 100 ký tự",
  "description": "Mô tả, tối đa 5000 ký tự",
  "tags": ["tag1", "tag2"],
  "categoryId": "27",
  "privacyStatus": "private",
  "publishAt": "2026-11-05T12:00:00Z",
  "containsSyntheticMedia": true,
  "madeForKids": false
}
```

Chỉ `title` là bắt buộc. Mặc định: `categoryId` `22`, `privacyStatus`
`private`. Có `publishAt` thì `privacyStatus` **bị ép về `private`** — YouTube
chỉ cho hẹn giờ video đang private.

`containsSyntheticMedia: true` là khai báo nội dung do AI tạo trông như thật.
Khai báo thiếu là rủi ro chính sách lớn hơn nhiều so với việc khai báo thừa.

✅ Kiểm tra: `--dry-run` in ra JSON đúng ý.

---

## 9. Chạy thử

### Lần chạy thành công đầu tiên

Dùng video ngắn (10–50MB) cho nhanh:

```bash
python scripts/push_to_drive.py --channel ten-kenh --video test.mp4 --title "Test $(date +%s)" --dry-run
# bỏ --dry-run khi đã ổn
```

Theo dõi n8n → **Executions**. Khi xong:

- video xuất hiện trong YouTube Studio ở trạng thái *Private*
- cả `test.mp4` + `test.json` nằm trong `done/`
- `pending/` trống

### Thử lỗi

Upload một file `.json` mà **không** có `.mp4` cùng tên:

```bash
echo '{"title":"Test loi"}' > loi-that.json
# upload tay file này vào pending/ qua web Drive
```

Mong đợi: execution failed với thông báo *"Khong tim thay loi-that.mp4 trong
folder pending"*, và `loi-that.json` bị chuyển sang `failed/`.

### Cách xử lý video trong `failed/`

Sửa nguyên nhân, rồi **chuyển file `.mp4`/`.jpg` về `pending/` trước, file
`.json` sau cùng** — thứ tự này là bắt buộc, vì `.json` mới là thứ kích hoạt
workflow.

✅ Kiểm tra: cả hai trường hợp trên đúng như mong đợi.

---

## 10. Quota và audit

### Quota mặc định

Mặc định 10.000 units/ngày, mà `videos.insert` tốn **1.600 units**:

| Lệnh | Units |
|---|---|
| `videos.insert` | 1600 |
| `videos.update` | 50 |
| `thumbnails.set` | 50 |

→ Mỗi video tốn ~1.700 units → **khoảng 5–6 video/ngày** cho cả project (dùng
chung cho mọi kênh). Hết quota thì API trả `quotaExceeded`, reset 0:00 theo
giờ Thái Bình Dương (15:00 giờ VN).

Ghi chú trong CLAUDE.md: từ 01/06/2026 `videos.insert` có bucket riêng, mặc
định ~100 upload/ngày. **Kiểm tra số thực của project bạn** tại
Cloud Console → **IAM & Admin → Quotas**, lọc theo *YouTube Data API v3* — đây
là con số quyết định bạn đăng được mấy video/ngày.

### Audit

Project chưa qua audit thì **mọi video upload bằng API bị ép `private` và
không thể chuyển sang public**, kể cả bấm tay trong Studio. `publishAt` cũng
sẽ bị từ chối.

Muốn đăng public / hẹn giờ tự động: nộp **YouTube API Services Audit and Quota
Extension Form**. Cần:

- link privacy policy công khai (xem `docs/privacy-policy.md`)
- mô tả đúng cách dùng: tool nội bộ, upload video của chính chủ kênh
- demo luồng

---

## 11. Sự cố thường gặp

| Hiện tượng | Nguyên nhân / xử lý |
|---|---|
| Sau 7 ngày mọi credential chết | Consent screen còn ở **Testing**. Publish app (bước 4.2) rồi Connect lại. |
| `Dat thumbnail` lỗi 403 | Kênh chưa xác minh số điện thoại. Vào youtube.com/verify. |
| `Cap nhat status video` lỗi khi có `publishAt` | Project chưa audit, không được hẹn giờ. Bỏ `publishAt` hoặc nộp form audit. |
| Video lên nhưng title/tags bị cắt | Title > 100 ký tự hoặc tags > 480 ký tự, workflow tự cắt. Sửa ở pipeline. |
| Trigger không bắt file | File nằm trong subfolder của `pending/` (trigger không nhìn subfolder), hoặc credential Drive không thấy folder đó. |
| Workflow chạy khi chưa có video | Pipeline upload `.json` trước `.mp4`. Luôn dùng `push_to_drive.py` để đảm bảo thứ tự. |
| n8n bị OOM-kill | Hạ `N8N_MAX_HEAP_MB`, hoặc thêm RAM. Kiểm tra `docker compose logs n8n` có `heap out of memory`. |
| Ổ đĩa đầy | `docker system df`, và kiểm tra volume `n8n_data`. Compose đã bật prune + không lưu execution thành công, nhưng video 1GB đang xử lý vẫn chiếm chỗ. |
| `quotaExceeded` | Hết quota ngày, xem bước 10. |
| Upload chạy được một lúc rồi lỗi mạng | Node YouTube không resume được: nó chỉ tha HTTP 308, lỗi khác là hỏng cả lần upload và **1600 quota vẫn mất**. Ưu tiên mạng dây, tránh chạy khi đường truyền chập chờn. Xử lý: chuyển file từ `failed/` về `pending/` (mp4 trước, json sau cùng). |
| Log báo `N8N_RUNNERS_MODE -> internal task runner mode is deprecated` | Vô hại. n8n 2.x luôn bật task runner ở chế độ internal; chuyển sang `external` cần chạy launcher thành process riêng, không cần thiết cho luồng này. |
| `n8n Task Broker's port 5679 is already in use` | Gặp khi chạy `n8n execute` trong lúc n8n đang chạy. Thêm `N8N_RUNNERS_BROKER_PORT=5690` cho lệnh đó. |
| Một video upload 2 lần | Workflow đã chặn: sau khi upload thành công, file được chuyển khỏi `pending/` nên trigger không bắt lại. Nếu lỗi xảy ra *sau* khi video đã lên YouTube, file được chuyển sang `done/` (không phải `failed/`) chính để tránh upload lại. |

### Xem log

```bash
docker compose logs -f --tail=100 n8n
docker compose logs -f --tail=50 cloudflared
docker stats n8n            # RAM thực tế đang dùng
```

### Cập nhật n8n

```bash
docker compose pull && docker compose up -d
```
