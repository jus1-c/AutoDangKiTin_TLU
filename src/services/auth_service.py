import json
import os
from typing import Tuple
from src.core.client import TLUClient
from src.models.user import User
from src.config import Config

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
            # Session valid, just fetch data
             return await self.fetch_user_data(username, password)
        else:
            # Session invalid, re-login
            return await self.login(username, password)

    async def fetch_user_data(self, username, password) -> User:
        """Fetches student and semester info to populate User object."""
        student_info = await self.client.get_student_info()
        semester_info = await self.client.get_semester_info()
        
        user = User(username=username, password=password)
        user.full_name = student_info.get('displayName')
        user.student_id = student_info.get('id')
        
        # Legacy logic for semester IDs
        # TODO: Improve this to dynamically find the active semester
        periods = semester_info.get('semesterRegisterPeriods', [])
        if len(periods) > 0:
            user.semester_id = periods[0].get('id')
        if len(periods) > 6:
            user.semester_summer_id = periods[6].get('id')
            
        return user
