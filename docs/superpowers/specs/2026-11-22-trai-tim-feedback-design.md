# Trái tim của cỗ máy: vòng feedback quyết định mọi video mới

Ngày: 2026-11-22. Trạng thái: bản nháp chờ chủ kênh duyệt.

## 1. Ý định và lý do

Chủ kênh, nguyên văn:
- "tôi muốn feedback loop như trái tim của cả cổ máy đang chạy, mọi quyết định, phân tích đều đi qua nó"
- "tự quyết định dựa trên số, tôi không cần duyệt"
- "vì tôi sẽ up video daily, nên sẽ thử nghiệm ở video mới, vid cũ thất bại thì không cần sửa"
- Chọn cách 1: "vòng tính, nhân viên làm".

Hiện nay vòng studio (VPS đọc Studio 18:00, máy chủ kênh phân tích 20:30) đo kết quả rất đầy đủ, nhưng nó đứng bên cạnh
cỗ máy, chưa nằm ở giữa:
- Tác vụ dựng phim 06:10, nơi chốt tiêu đề, thumbnail, hook và footage, không đọc gì từ vòng.
- Lúc ra quyết định, không ai ghi "chọn gì, vì sao, đoán bao nhiêu". Vòng phải đoán ngược từ file dự án. Ledger ngày
  2026-11-21: 4/35 video không khớp được dự án; cột % footage có 1/35, giây/cảnh 0/35.
- Bài học đi vào luật chỉ qua tác vụ cải tiến (một AI), chậm (≥5 video) và mỗi ngày phán đoán một khác.
- Nhiều chỗ vẫn chờ chủ kênh: kế hoạch tuần "trả lời để đổi", mục "cần chủ kênh quyết", đề xuất chờ duyệt.

Đích: mỗi video mới là một lần thử có ghi chép. Vòng chấm nó và tự chỉnh lựa chọn cho video sau, không ai phải duyệt.

## 2. Mục tiêu, không phải mục tiêu, phản mục tiêu

**Mục tiêu**
- **M1.** Mọi quyết định định hình một video mới đọc **chỉ thị ngày** của vòng trước khi chốt: dòng chủ đề, kiểu đóng
  gói, khuôn tiêu đề, phong cách thumbnail, các lựa chọn kịch bản/hình đo được, và giờ đăng (máy tự hẹn, mục 9).
- **M2.** Mọi quyết định đó được ghi vào **sổ quyết định**: giá trị từng biến, biến đang thử và nhánh, lý do, dự đoán.
- **M3.** Vòng tự chấm từng dòng của sổ khi video qua mốc 24 giờ và 7 ngày, cập nhật thống kê từng nhánh, rồi viết chỉ
  thị cho ngày hôm sau.
- **M4.** Mỗi video chỉ thử MỘT biến. Các biến khác lấy nhánh đang thắng. Khoảng 70% lựa chọn theo cái thắng, 30% thử.
- **M5.** Không có bước nào chờ chủ kênh duyệt. Chủ kênh chỉ xem: bảng điều khiển có mục "Thử nghiệm", báo cáo tối có
  mục "Trái tim".
- **M6.** Mọi phân tích dùng cùng một nguồn số là đầu ra của vòng: ledger, sổ quyết định, `kenh.md`, `nghien-cuu.md`.
  Gồm báo cáo tối, tác vụ cải tiến, chọn chủ đề và bảng điều khiển. Không tác vụ nào tự đi đo lại bằng cách riêng khi
  số của vòng đã có.

**Không phải mục tiêu**
- Sửa video cũ thất bại. Không tự đổi tiêu đề video đã đăng. Tự đổi thumbnail (đang có) chỉ còn trong **7 ngày đầu**
  của một video, vì đó vẫn là thử trên video mới.
- Để code viết tiêu đề, kịch bản hay chọn góc chủ đề. Phần sáng tạo vẫn là nhân viên AI; code quyết **cái gì được thử**
  và **cái gì đang thắng**.
