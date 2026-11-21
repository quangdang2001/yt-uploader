# Trái tim feedback — Kế hoạch dựng

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vòng studio trở thành nơi ra quyết định cho mọi video mới. Mỗi tối nó viết chỉ thị (nhánh thắng của từng biến, biến thử, dự đoán). Các tác vụ đọc chỉ thị trước khi chốt và ghi sổ quyết định; vòng chấm sổ ở 24 giờ / 7 ngày. Máy tự hẹn giờ đăng.

**Architecture:** Code xác định trong `loop/` của yt-uploader:
- `heart_config` — cấu hình biến và nhánh;
- `book` — sổ quyết định, chỉ thêm dòng, có khoá, kèm CLI;
- `heart_score` — chấm sổ theo snapshot;
- `heart` — thống kê nhánh, chọn biến thử, viết chỉ thị.

`run.ps1` gọi `heart` sau ledger. Phần còn lại là nối dây:
- `push_to_pending.py` (repo dựng) ghi `publishAt` khi cổng sạch;
- uploader trên VPS đăng lại không kèm `publishAt` nếu bị từ chối;
- bảng điều khiển có thêm mục "Thử nghiệm";
- prompt các tác vụ hẹn giờ và sổ tay đọc chỉ thị và ghi sổ.

**Tech Stack:** Python 3.14 chỉ thư viện chuẩn trong `loop/` (giống `ledger.py`), unittest/pytest, PowerShell `run.ps1`, Artifact Data (bảng điều khiển).

**Spec:** `docs/superpowers/specs/2026-11-22-trai-tim-feedback-design.md` (chủ kênh duyệt 2026-11-22). Đọc cùng kế hoạch này.

## Global Constraints

- **Hai repo:**
  - yt-uploader ở worktree `D:/Youtube/n8n-upload-studio`, nhánh `feat/studio-loop`. Gộp vào `main` bằng fast-forward ở
    `D:/Youtube/n8n-upload`, rồi push.
  - Repo dựng `D:/Youtube/video_footage_re_create`, nhánh `main`: chỉ stage. Chủ kênh bảo mới commit.
- **Vault:** `V = D:/Youtube/video_footage_re_create/Bare Skull - 2nd brain`. Thư mục trái tim là `V/van-hanh/tim/`, suy
  ra từ `config.report_dir` (`.../van-hanh/bao-cao/studio`) bằng `Path(report_dir).parents[1] / "tim"`.
- **Hằng số của spec**, ghi đúng như vậy vào `heart_config.json`:
  - `ti_le_thu` = 0.3, `san_thu` = 0.1;
  - `n_thang` = 3, `n_loai` = 6, `thu_lai_ngay` = 30;
  - "lượt bỏ qua tối đa của một biến" = 7 ngày;
  - cửa sổ CTR 24 giờ = 18–36 giờ; cửa sổ view 7 ngày = 144–216 giờ;
  - chỉ thị còn hạn 3 ngày; thumbnail chỉ tự đổi khi video ≤168 giờ tuổi;
  - giờ hẹn ≥ 2 giờ sau lượt nhận file kế tiếp của VPS (9:00 và 21:00 giờ VN).
- **Quy tắc loại:** một nhánh "loại" chỉ khi `n ≥ 6` VÀ trung vị của nó < phân vị 25 của nhánh thắng.
- **Thước chính:** `log(views_7d)`, riêng biến `hook` dùng `apv_pct`. Video cũ KHÔNG tính vào `n`.
- **Hạt giống bốc thăm:** `int(sha256(slug.encode()).hexdigest()[:12], 16)`. Cùng slug và cùng dữ liệu thì cùng kết quả.
- **Không gì chặn phim:**
  - `heart` lỗi thì `run.ps1` vẫn thoát 0 và ghi `alerts.md`.
  - Lệnh ghi sổ hỏng hai lần thì tác vụ dựng đi tiếp, có cảnh báo.
- **Giới hạn cứng:**
  - Không sửa luật chính sách, `POLICY_GATE.md`, ba trần hình.
  - Không viết code qua heredoc: viết file rồi chạy.
  - Đọc hình qua Qwen.
- Chữ hiện cho chủ kênh viết bằng tiếng Việt dễ hiểu (như bảng điều khiển hiện nay).

## Review Focus

1. **Phim bị bỏ (`buoc = hoan`) có dòng sổ nhưng không bao giờ có video.** Không được chấm, không tính `n`, không lỗi.
   → test ở Task 3.
2. **Tiêu đề live khác tiêu đề trong sổ** (chủ kênh hay Studio sửa). Vẫn khớp được bằng slug; lệch tiêu đề thành cảnh báo,
   không làm hỏng. → test ở Task 3.
