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

    async def register_subjects(self, user: User, subject_indices: List[int], all_courses: List[List[Course]], is_summer: bool = False):
        """
        Registers for multiple subjects.
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
            return

        print("Đang gửi yêu cầu đăng ký...")
        await asyncio.gather(*tasks)
        
        # Auto Sniffing if failed
        if failed_courses_to_sniff:
            print(f"\nCó {len(failed_courses_to_sniff)} môn đăng ký thất bại. Tự động chuyển sang chế độ 'săn' (Sniffing)...")
            await self.sniffing_loop(user, failed_courses_to_sniff, is_summer)

    async def register_custom(self, user: User, courses: List[Course]):
        """Registers a specific list of courses (from Custom File)."""
        url = user.register_url # Defaulting to main semester
        
        failed_courses = []
        tasks = []
        for course in courses:
             tasks.append(self.register_single_subject(url, [course], failed_courses))
             
        await asyncio.gather(*tasks)
        
        if failed_courses:
             print(f"\nCó {len(failed_courses)} môn thất bại. Sniffing...")
             await self.sniffing_loop(user, failed_courses)

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
        """Sends a single registration request with infinite retry on network failure."""
        async with self.semaphore:
            while True:
                try:
                    response = await self.client.request("POST", url, json=data)
                    
                    try:
                        res_json = response.json()
                        # Return True if Success (0), False if Logic Fail (-x)
                        return res_json.get('status') == 0
                    except:
                        # Json parse error (Server overloaded returning HTML?) -> Retry
                        continue
                        
                except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout, httpx.WriteTimeout):
                    # Network error -> Retry immediately
                    continue
                except Exception:
                    # Unknown error -> Retry
                    continue

    async def sniffing_loop(self, user: User, courses: List[Course], is_summer: bool = False):
        print("Bắt đầu chế độ 'săn' (Sniffing mode)... Nhấn Ctrl+C để dừng.")
        url = user.register_summer_url if is_summer else user.register_url
        
        while True:
            if not courses:
                print("Đã săn hết các môn!")
                break

            try:
                for course in courses[:]: # Copy list to iterate safely
                    success = await self._burst_request(url, course.data, count=1)
                    if success:
                        print(f"SNIFF THÀNH CÔNG: Đã đăng ký {course.display_name}")
                        courses.remove(course) 
            except Exception as e:
                print(f"Sniff error: {e}")
            
            await asyncio.sleep(2) 
