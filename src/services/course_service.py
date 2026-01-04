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
                # Check if empty response
                if not response.text.strip():
                    print("[WARNING] API returned empty body.")
                    raise Exception("Empty response from API")
                    
                data = response.json()
                # Save to file
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
            # If we still have no data, raise exception so UI can show it
            raise Exception("Không thể tải dữ liệu môn học (API lỗi & không có Cache).")

        return self._parse_courses(data)

    def _parse_courses(self, data: Dict[str, Any]) -> Tuple[List[List[Course]], List[str]]:
        """Parses the complex JSON structure into Course objects."""
        course_groups = []
        subject_names = []
        
        try:
            # Validate structure
            if not data or 'courseRegisterViewObject' not in data:
                print("[ERROR] Invalid JSON structure: Missing 'courseRegisterViewObject'")
                return [], []
                
            subjects = data['courseRegisterViewObject'].get('listSubjectRegistrationDtos')
            if not subjects:
                print("[INFO] No subjects found in response.")
                return [], []
        except (KeyError, TypeError, AttributeError) as e:
             print(f"[ERROR] Error parsing course root: {e}")
             return [], []

        for subject in subjects:
            courses_in_subject = []
            
            def add_course(c_data):
                if c_data:
                    courses_in_subject.append(Course(data=c_data))

            try:
                course_dtos = subject.get('courseSubjectDtos', [])
                # Some subjects might rely solely on subCourseSubjects?
                # Logic: Check top level first
                
                # If top level has items
                if course_dtos:
                    # Check if first item has sub-courses (Labs)
                    first_dto = course_dtos[0]
                    sub_courses = first_dto.get('subCourseSubjects')
                    
                    if sub_courses:
                        for sub in sub_courses:
                            add_course(sub)
                    else:
                        for dto in course_dtos:
                            add_course(dto)
                
                # Only add if we found courses
                if courses_in_subject:
                    course_groups.append(courses_in_subject)
                    subject_names.append(subject.get('subjectName', 'Unknown'))
                
            except (KeyError, IndexError, TypeError) as e:
                print(f"[DEBUG] Error parsing subject: {e}")
                continue
                
        return course_groups, subject_names