3. **Hàng chờ trống.** Chỉ thị vẫn có mục `_tu-chon` cho chủ đề tác vụ dựng tự chọn. → test ở Task 4.
4. **Hai tác vụ ghi sổ cùng lúc** (dựng 06:10 và một phiên tay). Không có dòng đan xen; khoá cũ hơn 5 phút được phá.
   → test ở Task 2.
5. **Cấu hình đổi:** sổ có nhánh không còn trong `heart_config.json`. Thống kê bỏ qua nhánh đó và in cảnh báo, không
   lỗi. → test ở Task 4.

---

### Task 1: Cấu hình biến và nhánh

**Files:**
- Create: `loop/heart_config.json`, `loop/heart_config.py`
- Test: `loop/test_heart_config.py`

**Interfaces:**
- Produces:
  - `load_config(path: Path) -> dict`, ném `ConfigError(str)` khi sai.
  - `arms(cfg, var: str) -> list[str]`.
  - `enabled_vars(cfg) -> list[str]`: các biến có `bat: true` và không `khong_kha_dung`.
  - `default(cfg, var) -> str`.

`heart_config.json`:
- Khoá gốc: `giai_doan` (1), các hằng số của Global Constraints, `bien`.
- Mỗi biến trong `bien`: `nhanh` (list), `mac_dinh`, `can_cu` (một câu, số làm căn cứ), `ai_quyet`
  (`chu-de` | `dung` | `dang`), `do` (`views_7d` | `apv_pct`), `bat` (bool), `khong_kha_dung` (bool, mặc định false),
  `cot_ledger` (tên cột ledger dùng để kiểm lệch, có thể null).
- Bảy biến:

| Biến | Nhánh | Mặc định | Thước đo | Bật | Ghi chú |
|---|---|---|---|---|---|
| `dong` | `gia-chuyen-hang`, `lineup`, `xep-hang` | `gia-chuyen-hang` | `views_7d` | có | |
| `khuon_tieu_de` | 11 id hiện có trong `topic_engine/ctr/own_shapes.json` → `shapes`, cộng `moi` | khuôn có CTR đã đo cao nhất trong `ctr.json` (người dựng tra và ghi số vào `can_cu`) | `views_7d` | có | |
| `ten_quen` | `hang-lon`, `hang-vua` | `hang-lon` | `views_7d` | có | `khong` bị cổng lượt bấm cấm nên không là nhánh |
| `thumb_sang` | `sang`, `toi` | `sang` | `views_7d` | có | ngưỡng 0,5 của `thumb_brightness` |
| `thumb_chu` | `2-3-tu`, `4-tu` | `2-3-tu` | `views_7d` | có | |
| `hook` | `su-viec-hang`, `cu-soc-xep-hang`, `gia-truoc` | `su-viec-hang` | `apv_pct` | có | |
| `gio_dang` | `19-22h`, `02-05h` | `19-22h` | `views_7d` | **không** | bật ở Task 10 sau khi `publishAt` được chấp nhận |

- [ ] **Step 1: Test.** `test_heart_config.py`:
  - `test_loads_shipped_config`: 7 biến; `enabled_vars` không có `gio_dang`.
  - `test_default_must_be_an_arm`: `mac_dinh` không thuộc `nhanh` → `ConfigError`.
  - `test_constants_match_spec`: các hằng số bằng đúng giá trị của Global Constraints.
  - `test_unknown_key_rejected`: biến có khoá lạ → `ConfigError`.
  - `test_unavailable_var_not_enabled`: `khong_kha_dung: true` → không có trong `enabled_vars`.
- [ ] **Step 2: Chạy, xem fail.** Lệnh: `python -m pytest -q loop/test_heart_config.py`.
- [ ] **Step 3: Viết code.** `heart_config.py` và `heart_config.json` với các giá trị trên. Mỗi `can_cu` lấy số từ ledger
  hoặc `ctr.json` hiện tại. Ví dụ `dong`: "16/26 phim giá Tốt; 3/3 phim xếp hạng dưới vạch (ledger 2026-11-21)".
- [ ] **Step 4: Chạy lại, xem pass.**
- [ ] **Step 5: Commit** `feat(heart): variables and arms config`.

### Task 2: Sổ quyết định và lệnh ghi

**Files:**
- Create: `loop/book.py`
- Create (repo dựng, chỉ stage): `.claude/skills/quan-ly-kenh/scripts/ghi_quyet_dinh.py`
- Test: `loop/test_book.py`

