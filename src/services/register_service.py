import asyncio
import httpx
from typing import List, Optional
from src.core.client import TLUClient
from src.models.user import User
from src.models.course import Course
from src.config import Config

class RegisterService:
    def __init__(self, client: TLUClient):
        self.client = client
        self.semaphore = asyncio.Semaphore(Config.CONCURRENCY_LIMIT)

    async def register_subjects(self, user: User, subject_indices: List[int], all_courses: List[List[Course]], is_summer: bool = False) -> List[Course]:
        """
        Registers for multiple subjects. Returns list of failed courses.
        """
        url = user.register_summer_url if is_summer else user.register_url
        
        tasks = []
        failed_courses_to_sniff = []

        print("Đang chuẩn bị đăng ký...")

        for idx in subject_indices:
            subject_group = all_courses[idx]
            if not subject_group:
                continue
            tasks.append(self.register_single_subject(url, subject_group, failed_courses_to_sniff))

        if not tasks:
            print("Không có môn nào để đăng ký.")
            return []

        print("Đang gửi yêu cầu đăng ký...")
        await asyncio.gather(*tasks)
        
        # Don't auto-loop here, return to UI controller
        return failed_courses_to_sniff

    async def register_custom(self, user: User, courses: List[Course]) -> List[Course]:
        """Registers a specific list of courses. Returns failed courses."""
        url = user.register_url 
        
        failed_courses = []
        tasks = []
        for course in courses:
             tasks.append(self.register_single_subject(url, [course], failed_courses))
             
        await asyncio.gather(*tasks)
        return failed_courses

    async def register_single_subject(self, url: str, courses: List[Course], failed_list: List[Course]) -> bool:
        """
        Attempts to register. If all fail, adds the first course to failed_list for sniffing.
        """
        for course in courses:
            print(f"Đang thử đăng ký: {course.display_name} ({course.code})")
            success = await self._burst_request(url, course.data)
            if success:
                print(f"THÀNH CÔNG: Đã đăng ký {course.display_name}")
                return True
        
        print(f"THẤT BẠI: Không đăng ký được môn {courses[0].display_name if courses else ''}")
        if courses:
            failed_list.append(courses[0])
        return False

    async def _burst_request(self, url: str, data: dict, count: int = 5) -> bool:
        tasks = [self._send_register_request(url, data) for _ in range(count)]
        results = await asyncio.gather(*tasks)
        return any(results)

    async def _send_register_request(self, url: str, data: dict) -> bool:
        async with self.semaphore:
            while True:
                try:
                    response = await self.client.request("POST", url, json=data)
                    try:
                        res_json = response.json()
                        return res_json.get('status') == 0
                    except:
                        continue
                except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout, httpx.WriteTimeout):
                    continue
                except Exception:
                    continue

    # sniffing_loop logic moved to UI controller or kept as utility if needed, 
    # but for GUI we use the UI loop. Keep it for CLI compatibility if needed.
    async def sniffing_loop(self, user: User, courses: List[Course], is_summer: bool = False):
        # CLI version
        pass