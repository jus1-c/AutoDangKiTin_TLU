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
    
    # App Settings
    CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", 20))
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30))
    DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
    
    # Paths
    RES_DIR = "res"
    TOKEN_FILE = os.path.join(RES_DIR, "token.json")
    USER_INFO_FILE = os.path.join(RES_DIR, "user_info.json")
    LOGIN_FILE = os.path.join(RES_DIR, "login.json")
    GOOGLE_TOKEN_FILE = os.path.join(RES_DIR, "token_google.json")

    @classmethod
    def ensure_dirs(cls):
        if not os.path.exists(cls.RES_DIR):
            os.makedirs(cls.RES_DIR)
        if not os.path.exists(os.path.join(cls.RES_DIR, "custom")):
            os.makedirs(os.path.join(cls.RES_DIR, "custom"))