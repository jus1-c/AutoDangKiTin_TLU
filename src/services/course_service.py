import json
import os
from typing import List, Tuple, Dict, Any
from src.core.client import TLUClient
from src.models.user import User
from src.models.course import Course
from src.config import Config

class CourseService:
    def __init__(self, client: TLUClient):
        self.client = client
        self.last_meta = {} # Lưu trữ metadata

    async def fetch_courses(self, user: User, is_summer: bool = False) -> Tuple[List[List[Course]], List[str]]:
        """
        Fetches courses from API.
        Returns a tuple: (List of Course Lists (grouped by subject), List of Subject Names)
        """
        url = user.course_summer_url if is_summer else user.course_url
        print(f"[INFO] Fetching courses from: {url}")
        
        filename = "all_course_summer.json" if is_summer else "all_course.json"
        filepath = os.path.join(Config.RES_DIR, filename)
        
        data = None

        try:
            response = await self.client.request("GET", url)
            if response.status_code == 200:
                if not response.text.strip():
                    print("[WARNING] API returned empty body.")
                    raise Exception("Empty response from API")
                    
                data = response.json()
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                raise Exception(f"Failed to fetch courses: {response.status_code}")
        except Exception as e:
            print(f"[WARNING] Fetch failed ({e}). Trying to load from cache.")
            if os.path.exists(filepath):
                try:
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                except json.JSONDecodeError:
                    print("[ERROR] Cache file is corrupted.")
                    data = None
            else:
                print("[ERROR] No cache available.")
                data = None

        if not data:
            raise Exception("Không thể tải dữ liệu môn học (API lỗi & không có Cache).")

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
