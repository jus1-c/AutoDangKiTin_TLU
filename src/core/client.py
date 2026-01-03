import httpx
import json
import urllib.parse
import os
import asyncio
from typing import Optional, Tuple, Dict, Any
from src.config import Config
from src.core.exceptions import LoginError, NetworkError, SessionExpiredError

# Suppress InsecureRequestWarning if using verify=False
import warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class TLUClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            verify=False, # University site often has cert issues
            timeout=Config.REQUEST_TIMEOUT
        )
        self.headers = {}
        self.cookies = {}
        self.is_authenticated = False

    async def close(self):
        await self.client.aclose()

    async def load_session(self) -> bool:
        """Loads session from file. Returns True if successful."""
        if not os.path.exists(Config.TOKEN_FILE):
            return False
        
        try:
            with open(Config.TOKEN_FILE, 'r') as f:
                data = json.load(f)
                self.headers = {"Authorization": data.get("Authorization", "")}
                self.cookies = {"token": data.get("token", "")}
                
                # Verify if token is still valid
                try:
                    await self.get_student_info(check_only=True)
                    self.is_authenticated = True
                    return True
                except (SessionExpiredError, NetworkError):
                    return False
        except Exception:
            return False

    async def login(self, username, password) -> bool:
        """Performs login and saves session."""
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
                raise LoginError(f"Login failed: {response.status_code} - {response.text}")
            
            data = response.json()
            if "error" in data:
                raise LoginError(f"Login error: {data.get('error_description', 'Unknown error')}")

            # Set headers and cookies
            access_token = data.get('access_token')
            self.headers = {"Authorization": f"Bearer {access_token}"}
            # The legacy code sets the cookie as the urlencoded response text.
            self.cookies = {"token": urllib.parse.quote_plus(response.text)}
            
            self.is_authenticated = True
            self._save_session()
            return True
            
        except httpx.RequestError as e:
            raise NetworkError(f"Connection failed: {e}")

    def _save_session(self):
        """Saves session to file."""
        Config.ensure_dirs()
        data = {
            "token": self.cookies.get("token"),
            "Authorization": self.headers.get("Authorization")
        }
        with open(Config.TOKEN_FILE, 'w') as f:
            json.dump(data, f)

    async def get_student_info(self, check_only=False) -> Dict[str, Any]:
        """Fetches student info. Used to verify session or get data."""
        if not self.headers:
            raise SessionExpiredError("No credentials provided.")

        try:
            response = await self.client.get(
                Config.TLU_INFO_URL, 
                headers=self.headers, 
                cookies=self.cookies
            )
            
            if response.status_code > 399:
                 if response.status_code == 401:
                     raise SessionExpiredError("Token expired.")
                 raise NetworkError(f"API Error: {response.status_code}")
            
            return response.json()
            
        except httpx.RequestError as e:
            raise NetworkError(f"Connection failed: {e}")

    async def get_semester_info(self) -> Dict[str, Any]:
        """Fetches semester info."""
        try:
            response = await self.client.get(
                Config.TLU_SEMESTER_URL,
                headers=self.headers,
                cookies=self.cookies
            )
            if response.status_code != 200:
                raise NetworkError(f"Failed to get semester info: {response.status_code}")
            return response.json()
        except httpx.RequestError as e:
            raise NetworkError(f"Connection failed: {e}")
            
    async def request(self, method: str, url: str, **kwargs):
        """Generic authenticated request wrapper with logging."""
        kwargs.setdefault('headers', {}).update(self.headers)
        kwargs.setdefault('cookies', {}).update(self.cookies)
        
        if Config.DEBUG:
            print(f"\n[DEBUG REQUEST] {method} {url}")
            if 'json' in kwargs:
                print(f"[DEBUG PAYLOAD] {json.dumps(kwargs['json'], ensure_ascii=False)[:200]}...")
            elif 'data' in kwargs:
                print(f"[DEBUG DATA] {kwargs['data']}")

        try:
            response = await self.client.request(method, url, **kwargs)
            
            if Config.DEBUG:
                print(f"[DEBUG RESPONSE] {response.status_code}")
                # Try to print some content
                try:
                    print(f"[DEBUG BODY] {response.text[:300]}...") 
                except: pass
                
            return response
        except httpx.RequestError as e:
            if Config.DEBUG:
                print(f"[DEBUG ERROR] {e}")
            raise NetworkError(f"Request failed: {e}")