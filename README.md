# AutoDangKiTin_TLU

Tool tự động đăng ký tín chỉ trường ĐH Thăng Long (TLU).

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Cấu hình `.env` với các URL TLU + Google OAuth credentials (copy từ `.env.example` nếu có).

## Chạy

**Không tham số → TUI (Textual):**
```bash
python main.py
```

**Có tham số → CLI (Typer):**
```bash
python main.py login                                              # đăng nhập
python main.py register --index 0 --index 1                      # đăng ký theo chỉ số
python main.py register --all                                    # đăng ký tất cả
python main.py register --all --no-auto-sniff                    # tắt auto-sniff
python main.py register --profile auto_request_290625.json       # chạy custom profile
python main.py sniff --index 0 --interval 2                      # săn riêng
python main.py export-ics                                        # xuất lịch .ics
python main.py sync-calendar                                     # đồng bộ Google Calendar
python main.py profile list                                      # liệt kê custom profile
python main.py profile run NAME                                  # chạy custom profile
python main.py profile delete NAME                               # xóa custom profile
```

## Tính năng

- **Đăng ký nhanh** — chọn môn (cursor + Enter) → burst register (giống cũ).
- **Sniffing** — chỉ bắn POST khi GET xác nhận `isFullClass == False`, tránh spam → giảm rủi ro ban so với bản cũ.
- **Auto-sniff sau đăng ký** — môn fail tự chuyển sang sniffing, tùy chọn `--no-auto-sniff`.
- **Custom profile** — lưu hồ sơ JSON vào `res/custom/`, chạy lại được.
- **Lịch** — export `.ics` + sync Google Calendar (tạo calendar mới).
- **Settings** — toggle debug, chỉnh sniff interval, đăng xuất.

## Tại sao "check-then-register" thay vì "burst spam"?

- Lúc mở đăng ký: server tạm tắt rate-limit → burst spam POST **an toàn**, không bị ban.
- Sau khi mở đăng ký (sniff): server bật lại check → spam POST có rủi ro.
- Sniffing mới chỉ **GET** danh sách môn (request nhẹ) → chỉ khi thấy slot trống mới **POST** register.
- Tổng request giảm mạnh, ban risk giảm theo.

## Cấu trúc

```
main.py                      # entry: no-arg -> TUI, args -> CLI
src/
  cli/app.py                 # Typer CLI
  tui/app.py                 # Textual TUI (5 use cases + log screen)
  services/
    register_service.py      # register_subjects/custom + sniffing_loop
    course_service.py        # fetch + parse course list
    auth_service.py          # login + load_saved_user
    calendar_service.py      # export ICS + Google sync
    custom_service.py        # quản lý hồ sơ JSON
  core/
    client.py                # httpx client + session
    exceptions.py
  models/                    # Course, User
  config.py                  # env-based config
res/
  custom/                    # custom profile JSON
  login.json, token.json     # session (plaintext — tự bảo quản)
  *.ics                      # exported schedule
```

## Lưu ý

- `res/login.json` chứa **plaintext** mật khẩu. Đã gitignored — tự bảo quản, không commit.
- `.env` chứa URL + Google OAuth secrets — gitignored.
- Interval sniff mặc định **2s** (điều chỉnh qua flag `--interval` hoặc env `SNIFF_INTERVAL`).
- Dependency đã pin `tatsu<5.12` để tương thích `ics==0.7.2`.