**Interfaces:**
- Consumes: `load_config`, `arms` (Task 1).
- Produces:
  - `BOOK_NAME = "so-quyet-dinh.csv"`.
  - `BOOK_COLUMNS`: `id, thoi_diem, tac_vu, slug, phien_ban_chi_thi, dong, khuon_tieu_de, ten_quen, thumb_sang,
    thumb_chu, hook, gio_dang, bien_thu, nhanh_thu, lech, ly_do, du_doan_7d, tieu_de, gio_hen, footage_pct,
    sec_per_cut, render_pct, thumb_brightness, script_words, runtime_s, ghi_chu`.
  - `TAC_VU = ("ke-hoach", "dung", "so-do", "doi-luat")`.
  - `append(book: Path, row: dict, cfg: dict, now: datetime) -> str`: trả `id` = `<YYYYMMDDHHMMSS>-<slug>-<tac_vu>`.
    Ném `BookError` khi tác vụ lạ, nhánh lạ, `bien_thu` không phải biến, `nhanh_thu` không thuộc biến đó, `du_doan_7d`
    không dạng `lo-hi`, hoặc `lech` có mà `ly_do` rỗng.
  - `read(book: Path) -> list[dict]`.
  - `merged(rows) -> dict[str, dict]`: mỗi slug lấy giá trị không rỗng mới nhất của từng cột. Thứ tự ưu tiên tăng dần:
    `ke-hoach` < `dung` < `so-do`. Bỏ dòng `doi-luat`.
  - CLI: `python -m loop.book ghi --tim <dir> --config loop/heart_config.json --tac-vu <t> --slug <s> [--gia-tri k=v ...]
    [--thu bien=nhanh] [--du-doan lo-hi] [--lech] [--ly-do "..."] [--tieu-de "..."] [--gio-hen ISO] [--so-do k=v ...]`.
    - Thoát 0 và in `id`.
    - Thoát 2 kèm thông điệp tiếng Việt nói rõ giá trị nào sai và danh sách nhánh hợp lệ.

**Lock:**
- File khoá `<book>.lock` tạo bằng `O_CREAT|O_EXCL`, chứa pid và thời điểm. Chờ tối đa 30 giây.
- Khoá cũ hơn 300 giây thì xoá rồi lấy lại.
- File CSV ghi đầu cột khi mới tạo, mã hoá `utf-8`, `newline=""`.

**Wrapper repo dựng:** `ghi_quyet_dinh.py` chạy `python -m loop.book ghi --tim "<V>/van-hanh/tim" --config
D:/Youtube/n8n-upload-studio/loop/heart_config.json …` với `cwd=D:/Youtube/n8n-upload-studio`, chuyển nguyên tham số và
mã thoát.

- [ ] **Step 1: Test.** `test_book.py`:
  - `test_append_and_read_roundtrip`;
  - `test_rejects_unknown_arm` (thông điệp có tên nhánh hợp lệ);
  - `test_rejects_lech_without_reason`;
  - `test_merged_prefers_so_do_over_dung_over_ke_hoach`;
  - `test_merged_skips_doi_luat`;
  - `test_concurrent_appends_no_interleave`: 2 tiến trình × 50 dòng → 100 dòng, mỗi dòng đủ cột;
  - `test_stale_lock_is_broken`: khoá 301 giây tuổi → ghi được;
  - `test_cli_exit_codes` (0 và 2).
- [ ] **Step 2: Chạy, xem fail.**
- [ ] **Step 3: Viết `book.py` và wrapper.**
- [ ] **Step 4: Chạy, xem pass.** Thêm: `python ghi_quyet_dinh.py ghi --tac-vu dung --slug thu --gia-tri dong=sai` thoát 2.
- [ ] **Step 5: Commit** `feat(heart): decision book with locked appends and CLI` (repo dựng: chỉ stage wrapper).

### Task 3: Chấm sổ

**Files:**
- Create: `loop/heart_score.py`
- Test: `loop/test_heart_score.py`

**Interfaces:**
- Consumes:
  - `book.read`, `book.merged` (Task 2);
  - `ledger.pick_snapshot`, `ledger.norm_title`, `fetch.load_snapshots`, `state.since_published` (hàm
    `ledger.build_ledger` đang dùng để gom snapshot theo video);
  - file `ledger.csv`.
- Produces:
  - `RESULT_NAME = "ket-qua.csv"`.
  - `RESULT_COLUMNS = slug, video_id, ctr_24h, impr_24h, views_7d, apv_pct, ret_30s, verdict, ket_qua, canh_bao`.
  - `score(merged: dict, ledger_rows: list[dict], snaps_by_vid: dict) -> list[dict]`.
  - `write_results(path: Path, rows: list[dict]) -> None`.

