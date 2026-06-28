from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class User:
    username: str
    password: str = field(repr=False) 
    full_name: Optional[str] = None
    student_id: Optional[str] = None
    semester_id: Optional[int] = None
    semester_summer_id: Optional[int] = None
    
    # ID lấy từ root của semester_info API (ví dụ: 14)
    semester_root_id: Optional[int] = None 
    
    def course_url(self, semester_id: Optional[int] = None) -> str:
        """findByPeriod endpoint. semester_id defaults to the main
        semester; pass semester_summer_id to query the summer semester.
        """
        sid = semester_id if semester_id is not None else self.semester_id
        return f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/{self.student_id}/{sid}"

    def register_url(self, semester_id: Optional[int] = None) -> str:
        """add-register endpoint. semester_id defaults to the main
        semester; pass semester_summer_id to register into the summer
        semester.
        """
        sid = semester_id if semester_id is not None else self.semester_id
        return f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/{self.student_id}/{sid}"

    @property
    def schedule_url(self) -> str:
        # Sử dụng semester_root_id theo đúng quan sát mới nhất
        return f"https://sinhvien1.tlu.edu.vn/education/api/StudentCourseSubject/studentLoginUser/{self.semester_root_id}"
