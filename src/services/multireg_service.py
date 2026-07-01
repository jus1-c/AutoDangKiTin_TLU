# -*- coding: utf-8 -*-
"""Multi-account register service.

Chạy đăng ký cho N account song song từ 1 file định nghĩa
(res/multireg/*.json). Mỗi account:
- 1 TLUClient riêng (mỗi account 1 session độc lập)
- login qua login_until_success (retry vô hạn khi mạng lỗi)
- load profile (custom hoặc shared) → register_custom_for_semester
- fail + Config.AUTO_SNIFF_FALLBACK bật → sniffing_loop (giống menu
  "1. Đăng ký nhanh": pick + register, sniff fallback là mặc định global)

Log tách theo account vào res/logs/{username}_{ts}.log.

File format (v1):
{
  "version": 1,
  "name": "dot1",
  "shared_profile": "custom_x.json" | null,
  "accounts": [
    {
      "username": "sv001",
      "password": "...",
      "profile": null | "custom_y.json"
    }
  ]
}

profile=null → dùng shared_profile. HK chính/hè lấy từ semester_id đã
lưu trong profile v2 (is_summer chỉ là fallback cho profile v1 legacy).
Sniff fallback khi lớp đầy = hành vi mặc định (Config.AUTO_SNIFF_FALLBACK).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.config import Config
from src.core.client import TLUClient
from src.models.course import Course
from src.services.auth_service import AuthService
from src.services.custom_service import CustomService
from src.services.register_service import RegisterService


# Callback per-account: (username, event_type, message)
# event_type: "start"|"login"|"register"|"sniff"|"done"|"error"
ProgressFn = Callable[[str, str, str], None]


@dataclass
class MultiRegAccount:
    username: str
    password: str
    profile: Optional[str] = None  # None → dùng shared_profile
    is_summer: bool = False  # fallback cho profile v1 (không có sem_id)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MultiRegAccount":
        u = d.get("username")
        p = d.get("password")
        if not u or not p:
            raise ValueError(
                f"Account thiếu username/password: {d}"
            )
        # Bỏ qua key "quick" cũ nếu file legacy có — sniff fallback giờ là
        # global setting (Config.AUTO_SNIFF_FALLBACK), không còn per-account.
        return cls(
            username=str(u).strip(),
            password=str(p),
            profile=d.get("profile") or None,
            is_summer=bool(d.get("is_summer", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "password": self.password,
            "profile": self.profile,
            "is_summer": self.is_summer,
        }


@dataclass
class MultiRegConfig:
    version: int = 1
    name: str = ""
    shared_profile: Optional[str] = None
    accounts: List[MultiRegAccount] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MultiRegConfig":
        ver = int(d.get("version", 1))
        if ver != 1:
            raise ValueError(f"Chỉ hỗ trợ version=1, gặp {ver!r}")
        accs_raw = d.get("accounts", [])
        if not isinstance(accs_raw, list) or not accs_raw:
            raise ValueError("Thiếu field 'accounts' (list, ít nhất 1 account)")
        accs = [MultiRegAccount.from_dict(a) for a in accs_raw]
        # Trùng username → lỗi (không cho phép cùng 1 account 2 lần)
        seen = set()
        for a in accs:
            if a.username in seen:
                raise ValueError(f"username trùng: {a.username}")
            seen.add(a.username)
        return cls(
            version=ver,
            name=str(d.get("name") or ""),
            shared_profile=d.get("shared_profile") or None,
            accounts=accs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "shared_profile": self.shared_profile,
            "accounts": [a.to_dict() for a in self.accounts],
        }

    def resolve_profile(self, account: MultiRegAccount) -> Optional[str]:
        """Trả profile filename dùng cho account (per-account override
        > shared > None). None nghĩa là account này không có profile."""
        return account.profile or self.shared_profile


class MultiRegService:
    """CRUD file multireg + orchestrator chạy N account song song."""

    def __init__(self):
        Config.ensure_dirs()
        self.dir = Config.MULTIREG_DIR

    # ---------- File CRUD ----------

    def list_files(self) -> List[str]:
        if not os.path.isdir(self.dir):
            return []
        files = [f for f in os.listdir(self.dir) if f.endswith(".json")]
        files.sort()
        return files

    def load(self, filename: str) -> MultiRegConfig:
        path = os.path.join(self.dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MultiRegConfig.from_dict(data)

    def save(self, cfg: MultiRegConfig, name: str = "") -> str:
        """Ghi file. Tên file suy từ cfg.name (safe-slug) hoặc timestamp.
        Nếu name đã tồn tại → thêm _1, _2, ...
        """
        if name and name.strip():
            base = re.sub(r"[^\w\-.]+", "_", name.strip()).strip("._")
        elif cfg.name.strip():
            base = re.sub(r"[^\w\-.]+", "_", cfg.name.strip()).strip("._")
        else:
            base = f"multireg_{int(time.time())}"
        if not base:
            base = f"multireg_{int(time.time())}"
        if not base.lower().endswith(".json"):
            base = f"{base}.json"
        filename = base
        counter = 1
        while os.path.exists(os.path.join(self.dir, filename)):
            stem, dot, ext = base.partition(".")
            filename = f"{stem}_{counter}.{ext}" if dot else f"{base}_{counter}"
            counter += 1
        path = os.path.join(self.dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        return filename

    def delete(self, filename: str) -> None:
        path = os.path.join(self.dir, filename)
        if os.path.exists(path):
            os.remove(path)

    # ---------- Runner ----------

    async def run(
        self,
        cfg: MultiRegConfig,
        on_progress: Optional[ProgressFn] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Chạy toàn bộ account song song. Trả dict {username: result}.

        result = {
          "success": bool,
          "registered": int,   # số lớp đăng ký được
          "failed": [str],      # code lớp fail cuối cùng
          "sniffed": [str],     # code lớp sniff được
          "log_file": str,      # đường dẫn file log
          "error": str | None,  # exception message nếu có
        }
        """
        Config.ensure_dirs()
        # Load settings (SNIFF_INTERVAL, CONCURRENCY_LIMIT, ...) — có sẵn
        # trong Config, đã được load ở entry point (main.py). Không cần
        # gọi lại ở đây; nếu caller quên gọi thì .env defaults vẫn OK.

        tasks = [
            self._run_one(cfg, acc, on_progress) for acc in cfg.accounts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: Dict[str, Dict[str, Any]] = {}
        for acc, res in zip(cfg.accounts, results):
            if isinstance(res, Exception):
                out[acc.username] = {
                    "success": False,
                    "registered": 0,
                    "failed": [],
                    "sniffed": [],
                    "log_file": "",
                    "error": f"{type(res).__name__}: {res}",
                }
            else:
                out[acc.username] = res
        return out

    async def _run_one(
        self,
        cfg: MultiRegConfig,
        acc: MultiRegAccount,
        on_progress: Optional[ProgressFn],
    ) -> Dict[str, Any]:
        """Run 1 account. Return result dict (không raise — mọi lỗi
        được catch + gói vào result['error'])."""
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_user = re.sub(r"[^\w\-.]+", "_", acc.username)
        log_path = os.path.join(Config.LOGS_DIR, f"{safe_user}_{ts}.log")

        def _log(msg: str) -> None:
            """Ghi 1 dòng vào file log của account này (append) + stdout."""
            line = f"[{_dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
            print(f"[{acc.username}] {msg}")

        def _emit(ev: str, msg: str) -> None:
            _log(msg)
            if on_progress:
                try:
                    on_progress(acc.username, ev, msg)
                except Exception:  # noqa: BLE001
                    pass

        result: Dict[str, Any] = {
            "success": False,
            "registered": 0,
            "failed": [],
            "sniffed": [],
            "log_file": log_path,
            "error": None,
        }

        _emit("start", f"Bắt đầu (profile={cfg.resolve_profile(acc)}, "
                       f"is_summer={acc.is_summer})")

        # Load profile trước khi login (fail sớm nếu profile thiếu)
        profile_name = cfg.resolve_profile(acc)
        if not profile_name:
            result["error"] = "Không có profile (per-account và shared đều rỗng)"
            _emit("error", result["error"])
            return result

        profile_path = os.path.join(Config.RES_DIR, "custom", profile_name)
        if not os.path.exists(profile_path):
            result["error"] = f"Profile không tồn tại: {profile_name}"
            _emit("error", result["error"])
            return result

        try:
            sem_id_from_profile, courses = CustomService.load_profile(profile_path)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            result["error"] = f"Profile lỗi: {e}"
            _emit("error", result["error"])
            return result

        if not courses:
            result["error"] = f"Profile rỗng: {profile_name}"
            _emit("error", result["error"])
            return result

        _emit("start", f"Profile {profile_name}: {len(courses)} lớp")

        # Login + register + (optional) sniff — mỗi account 1 client
        client = TLUClient()
        try:
            auth = AuthService(client)
            _emit("login", "Đang login...")
            # Không lưu login.json (đã có creds trong multireg file) —
            # tránh ghi đè login.json của user chính trên máy.
            user = await auth.login(acc.username, acc.password, save=False)
            _emit("login", f"Login OK: {user.full_name} (student_id={user.student_id})")

            # Pick semester
            active_sem_id: int
            if sem_id_from_profile is not None:
                active_sem_id = sem_id_from_profile
                _emit("register", f"Semester từ profile: {active_sem_id}")
            elif acc.is_summer:
                active_sem_id = user.semester_summer_id
                _emit("register", f"Semester = HK hè: {active_sem_id}")
            else:
                active_sem_id = user.semester_id
                _emit("register", f"Semester = HK chính: {active_sem_id}")

            register = RegisterService(client)

            # register_custom_for_semester nhận (user, courses, semester_id)
            def _on_start(course: Course) -> None:
                _log(f"→ gửi {course.code} ({course.display_name})")

            def _on_progress(course: Course, success: bool) -> None:
                if success:
                    _log(f"✓ OK {course.code}")
                else:
                    _log(f"✗ FAIL {course.code}")

            failed = await register.register_custom_for_semester(
                user, courses, semester_id=active_sem_id,
                on_start=_on_start, on_progress=_on_progress,
            )

            result["registered"] = len(courses) - len(failed)
            result["failed"] = [c.code for c in failed]
            _emit("register", f"Register xong: OK {result['registered']}/{len(courses)}, "
                              f"fail {len(failed)}")

            # Sniff fallback khi lớp đầy — hành vi MẶC ĐỊNH global giống
            # menu "1. Đăng ký nhanh" (Config.AUTO_SNIFF_FALLBACK). Chỉ
            # status=-6 (lớp đầy) mới vào failed_list để sniff.
            if failed and Config.AUTO_SNIFF_FALLBACK:
                _emit("sniff", f"Sniff {len(failed)} lớp fail (AUTO_SNIFF_FALLBACK)")
                is_summer_for_sniff = (
                    active_sem_id == user.semester_summer_id
                )
                sniff_failed = await register.sniffing_loop(
                    user, failed,
                    is_summer=is_summer_for_sniff,
                    interval=Config.SNIFF_INTERVAL,
                    jitter=Config.SNIFF_JITTER,
                    max_duration_min=Config.SNIFF_MAX_DURATION_MIN,
                    on_log=lambda m: _log(f"[SNIFF] {m}"),
                )
                still_failed_codes = {c.code for c in sniff_failed}
                sniffed = [c for c in failed if c.code not in still_failed_codes]
                result["sniffed"] = [c.code for c in sniffed]
                result["registered"] += len(sniffed)
                result["failed"] = [c.code for c in sniff_failed]
                _emit("sniff", f"Sniff xong: săn được {len(sniffed)}, "
                               f"còn fail {len(sniff_failed)}")

            result["success"] = not result["failed"]
            _emit("done",
                  f"Hoàn tất: OK {result['registered']}/{len(courses)}, "
                  f"còn fail {len(result['failed'])}")
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
            _emit("error", result["error"])
        finally:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
        return result