Luật:
- **Khớp video:** slug của ledger trước; sau đó so `norm_title(live_title) == norm_title(tieu_de)`.
- **Số đo theo cửa sổ:** `ctr_24h` chọn bằng `pick_snapshot(target=24, lo=18, hi=36)`; `views_7d` chọn bằng
  `pick_snapshot(target=168, lo=144, hi=216)`. Ngoài cửa sổ thì để trống, và `canh_bao` có `lo-moc-24h` / `lo-moc-7d`
  khi tuổi đã quá cửa sổ.
- **`ket_qua`:** dựa trên `du_doan_7d` dạng `lo-hi`: `tren` khi > hi, `trong`, `duoi` khi < lo, rỗng khi chưa có
  `views_7d`.
- **`canh_bao`:**
  - `lech-tieu-de` khi tiêu đề live khác tiêu đề trong sổ;
  - `lech-thumb` khi `thumb_sang` của sổ trái với `thumb_brightness` của ledger (ngưỡng 0,5);
  - `chua-co-video` khi slug không khớp.

- [ ] **Step 1: Test.** `test_heart_score.py`:
  - `test_windows_pick_only_inside` (snapshot 40 giờ không thành `ctr_24h`);
  - `test_match_by_slug_then_title`;
  - `test_abandoned_slug_not_scored` (Review Focus 1: không có video → chỉ `chua-co-video`, không lỗi);
  - `test_live_title_changed_still_matched_by_slug` (Review Focus 2: có `lech-tieu-de`);
  - `test_ket_qua_bands`;
  - `test_thumb_mismatch_flagged`.
- [ ] **Step 2: Chạy, xem fail.**
- [ ] **Step 3: Viết `heart_score.py`.**
- [ ] **Step 4: Chạy, xem pass.**
- [ ] **Step 5: Commit** `feat(heart): grade the decision book at 24 h and 7 days`.

### Task 4: Thống kê nhánh, chọn biến thử, chỉ thị

**Files:**
- Create: `loop/heart.py`
- Test: `loop/test_heart.py`

**Interfaces:**
- Consumes: Task 1–3; `channel_view.latest_doc` (tín hiệu khán giả); file `V/van-hanh/bao-cao/studio/nghien-cuu.md`
  (chỉ đọc 5 dòng đầu bảng "Cụm chủ đề").
- Produces:
  - `arm_stats(scored, merged, cfg) -> dict[str, dict[str, dict]]`. Mỗi nhánh có `n, trung_vi, p25, p75, trang_thai`
    (`thang` | `dang-thu` | `loai` | `chua-du`), `lan_thu_cuoi` (ngày).
  - `winners(stats, cfg) -> dict[str, str]`.
  - `choose_test(slug, stats, cfg, last_tested: dict[str, date], today: date, cam: dict[str, set]) -> dict`. Trả về
    `{"bien": str, "nhanh": str, "kieu": "khai-thac"|"thu"}`.
  - `predict(stats, cfg, bien, nhanh, goc: float | None) -> str` (dạng `"lo-hi"`).
  - `build_directive(queue: list[str], stats, cfg, today, signals: dict, phien_ban: str) -> dict`.
  - `write_directive(tim: Path, directive: dict) -> None`: ghi `chi-thi.json` và `chi-thi.md`.
  - `main(argv) -> int` (`python -m loop.heart --config loop/config.json`).

**Thống kê:**
- Chỉ dòng `dung` đã khớp video mới tính.
- Thước đo của biến: `log(views_7d)` hoặc `apv_pct`.
- Nhánh không có trong config thì bỏ qua và in `heart: nhánh lạ <bien>=<nhanh> (bỏ qua)`.
- `thang` là nhánh có trung vị cao nhất trong các nhánh `n ≥ n_thang`.
- `loai` theo quy tắc của Global Constraints.

**`choose_test` (thuật toán người dựng không tự suy ra được):**
1. `rng = random.Random(seed(slug))`.
2. Ứng viên biến = `enabled_vars` có ít nhất 2 nhánh hợp lệ (không thuộc `cam[bien]`).
3. Biến nào `today - last_tested[bien] > 7` ngày (hoặc chưa thử lần nào) thì chọn biến đó, lấy biến bị bỏ lâu nhất.
   Không có biến như vậy thì chọn biến có `min(n)` nhỏ nhất trong các nhánh chưa `loai`. Hoà nhau thì chọn biến có hai
   trung vị đầu gần nhau nhất. Vẫn hoà thì theo thứ tự trong config.
