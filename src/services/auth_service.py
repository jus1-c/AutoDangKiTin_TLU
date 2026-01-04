import json
import os
from typing import Tuple
from src.config import Config
from src.core.client import TLUClient
from src.models.user import User

class AuthService:
    def __init__(self, client: TLUClient):
        self.client = client

    async def login(self, username, password) -> User:
        """Logs in and returns a User object with populated IDs."""
        await self.client.login(username, password)
        
        # Save credentials for auto-login
        with open(Config.LOGIN_FILE, 'w') as f:
            json.dump({"username": username, "password": password}, f)
            
        return await self.fetch_user_data(username, password)

    async def load_saved_user(self) -> User:
        """Attempts to load saved user and session."""
        if not os.path.exists(Config.LOGIN_FILE):
            raise Exception("No saved login found.")
            
        with open(Config.LOGIN_FILE, 'r') as f:
            data = json.load(f)
            username = data["username"]
            password = data["password"]

        if await self.client.load_session():
             return await self.fetch_user_data(username, password)
        else:
            return await self.login(username, password)

    async def fetch_user_data(self, username, password) -> User:
        """Fetches student and semester info to populate User object."""
        student_info = await self.client.get_student_info()
        semester_info = await self.client.get_semester_info()
        
        user = User(username=username, password=password)
        user.full_name = student_info.get('displayName')
        user.student_id = student_info.get('id')
        
        # 1. Lấy ID cho lịch học từ root JSON (ví dụ: 14)
        user.semester_root_id = semester_info.get('id')
        print(f"[INFO] Semester Root ID (for schedule): {user.semester_root_id}")
        
        periods = semester_info.get('semesterRegisterPeriods', [])
        
        # 2. Logic tìm kỳ học dựa trên mẫu JSON cung cấp
        # Kỳ chính thường là 66, Kỳ hè thường là 72
        user.semester_id = 66
        user.semester_summer_id = 72

        exists_66 = any(p.get('id') == 66 for p in periods)
        exists_72 = any(p.get('id') == 72 for p in periods)

        if not exists_66 and periods:
            user.semester_id = periods[0].get('id')
            print(f"[WARNING] Không tìm thấy ID 66, dùng ID tại index 0: {user.semester_id}")
        
        if not exists_72:
            # Tìm kỳ có tên "Học kỳ phụ" hoặc "Hè" như trong mẫu JSON
            for p in periods:
                p_name = p.get('name', '').lower()
                if 'phụ' in p_name or 'hè' in p_name:
                    user.semester_summer_id = p.get('id')
                    print(f"[INFO] Tìm thấy kỳ hè (phụ) theo tên: {user.semester_summer_id}")
                    break
            
            if not user.semester_summer_id and len(periods) > 1:
                user.semester_summer_id = periods[1].get('id')
                print(f"[WARNING] Fallback kỳ hè về index 1: {user.semester_summer_id}")

        return user
