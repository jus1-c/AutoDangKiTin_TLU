# -*- coding: utf-8 -*-
"""Class transfer engine giữa 2 account (Feature 2).

Mô hình 2 luồng độc lập:
- Lớp tick bên A → A nhả, B chụp (A cho B). "simple give".
- Lớp tick bên B → B nhả, A chụp (B cho A). "simple give" chiều ngược.
- Nếu 1 lớp A cho (X) và 1 lớp B cho (Y) TRÙNG SLOT → β swap: phải
  drop cả 2 rồi mới chụp chéo được (không thể pre-burst give thường vì
  own-conflict -2). Xử lý bằng drop song song + grab song song.

Pre-burst: receiver bắt đầu bắn register TRƯỚC khi giver drop
`TRANSFER_PRE_BURST_LEAD` giây → request đập vào ngay khoảnh khắc slot
mở, giành trước người ngoài. Grab timeout `TRANSFER_GRAB_TIMEOUT` giây
rồi bỏ cuộc + báo trạng thái cuối.

RỦI RO β: sau khi drop cả 2 mà grab hụt (người ngoài nhanh hơn) →
mất lớp không rollback. UI phải cảnh báo trước.
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import Config
from src.models.course import Course
from src.models.user import User
from src.services.register_service import (
    RegisterService,
    SUBJECT_LEVEL_ERRORS,
    status_label,
)

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]

# Status đáng SPIN tiếp trong _grab_loop (ngữ cảnh transfer):
# - -6 (lớp đầy): chờ slot mở khi giver drop.
# - -2 (own-conflict TẠM THỜI trong swap β): còn giữ lớp cũ, sẽ hết sau drop.
# Khác với ĐĂNG KÝ thường (ở đó -2 là trùng lịch cố định → bỏ lớp). Vì vậy
# tập này ĐỊNH NGHĨA RIÊNG cho transfer, không tái dùng CLASS_LEVEL_ERRORS.
# Gặp status ngoài tập này (vd -4 đã ĐK môn, hoặc lỗi lạ) → dừng grab sớm.
GRAB_SPIN_STATUSES = {-2, -6}

# Status mà _grab_loop VẪN spin tiếp (không dừng sớm):
# - -6 (lớp đầy): chờ slot mở → đúng mục đích grab.
# - -2 (own-conflict TẠM THỜI trong swap β): còn giữ lớp cũ, sẽ hết sau khi
#   drop → phải spin. KHÁC ngữ nghĩa -2 ở register (trùng lịch cố định).
# Mọi status khác (đặc biệt lỗi CẤP MÔN như -4 đã ĐK, hoặc lỗi lạ) → grab
# vô nghĩa (spin tới hết timeout không đổi kết quả) → DỪNG SỚM.
GRAB_SPIN_STATUSES = {-6, -2}


class TransferService:
    """Stateless engine — nhận register/user/period của 2 bên qua tham số."""

    # ---------- Planning (phân loại lớp tick) ----------

    @staticmethod
    def plan(
        a_gives: List[Course],
        a_enrolled: List[Course],
        b_gives: List[Course],
        b_enrolled: List[Course],
    ) -> Dict[str, Any]:
        """Phân loại các lớp tick thành β pairs / simple gives / α-γ errors.

        - β pair (X, Y): X ∈ a_gives trùng slot Y ∈ b_gives → swap cùng slot.
        - α/γ error: X (A→B) trùng lịch lớp B GIỮ (không cho); hoặc Y (B→A)
          trùng lịch lớp A GIỮ. Nếu có error → CALLER không được thực thi.
        - simple give: còn lại.

        "Lớp GIỮ" = enrolled trừ gives (lớp không nằm trong kế hoạch nhả).

        Trả dict {errors, beta_pairs, simple_a_to_b, simple_b_to_a}.
        """
        a_give_ids = {id(c) for c in a_gives}
        b_give_ids = {id(c) for c in b_gives}
        a_kept = [c for c in a_enrolled if id(c) not in a_give_ids]
        b_kept = [c for c in b_enrolled if id(c) not in b_give_ids]

        errors: List[str] = []
        beta: List[Tuple[Course, Course]] = []
        matched_a: set = set()
        matched_b: set = set()

        # β pairs: X (a_gives) trùng slot Y (b_gives).
        for x in a_gives:
            for y in b_gives:
                if id(y) in matched_b:
                    continue
                if x.conflicts_with(y):
                    beta.append((x, y))
                    matched_a.add(id(x))
                    matched_b.add(id(y))
                    break

        # α/γ error: lớp cho đi trùng lịch lớp bên nhận GIỮ lại.
        for x in a_gives:
            if id(x) in matched_a:
                continue
            for k in b_kept:
                if x.conflicts_with(k):
                    errors.append(
                        f"Lớp {x.code} (A→B) trùng lịch lớp {k.code} mà B đang giữ."
                    )
                    break
        for y in b_gives:
            if id(y) in matched_b:
                continue
            for k in a_kept:
                if y.conflicts_with(k):
                    errors.append(
                        f"Lớp {y.code} (B→A) trùng lịch lớp {k.code} mà A đang giữ."
                    )
                    break

        simple_a_to_b = [x for x in a_gives if id(x) not in matched_a]
        simple_b_to_a = [y for y in b_gives if id(y) not in matched_b]

        return {
            "errors": errors,
            "beta_pairs": beta,
            "simple_a_to_b": simple_a_to_b,
            "simple_b_to_a": simple_b_to_a,
        }

    # ---------- Grab primitive ----------

    async def _grab_loop(
        self,
        register: RegisterService,
        url: str,
        course_data: dict,
        timeout: float,
        count: int,
        on_log: LogFn,
        label: str,
    ) -> Tuple[bool, Any]:
        """Bắn burst register liên tục tới khi status=0 hoặc hết timeout.

        Dùng _burst_request (song song `count` request/lần). Trả
        (success, last_status).

        DỪNG SỚM để tránh spin vô nghĩa: chỉ -6 (lớp đầy, chờ slot mở) và
        -2 (own-conflict tạm thời trong swap β, sẽ hết sau khi drop) mới
        đáng spin tiếp (GRAB_SPIN_STATUSES). Gặp status khác — lỗi CẤP MÔN
        (-4 đã ĐK) hoặc lỗi lạ — spin thêm cũng không đổi kết quả → return
        ngay thay vì đợi hết timeout.
        """
        deadline = _time.monotonic() + timeout
        attempts = 0
        last_status: Any = None
        while _time.monotonic() < deadline:
            attempts += 1
            try:
                success, status = await register._burst_request(url, course_data, count)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                on_log(f"[GRAB {label}] lỗi burst: {type(e).__name__}: {e}")
                await asyncio.sleep(0.1)
                continue
            last_status = status
            if success:
                on_log(f"[GRAB {label}] ✓ CHỤP ĐƯỢC (sau {attempts} lượt burst).")
                return True, 0
            # Dừng sớm nếu status không đáng spin (None=network cứ thử lại).
            if status is not None and status not in GRAB_SPIN_STATUSES:
                on_log(
                    f"[GRAB {label}] ✗ DỪNG SỚM: {status_label(status)} "
                    f"(status={status}) — spin thêm vô nghĩa."
                )
                return False, status
            await asyncio.sleep(0.05)
        on_log(
            f"[GRAB {label}] ✗ hết {timeout:.1f}s ({attempts} lượt burst), "
            f"status cuối={last_status}."
        )
        return False, last_status

    # ---------- Simple give (1 chiều) ----------

    async def simple_give(
        self,
        giver_reg: RegisterService, giver_user: User, giver_pid: int,
        receiver_reg: RegisterService, receiver_user: User, receiver_pid: int,
        course: Course,
        lead: float, timeout: float, count: int,
        on_log: LogFn,
    ) -> Dict[str, Any]:
        """Giver drop lớp `course`, receiver pre-burst chụp.

        Receiver bắt đầu grab TRƯỚC (lead giây), rồi giver drop → slot mở →
        receiver chụp. Nếu drop fail → hủy grab, giver giữ nguyên lớp.
        """
        code = course.code
        grab_url = receiver_user.register_url(receiver_pid)
        on_log(f"[GIVE {code}] receiver pre-burst (lead {lead:.2f}s)...")
        grab_task = asyncio.create_task(
            self._grab_loop(receiver_reg, grab_url, course.data, timeout, count, on_log, code)
        )
        await asyncio.sleep(lead)
        on_log(f"[GIVE {code}] giver DROP lớp...")
        drop_ok, drop_status = await giver_reg.drop_class(
            giver_user, giver_pid, course.data
        )
        if not drop_ok:
            on_log(
                f"[GIVE {code}] ✗ DROP fail (status={drop_status}). "
                f"Hủy grab — giver GIỮ NGUYÊN lớp."
            )
            grab_task.cancel()
            try:
                await grab_task
            except asyncio.CancelledError:
                pass
            return {
                "code": code, "dropped": False, "grabbed": False,
                "drop_status": drop_status, "grab_status": None,
            }
        on_log(f"[GIVE {code}] drop OK — chờ receiver chụp...")
        grabbed, grab_status = await grab_task
        if grabbed:
            on_log(f"[GIVE {code}] ✓ HOÀN TẤT: giver nhả, receiver chụp được.")
        else:
            on_log(
                f"[GIVE {code}] ⚠ giver ĐÃ NHẢ nhưng receiver chụp HỤT "
                f"(status={grab_status}). Lớp có thể bị người ngoài chiếm!"
            )
        return {
            "code": code, "dropped": True, "grabbed": grabbed,
            "drop_status": 0, "grab_status": grab_status,
        }

    # ---------- β swap cùng slot (drop-drop-grab-grab) ----------

    async def swap_same_slot(
        self,
        a_reg: RegisterService, a_user: User, a_pid: int, x_course: Course,
        b_reg: RegisterService, b_user: User, b_pid: int, y_course: Course,
        lead: float, timeout: float, count: int,
        on_log: LogFn,
    ) -> Dict[str, Any]:
        """A giữ X muốn Y; B giữ Y muốn X; X,Y TRÙNG SLOT.

        Không pre-burst give thường được: A grab Y sẽ bị -2 (còn giữ X),
        B grab X bị -2 (còn giữ Y). Phải:
          1. A grab-Y ‖ B grab-X burst START (đều -2 lúc này).
          2. Sau lead: A drop X ‖ B drop Y (song song) → hết own-conflict.
          3. 2 grab đang chạy chụp được ngay.

        RỦI RO: sau bước 2, cả X,Y đều mở + không pre-burst bảo vệ được
        (phải drop trước) → người ngoài có thể chụp. Grab hụt = mất lớp.
        """
        x_code, y_code = x_course.code, y_course.code
        on_log(
            f"[SWAP {x_code}↔{y_code}] pre-burst song song (lead {lead:.2f}s). "
            f"⚠ Sẽ drop cả 2 trước khi giành lại."
        )
        grab_y_url = a_user.register_url(a_pid)   # A chụp Y
        grab_x_url = b_user.register_url(b_pid)   # B chụp X
        grab_y_task = asyncio.create_task(
            self._grab_loop(a_reg, grab_y_url, y_course.data, timeout, count, on_log, f"A←{y_code}")
        )
        grab_x_task = asyncio.create_task(
            self._grab_loop(b_reg, grab_x_url, x_course.data, timeout, count, on_log, f"B←{x_code}")
        )
        await asyncio.sleep(lead)
        on_log(f"[SWAP {x_code}↔{y_code}] DROP cả 2 SONG SONG...")
        drop_results = await asyncio.gather(
            a_reg.drop_class(a_user, a_pid, x_course.data),
            b_reg.drop_class(b_user, b_pid, y_course.data),
            return_exceptions=True,
        )
        a_drop, b_drop = drop_results[0], drop_results[1]
        a_drop_ok = isinstance(a_drop, tuple) and bool(a_drop[0])
        b_drop_ok = isinstance(b_drop, tuple) and bool(b_drop[0])
        on_log(
            f"[SWAP {x_code}↔{y_code}] A drop X={a_drop_ok}, B drop Y={b_drop_ok}. "
            f"Chờ 2 grab..."
        )
        grabbed_y, gy_status = await grab_y_task
        grabbed_x, gx_status = await grab_x_task
        if grabbed_x and grabbed_y:
            on_log(f"[SWAP {x_code}↔{y_code}] ✓ HOÀN TẤT: A có {y_code}, B có {x_code}.")
        else:
            on_log(
                f"[SWAP {x_code}↔{y_code}] ⚠ KHÔNG trọn vẹn: "
                f"A chụp {y_code}={grabbed_y}, B chụp {x_code}={grabbed_x}. "
                f"Kiểm tra lại — có thể 1 hoặc cả 2 lớp bị mất!"
            )
        return {
            "x_code": x_code, "y_code": y_code,
            "a_dropped_x": a_drop_ok, "b_dropped_y": b_drop_ok,
            "a_grabbed_y": grabbed_y, "b_grabbed_x": grabbed_x,
            "grab_y_status": gy_status, "grab_x_status": gx_status,
        }

    # ---------- Orchestrator ----------

    async def execute(
        self,
        plan: Dict[str, Any],
        ctx_a: Dict[str, Any],
        ctx_b: Dict[str, Any],
        on_log: LogFn,
        should_stop: Optional[StopFn] = None,
    ) -> Dict[str, Any]:
        """Thực thi transfer plan.

        ctx_a/ctx_b = {"register": RegisterService, "user": User, "period_id": int}.
        β pairs chạy trước (mỗi pair 1 drop-drop-grab-grab, tuần tự giữa các
        pair). Simple gives chạy sau, tuần tự.
        """
        lead = float(getattr(Config, "TRANSFER_PRE_BURST_LEAD", 0.5))
        timeout = float(getattr(Config, "TRANSFER_GRAB_TIMEOUT", 10.0))
        count = int(getattr(Config, "BURST_COUNT", 5))
        results: Dict[str, Any] = {"beta": [], "simple": []}

        def _stop() -> bool:
            return bool(should_stop and should_stop())

        # β pairs
        for (x, y) in plan.get("beta_pairs", []):
            if _stop():
                on_log("[TRANSFER] ⏹ Dừng theo yêu cầu (trước β pair).")
                break
            r = await self.swap_same_slot(
                ctx_a["register"], ctx_a["user"], ctx_a["period_id"], x,
                ctx_b["register"], ctx_b["user"], ctx_b["period_id"], y,
                lead, timeout, count, on_log,
            )
            results["beta"].append(r)

        # simple A→B (A drop, B chụp)
        for x in plan.get("simple_a_to_b", []):
            if _stop():
                on_log("[TRANSFER] ⏹ Dừng theo yêu cầu (trước simple A→B).")
                break
            r = await self.simple_give(
                ctx_a["register"], ctx_a["user"], ctx_a["period_id"],
                ctx_b["register"], ctx_b["user"], ctx_b["period_id"],
                x, lead, timeout, count, on_log,
            )
            r["dir"] = "A→B"
            results["simple"].append(r)

        # simple B→A (B drop, A chụp)
        for y in plan.get("simple_b_to_a", []):
            if _stop():
                on_log("[TRANSFER] ⏹ Dừng theo yêu cầu (trước simple B→A).")
                break
            r = await self.simple_give(
                ctx_b["register"], ctx_b["user"], ctx_b["period_id"],
                ctx_a["register"], ctx_a["user"], ctx_a["period_id"],
                y, lead, timeout, count, on_log,
            )
            r["dir"] = "B→A"
            results["simple"].append(r)

        return results
