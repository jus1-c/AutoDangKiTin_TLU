# AutoDangKiTin TLU

Tool tự động đăng ký tín chỉ trường ĐH Thuỷ Lợi (TLU).

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Cấu hình `.env` với các URL TLU + Google OAuth credentials (copy từ `.env.example` nếu có).

## Chạy

```bash
python main.py                    # TUI (giao diện terminal, mặc định)
python main.py --help             # xem tất cả lệnh CLI
```

### CLI

```bash
# Đăng nhập
python main.py login

# Đăng ký theo chỉ số môn
python main.py register --index 0 --index 1
python main.py register --all
python main.py register --all --no-auto-sniff          # tắt sniff khi môn đầy
python main.py register --profile ten_file.json        # chạy từ custom profile

# Săn môn (check-then-register) đến khi trúng hoặc Ctrl-C
python main.py sniff --index 0 --interval 2

# Đăng xuất (xóa token + login)
python main.py login --no-save

# Lịch
python main.py export-ics                              # xuất .ics
python main.py sync-calendar                           # đồng bộ Google Calendar

# Custom profile
python main.py profile list                           # liệt kê
python main.py profile run NAME                       # chạy
python main.py profile delete NAME                    # xóa

# Multi-account (chỉ CLI chạy được)
python main.py multireg list                          # liệt kê file định nghĩa
python main.py multireg create --name dot1 \
  --account sv001:pass:profile.json:main \
  --account sv002:pass:profile.json:summer           # tạo file
python main.py multireg run dot1.json                  # chạy N account song song

# Offline mode (dùng cache user_info.json, 0 API call lúc khởi động)
python main.py register --all --offline
```

### TUI (mặc định)

Chạy `python main.py` (không tham số) để mở giao diện terminal. Sau khi đăng nhập, menu chính có 7 chức năng:

| Phím | Chức năng | Mô tả |
|------|----------|-------|
| **1** | Đăng ký nhanh | Chọn môn (multi-select) → bắn N request song song → môn đầy tự sniff |
| **2** | Tạo danh sách custom | Chọn lớp cho từng môn (bảngDataTable, trùng lịch bị xám) → lưu file JSON |
| **3** | Đăng ký theo profile | Chọn file profile đã lưu → đăng ký → sniff nếu môn đầy |
| **4** | Lịch | Xuất file ICS hoặc đồng bộ Google Calendar |
| **5** | Settings | Chỉnh sniff interval, burst count, concurrency, hẹn giờ đăng ký |
| **6** | Multi-account | Tạo/quản lý file định nghĩa nhiều account (chạy bằng CLI hoặc nút **Chạy** trong TUI) |
| **7** | Chuyển lớp | Đăng nhập 2 account → tick lớp cần chuyển → engine tự drop + chụp |

## Tính năng

### Đăng ký nhanh
- Chọn nhiều môn cùng lúc (SelectionList), bắn `BURST_COUNT` request **song song** mỗi môn.
- Môn nào lớp đầy (status `-6`) → tự động sniff: GET danh sách mỗi `SNIFF_INTERVAL` giây, chỉ POST khi thấy slot trống.
- Môn nào đã đăng ký rồi (status `-4`) → dừng ngay, không thử các lớp khác trong môn.
- Môn trùng lịch (status `-2`) → bỏ lớp đó, thử lớp khác cùng môn.

### Sniffing (săn môn)
- **Check-then-register**: GET danh sách môn (request nhẹ) → chỉ POST register khi `isFullClass == False`.
- Tránh spam POST → giảm rủi ro bị ban.
- Giới hạn thời gian qua `SNIFF_MAX_DURATION_MIN` (Settings, 0 = vô hạn).

### Hẹn giờ đăng ký (Schedule)
- Bật trong Settings → nút "Đăng ký" sẽ qua màn hình đếm ngược trước khi vào flow đăng ký.
- Mốc đích = thời gian mở đăng ký (lấy tự động từ API TLU).
- `SCHEDULE_LEAD_SECONDS` = giây đạn trước khi mở (mặc định 30s).

### Multi-account register
- Tạo file định nghĩa `res/multireg/*.json` (qua TUI hoặc CLI): danh sách account + profile.
- Mỗi account: 1 session riêng, đăng nhập độc lập, chạy **song song** qua `asyncio.gather`.
- 2 chế độ: **profile-mode** (pick lớp cụ thể) hoặc **subject-mode** (pick môn, thử các lớp — cần đăng nhập lúc tạo file).
- Log riêng mỗi account: `res/logs/{username}_{timestamp}.log`.
- Sniff fallback khi lớp đầy là mặc định global (`AUTO_SNIFF_FALLBACK`).
- Nút **Dừng** / **Quay lại** cắt sniffing ngay (dừng mềm).

### Chuyển lớp giữa 2 account
- 2 form đăng nhập trái/phải (nút "Dùng user hiện tại" đọc từ `login.json`).
- Sau khi đăng nhập: bảng lớp đã đăng ký hiện ra, lớp bên nhận không đủ điều kiện bị **xám**.
- Tick lớp bên muốn nhả → bên kia chụp. Có thể tick cả 2 bên (swap).
- **Pre-burst**: bên nhận bắt đầu bắn register trước khi bên cho drop lớp → giành slot ngay khi mở.
- **Swap cùng slot** (β): drop cả 2 song song rồi chụp chéo — cảnh báo rủi ro mất lớp.
- Lỗi trùng lịch với lớp đang giữ → báo lỗi, không thực thi cho đến khi sửa selection.
- 2 `TLUClient` riêng, tắt auto-renew token (tránh nhầm creds).

