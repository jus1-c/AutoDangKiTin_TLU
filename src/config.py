import json
import os
import sys
from dotenv import load_dotenv

# Support for bundled .env file in PyInstaller
if getattr(sys, 'frozen', False):
    env_path = os.path.join(sys._MEIPASS, '.env')
    load_dotenv(env_path)
else:
    load_dotenv()


class Config:
    # TLU URLs
    TLU_LOGIN_URL = os.getenv("TLU_LOGIN_URL")
    TLU_INFO_URL = os.getenv("TLU_INFO_URL")
    TLU_SEMESTER_URL = os.getenv("TLU_SEMESTER_URL")
    TLU_API_BASE_URL = os.getenv("TLU_API_BASE_URL")

    # Google Credentials
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    
    # App Settings (defaults from .env; overridable at runtime + persisted to SETTINGS_FILE)
    CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", 20))
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30))
    SNIFF_INTERVAL = float(os.getenv("SNIFF_INTERVAL", 2.0))
    SNIFF_JITTER = float(os.getenv("SNIFF_JITTER", 0.5))
    SNIFF_MAX_DURATION_MIN = int(os.getenv("SNIFF_MAX_DURATION_MIN", 60))  # 0 = infinite
    BURST_COUNT = int(os.getenv("BURST_COUNT", 5))
    # Timeout riêng cho register request (giây). Ngắn hơn timeout
    # chung để tránh treo quá lâu.
    BURST_REQUEST_TIMEOUT = float(os.getenv("BURST_REQUEST_TIMEOUT", 10.0))
    # NOTE: BURST_MAX_ATTEMPTS đã bỏ (giờ retry vô hạn cho transient
    # errors trong _send_register_request). Không dùng nữa.
    AUTO_SNIFF_FALLBACK = os.getenv("AUTO_SNIFF_FALLBACK", "True").lower() in ("true", "1", "yes")
    DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
    # Schedule: khi bật, các nút "Đăng ký" sẽ qua màn hình đếm ngược
    # trước khi vào register UI. Mốc đích = thời gian mở đăng kí (lấy từ API).
    SCHEDULE_ENABLED = os.getenv("SCHEDULE_ENABLED", "False").lower() in ("true", "1", "yes")
    SCHEDULE_LEAD_SECONDS = int(os.getenv("SCHEDULE_LEAD_SECONDS", 30))

    # User-editable settings persisted to SETTINGS_FILE (subset of the above)
    _PERSIST_KEYS = (
        "CONCURRENCY_LIMIT",
        "SNIFF_INTERVAL",
        "SNIFF_JITTER",
        "SNIFF_MAX_DURATION_MIN",
        "BURST_COUNT",
        "BURST_REQUEST_TIMEOUT",
        "AUTO_SNIFF_FALLBACK",
        "SCHEDULE_ENABLED",
        "SCHEDULE_LEAD_SECONDS",
        "DEBUG",
    )
    
    # Paths
    # RES_DIR phải là absolute path dựa trên project root, KHÔNG phụ thuộc
    # working directory. Nếu dùng đường dẫn tương đối ("res"), file sẽ bị
    # tạo ở thư mục sai khi user chạy app từ chỗ khác (vd: shortcut, alias,
    # service, IDE debugger). Bug này khiến user_info.json không xuất hiện
    # ở project root → offline mode không hoạt động.
    if getattr(sys, 'frozen', False):
        # PyInstaller: dùng thư mục chứa executable
        _PROJECT_ROOT = os.path.dirname(sys.executable)
    else:
        # src/config.py → src/ → project root
        _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RES_DIR = os.path.join(_PROJECT_ROOT, "res")
    TOKEN_FILE = os.path.join(RES_DIR, "token.json")
    USER_INFO_FILE = os.path.join(RES_DIR, "user_info.json")
    LOGIN_FILE = os.path.join(RES_DIR, "login.json")
    GOOGLE_TOKEN_FILE = os.path.join(RES_DIR, "token_google.json")
    SETTINGS_FILE = os.path.join(RES_DIR, "settings.json")
    # Multi-account register: file định nghĩa (res/multireg/*.json) + log
    # chạy theo từng account (res/logs/{username}_{ts}.log).
    MULTIREG_DIR = os.path.join(RES_DIR, "multireg")
    LOGS_DIR = os.path.join(RES_DIR, "logs")

    @classmethod
    def ensure_dirs(cls):
        os.makedirs(cls.RES_DIR, exist_ok=True)
        os.makedirs(os.path.join(cls.RES_DIR, "custom"), exist_ok=True)
        os.makedirs(cls.MULTIREG_DIR, exist_ok=True)
        os.makedirs(cls.LOGS_DIR, exist_ok=True)

    @classmethod
    def load_settings(cls):
        """Load persisted user settings, overriding the .env defaults.

        Silently ignores a missing/corrupt file (falls back to .env defaults).
        """
        if not os.path.exists(cls.SETTINGS_FILE):
            return
        try:
            with open(cls.SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for key in cls._PERSIST_KEYS:
            if key in data:
                setattr(cls, key, data[key])

    @classmethod
    def save_settings(cls):
        """Persist the user-editable settings to SETTINGS_FILE."""
        cls.ensure_dirs()
        data = {key: getattr(cls, key) for key in cls._PERSIST_KEYS}
        with open(cls.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)