- Đổi luật chính sách YouTube, `playbook/POLICY_GATE.md`, ba trần hình, hay cổng lượt bấm. Chỉ thị không bao giờ được
  bảo một tác vụ bỏ qua các cổng đó.

**Phản mục tiêu (làm vậy là hỏng, dù chạy được)**
- Một nhánh bị "kết án" vì vài video kém may rồi không bao giờ được thử lại.
- Một video đổi hai biến cùng lúc, nên không biết biến nào làm nên kết quả.
- Cỗ máy đứng chờ chủ kênh, hoặc dừng dựng vì chỉ thị lỗi hay thiếu.
- Tối ưu lượt bấm bằng tiêu đề sai sự thật (vi phạm chính sách; kênh cách gậy một lần).
- Hai nguồn số mâu thuẫn nhau (ví dụ báo cáo tối tự suy CTR từ view trong khi vòng có CTR thật).

## 3. Ràng buộc

- **Chính sách:** `POLICY_GATE`, năm lằn ranh giật tít của sổ tay `nv-chu-de`, ba trần hình. Chỉ thị nằm DƯỚI các
  luật này. Nhánh nào chạm luật thì không được đưa vào danh sách nhánh.
- **Số mẫu nhỏ:** một video mỗi ngày, tức khoảng 7 điểm dữ liệu mỗi tuần, và kết quả 7 ngày đến chậm một tuần. Mọi so
  sánh cần ngưỡng mẫu tối thiểu và phải so cùng tuổi video (CTR ~24 giờ, view ~7 ngày).
- **Nhịp dữ liệu:** Studio đọc 18:00, vòng 20:30, chỉ thị viết xong khoảng 20:40. Tác vụ cải tiến 05:30, dựng 06:10,
  chọn chủ đề Chủ nhật 19:00.
- **Việc chỉ chủ kênh làm được (vẫn báo, không tính là duyệt):** đăng nhập lại trên VPS, đọc giá ở shop Mỹ (shop chặn
  máy này), lỗi máy móc.
