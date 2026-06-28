import asyncio
import random
import httpx
from typing import Callable, List, Optional
from src.core.client import TLUClient
from src.models.user import User
from src.models.course import Course
from src.config import Config

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]

class RegisterService:
    def __init__(self, client: TLUClient):
        self.client = client
        self._semaphore_limit = Config.CONCURRENCY_LIMIT
        self.semaphore = asyncio.Semaphore(self._semaphore_limit)

    async def register_subjects(self, user: User, subject_indices: List[int], all_courses: List[List[Course]], is_summer: bool = False, on_progress: Optional[LogFn] = None) -> List[Course]:
        """
        Registers for multiple subjects. Returns list of failed courses.

        `on_progress(idx, success, course)` is called after each subject
        completes (success or failure) so the UI can update per-row status
        in real time. The registration tasks themselves still run
        concurrently — we just await them in submission order to report
        progress sequentially.
        """
        url = user.register_summer_url if is_summer else user.register_url

        tasks = []
        subject_info = []  # (idx, first_course) for progress reporting
        failed_courses_to_sniff = []

        print("Đang chuẩn bị đăng ký...")

        for idx in subject_indices:
            subject_group = all_courses[idx]
            if not subject_group:
                continue
            tasks.append(self.register_single_subject(url, subject_group, failed_courses_to_sniff))
            subject_info.append((idx, subject_group[0] if subject_group else None))

        if not tasks:
            print("Không có môn nào để đăng ký.")
            return []

        print("Đang gửi yêu cầu đăng ký...")
        # Await each in submission order so on_progress fires per-subject.
        # Tasks themselves still run concurrently via asyncio.create_task
        # inside register_single_subject (they share the semaphore).
        for (idx, first_course), task in zip(subject_info, tasks):
            success = await task
            if on_progress is not None:
                try:
                    on_progress(idx, success, first_course)
                except Exception:
                    pass

        return failed_courses_to_sniff

    async def register_custom(self, user: User, courses: List[Course], on_progress: Optional[LogFn] = None) -> List[Course]:
        """Registers a specific list of courses. Returns failed courses.

        `on_progress(course, success)` is called after each course finishes.
        """
        url = user.register_url

        failed_courses = []
        tasks = []
        for course in courses:
             tasks.append(self.register_single_subject(url, [course], failed_courses))

        # Await in submission order for per-course progress reporting.
        for course, task in zip(courses, tasks):
            success = await task
            if on_progress is not None:
                try:
                    on_progress(course, success)
                except Exception:
                    pass
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

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Rebuild semaphore if Config.CONCURRENCY_LIMIT changed at runtime."""
        if self._semaphore_limit != Config.CONCURRENCY_LIMIT:
            self._semaphore_limit = Config.CONCURRENCY_LIMIT
            self.semaphore = asyncio.Semaphore(self._semaphore_limit)
        return self.semaphore

    async def _burst_request(self, url: str, data: dict, count: Optional[int] = None) -> bool:
        n = count if count is not None else Config.BURST_COUNT
        tasks = [self._send_register_request(url, data) for _ in range(n)]
        results = await asyncio.gather(*tasks)
        return any(results)

    async def _send_register_request(self, url: str, data: dict) -> bool:
        async with self._get_semaphore():
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

    async def _find_course_info(self, data: dict, target_code: str) -> Optional[dict]:
        """
        Walks the course-list JSON to find a course by code.

        Mirrors the parse depth of CourseService._parse_courses: visits both
        top-level `courseSubjectDtos` and their nested `subCourseSubjects`
        (the gốc `find_course_info` bỏ sót `subCourseSubjects`).
        """
        if not data or 'courseRegisterViewObject' not in data:
            return None
        try:
            subjects = data['courseRegisterViewObject'].get('listSubjectRegistrationDtos', [])
        except (KeyError, TypeError, AttributeError):
            return None

        for subject in subjects:
            course_dtos = subject.get('courseSubjectDtos', []) or []
            for course in course_dtos:
                if course and course.get('code') == target_code:
                    return course
                sub_courses = course.get('subCourseSubjects') if course else None
                if sub_courses:
                    for sub in sub_courses:
                        if sub and sub.get('code') == target_code:
                            return sub
        return None

    async def sniffing_loop(
        self,
        user: User,
        courses: List[Course],
        is_summer: bool = False,
        interval: float = 2.0,
        jitter: float = 0.5,
        on_log: Optional[LogFn] = None,
        should_stop: Optional[StopFn] = None,
    ) -> List[Course]:
        """
        GET-gated check-then-register sniff loop.

        - Polls the course-list endpoint every `interval` (+/- `jitter`) seconds
          (cheap GET, normal traffic — avoids the ban risk of spamming POST).
        - Only calls the register endpoint (existing burst) when a target course
          is found with `isFullClass == False`.
        - Returns the list of courses that could not be registered before stop.

        `on_log(msg)` is called for user-facing log lines; `should_stop()` is
        polled each iteration and should return True to abort cleanly.
        """
        def log(msg: str) -> None:
            print(msg)
            if on_log:
                try:
                    on_log(msg)
                except Exception:
                    pass

        def stopped() -> bool:
            return bool(should_stop and should_stop())

        if not courses:
            log("Không có môn nào để săn.")
            return []

        list_url = user.course_summer_url if is_summer else user.course_url
        register_url = user.register_summer_url if is_summer else user.register_url

        targets: List[Course] = list(courses)
        log(f"Bắt đầu săn {len(targets)} môn (interval ~{interval}s).")

        while targets and not stopped():
            try:
                response = await self.client.request("GET", list_url)
                data = response.json()
            except Exception as e:
                log(f"[SNIFF] Lỗi tải danh sách: {e}. Thử lại sau interval.")
                await self._sleep_interval(interval, jitter, stopped)
                continue

            still_failed: List[Course] = []
            for course in targets:
                info = await self._find_course_info(data, course.code)
                if info is None:
                    log(f"[SNIFF] {course.code}: không còn trong danh sách môn.")
                    continue

                is_full = bool(info.get('isFullClass'))
                if is_full:
                    still_failed.append(course)
                    continue

                log(f"[SNIFF] {course.code}: phát hiện slot trống! Gửi yêu cầu đăng ký.")
                success = await self.register_single_subject(register_url, [course], [])
                if not success:
                    log(f"[SNIFF] {course.code}: đăng ký vẫn thất bại, tiếp tục săn.")
                    still_failed.append(course)

            if not still_failed:
                log("[SNIFF] Đã săn hết.")
                targets = []
                break
            targets = still_failed

            if stopped():
                break
            await self._sleep_interval(interval, jitter, stopped)

        if targets:
            log(f"[SNIFF] Dừng. Còn {len(targets)} môn chưa săn được.")
        return targets

    async def _sleep_interval(self, base: float, jitter: float, should_stop: Optional[StopFn]) -> None:
        delay = max(0.0, base + random.uniform(-jitter, jitter))
        deadline = asyncio.get_event_loop().time() + delay
        while True:
            if should_stop and should_stop():
                return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.2))