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
        filename = "all_course_summer.json" if is_summer else "all_course.json"
        filepath = os.path.join(Config.RES_DIR, filename)

        try:
            response = await self.client.request("GET", url)
            if response.status_code == 200:
                data = response.json()
                # Save to file
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                raise Exception(f"Failed to fetch courses: {response.status_code}")
        except Exception as e:
            print(f"Warning: Could not fetch new data ({e}). Trying to load from cache.")
            if os.path.exists(filepath):
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                raise Exception("No course data available.")

        return self._parse_courses(data)

    def _parse_courses(self, data: Dict[str, Any]) -> Tuple[List[List[Course]], List[str]]:
        """Parses the complex JSON structure into Course objects."""
        course_groups = []
        subject_names = []
        
        try:
            subjects = data['courseRegisterViewObject']['listSubjectRegistrationDtos']
        except (KeyError, TypeError):
             return [], []

        for subject in subjects:
            courses_in_subject = []
            
            # Helper to add course if valid
            def add_course(c_data):
                if c_data:
                    courses_in_subject.append(Course(data=c_data))

            try:
                # Check for sub-courses (e.g. labs) in the first course subject?
                # Legacy logic: 
                # if courseSubjectDtos[0]['subCourseSubjects'] is not None: ...
                
                course_dtos = subject.get('courseSubjectDtos', [])
                if not course_dtos:
                    continue
                    
                first_dto = course_dtos[0]
                sub_courses = first_dto.get('subCourseSubjects')
                
                if sub_courses:
                    for sub in sub_courses:
                        add_course(sub)
                else:
                    for dto in course_dtos:
                        add_course(dto)
            
                course_groups.append(courses_in_subject)
                subject_names.append(subject.get('subjectName', 'Unknown'))
                
            except (KeyError, IndexError, TypeError):
                continue
                
        return course_groups, subject_names
