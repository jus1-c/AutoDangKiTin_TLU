import json
import os
from datetime import datetime
from typing import List, Optional, Tuple
from src.config import Config
from src.core.client import TLUClient
from src.models.user import User


# Known fallback IDs (from old hardcoded logic — used as last resort)
_FALLBACK_MAIN_ID = 66
_FALLBACK_SUMMER_ID = 72

# Keywords that identify a summer semester in period name
_SUMMER_NAME_KEYWORDS = ("hè", "he ", "phụ", "phu", "summer", "he,")

# Months (1-12) that indicate a summer-semester startDate
_SUMMER_MONTHS = (4, 5, 6, 7, 8)  # Apr–Aug


class AuthService:
    def __init__(self, client: TLUClient):
        self.client = client

    async def login(self, username, password, save: bool = True) -> User:
        """Logs in and returns a User object with populated IDs.

        If `save` is True, persists credentials to Config.LOGIN_FILE for
        auto-login. If False, removes any existing saved credentials.
        """
        await self.client.login(username, password)

        if save:
            os.makedirs(os.path.dirname(Config.LOGIN_FILE) or ".", exist_ok=True)
            with open(Config.LOGIN_FILE, 'w', encoding='utf-8') as f:
                json.dump({"username": username, "password": password}, f)
        elif os.path.exists(Config.LOGIN_FILE):
            try:
                os.remove(Config.LOGIN_FILE)
            except OSError:
                pass

        return await self.fetch_user_data(username, password)

    async def load_saved_user(self) -> User:
        """Attempts to load saved user and session."""
        if not os.path.exists(Config.LOGIN_FILE):
            raise Exception("No saved login found.")

        with open(Config.LOGIN_FILE, 'r') as f:
            data = json.load(f)
            username = data["username"]
            password = data["password"]

        if await self.client.load_session():
             return await self.fetch_user_data(username, password)
        else:
            return await self.login(username, password)

    @staticmethod
    def _period_start_month(period: dict) -> Optional[int]:
        """Extract start month (1-12) from a period's startDate (ms timestamp)."""
        ts = period.get("startDate")
        if not isinstance(ts, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(ts / 1000).month
        except (ValueError, TypeError, OSError):
            return None

    @staticmethod
    def _find_semester_id(
        periods: List[dict],
        *,
        name_keywords: tuple = (),
        start_months: tuple = (),
        prefer_after: Optional[dict] = None,
        exclude_ids: tuple = (),
    ) -> Optional[int]:
        """Find a semester period matching the given heuristics.

        Tries strategies in order:
          1. Name contains any of `name_keywords` (case-insensitive)
          2. startDate month in `start_months`
          3. If `prefer_after` given: a period whose startDate is strictly
             after the given period's startDate
        Skips any period whose id is in `exclude_ids`.
        Returns the period's id, or None.
        """
        if not periods:
            return None

        def _eligible(p: dict) -> bool:
            return p.get("id") not in exclude_ids

        # 1. Name match
        for p in periods:
            if not _eligible(p):
                continue
            name = str(p.get("name", "")).lower()
            if any(kw in name for kw in name_keywords):
                return p.get("id")

        # 2. Start-month match
        for p in periods:
            if not _eligible(p):
                continue
            month = AuthService._period_start_month(p)
            if month in start_months:
                return p.get("id")

        # 3. Period that starts strictly after another period
        if prefer_after is not None:
            after_ts = prefer_after.get("startDate")
            if isinstance(after_ts, (int, float)):
                candidates = [
                    p for p in periods
                    if _eligible(p)
                    and isinstance(p.get("startDate"), (int, float))
                    and p.get("startDate") > after_ts
                ]
                if candidates:
                    return min(candidates, key=lambda x: x["startDate"]).get("id")

        return None

    async def fetch_user_data(self, username, password) -> User:
        """Fetches student and semester info to populate User object."""
        student_info = await self.client.get_student_info()
        semester_info = await self.client.get_semester_info()

        user = User(username=username, password=password)
        user.full_name = student_info.get('displayName')
        user.student_id = student_info.get('id')

        user.semester_root_id = semester_info.get('id')
        print(f"[INFO] Semester Root ID (for schedule): {user.semester_root_id}")

        periods = semester_info.get('semesterRegisterPeriods', [])

        # ---- Main semester ----
        # Prefer the exact match for the known fallback ID; otherwise
        # pick the period that started most recently (i.e. the one that
        # is currently or most-nearly active).
        user.semester_id = _FALLBACK_MAIN_ID
        if not any(p.get("id") == _FALLBACK_MAIN_ID for p in periods):
            if periods:
                now_ms = datetime.now().timestamp() * 1000
                with_dates = [
                    p for p in periods
                    if isinstance(p.get("startDate"), (int, float))
                ]
                if with_dates:
                    # Prefer periods that have already started; among those,
                    # pick the one with the latest startDate (= the one that
                    # is currently or most-nearly active).
                    started = [p for p in with_dates if p["startDate"] <= now_ms]
                    pool = started or with_dates
                    user.semester_id = max(pool, key=lambda x: x["startDate"]).get("id")
                    print(f"[INFO] Main semester auto-detected (latest start): {user.semester_id}")
                else:
                    user.semester_id = periods[0].get("id")
                    print(f"[WARNING] No startDate in periods, fallback to index 0: {user.semester_id}")

        # ---- Summer semester ----
        # Strategy (in order):
        #   a. Exact match for known fallback ID (72)
        #   b. Period name contains a summer keyword (hè/phụ/summer/...)
        #   c. Period whose startDate month is in Apr–Aug
        #   d. Period that starts strictly after the chosen main semester
        #   e. Second period in the list
        summer_id: Optional[int] = None
        if any(p.get("id") == _FALLBACK_SUMMER_ID for p in periods):
            summer_id = _FALLBACK_SUMMER_ID
        else:
            summer_id = self._find_semester_id(
                periods,
                name_keywords=_SUMMER_NAME_KEYWORDS,
                start_months=_SUMMER_MONTHS,
                exclude_ids=(user.semester_id,),
            )
            if summer_id is not None:
                print(f"[INFO] Summer semester auto-detected: {summer_id}")

            # If still nothing, pick the period that starts after main
            if summer_id is None and periods:
                main_period = next(
                    (p for p in periods if p.get("id") == user.semester_id),
                    None,
                )
                if main_period is not None:
                    summer_id = self._find_semester_id(
                        periods,
                        prefer_after=main_period,
                        exclude_ids=(user.semester_id,),
                    )
                    if summer_id is not None:
                        print(f"[INFO] Summer semester = period after main: {summer_id}")

            # Last resort: second period
            if summer_id is None and len(periods) >= 2:
                summer_id = periods[1].get("id")
                print(f"[WARNING] Summer semester fallback to index 1: {summer_id}")

        if summer_id is not None:
            user.semester_summer_id = summer_id
        else:
            user.semester_summer_id = _FALLBACK_SUMMER_ID
            print(f"[WARNING] No summer period found, using fallback ID: {_FALLBACK_SUMMER_ID}")

        return user