### Phân loại status code
Server TLU trả status JSON khi đăng ký. Phần mềm phân loại để tránh burst/spin vô nghĩa:

| Status | Ý nghĩa | Hành vi |
|--------|---------|---------|
| `0` | Thành công | Dừng |
| `-2` | Trùng lịch | Bỏ lớp này, thử lớp khác cùng môn |
| `-4` | Đã đăng ký môn | Dừng cả môn (mọi lớp cùng môn cũng fail) |
| `-6` | Lớp đầy | Đưa vào sniffing (chờ slot mở) |
| khác | Lỗi không xác định | Thử lớp kế, không sniff |

### Offline mode
- Bật toggle "Offline" ở màn đăng nhập (hoặc `--offline` CLI).
- Dùng `res/user_info.json` (cache danh tính) thay vì gọi API đăng nhập → 0 request lúc khởi động.
- Yêu cầu: đã đăng nhập online ít nhất 1 lần trước đó.
- Nút "Lịch" bị tắt trong offline mode.

### Auto-renew token
- Khi token hết hạn giữa chừng (HTTP 401), tự động đăng nhập lại từ `res/login.json` + retry request.
- Lock chống concurrent renew (nếu 10 request cùng lúc 401, chỉ 1 renew).

### Bắn login liên tục
- Bật toggle ở màn đăng nhập → bắn request login liên tục đến khi lấy được token.
- Dùng khi server TLU quá tải (giờ cao điểm), login endpoint hay timeout/503.
- Backoff 0.5s → 3s + jitter. Auth error (sai mật khẩu) → dừng ngay.
- Nút "Thoát" giữa chừng → hủy.

### Custom profile
- Lưu danh sách lớp cụ thể vào `res/custom/*.json` (envelope v2: version, semester_id, courses).
- Tái sử dụng lần sau. Học kỳ lấy tự động từ profile.
- CLI: `python main.py profile run NAME`.

### Lịch
- Export thời khóa biểu ra file `.ics` (mở bằng Google Calendar, Outlook, Apple Calendar).
- Đồng bộ trực tiếp lên Google Calendar (tạo calendar mới, cần OAuth).

### Settings (cấu hình runtime)
- `AUTO_SNIFF_FALLBACK` — tự sniff khi môn đầy (bật/tắt).
- `BURST_COUNT` — số request song song mỗi lần thử (mặc định 5).
- `CONCURRENCY_LIMIT` — giới hạn tổng request đồng thời (mặc định 20).
- `SNIFF_INTERVAL` / `SNIFF_JITTER` — khoảng + nhiễu sniff (giây).
- `SNIFF_MAX_DURATION_MIN` — giới hạn thời gian sniff (0 = vô hạn).
- `SCHEDULE_ENABLED` / `SCHEDULE_LEAD_SECONDS` — hẹn giờ đăng ký.
- `TRANSFER_PRE_BURST_LEAD` / `TRANSFER_GRAB_TIMEOUT` — chuyển lớp: lead pre-burst + timeout grab.
- `DEBUG` — log chi tiết request/response.
- Lưu vào `res/settings.json`, áp dụng lần sau.

## Cấu trúc

```
main.py                          # entry: no-arg → TUI, args → CLI
src/
  cli/app.py                     # Typer CLI
  tui/app.py                     # Textual TUI (7 chức năng + log/transfer/multireg)
  services/
    register_service.py           # register + sniff + drop + transfer API
    transfer_service.py           # transfer engine (pre-burst, β swap)
    multireg_service.py           # multi-account orchestrator
    course_service.py             # fetch + parse course list
    auth_service.py               # login + offline + auto-renew
    calendar_service.py           # export ICS + Google sync
    custom_service.py             # quản lý profile JSON
  core/
    client.py                     # httpx client + session + auto-renew
    exceptions.py
  models/                         # Course, User
  config.py                       # env + settings.json persistence
res/
  custom/                         # custom profile JSON
  multireg/                       # file định nghĩa multi-account
  logs/                           # log chạy multi-account (per-account)
  login.json, token.json          # session (plaintext — tự bảo quản)
  user_info.json                  # cache danh tính cho offline
  settings.json                   # cấu hình runtime
  *.ics                           # exported schedule
```

## Lưu ý

- `res/login.json` chứa **plaintext** mật khẩu. Đã gitignored — tự bảo quản, không commit.
- `res/multireg/*.json` cũng chứa plaintext credentials của nhiều account.
- `.env` chứa URL + Google OAuth secrets — gitignored.
- `SNIFF_INTERVAL` mặc định **2s** (điều chỉnh qua Settings hoặc env).
- Dependency đã pin `tatsu<5.12` để tương thích `ics==0.7.2`.
- Transfer (chuyển lớp) chỉ chạy được khi cửa sổ đăng ký/hủy đang mở (`allowRegister` hoặc `isAllowUnRegister` = true).