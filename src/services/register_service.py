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

        Verbose logging tracks:
          - iteration count + elapsed time per poll
          - per-course status (slot trống / sĩ số full / không còn trong DS)
          - HTTP errors with status code + body excerpt
          - per-poll "all full" heartbeat so the user knows the loop is alive
          - final summary: tổng vòng, tổng TG, số lần phát hiện slot, số lần
            đăng ký OK, số lần fail
        """
        import time as _time

        def log(msg: str) -> None:
            # Use on_log if provided (TUI: writes to log_widget; CLI: pass
            # `print` as on_log). Fallback to print if no on_log at all.
            if on_log:
                try:
                    on_log(msg)
                except Exception:
                    pass
            else:
                print(msg)

        def stopped() -> bool:
            return bool(should_stop and should_stop())

        if not courses:
            log("Không có môn nào để săn.")
            return []

        list_url = user.course_summer_url if is_summer else user.course_url
        register_url = user.register_summer_url if is_summer else user.register_url

        targets: List[Course] = list(courses)
        start_ts = _time.monotonic()
        stats = {
            "polls": 0,
            "slots_detected": 0,
            "register_attempts": 0,
            "register_successes": 0,
            "register_failures": 0,
            "network_errors": 0,
            "missing_courses": set(),  # codes that disappeared
        }

        log(f"[SNIFF] === BẮT ĐẦU SĂN {len(targets)} MÔN ===")
        for c in targets:
            log(f"  • {c.code} | GV: {c.teacher_name or '?'} | Lịch: {c.sessions_summary or '?'}")
        log(f"[SNIFF] interval ~{interval}s (±{jitter}s jitter) | "
            f"endpoint: {list_url}")

        while targets and not stopped():
            iter_start = _time.monotonic()
            stats["polls"] += 1
            poll_no = stats["polls"]
            elapsed = iter_start - start_ts
            log(f"[SNIFF] ── Vòng #{poll_no} (đã chạy {elapsed:.1f}s) ── "
                f"check {len(targets)} môn...")

            # --- 1) GET the course list ---
            try:
                response = await self.client.request("GET", list_url)
                status = getattr(response, "status_code", "?")
                if status != 200:
                    stats["network_errors"] += 1
                    body_excerpt = (getattr(response, "text", "") or "")[:200]
                    log(f"[SNIFF] ⚠ HTTP {status} từ list endpoint. Body: {body_excerpt!r}")
                    if 400 <= status < 500:
                        log("[SNIFF] ⚠ Client error (4xx) — kiểm tra token/session. "
                            "Có thể phiên đăng nhập đã hết hạn.")
                    await self._sleep_interval(interval, jitter, stopped)
                    continue
                data = response.json()
            except Exception as e:
                stats["network_errors"] += 1
                log(f"[SNIFF] ⚠ Lỗi mạng khi tải DS: {type(e).__name__}: {e}. "
                    f"Thử lại sau interval.")
                await self._sleep_interval(interval, jitter, stopped)
                continue

            # --- 2) Validate response structure ---
            if not isinstance(data, dict) or 'courseRegisterViewObject' not in data:
                stats["network_errors"] += 1
                log(f"[SNIFF] ⚠ Response không có 'courseRegisterViewObject'. "
                    f"Type={type(data).__name__}, keys={list(data.keys())[:5] if isinstance(data, dict) else 'n/a'}")
                await self._sleep_interval(interval, jitter, stopped)
                continue

            # --- 3) Per-course check ---
            still_failed: List[Course] = []
            state_counts = {"empty": 0, "full": 0, "available": 0, "no_field": 0}

            for course in targets:
                if stopped():
                    log(f"[SNIFF] ⏹ Dừng theo yêu cầu trong vòng #{poll_no}.")
                    break

                info = await self._find_course_info(data, course.code)
                if info is None:
                    state_counts["empty"] += 1
                    stats["missing_courses"].add(course.code)
                    log(f"[SNIFF]   ⚠ {course.code}: KHÔNG còn trong DS môn học. "
                        f"Có thể lớp đã bị xoá hoặc chưa mở đăng ký.")
                    # Don't put back into still_failed — this course is GONE.
                    continue

                if 'isFullClass' not in info:
                    state_counts["no_field"] += 1
                    log(f"[SNIFF]   ? {course.code}: response thiếu 'isFullClass'. "
                        f"Fields: {list(info.keys())[:8]}")
                    still_failed.append(course)
                    continue

                is_full = bool(info.get('isFullClass'))
                if is_full:
                    state_counts["full"] += 1
                    still_failed.append(course)
                    continue

                # === SLOT TRỐNG — fire register ===
                state_counts["available"] += 1
                stats["slots_detected"] += 1
                cur = info.get('numberStudent', 0)
                mx = info.get('maxStudent', 0)
                log(f"[SNIFF]   ★ {course.code}: SLOT TRỐNG ({cur}/{mx})! "
                    f"Gửi burst đăng ký...")
                stats["register_attempts"] += 1
                success = await self.register_single_subject(register_url, [course], [])
                if success:
                    stats["register_successes"] += 1
                    log(f"[SNIFF]   ✓ {course.code}: ĐĂNG KÝ THÀNH CÔNG!")
                else:
                    stats["register_failures"] += 1
                    log(f"[SNIFF]   ✗ {course.code}: register fail (slot bị chiếm "
                        f"ngay khi gửi). Tiếp tục săn.")
                    still_failed.append(course)

            # --- 4) Per-iteration summary ---
            n_empty = state_counts["empty"]
            n_full = state_counts["full"]
            n_avail = state_counts["available"]
            n_nofield = state_counts["no_field"]
            n_remaining = len(still_failed)
            log(
                f"[SNIFF]   Kết quả vòng #{poll_no}: "
                f"trống={n_avail} (đã thử {n_avail}/{n_avail} register) | "
                f"full={n_full} | mất tích={n_empty} | lỗi_field={n_nofield} | "
                f"còn lại {n_remaining}/{len(targets)}"
            )

            # If all targets disappeared from the list, stop with a
            # clear explanation instead of looping forever.
            if n_empty == len(targets):
                log(
                    f"[SNIFF] ⏹ Tất cả {len(targets)} môn đều không còn trong DS. "
                    f"Có thể server đã đóng đăng ký hoặc lớp bị xoá. DỪNG săn."
                )
                targets = []
                break

            if not still_failed:
                log("[SNIFF] === ĐÃ SĂN HẾT ===")
                targets = []
                break

            if stopped():
                log(f"[SNIFF] ⏹ Dừng theo yêu cầu sau vòng #{poll_no}.")
                break

            targets = still_failed

            # Heartbeat: how long until next poll
            this_iter_dt = _time.monotonic() - iter_start
            log(f"[SNIFF]   ⏱ Vòng #{poll_no} mất {this_iter_dt:.2f}s. "
                f"Đợi ~{interval}s trước vòng #{poll_no + 1}...")
            await self._sleep_interval(interval, jitter, stopped)

        # --- 5) Final summary ---
        elapsed_total = _time.monotonic() - start_ts
        summary_lines = [
            "[SNIFF] === TÓM TẮT ===",
            f"  Tổng thời gian:    {elapsed_total:.1f}s",
            f"  Tổng số vòng poll: {stats['polls']}",
            f"  Lỗi mạng:          {stats['network_errors']}",
            f"  Slot trống phát hiện:  {stats['slots_detected']}",
            f"  Register thử:      {stats['register_attempts']}",
            f"  Register thành công: {stats['register_successes']}",
            f"  Register thất bại: {stats['register_failures']}",
        ]
        if stats["missing_courses"]:
            summary_lines.append(
                f"  Môn bị mất tích:   {', '.join(sorted(stats['missing_courses']))}"
            )
        if targets:
            summary_lines.append(
                f"  CÒN {len(targets)} môn chưa săn được: "
                f"{', '.join(c.code for c in targets)}"
            )
            summary_lines.append(
                f"  ⚠ Lý do có thể: server từ chối (403/401), sĩ số full mãi, "
                f"hoặc endpoint /add-register bị lỗi."
            )
        for line in summary_lines:
            log(line)

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