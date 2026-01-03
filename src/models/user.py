from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class User:
    username: str
    password: str = field(repr=False) # Do not print password
    full_name: Optional[str] = None
    student_id: Optional[str] = None
    semester_id: Optional[str] = None
    semester_summer_id: Optional[str] = None
    
    # Dynamic URLs based on student/semester ID
    @property
    def course_url(self) -> str:
        return f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/{self.student_id}/{self.semester_id}"

    @property
    def register_url(self) -> str:
        return f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/{self.student_id}/{self.semester_id}"

    @property
    def course_summer_url(self) -> str:
        return f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/{self.student_id}/{self.semester_summer_id}"

    @property
    def register_summer_url(self) -> str:
        return f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/{self.student_id}/{self.semester_summer_id}"
    
    @property
    def schedule_url(self) -> str:
        return f"https://sinhvien1.tlu.edu.vn/education/api/StudentCourseSubject/studentLoginUser/{self.student_id}"
