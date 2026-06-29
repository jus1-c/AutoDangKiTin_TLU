import httpx
import json
import urllib.parse
import os
import asyncio
from typing import Optional, Tuple, Dict, Any
from src.config import Config
from src.core.exceptions import LoginError, NetworkError, SessionExpiredError

import warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class TLUClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            verify=False,
            timeout=Config.REQUEST_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Host": "sinhvien1.tlu.edu.vn"
            }
        )
        self.headers = {}
        self.cookies = {}
        self.is_authenticated = False
        # Lock chống concurrent auto-renew. Nếu 10 request cùng lúc
        # đều 401, chỉ 1 renew, 9 request kia đợi rồi retry với token mới.
        self._renew_lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def load_session(self, validate: bool = True) -> bool:
        """Load saved token into headers/cookies.

        validate=True (default): gọi get_student_info để kiểm tra token
        còn sống. Dùng cho auto-login online.
        validate=False: chỉ nạp token, không gọi API. Dùng cho offline mode
        (các call sau sẽ tự fail nếu mạng mất, nhưng không block ngay).
        """
        if not os.path.exists(Config.TOKEN_FILE):
            return False
        
        try:
            with open(Config.TOKEN_FILE, 'r') as f:
                data = json.load(f)
                self.headers = {"Authorization": data.get("Authorization", "")}
                self.cookies = {"token": data.get("token", "")}

                if not validate:
                    # Offline mode — không gọi API, đánh dấu authenticated
                    # theo token cache. Caller chịu trách nhiệm xử lý khi
                    # các call sau gặp lỗi mạng.
                    self.is_authenticated = True
                    return True

                try:
                    await self.get_student_info(check_only=True)
                    self.is_authenticated = True
                    return True
                except (SessionExpiredError, NetworkError):
                    return False
        except Exception:
            return False

    async def login(self, username, password) -> bool:
        login_data = {
            "client_id": "education_client", 
            "grant_type": "password", 
            "username": username, 
            "password": password, 
            "client_secret": "password"
        }
        
        try:
            response = await self.client.post(Config.TLU_LOGIN_URL, data=login_data)
            
            if response.status_code != 200:
                if Config.DEBUG:
                    print(f"[DEBUG ERROR BODY] {response.text}")
                raise LoginError(f"Login failed: {response.status_code} - {response.text}")
            
            data = response.json()
            if "error" in data:
                raise LoginError(f"Login error: {data.get('error_description', 'Unknown error')}")

            access_token = data.get('access_token')
            self.headers = {"Authorization": f"Bearer {access_token}"}
            self.cookies = {"token": urllib.parse.quote_plus(response.text)}
            
            self.is_authenticated = True
            self._save_session()
            return True
            
        except httpx.RequestError as e:
            raise NetworkError(f"Connection failed: {e}")

    def _save_session(self):
        Config.ensure_dirs()
        data = {
            "token": self.cookies.get("token"),
            "Authorization": self.headers.get("Authorization")
        }
        with open(Config.TOKEN_FILE, 'w') as f:
            json.dump(data, f)

    async def get_student_info(self, check_only=False) -> Dict[str, Any]:
        if not self.headers:
            raise SessionExpiredError("No credentials provided.")

        try:
            req_headers = self.client.headers.copy()
            req_headers.update(self.headers)

            response = await self.client.get(
                Config.TLU_INFO_URL, 
                headers=req_headers,
                cookies=self.cookies
            )
            
            if response.status_code > 399:
                 if Config.DEBUG:
                     print(f"[DEBUG ERROR BODY] {response.text}")
                 if response.status_code == 401:
                     raise SessionExpiredError("Token expired.")
                 raise NetworkError(f"API Error: {response.status_code}")
            
            return response.json()
            
        except httpx.RequestError as e:
            raise NetworkError(f"Connection failed: {e}")

    async def get_semester_info(self) -> Dict[str, Any]:
        try:
            req_headers = self.client.headers.copy()
            req_headers.update(self.headers)

            response = await self.client.get(
                Config.TLU_SEMESTER_URL,
                headers=req_headers,
                cookies=self.cookies
            )
            if response.status_code != 200:
                if Config.DEBUG:
                    print(f"[DEBUG ERROR BODY] {response.text}")
                raise NetworkError(f"Failed to get semester info: {response.status_code}")
            return response.json()
        except httpx.RequestError as e:
            raise NetworkError(f"Connection failed: {e}")
            
    async def request(self, method: str, url: str, **kwargs):
        req_headers = kwargs.get('headers', {})
        req_headers.update(self.headers)
        kwargs['headers'] = req_headers

        req_cookies = kwargs.get('cookies', {})
        req_cookies.update(self.cookies)
        kwargs['cookies'] = req_cookies

        if Config.DEBUG:
            print(f"\n[DEBUG REQUEST] {method} {url}")
            print(f"[DEBUG HEADERS] {kwargs['headers']}")

        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.RequestError as e:
            if Config.DEBUG:
                print(f"[DEBUG ERROR] {e}")
            raise NetworkError(f"Request failed: {e}")

        # Auto-renew token: 401 → thử login lại từ login.json + retry 1 lần.
        # Lock ngăn 10 request đồng thời đều renew (chỉ 1 renew, 9 kia
        # đợi rồi retry với token mới). get_student_info / get_semester_info
        # / login KHÔNG đi qua request() → không bị auto-renew (giữ
        # được validation flow cho load_session).
        if response.status_code == 401:
            renewed = await self._auto_renew_and_retry(method, url, **kwargs)
            if renewed is not None:
                response = renewed

        if Config.DEBUG:
            print(f"[DEBUG RESPONSE] {response.status_code}")
            if response.status_code != 200:
                print(f"[DEBUG ERROR BODY] {response.text[:1000]}")
            else:
                try:
                    print(f"[DEBUG BODY SNIPPET] {response.text[:200]}...")
                except Exception:
                    pass

        return response

    async def _auto_renew_and_retry(
        self, method: str, url: str, **kwargs
    ):
        """Thử renew token + retry request 1 lần. Trả response mới nếu
        OK, None nếu renew/retry fail. Dùng lock + version counter để
        chống concurrent renew thừa.

        Nếu 10 request đồng thời đều 401:
        - Tất cả capture _renew_version TRƯỚC khi vào lock
        - Lock serialize. Request đầu vào lock thấy version chưa đổi
          → gọi _try_renew_token, tăng version.
        - 9 request sau vào lock thấy version ĐÃ đổi → skip renew,
          chỉ retry với token mới. Tổng: 1 renew, 10 retry.
        """
        old_version = getattr(self, "_renew_version", 0)
        async with self._renew_lock:
            new_version = getattr(self, "_renew_version", 0)
            if new_version == old_version:
                # Chưa ai renew trong lúc ta đợi lock → ta renew
                if not await self._try_renew_token():
                    return None
                self._renew_version = old_version + 1
            # else: request khác đã renew trong lúc ta đợi lock
            # Cập nhật headers/cookies với token MỚI — copy kwargs để
            # không mutate input.
            req_headers = dict(kwargs.get("headers", {}))
            req_headers.update(self.headers)
            req_cookies = dict(kwargs.get("cookies", {}))
            req_cookies.update(self.cookies)
            try:
                return await self.client.request(
                    method, url,
                    headers=req_headers, cookies=req_cookies, **{
                        k: v for k, v in kwargs.items()
                        if k not in ("headers", "cookies")
                    },
                )
            except httpx.RequestError as e:
                raise NetworkError(f"Request failed (after renew): {e}")

    async def _try_renew_token(self) -> bool:
        """Đọc username/password từ login.json và gọi self.login().
        Trả True nếu renew OK (token mới đã lưu vào self.headers).
        Trả False nếu thiếu file, sai format, hoặc login fail.
        """
        if not os.path.exists(Config.LOGIN_FILE):
            if Config.DEBUG:
                print("[DEBUG] Auto-renew: không có login.json → bỏ qua")
            return False
        try:
            with open(Config.LOGIN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            username = data.get("username")
            password = data.get("password")
            if not username or not password:
                if Config.DEBUG:
                    print("[DEBUG] Auto-renew: login.json thiếu user/pass")
                return False
            if Config.DEBUG:
                print(f"[DEBUG] Auto-renew: thử login lại với user={username}")
            await self.login(username, password)
            return True
        except (json.JSONDecodeError, OSError) as e:
            if Config.DEBUG:
                print(f"[DEBUG] Auto-renew: đọc login.json lỗi: {e}")
            return False
        except Exception as e:
            if Config.DEBUG:
                print(f"[DEBUG] Auto-renew: login() fail: {e}")
            return False