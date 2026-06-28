import json
import os
import re
import time
import datetime
from typing import Any, Dict, List, Optional, Tuple
from src.config import Config
from src.models.course import Course

class CustomService:
    def __init__(self):
        Config.ensure_dirs()
        self.custom_dir = os.path.join(Config.RES_DIR, "custom")

    def list_files(self) -> List[str]:
        """Returns list of json files in res/custom."""
        files = [f for f in os.listdir(self.custom_dir) if f.endswith('.json')]
        files.sort()
        return files

    def delete_files(self, filenames: List[str]):
        for f in filenames:
            path = os.path.join(self.custom_dir, f)
            if os.path.exists(path):
                os.remove(path)

    def save_request(self, courses: List[Course]) -> str:
        """Saves list of courses to a formatted JSON file."""
        date_str = datetime.datetime.now().strftime("%d%m%y")
        base_name = f"auto_request_{date_str}"
        filename = f"{base_name}.json"
        counter = 1

        while os.path.exists(os.path.join(self.custom_dir, filename)):
            filename = f"{base_name}_{counter}.json"
            counter += 1

        return self._write(courses, filename)

    def save_named(self, courses: List[Course], name: str = "", semester_id: Optional[int] = None) -> str:
        """Saves list of courses to a user-named (or timestamped) JSON file.

        - If `name` is empty/blank, filename is `custom_{epoch}.json`.
        - If `name` has invalid characters, they're stripped to underscores.
        - ".json" suffix is added if missing.
        - If the resolved filename already exists, an incrementing suffix
          (`_1`, `_2`, ...) is appended to avoid overwriting.

        The file uses a versioned envelope so we can remember which
        semester (HK chính / HK hè) the profile was created for:
            {
              "version": 2,
              "semester_id": 66,           # or 78 for summer
              "saved_at": "2024-01-01T12:00:00",
              "courses": [ <course_data>, ... ]
            }
        `semester_id` is optional for backward compat with v1 files.
        """
        if not name or not name.strip():
            base = f"custom_{int(time.time())}.json"
        else:
            base = re.sub(r"[^\w\-.]+", "_", name.strip()).strip("._")
            if not base:
                base = f"custom_{int(time.time())}.json"
            if not base.lower().endswith(".json"):
                base = f"{base}.json"
        filename = base
        counter = 1
        while os.path.exists(os.path.join(self.custom_dir, filename)):
            stem, dot, ext = base.partition(".")
            filename = f"{stem}_{counter}.{ext}" if dot else f"{base}_{counter}"
            counter += 1
        return self._write(courses, filename, semester_id=semester_id)

    def _write(self, courses: List[Course], filename: str,
               semester_id: Optional[int] = None) -> str:
        envelope: Dict[str, Any] = {
            "version": 2,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "courses": [c.data for c in courses],
        }
        if semester_id is not None:
            envelope["semester_id"] = semester_id
        path = os.path.join(self.custom_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2)
        return filename

    @staticmethod
    def load_profile(path: str) -> tuple:
        """Load a profile JSON. Returns (semester_id, [Course]).

        Backward compatible:
          - v2 envelope with `semester_id` key → return that.
          - v2 envelope without `semester_id` → returns None.
          - v1 list (legacy) → returns None (use current semester).
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # v1: list of course data dicts
        if isinstance(data, list):
            return None, [Course(d) for d in data]
        # v2: envelope
        if isinstance(data, dict):
            sem_id = data.get("semester_id")
            courses_data = data.get("courses", [])
            return sem_id, [Course(d) for d in courses_data]
        # Unknown shape
        return None, []