4. `rng.random() < ti_le_thu` → kiểu `thu`: bốc trong các nhánh ≠ nhánh thắng.
   - Trọng số = 1 cho nhánh `n < n_loai`; = `san_thu` cho nhánh `n ≥ n_loai` chưa `loai`.
   - Nhánh `loai` chỉ có trọng số `san_thu` khi `lan_thu_cuoi` cách hôm nay ≥ `thu_lai_ngay`; nếu không thì trọng số 0.
   - Không còn nhánh nào có trọng số → kiểu `khai-thac`.
5. Ngược lại → kiểu `khai-thac`, nhánh = nhánh thắng (hoặc `mac_dinh`), biến vẫn ghi là biến được theo dõi.

**`build_directive`:**
- Tạo mục cho 3 slug đầu `queue`. Queue rỗng thì một mục `_tu-chon` (Review Focus 3).
- Mỗi mục có: `gia_tri` = nhánh thắng của mọi biến, đè bằng nhánh thử nếu có; `bien_thu`, `nhanh_thu`, `kieu`;
  `du_doan_7d`; `con_han_toi` = today + 3 ngày.
- Thêm `co_cau_tuan`: số video của 7 video tới cho từng nhánh của `dong` và `ten_quen`, theo tỉ lệ 70/30.
- `goc` của dự đoán: trung vị `views_7d` của các dòng đã chấm. Có dưới 3 dòng thì dùng trung vị `views_latest` của video
  đăng 8 tuần, quá 7 ngày, trong ledger, và ghi "goc tạm" (giống `ke-hoach-tuan.md`).
- `chi-thi.md` viết lời tiếng Việt:
  - mỗi biến một dòng "đang dùng X vì <n video, trung vị Y> / chưa đủ số, dùng mặc định vì <can_cu>";
  - sau đó mục "Video kế tiếp";
  - rồi "Tín hiệu khán giả và thị trường" (5 cụm đầu + từ khoá tìm kiếm);
  - rồi "Ràng buộc luôn áp" (chép đúng các ràng buộc ở spec 5.1).

**`main`:**
- Đọc config vòng và `heart_config`, `ledger.csv`, snapshot cache, sổ.
- Đọc `V/van-hanh/ke-hoach/hang-cho.md`: cột đầu bảng là slug; bỏ dòng tiêu đề.
- Đọc `cache/upload-history.csv`: có dòng `ghi_chu` chứa `publishAt rejected` → coi `gio_dang` là `khong_kha_dung` khi
  dựng chỉ thị, và in một dòng cảnh báo.
- Ghi `ket-qua.csv`, `chi-thi.json`, `chi-thi.md`. `phien_ban` = `YYYY-MM-DD-HHMM`.
- Thoát 0 nếu ghi được chỉ thị; thoát 1 nếu lỗi.

- [ ] **Step 1: Test.** `test_heart.py`:
  - `test_stats_ignore_unscored_and_unknown_arms` (Review Focus 5);
  - `test_winner_needs_n_thang`;
  - `test_kill_rule_rank_based` (n=6 dưới p25 → `loai`; n=5 → không);
  - `test_choose_test_deterministic_per_slug`;
  - `test_explore_share_close_to_30pct_over_1000_slugs` (từ 0,25 tới 0,35);
  - `test_forced_variable_after_7_days`;
  - `test_killed_arm_only_after_30_days`;
  - `test_forbidden_arm_never_drawn`;
  - `test_directive_has_tu_chon_when_queue_empty`;
  - `test_gio_dang_off_when_publish_rejected`;
  - `test_directive_md_mentions_every_enabled_var`.
- [ ] **Step 2: Chạy, xem fail.**
- [ ] **Step 3: Viết `heart.py`.**
- [ ] **Step 4: Chạy, xem pass.** Thêm: `python -m loop.heart --config loop/config.json` trên dữ liệu thật in ra 7 biến,
  không biến nào đủ `n` (sổ còn rỗng), mục cho 3 slug của hàng chờ.
- [ ] **Step 5: Commit** `feat(heart): arm stats, one-variable test draw, nightly directive`.

### Task 5: Nối vào vòng và ledger

**Files:**
- Modify: `loop/run.ps1` (sau bước ledger, trước dashboard)
- Modify: `loop/ledger.py` (`build_ledger`)
- Modify: `docs/loop-pc.md` (mục "Trái tim")
- Test: `loop/test_ledger.py`, `loop/test_notify_fail.py` nếu nó kiểm nội dung `run.ps1`

**Interfaces:**
- Consumes: `book.read`, `book.merged` (Task 2); `heart.main` (Task 4).
- Produces: dòng ledger lấy `footage_pct, sec_per_cut, render_pct, thumb_brightness, script_words, runtime_s` từ dòng
  `so-do` của sổ khi có, trước khi đoán ngược từ file dự án.

