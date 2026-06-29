import json
import os
from typing import List, Tuple, Dict, Any, Optional
from src.core.client import TLUClient
from src.models.user import User
from src.models.course import Course
from src.config import Config

class CourseService:
    def __init__(self, client: TLUClient):
        self.client = client
        self.last_meta = {} # Lưu trữ metadata

    async def _try_fetch_period(
        self, user: User, is_summer: bool, filepath: str
    ) -> tuple:
        """Try to fetch courses for the current user.semester_id / summer_id.

        Returns (data, status_code) on API response, (None, None) on network
        error. Caller decides what to do with the result.
        """
        sem_id = user.semester_summer_id if is_summer else user.semester_id
        url = user.course_url(sem_id)
        print(f"[INFO]   Try semester_id={sem_id} → {url}")
        try:
            response = await self.client.request("GET", url)
        except Exception as e:
            print(f"[WARNING]   Network error: {e}")
            return None, None
        print(f"[INFO]   Response status: {response.status_code}")
        if response.status_code == 200 and response.text.strip():
            data = response.json()
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return data, response.status_code
        # Extract server error message for logging
        server_msg = ""
        try:
            err_body = response.json()
            if isinstance(err_body, dict):
                server_msg = err_body.get("message") or err_body.get("error") or ""
        except Exception:
            server_msg = (response.text or "")[:200]
        print(f"[WARNING]   semester_id={sem_id} failed ({response.status_code}): {server_msg or response.reason_phrase}")
        return None, response.status_code

    async def get_registration_start(
        self, user: User, is_summer: bool = False
    ) -> Optional[int]:
        """Fetch the registration period start (ms epoch) from the API.

        This is the time the registration window OPENS (fetched fresh
        each call, not cached). Returns None on network/parse error.
        """
        sem_id = user.semester_summer_id if is_summer else user.semester_id
        url = user.course_url(sem_id)
        try:
            response = await self.client.request("GET", url)
            if response.status_code != 200 or not response.text.strip():
                return None
            data = response.json()
            view_obj = data.get("courseRegisterViewObject", {}) or {}
            ts = view_obj.get("startDate")
            if isinstance(ts, (int, float)) and ts > 0:
                return int(ts)
        except Exception as e:
            print(f"[WARN] get_registration_start: {e}")
        return None

    async def fetch_courses(self, user: User, is_summer: bool = False) -> Tuple[List[List[Course]], List[str]]:
        """
        Fetches courses from API.
        Returns a tuple: (List of Course Lists (grouped by subject), List of Subject Names)

        If the primary semester_id fails, automatically tries every other
        period from semesterRegisterPeriods until one works (auto-fix).
        """
        filename = "all_course_summer.json" if is_summer else "all_course.json"
        filepath = os.path.join(Config.RES_DIR, filename)

        original_id = (
            user.semester_summer_id if is_summer else user.semester_id
        )
        print(f"[INFO] Fetching courses (summer={is_summer}, original_id={original_id})")

        # Try the primary ID first
        data, _ = await self._try_fetch_period(user, is_summer, filepath)

        # If primary failed, fall back to trying every other period
        if data is None and self.client is not None:
            try:
                semester_info = await self.client.get_semester_info()
                periods = semester_info.get("semesterRegisterPeriods", [])
            except Exception as e:
                print(f"[WARNING] Cannot fetch semester info for fallback: {e}")
                periods = []

            if periods:
                print(f"[INFO] Primary failed — trying {len(periods)} other periods...")
                for p in periods:
                    pid = p.get("id")
                    if not pid or pid == original_id:
                        continue
                    if is_summer:
                        user.semester_summer_id = pid
                    else:
                        user.semester_id = pid
                    data, _ = await self._try_fetch_period(user, is_summer, filepath)
                    if data is not None:
                        print(
                            f"[INFO] Auto-fixed: {('semester_summer_id' if is_summer else 'semester_id')} "
                            f"{original_id} → {pid} (data returned)"
                        )
                        break

        # Last resort: load from cache
        if data is None and os.path.exists(filepath):
            print(f"[WARNING] All API attempts failed; loading from cache {filepath}")
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                print("[ERROR] Cache file is corrupted.")
                data = None

        if not data:
            sem_id = (
                user.semester_summer_id if is_summer else user.semester_id
            )
            raise Exception(
                f"Không thể tải dữ liệu môn học (semester_id={sem_id}, "
                f"is_summer={is_summer}). API lỗi & không có Cache."
            )

        # Lưu metadata
        try:
            view_obj = data.get('courseRegisterViewObject', {})
            self.last_meta = {
                'startDate': view_obj.get('startDate'),
                'endDate': view_obj.get('endDate')
            }
        except:
            self.last_meta = {}

        return self._parse_courses(data)

    def _parse_courses(self, data: Dict[str, Any]) -> Tuple[List[List[Course]], List[str]]:
        """Parses the complex JSON structure into Course objects."""
        course_groups = []
        subject_names = []
        
        try:
            if not data or 'courseRegisterViewObject' not in data:
                return [], []
            subjects = data['courseRegisterViewObject'].get('listSubjectRegistrationDtos')
            if not subjects:
                return [], []
        except (KeyError, TypeError, AttributeError):
             return [], []

        for subject in subjects:
            courses_in_subject = []
            def add_course(c_data):
                if c_data:
                    courses_in_subject.append(Course(data=c_data))

            try:
                course_dtos = subject.get('courseSubjectDtos', [])
                if course_dtos:
                    first_dto = course_dtos[0]
                    sub_courses = first_dto.get('subCourseSubjects')
                    if sub_courses:
                        for sub in sub_courses: add_course(sub)
                    else:
                        for dto in course_dtos: add_course(dto)
                
                if courses_in_subject:
                    course_groups.append(courses_in_subject)
                    subject_names.append(subject.get('subjectName', 'Unknown'))
            except (KeyError, IndexError, TypeError):
                continue
                
        return course_groups, subject_names
