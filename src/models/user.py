from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class User:
    username: str
    password: str = field(repr=False, default="")
    full_name: Optional[str] = None
    student_id: Optional[str] = None
    semester_id: Optional[int] = None
    semester_summer_id: Optional[int] = None
    
    # ID lấy từ root của semester_info API (ví dụ: 14)
    semester_root_id: Optional[int] = None 

    def to_dict(self) -> Dict[str, Any]:
        """Serialize user identity for offline cache.
        Không bao gồm password — chỉ dùng để dựng User từ user_info.json.
        """
        return {
            "username": self.username,
            "full_name": self.full_name,
            "student_id": self.student_id,
            "semester_id": self.semester_id,
            "semester_summer_id": self.semester_summer_id,
            "semester_root_id": self.semester_root_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        """Rebuild User from cached dict. Password = "" vì không
        cần cho các thao tác offline (đăng ký/sniff sẽ tự gọi
        load_session từ token.json).
        """
        return cls(
            username=data.get("username", ""),
            password="",
            full_name=data.get("full_name"),
            student_id=data.get("student_id"),
            semester_id=data.get("semester_id"),
            semester_summer_id=data.get("semester_summer_id"),
            semester_root_id=data.get("semester_root_id"),
        )

    
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