`run.ps1`:
- Chạy `python -m loop.heart --config $config`.
- Thoát ≠ 0 → `Write-Warning`, rồi ghi một dòng `- <giờ> heart exit <code>` vào `alerts.md` (đường dẫn suy từ
  `report_dir`).
- Không thoát sớm.

- [ ] **Step 1: Test.**
  - `test_ledger_prefers_book_measures`: sổ có `footage_pct=55` cho slug → ledger ghi 55, dù file dự án nói khác.
  - `test_run_ps1_calls_heart_non_fatal`: đọc nội dung `run.ps1`, kiểm có `loop.heart` và không có `exit` sau nó.
- [ ] **Step 2: Chạy, xem fail.**
- [ ] **Step 3: Sửa `ledger.py` và `run.ps1`. Viết mục trong `docs/loop-pc.md`.**
- [ ] **Step 4: Chạy toàn bộ `python -m pytest -q`, xem pass.** Thêm: `powershell -File loop/run.ps1` thoát 0 và
  `V/van-hanh/tim/chi-thi.md` tồn tại.
- [ ] **Step 5: Commit** `feat(heart): run the heart in the nightly loop; ledger reads book measures`.

### Task 6: Chỉ tự đổi thumbnail trong 7 ngày đầu

**Files:**
- Modify: `loop/rules.py` (`_swap_allowed`, hằng `MAX_SWAP_AGE_H = 168`)
- Test: `loop/test_rules.py`

**Interfaces:**
- Produces:
  - `_swap_allowed` trả `False` khi `snap["age_hours"] > MAX_SWAP_AGE_H`.
  - Ở nhánh "clearly low" của `_decide`, video quá 168 giờ trả `Verdict.CHANGE_TITLE`, lý do kết thúc bằng
    `", over 7 days: left as is"`.

- [ ] **Step 1: Test.**
  - `test_no_swap_after_7_days`: 169 giờ, CTR thấp rõ → `CHANGE_TITLE`, có hậu tố.
  - `test_swap_still_allowed_at_100h`.
- [ ] **Step 2: Chạy, xem fail.**
- [ ] **Step 3: Sửa `rules.py`.** Thêm vào `loop/dashboard.py` `reason_plain`: hậu tố đó thành câu "Video đã quá 7 ngày:
  để nguyên, không sửa." (thay cho câu "Nên thử tiêu đề hoặc thumbnail khác").
- [ ] **Step 4: Chạy toàn bộ test, xem pass.**
- [ ] **Step 5: Commit** `feat(rules): thumbnail swaps only in a video's first 7 days`.

### Task 7: Hẹn giờ đăng trong `push_to_pending.py` (repo dựng)

**Files:**
- Modify (chỉ stage): `D:/Youtube/video_footage_re_create/playbook/upload_tools/push_to_pending.py`
- Test (chỉ stage): `D:/Youtube/video_footage_re_create/playbook/tests/test_publish_at.py`

**Interfaces:**
- Produces:
  - Tham số `--publish-at <ISO 8601 có múi giờ>`.
  - `next_pickup(now: datetime) -> datetime`: lần nhận file kế tiếp, 09:00 hoặc 21:00 giờ `Asia/Ho_Chi_Minh`, tính sau
    `now`.
  - `publish_at_ok(publish_at, now) -> bool`: `publish_at >= next_pickup(now) + 2h`.
  - `gates_clean(proj: Path) -> tuple[bool, str]`: đọc `trang-thai.json` của dự án; đúng khi `ship_gate_fail == 0` và
    `policy_open == 0` (hai khoá do tác vụ dựng ghi, xem Task 10). Thiếu khoá thì là không sạch, kèm lý do.

Hành vi:
- Cả hai điều kiện đúng → `meta["publishAt"]` = giá trị đã cho. Uploader đã hỗ trợ trường này, `rfc3339_utc`.
- Ngược lại → không ghi `publishAt`, in `WARNING: ... video stays private`, và thêm một dòng
  `- <giờ> publishAt not set for <base>: <lý do>` vào `V/van-hanh/bao-cao/studio/alerts.md`.
- Không có `--publish-at` → như hiện nay.

- [ ] **Step 1: Test.**
  - `test_next_pickup_morning_and_evening` (08:59 → 09:00 cùng ngày; 09:01 → 21:00; 21:30 → 09:00 hôm sau);
  - `test_publish_at_too_soon_rejected`;
  - `test_gates_missing_keys_not_clean`;
  - `test_meta_has_publish_at_only_when_clean` (dùng `build_meta` + hàm mới, không gọi Drive).