- **Hai repo:**
  - Code vòng và bảng điều khiển ở `yt-uploader` (worktree `D:/Youtube/n8n-upload-studio`, nhánh làm việc theo `main`).
  - Sổ tay, skill, tác vụ và báo cáo ở repo dựng `D:/Youtube/video_footage_re_create`. Repo dựng commit khi chủ kênh
    bảo.
  - Các tác vụ hẹn giờ ở `C:\Users\Admin\.claude\scheduled-tasks\`.
- Đọc hình vẫn qua Qwen (`playbook/qwen_screen.py`). Không viết code qua heredoc.

## 4. Các cách đã cân nhắc

1. **Vòng tính, nhân viên làm (đã chọn).** Code của vòng viết chỉ thị và chấm sổ quyết định một cách xác định (có test).
   Nhân viên AI sáng tác bên trong chỉ thị và ghi quyết định qua một lệnh có kiểm.
2. **AI cải tiến làm trái tim.** Ít code, nhưng phán đoán dao động mỗi ngày, khó kiểm. Đã thấy nó so nhầm hai nhóm video
   khác tuổi. Bị loại vì đi ngược "quyết theo số".
3. **Code quyết hết, nhân viên chỉ làm theo.** Code không viết được tiêu đề và góc chủ đề tốt, cứng khi thị trường đổi,
   dựng nặng. Bị loại.

Chủ kênh chọn cách 1.

## 5. Thiết kế

### 5.1 Các biến và nhánh

Mỗi **biến** là một lựa chọn định hình video và đo được. Mỗi **nhánh** là một giá trị của biến đó. Danh sách nằm trong
một file cấu hình có test, `loop/heart_config.json`; thêm hay bớt nhánh là sửa file này, không sửa code.

Bộ khởi đầu, rút từ số đã đo:

| Biến | Nhánh khởi đầu | Ai quyết | Ghi chú |
|---|---|---|---|
| `dong` (kiểu đóng gói) | `gia-chuyen-hang`, `lineup`, `xep-hang` | chọn chủ đề | Đã đo: giá 16/26 Tốt; xếp hạng 3/3 dưới vạch; lineup theo độ quen của hãng |
| `khuon_tieu_de` | id trong `topic_engine/ctr/own_shapes.json`, cộng `moi` | dựng, bước A | `moi` = khuôn chưa đo trên kênh, tối đa 2/tuần như luật hiện hành |
| `ten_quen` | `hang-lon` (Ruger, S&W, Glock…), `hang-vua`, `khong` | chọn chủ đề | Cổng lượt bấm bắt buộc có tên quen, nên `khong` chỉ còn để đối chiếu và không được chọn |
| `thumb_sang` | `sang` (độ sáng ≥0,5), `toi` | dựng | Số đo từ `thumb_features.py` |
| `thumb_chu` | `2-3-tu`, `4-tu` | dựng | |
| `hook` | `su-viec-hang`, `cu-soc-xep-hang`, `gia-truoc` | dựng (kịch bản) | Giây-30 tốt nhất hiện là mở bằng sự việc của hãng |
| `gio_dang` | `19-22h`, `02-05h` (giờ VN) | chỉ thị → `push_to_pending.py` ghi `publishAt` | Máy tự hẹn giờ (mục 9) |

Các luật hiện hành không phải biến mà là **ràng buộc**: xoay loại súng (không 3 phim cùng loại liên tiếp), không lặp chủ
đề 8 tuần, khuôn tối đa 2 lần/8 tuần, ba trần hình, footage ≥50% và nhịp cảnh. Chỉ thị nhắc lại chúng nhưng không thử
nghiệm trên chúng.

### 5.2 Thước đo

- **Thước chính:** `log(view ở ~7 ngày)` của video (cột `views_7d` của ledger). Chủ kênh quan tâm lượt xem.
- **Tín hiệu sớm:** CTR ở ~24 giờ (`ctr_24h`). Một biến đóng gói (tiêu đề, thumbnail) được kết luận sớm theo CTR 24 giờ
  khi đủ mẫu, rồi xác nhận lại bằng view 7 ngày.
- **Phụ:** lượt hiển thị 7 ngày, % thời lượng xem, giữ chân 30 giây. Chỉ in ra, không dùng để chấm, trừ biến `hook`:
  biến này chấm bằng % thời lượng xem.
- Video chưa tới mốc thì chưa chấm. Video lệch chỉ thị vì một cổng cứng vẫn được chấm theo giá trị thật đã dùng, có cờ
  `lech`.

### 5.3 Cách chọn (xác định, có test)

Cho mỗi biến, vòng tính trên mọi video đã chấm: số mẫu `n`, trung vị thước đo của từng nhánh, và độ bất định.

- **Nhánh thắng** của một biến: nhánh có trung vị cao nhất trong các nhánh có `n ≥ 3`. Chưa nhánh nào đủ 3 mẫu thì dùng
  nhánh mặc định trong `heart_config.json` (lấy từ số hiện tại, ví dụ `gia-chuyen-hang`, `sang`).
- **Biến được thử hôm nay:** mỗi video thử đúng một biến. Chọn biến có "giá trị thông tin" cao nhất: nhánh có ít mẫu
  nhất, hoặc hai nhánh đầu sát nhau nhất. Quay vòng để không biến nào bị bỏ quá 7 ngày.
- **Tỉ lệ thử:** khoảng 30% video thử một nhánh **không phải** nhánh thắng của biến được chọn; 70% còn lại dùng nhánh
  thắng của mọi biến, chỉ ghi tên biến được theo dõi. Bốc thăm có hạt giống theo ngày, nên chạy lại cùng ngày ra cùng
  chỉ thị.
- **Sàn thử:** nhánh nào chưa có 6 mẫu thì luôn còn ít nhất 10% cơ hội được thử, kể cả khi đang thua. Một nhánh chỉ bị
  đánh dấu "loại" khi có `n ≥ 6` và kém nhánh thắng rõ (khoảng tin cậy không chồng nhau). Kể cả khi đã "loại", mỗi
  30 ngày nó vẫn được thử lại một lần.
- **Ràng buộc trước, thử sau:** nhánh được bốc nhưng vi phạm một ràng buộc của ngày đó (ví dụ phim súng ngắn thứ ba liên
  tiếp) thì bốc lại trong các nhánh hợp lệ.

### 5.4 Chỉ thị ngày (vòng viết, mọi tác vụ đọc)

Vòng (`loop/heart.py`, chạy trong `run.ps1` sau ledger) viết hai file vào `van-hanh/tim/` của vault:

- `chi-thi.json` (máy đọc): ngày áp dụng, phiên bản, nhánh thắng từng biến kèm `n` và trung vị, biến thử và nhánh thử của
  video kế tiếp, các ràng buộc đang hiệu lực, dự đoán view 7 ngày (khoảng), và các tín hiệu khán giả/thị trường đáng chú
  ý nhất (top 5 cụm của `nghien-cuu.md`, từ khoá tìm kiếm).
- `chi-thi.md` (người và AI đọc): cùng nội dung, viết bằng lời, mỗi lựa chọn kèm một dòng "vì sao" bằng số.

Chỉ thị viết cho **video kế tiếp** của hàng chờ. Tác vụ chọn chủ đề Chủ nhật đọc thêm phần "cơ cấu tuần": tỉ lệ các
`dong` và `ten_quen` mà 7 video tuần tới nên có (theo nhánh thắng và tỉ lệ thử).

Nếu vòng lỗi hay thiếu dữ liệu, tác vụ dựng dùng chỉ thị gần nhất còn hạn 3 ngày. Không còn chỉ thị nào thì dùng mặc định
trong `heart_config.json` và ghi cờ `khong-chi-thi`. Cỗ máy không bao giờ dừng vì chỉ thị.

### 5.5 Sổ quyết định (mọi tác vụ ghi, vòng chấm)

- File: `van-hanh/tim/so-quyet-dinh.csv`, mỗi dòng một quyết định.
- Ghi qua một lệnh có kiểm `ghi_quyet_dinh.py` (trong scripts của skill `quan-ly-kenh`). Lệnh từ chối biến hay nhánh không
  có trong `heart_config.json`, và không cho ghi tay tự do.

**Một dòng gồm:**
- Thời điểm, tác vụ ghi, `slug`, `video_id` (vòng điền sau khi khớp được video).
- Phiên bản chỉ thị đã đọc.
- Giá trị của từng biến.
- Biến thử và nhánh thử.
- `lech` kèm lý do nếu làm khác chỉ thị (chỉ được lệch khi một cổng cứng chặn).
- Dự đoán view 7 ngày.
- Các số đo của phim: % footage, giây/cảnh, % render, độ sáng và màu thumbnail, số từ kịch bản, độ dài phim.

**Cột kết quả, chỉ vòng ghi:** `ctr_24h`, `impr_24h`, `views_7d`, `apv_pct`, `ret_30s`, phán quyết studio, và
`ket_qua` (trên/dưới dự đoán).

**Ai ghi khi nào:**
- Tác vụ chọn chủ đề ghi một dòng `ke-hoach` cho mỗi chủ đề vào hàng chờ: `dong`, `ten_quen`, khuôn dự kiến, dự đoán.
- Tác vụ dựng ghi dòng `dung` ở bước A (tiêu đề, thumbnail, hook đã chốt). Khi đẩy Drive, nó bổ sung các số đo của phim
  vào đúng dòng đó.
- Tác vụ cải tiến ghi mỗi lần đổi luật (mã TD) thành một dòng `doi-luat`, để vòng thấy luật đổi lúc nào.

**Khớp với video thật:** vòng khớp `slug` → `video_id` bằng nguồn ledger đang dùng (`videos.json`, `UPLOAD.md`), và
thêm khớp theo tiêu đề đã ghi trong sổ. Đây cũng là cách vá các cột còn thiếu của ledger: ledger đọc số đo của phim từ sổ
trước, rồi mới đoán ngược từ file dự án.

### 5.6 Mỗi tác vụ đổi gì

- **Dựng phim 06:10** (`bareskull-quan-ly-kenh-hang-ngay`, §4 của `tac-vu-tu-dong.md`):
  - Đọc `chi-thi.md` trước bước A.
  - Áp nhánh thắng cho mọi biến và nhánh thử cho biến được thử; nhân viên AI viết tiêu đề và thumbnail BÊN TRONG nhánh đó.
  - Chỉ được lệch khi một cổng cứng chặn; khi lệch, ghi `lech` kèm số đo.
  - Ghi dòng `dung`.
  - Bỏ mọi dòng "cần chủ kênh quyết", trừ ba việc chỉ chủ kênh làm được.
- **Chọn chủ đề Chủ nhật:**
  - Đọc chỉ thị. Cơ cấu hàng chờ theo phần "cơ cấu tuần".
  - Ghi dòng `ke-hoach`.
  - Báo cáo cuối chỉ để xem; bỏ câu "trả lời để bỏ hoặc đổi thứ tự".
  - Tỉ lệ ứng viên Cách 1/2/3 giữ nguyên.
- **Tác vụ cải tiến 05:30:**
  - Không còn tự đổi các luật số mà trái tim đã nắm: chọn `dong`, khuôn tiêu đề, độ sáng thumbnail.
  - Vẫn giữ bài học mềm, giả thuyết, câu chữ sổ tay và mục "Feedback từ số liệu".
  - Đọc thêm bảng nhánh của chỉ thị.
  - Đề xuất thêm hoặc bớt nhánh bằng cách ghi vào báo cáo của nó; vòng không tự thêm nhánh. Một nhánh mới vào
    `heart_config.json` khi tác vụ cải tiến ghi nó với bằng chứng ≥3 video (vẫn tự quyết, có nhật ký và hoàn lại như
    hiện nay).
- **Báo cáo tối 21:00:** thêm mục "Trái tim" ở đầu: quyết định hôm qua, thử nghiệm hôm nay, bảng nhánh (thắng/đang
  thử/đã loại), dòng nào vừa được chấm và trên hay dưới dự đoán.
- **Vòng studio 20:30:** chạy `heart.py` sau ledger. Tự đổi thumbnail chỉ cho video ≤7 ngày tuổi.
- **Bảng điều khiển:** thêm mục "Thử nghiệm": bảng biến × nhánh (n, trung vị, trạng thái), chỉ thị ngày mai, và 14 dòng
  mới nhất của sổ quyết định.

### 5.7 Chủ kênh còn thấy gì

Không có gì để duyệt. Chủ kênh chỉ thấy:
- bảng điều khiển;
- mục "Trái tim" trong báo cáo tối;
- cảnh báo cho ba việc chỉ chủ kênh làm được (đăng nhập VPS, đọc giá shop Mỹ, lỗi máy móc).

### 5.8 Dữ liệu khởi đầu và các quyết định kỹ thuật (người viết thiết kế quyết, sau lượt đọc của người dựng)

- **Không có view 7 ngày cho video cũ.** Ledger ngày 2026-11-21 chỉ có `views_7d` ở 3 dòng và `ctr_24h` ở 4 dòng, vì
  vòng mới chạy từ 20/11; Studio cũng không cho xem "7 ngày đầu" của video cũ. Vì vậy:
  - Video cũ KHÔNG tính vào `n` của nhánh.
  - Chúng chỉ dùng để chọn **nhánh mặc định** trong `heart_config.json`, theo số trọn đời đã có (ví dụ `dong` mặc định =
    `gia-chuyen-hang`: 16/26 phim giá được "Tốt", 3/3 phim xếp hạng dưới vạch).
  - `n` chỉ đếm từ các video mới có dòng sổ quyết định.
  - Thử nghiệm bật ngay từ giai đoạn 2, chạy trên mặc định cho tới khi nhánh đủ mẫu; không đợi dữ liệu.
- **Cùng tuổi:** `views_7d` chỉ hợp lệ khi đọc ở ngày 6–9 sau khi đăng. Nay VPS đọc mỗi ngày, nên video mới nào cũng có
  một lần đọc trong khoảng đó. `ctr_24h` hợp lệ ở 18–36 giờ. Ngoài khoảng → không chấm, ghi "lỡ mốc". Không ngoại suy.
- **Ánh xạ cột ledger → biến:**
  - `packaging` gia → `gia-chuyen-hang`; lineup → `lineup`; xep-hang → `xep-hang`; other → không thuộc nhánh nào.
  - `title_shape` → id khuôn.
  - `thumb_brightness` → `sang`/`toi` (ngưỡng 0,5).
  - `hook`, `ten_quen`, `thumb_chu` chỉ có ở dòng sổ của video mới (tác vụ dựng ghi); video cũ để trống.
  - Biến trống thì video đó bị bỏ khỏi phép tính của riêng biến đó, không bỏ cả video.
- **Sổ và kết quả là hai file:**
  - `so-quyet-dinh.csv`: các tác vụ chỉ thêm dòng, qua `ghi_quyet_dinh.py` có khoá file.
  - `ket-qua.csv`: vòng ghi, nối với sổ bằng `id` dòng.
  - Mọi bên ghi đều chạy trên máy chủ kênh; VPS không ghi. Không có hai bên sửa cùng một file.
- **Chỉ thị theo slug, không theo ngày:**
  - `chi-thi.json` ghi nhánh thử cho slug đầu hàng chờ và cho 2 slug kế tiếp. Hạt giống bốc thăm là slug.
  - Tác vụ dựng nhận lại phim dở thì giữ dòng sổ đã có của phim đó.
  - Ngày không ra video thì chỉ thị chuyển sang hôm sau. Ngày ra hai video thì video thứ hai lấy phần của slug kế tiếp.
- **Loại nhánh hiếm khi xảy ra:** dùng quy tắc theo hạng, không dùng khoảng tin cậy tham số. Một nhánh bị "loại" chỉ khi
  `n ≥ 6` VÀ trung vị của nó thấp hơn phân vị 25 của nhánh thắng. Mặc định 30 ngày thử lại vẫn áp.
- **Lệnh ghi không bao giờ chặn phim:**
  - `ghi_quyet_dinh.py` thoát mã khác 0 khi giá trị không hợp lệ; tác vụ dựng sửa giá trị rồi ghi lại.
  - Hỏng hai lần thì tác vụ dựng đi tiếp, ghi một dòng vào `alerts.md`, và vòng chấm phim đó với cờ `thieu-so`.
- **Dự đoán:**
  - Trước khi nhánh đủ mẫu: công thức sẵn có của `ke-hoach-tuan.md` (`goc` × hệ số).
  - Khi đủ mẫu: khoảng phân vị 25–75 của `views_7d` các video có cùng nhánh ở biến được thử.
  - `ket_qua` = trên / trong / dưới khoảng.
- **Sửa tác vụ:**
  - Người dựng sửa trực tiếp prompt của các tác vụ hẹn giờ (`C:\Users\Admin\.claude\scheduled-tasks\`), `tac-vu-tu-dong.md`
    và sổ tay.
  - Thay đổi trong repo dựng không commit cho tới khi chủ kênh bảo.
- **`gio_dang`** có trong cấu hình bản đầu (chủ kênh cho máy tự hẹn giờ, mục 9), nhưng chỉ bật sau khi giai đoạn 1 đã
  kiểm `publishAt` được YouTube chấp nhận.

## 6. Triển khai theo giai đoạn

1. **Sổ và chỉ thị ở chế độ chỉ ghi (2–3 ngày).**
   - `heart.py` viết chỉ thị.
   - Tác vụ dựng đọc chỉ thị và ghi sổ, nhưng chưa bắt buộc nhánh thử.
   - Kiểm tác vụ dựng ghi đủ dòng, ledger khớp được.
2. **Bật thử nghiệm.** Nhánh thử thành bắt buộc; tác vụ chọn chủ đề theo cơ cấu tuần.
3. **Chuyển quyền đổi luật số** từ tác vụ cải tiến sang trái tim; cập nhật prompt của tác vụ cải tiến.

Mỗi giai đoạn có test riêng cho `heart.py` và `ghi_quyet_dinh.py`.

## 7. Để lại cho người dựng

- Công thức chính xác của "giá trị thông tin" khi chọn biến thử, và cách tính khoảng tin cậy. Yêu cầu: xác định, có test,
  và trên dữ liệu 35 video hiện có cho ra nhánh thắng hợp lý (giá > xếp hạng).
- Định dạng chi tiết của `chi-thi.json` và các cột CSV.
- Cách bốc thăm có hạt giống theo ngày.
- Cách chọn nhánh mặc định từ số trọn đời của 35 video cũ (mục 5.8), ghi kèm số làm căn cứ trong `heart_config.json`.

## 8. Rủi ro

- **Mẫu nhỏ làm kết luận sai.** Giảm bằng sàn thử, ngưỡng `n` và thử lại sau 30 ngày.
- **Nhân viên AI "lách" chỉ thị.** Lệnh ghi có kiểm, và vòng so giá trị đã ghi với phim thật (tiêu đề live, độ sáng
  thumbnail đo được). Lệch không có lý do thì vào cảnh báo báo cáo tối.
- **Mùa vụ:** tháng 11–12 khác thường (Black Friday, quảng cáo Q4). Nhánh thắng được tính lại mỗi ngày, nên mùa vụ là
  nhiễu chung cho mọi nhánh của cùng thời kỳ, không lệch về nhánh nào.

- **Không còn ai xem phim trước khi lên công khai** (xem 9). Cổng chính sách của phim là lớp bảo vệ cuối cùng.

## 9. Giờ đăng: máy tự hẹn giờ (chủ kênh trả lời 2026-11-22: "Máy tự hẹn giờ")

Luật cũ "video luôn private, chủ kênh tự đăng" được thay bằng:

- Video vẫn tải lên ở chế độ private, kèm `publishAt` do chỉ thị chọn. YouTube tự chuyển sang công khai đúng giờ.
- `gio_dang` là một biến của `heart_config.json`, với hai nhánh khởi đầu:
  - `19-22h` (giờ VN, như hiện nay; mặc định);
  - `02-05h` (giờ VN, tức buổi chiều ở Mỹ, trước giờ khán giả đông nhất).
- Chỉ thị chọn một giờ cụ thể trong khoảng của nhánh. Giờ đó phải sau ít nhất 2 giờ kể từ lúc VPS dự kiến tải xong;
  VPS nhận file lúc 9:00 và 21:00.
- **Điều kiện để được hẹn giờ.** Vì không còn chủ kênh xem trước:
  - `push_to_pending.py` chỉ ghi `publishAt` khi `ship_gate` 0 FAIL và bản đọc chính sách của phim không có mục nào để
    ngỏ.
  - Thiếu một trong hai → không ghi `publishAt`: video nằm private, cảnh báo vào `alerts.md` và báo cáo tối. Đây là một
    trong các việc chỉ chủ kênh làm được.
- **Nếu YouTube từ chối `publishAt`** (dự án Google chưa qua audit, `docs/uploader.md`):
  - Uploader tải lên private không có `publishAt` và ghi lý do vào file trạng thái của video.
  - Vòng đặt `gio_dang` thành "không khả dụng" trong `heart_config.json`, để biến này không được thử nữa.
  - Báo cáo tối báo chủ kênh: tự đăng tay, hoặc nộp form audit.
  - Kiểm điều này trong giai đoạn 1, bằng một video thử ở chế độ private.
- **Ghi sổ:** dòng sổ ghi giờ đã hẹn; vòng ghi giờ thật video lên công khai, lấy từ `published_local` của ledger.
