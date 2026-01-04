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

    async def close(self):
        await self.client.aclose()

    async def load_session(self) -> bool:
        if not os.path.exists(Config.TOKEN_FILE):
            return False
        
        try:
            with open(Config.TOKEN_FILE, 'r') as f:
                data = json.load(f)
                self.headers = {"Authorization": data.get("Authorization", "")}
                self.cookies = {"token": data.get("token", "")}
                
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
            
            if Config.DEBUG:
                print(f"[DEBUG RESPONSE] {response.status_code}")
                # In ra body nếu lỗi hoặc debug bật
                if response.status_code != 200:
                    print(f"[DEBUG ERROR BODY] {response.text[:1000]}") # Print first 1000 chars
                else:
                    # Print snippet for success too if debug
                    try:
                        print(f"[DEBUG BODY SNIPPET] {response.text[:200]}...")
                    except:
                        pass
                
            return response
        except httpx.RequestError as e:
            if Config.DEBUG:
                print(f"[DEBUG ERROR] {e}")
            raise NetworkError(f"Request failed: {e}")