- [ ] **Step 2: Chạy, xem fail.** Lệnh: `python -I -m pytest -q playbook/tests/test_publish_at.py` ở repo dựng.
- [ ] **Step 3: Sửa `push_to_pending.py`.**
- [ ] **Step 4: Chạy, xem pass.** Thêm `--dry-run` trên một dự án có sẵn với `--publish-at` hợp lệ → in `publishAt` trong
  phần settings.
- [ ] **Step 5: Stage (không commit).**

### Task 8: Uploader đăng lại không kèm `publishAt` khi bị từ chối

**Files:**
- Modify: `scripts/youtube_uploader.py` (khối gọi `upload_video` trong lần xử lý một bộ file)
- Test: `scripts/test_youtube_uploader.py`

**Interfaces:**
- Produces:
  - `is_publish_at_rejection(exc: HttpError) -> bool`: mã 400 hoặc 403, và lý do hay nội dung có `publishAt` hoặc
    `invalidPublishAt`.
  - Khi đúng: xoá `status.publishAt` khỏi `body`, tải lại một lần, ghi lịch sử với
    `ghi_chu = "publishAt rejected: <lý do 120 ký tự>; " + where_ok`.

- [ ] **Step 1: Test.**
  - `test_publish_at_rejection_retries_private` (fake YouTube: lần đầu ném HttpError 403 có "publishAt", lần hai trả id
    → video_id đúng, lịch sử có `publishAt rejected`);
  - `test_other_403_not_retried`.
- [ ] **Step 2: Chạy, xem fail.**
- [ ] **Step 3: Sửa uploader.**
- [ ] **Step 4: Chạy `python -m pytest -q scripts`, xem pass.**
- [ ] **Step 5: Commit** `feat(uploader): fall back to a private upload when publishAt is refused`.

### Task 9: Dữ liệu "Thử nghiệm" cho bảng điều khiển

**Files:**
- Modify: `loop/dashboard.py`
- Test: `loop/test_dashboard.py`

**Interfaces:**
- Consumes:
  - `V/van-hanh/tim/chi-thi.json`, `ket-qua.csv`;
  - `book.merged`, `heart.arm_stats` (hoặc đọc `chi-thi.json`, nơi đã có thống kê nhánh: người dựng chọn một, miễn
    không tính lại hai lần).
- Produces: ba khoá mới trong `dashboard.json`.
  - `tn_nhanh`: `bien, nhanh, n, trung_vi, trang_thai, dang_dung`.
  - `tn_so`: 14 slug mới nhất: `slug, ngay, bien_thu, nhanh_thu, kieu, du_doan_7d, views_7d, ket_qua, canh_bao`.
  - `tn_ke_tiep`: các mục của chỉ thị.
  - Nhãn tiếng Việt dễ hiểu: tên biến hiển thị qua một bảng tên, ví dụ `dong` → "Kiểu video",
    `thumb_sang` → "Thumbnail sáng hay tối".

- [ ] **Step 1: Test.**
  - `test_dashboard_has_experiment_keys_when_heart_files_missing`: thiếu file → ba danh sách rỗng, không lỗi.
  - `test_tn_nhanh_labels_vietnamese`.
- [ ] **Step 2–4: Viết code, chạy pass.**
- [ ] **Step 5: Commit** `feat(dashboard): experiment data from the heart`.
- [ ] **Step 6 (người điều phối, không phải subagent): trang bảng điều khiển.**
  - Thêm 3 dataset (`tn_nhanh`, `tn_so`, `tn_ke_tiep`).
  - Thêm mục "Thử nghiệm" vào trang qua ArtifactData:
    - bảng biến × nhánh;
    - thẻ "Video kế tiếp thử gì";
    - bảng 14 quyết định gần nhất với cột "Đúng dự đoán?".
  - Thêm 3 dataset mới vào danh sách tác vụ 20:30 cập nhật.

### Task 10: Nối các tác vụ hẹn giờ và sổ tay; bật dần theo giai đoạn

Người điều phối làm task này. Nó sửa file ngoài repo yt-uploader: prompt tác vụ hẹn giờ, sổ tay trong vault, skill
`quan-ly-kenh`. Mọi sửa đều viết file Python vá rồi chạy, không heredoc.

**Files:**
- Modify (không thuộc git): `C:\Users\Admin\.claude\scheduled-tasks\{bareskull-quan-ly-kenh-hang-ngay,
  bareskull-chon-chu-de-tuan, bareskull-cai-tien-tuan, bareskull-bao-cao-toi, bareskull-studio-loop}\SKILL.md`
