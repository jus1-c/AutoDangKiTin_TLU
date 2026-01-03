import json
import os
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
            
        data = [c.data for c in courses]
        
        path = os.path.join(self.custom_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        return filename