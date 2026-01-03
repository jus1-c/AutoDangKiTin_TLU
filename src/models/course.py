from dataclasses import dataclass
from typing import Any, Dict, List, Optional

@dataclass
class TimeBlock:
    start_date: int       # Timestamp ms
    end_date: int         # Timestamp ms
    week_index: int       # Day of week (2=Mon, 8=Sun)
    start_period: int     # Start Period Index
    end_period: int       # End Period Index
    
    def conflicts_with(self, other: 'TimeBlock') -> bool:
        # 1. Check Date Range Intersection (Phase check)
        # Conflict if intervals overlap: StartA <= EndB AND StartB <= EndA
        if not (self.start_date <= other.end_date and other.start_date <= self.end_date):
            return False

        # 2. Check Day of Week
        if self.week_index != other.week_index:
            return False

        # 3. Check Period Intersection
        if (self.start_period <= other.end_period and other.start_period <= self.end_period):
            return True
            
        return False

@dataclass
class Course:
    data: Dict[str, Any]

    @property
    def code(self) -> str:
        return self.data.get("code", "")

    @property
    def display_name(self) -> str:
        return self.data.get("displayName", "")

    @property
    def is_full(self) -> bool:
        return self.data.get("isFullClass", False)

    @property
    def current_students(self) -> int:
        return self.data.get("numberStudent", 0)

    @property
    def max_students(self) -> int:
        return self.data.get("maxStudent", 0)
    
    @property
    def time_blocks(self) -> List[TimeBlock]:
        """Parses timetables into comparable TimeBlocks."""
        blocks = []
        timetables = self.data.get("timetables", [])
        if not timetables:
            return []

        for tt in timetables:
            try:
                blocks.append(TimeBlock(
                    start_date=tt['startDate'],
                    end_date=tt['endDate'],
                    week_index=tt['weekIndex'],
                    start_period=tt['startHour']['indexNumber'],
                    end_period=tt['endHour']['indexNumber']
                ))
            except (KeyError, TypeError):
                continue
        return blocks
    
    def conflicts_with(self, other: 'Course') -> bool:
        """Checks if this course conflicts with another course."""
        my_blocks = self.time_blocks
        other_blocks = other.time_blocks
        
        for mb in my_blocks:
            for ob in other_blocks:
                if mb.conflicts_with(ob):
                    return True
        return False

    def __str__(self):
        return f"[{self.code}] {self.display_name} ({self.current_students}/{self.max_students})"