- Modify (repo dựng, stage): `V/van-hanh/so-tay/tac-vu-tu-dong.md` (§3, §4, §6), `V/van-hanh/so-tay/nv-chu-de.md`,
  `.claude/skills/quan-ly-kenh/references/ke-hoach-tuan.md`

Nội dung từng sửa (theo spec 5.6):

- **Dựng 06:10 (§4).**
  - Đọc `V/van-hanh/tim/chi-thi.md` trước bước A; dùng mục của đúng slug, hoặc `_tu-chon`.
  - Chỉ thị quá `con_han_toi`, hoặc thiếu file → dùng `mac_dinh` của `heart_config.json` và ghi `--ly-do khong-chi-thi`.
    Không bao giờ dừng phim vì chỉ thị.
  - Áp `gia_tri`; viết tiêu đề và thumbnail BÊN TRONG nhánh đó.
  - Ghi `ghi_quyet_dinh.py ghi --tac-vu dung …` ở bước A.
  - Sau ship_gate: ghi `ship_gate_fail` và `policy_open` vào `trang-thai.json`, rồi `ghi … --tac-vu so-do …` với số đo
    phim.
  - Nếu `gio_dang` đang bật: gọi `push_to_pending.py --publish-at <giờ trong khoảng nhánh, ≥ next_pickup + 2h>`.
  - Bỏ "điều cần chủ kênh quyết", trừ ba việc chỉ chủ kênh làm được.
  - Giai đoạn 1: `gia_tri` là gợi ý; giai đoạn ≥2: bắt buộc, chỉ lệch khi cổng cứng chặn (`--lech --ly-do`).
- **Chọn chủ đề Chủ nhật (§3).**
  - Đọc `chi-thi.md`; cơ cấu hàng chờ theo `co_cau_tuan`.
  - Ghi `--tac-vu ke-hoach` cho từng chủ đề.
  - Bỏ câu "trả lời tên chủ đề muốn bỏ…"; báo cáo cuối chỉ để xem.
- **Cải tiến 05:30.**
  - Đọc `chi-thi.md` và `ket-qua.csv`.
  - Không đổi luật số của các biến có trong `heart_config.json`. Đề xuất nhánh mới bằng cách sửa `heart_config.json` chỉ
    khi có ≥3 video làm căn cứ: ghi TD như hiện nay, có bản lưu, tự hoàn lại.
  - Prompt hiện cấm nó đụng "cấu hình vòng studio (`loop/`)". Ghi rõ ngoại lệ duy nhất là thêm nhánh vào
    `loop/heart_config.json`. Không được xoá nhánh, đổi hằng số hay sửa file nào khác trong `loop/`. Sau mỗi lần sửa,
    nó chạy `python -m pytest -q loop/test_heart_config.py`; fail thì hoàn lại ngay từ bản lưu.
  - Mỗi TD còn ghi một dòng `--tac-vu doi-luat`.
- **Báo cáo tối 21:00.**
  - Thêm mục "Trái tim" ở đầu báo cáo ngày, lấy từ `chi-thi.md` và `ket-qua.csv`. Mọi dòng có `canh_bao` (lệch tiêu đề,
    lệch thumbnail, lỡ mốc) được nêu ở đó.
  - Bỏ đề xuất đổi tiêu đề video cũ (bước 4c).
- **Vòng 20:30.** Bước 4 cập nhật cả ba dataset `tn_*`.

**Bật dần theo giai đoạn:**
- [ ] **Giai đoạn 1:**
  - `giai_doan = 1`.
  - Theo dõi 2–3 phim: mỗi phim có đủ dòng `dung` và `so-do`, ledger khớp được, `chi-thi.md` được đọc (dấu vết trong
    `BUILD_LOG.md`).
  - Phim đầu tiên đẩy Drive với `--publish-at` để kiểm YouTube có nhận không, kể cả khi `gio_dang` chưa bật: giờ hẹn =
    khung `19-22h`.
- [ ] **Giai đoạn 2:**
  - Khi giai đoạn 1 sạch: `giai_doan = 2`.
  - `gio_dang.bat = true` nếu `publishAt` được nhận; bị từ chối thì `khong_kha_dung = true` và cảnh báo chủ kênh.
- [ ] **Giai đoạn 3:** đổi prompt tác vụ cải tiến (ở trên) và ghi bộ nhớ dự án.

---

**Kiểm cuối:**
- Chạy toàn bộ test hai repo.
- Một lượt `run.ps1` thật ra `chi-thi.md` và bảng điều khiển có mục "Thử nghiệm".
- Rà soát cả nhánh trước khi gộp vào `main` và kéo về VPS (Task 8 cần VPS kéo bản mới).
