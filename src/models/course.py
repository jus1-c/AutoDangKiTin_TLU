from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


def _fmt_date(ts_ms: int) -> str:
    """Format ms-timestamp as Vietnamese DD/MM/YYYY."""
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%d/%m/%Y")
    except (ValueError, TypeError, OSError):
        return ""


@dataclass
class TimeBlock:
    start_date: int       # Timestamp ms
    end_date: int         # Timestamp ms
    week_index: int       # Day of week (2=Mon, 8=Sun)
    start_period: int     # Start Period Index
    end_period: int       # End Period Index
    teacher_name: str = ""
    room_name: str = ""

    def conflicts_with(self, other: 'TimeBlock') -> bool:
        """Two blocks conflict if they share the same day-of-week AND
        their period ranges overlap AND their date windows overlap.

        Date range check: 2 lớp cùng thứ + trùng tiết nhưng khác nửa
        học kỳ (vd: tuần 1-8 vs tuần 9-16) KHÔNG conflict — có thể
        học cả hai. Nếu thiếu start_date/end_date (None/0) → coi
        như luôn overlap (an toàn: vẫn báo trùng).
        """
        if self.week_index != other.week_index:
            return False
        if not (self.start_period <= other.end_period
                and other.start_period <= self.end_period):
            return False
        # Date overlap check — chỉ áp khi cả 2 đều có date hợp lệ
        if (self.start_date and self.end_date
                and other.start_date and other.end_date):
            if not (self.start_date <= other.end_date
                    and other.start_date <= self.end_date):
                return False
        return True


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
                    end_period=tt['endHour']['indexNumber'],
                    teacher_name=str(tt.get('teacherName') or ""),
                    room_name=str(tt.get('roomName') or ""),
                ))
            except (KeyError, TypeError):
                continue
        return blocks

    @property
    def teacher_name(self) -> str:
        """First non-empty teacher name across timetables, else top-level."""
        for tb in self.time_blocks:
            if tb.teacher_name:
                return tb.teacher_name
        return str(self.data.get("teacherName") or "")

    @property
    def room_name(self) -> str:
        """First non-empty room name across timetables."""
        for tb in self.time_blocks:
            if tb.room_name:
                return tb.room_name
        return ""

    @property
    def period_range(self) -> str:
        """Like '1 -> 3' (one block) or '1 -> 3, 2 -> 4' (multi distinct block).

        Consecutive duplicate period strings are collapsed (TLU API often
        returns the same class with 4 identical timetable entries).
        """
        seen: List[str] = []
        for tb in self.time_blocks:
            seg = (
                str(tb.start_period)
                if tb.start_period == tb.end_period
                else f"{tb.start_period} -> {tb.end_period}"
            )
            if not seen or seen[-1] != seg:
                seen.append(seg)
        return ", ".join(seen)

    @property
    def date_range(self) -> str:
        """Like '2/7/2026' (one date) or '2/7 -> 15/7/2026' (different dates).

        Duplicate dates collapse to a single date, and the displayed range
        is sorted by the underlying timestamp (not by API order).
        """
        blocks = self.time_blocks
        if not blocks:
            return ""
        seen_ts: List[int] = []
        for b in blocks:
            if b.start_date not in seen_ts:
                seen_ts.append(b.start_date)
        seen_ts.sort()
        unique_dates = [_fmt_date(ts) for ts in seen_ts]
        unique_dates = [d for d in unique_dates if d]
        if not unique_dates:
            return ""
        if len(unique_dates) == 1:
            return unique_dates[0]
        return f"{unique_dates[0]} -> {unique_dates[-1]}"

    @property
    def date_range_short(self) -> str:
        """Compact date range: '13/4 → 4/5' (no year). Useful for narrow cells."""
        blocks = self.time_blocks
        if not blocks:
            return ""
        seen_ts: List[int] = []
        for b in blocks:
            if b.start_date not in seen_ts:
                seen_ts.append(b.start_date)
        seen_ts.sort()
        unique = []
        for ts in seen_ts:
            try:
                d = datetime.fromtimestamp(ts / 1000)
                unique.append(f"{d.day}/{d.month}")
            except (ValueError, TypeError, OSError):
                continue
        if not unique:
            return ""
        if len(unique) == 1:
            return unique[0]
        return f"{unique[0]} → {unique[-1]}"

    @property
    def week_day(self) -> str:
        """Day-of-week name for the first timetable block (e.g. 'Thứ 2')."""
        wd = ["", "Chủ nhật", "Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
        blocks = self.time_blocks
        if blocks and 1 <= blocks[0].week_index <= 8:
            return wd[blocks[0].week_index]
        return ""

    @staticmethod
    def _day_short(idx: int) -> str:
        """Short day label: 'T2', 'T3', ..., 'CN' (or '' for unknown)."""
        names = ["", "CN", "T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        if 1 <= idx <= 8:
            return names[idx]
        return ""

    @property
    def day_periods(self) -> List[str]:
        """All distinct (day, period) sessions, deduped + ordered by day/period.

        Each entry is like 'T2: 1-3' or 'T4: 5'. Order: by weekIndex then period.
        """
        seen: set = set()
        out: List[str] = []
        # Sort blocks by (week_index, start_period) for stable display
        sorted_blocks = sorted(self.time_blocks, key=lambda b: (b.week_index, b.start_period))
        for tb in sorted_blocks:
            day = self._day_short(tb.week_index)
            if tb.start_period == tb.end_period:
                p = str(tb.start_period)
            else:
                p = f"{tb.start_period}-{tb.end_period}"
            key = (tb.week_index, tb.start_period, tb.end_period)
            if key in seen:
                continue
            seen.add(key)
            label = f"{day}: {p}" if day else p
            out.append(label)
        return out

    @property
    def sessions_summary(self) -> str:
        """Compact single-line list of all sessions: 'T2: 1-3, T4: 4-6'."""
        return ", ".join(self.day_periods)

    @property
    def sessions_lines(self) -> str:
        """Multi-line list of all sessions: 'T2: 1-3\\nT4: 4-6' (for cell)."""
        return "\n".join(self.day_periods)

    @property
    def picker_detail(self) -> str:
        """Short summary shown in CustomBuilderScreen after a class is picked.

        Format: 'T2: 1-3, T4: 4-6, 13/04/2026 -> 04/05/2026' — all sessions
        plus date range. Falls back to whichever half is available.
        """
        sessions = self.sessions_summary
        d = self.date_range
        if sessions and d:
            return f"{sessions}, {d}"
        return sessions or d

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