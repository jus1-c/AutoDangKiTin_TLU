import json
import os
import re
import time
import datetime
from typing import List, Dict, Tuple
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

    def save_named(self, courses: List[Course], name: str = "") -> str:
        """Saves list of courses to a user-named (or timestamped) JSON file.

        - If `name` is empty/blank, filename is `custom_{epoch}.json`.
        - If `name` has invalid characters, they're stripped to underscores.
        - ".json" suffix is added if missing.
        - If the resolved filename already exists, an incrementing suffix
          (`_1`, `_2`, ...) is appended to avoid overwriting.
        """
        if not name or not name.strip():
            base = f"custom_{int(time.time())}.json"
        else:
            base = re.sub(r"[^\w\-.]+", "_", name.strip()).strip("._")
            if not base:
                base = f"custom_{int(time.time())}"
            if not base.lower().endswith(".json"):
                base = f"{base}.json"
        filename = base
        counter = 1
        while os.path.exists(os.path.join(self.custom_dir, filename)):
            stem, dot, ext = base.partition(".")
            filename = f"{stem}_{counter}.{ext}" if dot else f"{base}_{counter}"
            counter += 1
        return self._write(courses, filename)

    def _write(self, courses: List[Course], filename: str) -> str:
        data = [c.data for c in courses]
        path = os.path.join(self.custom_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return filename