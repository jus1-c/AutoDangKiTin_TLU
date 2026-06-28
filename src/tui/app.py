"""
AutoDangKiTin TLU - Textual TUI

Async-native terminal interface (replaces the hand-rolled ANSI TUI).

Menu (5 items + 2 footer buttons):
  1. Đăng ký nhanh        — multi-select (SelectionList) → burst register
                              → auto-sniff fails if AUTO_SNIFF_FALLBACK on
  2. Tạo danh sách custom — chọn lớp, lớp trùng lịch bị xám + không pick,
                              lưu file res/custom/{name}.json
                              (tên rỗng → custom_{time}.json)
  3. Đăng ký theo profile — load file JSON đã lưu → đăng ký
  4. Lịch                 — export ICS + sync Google Calendar
  5. Settings             — debug, interval, jitter, fallback toggle,
                              burst count, concurrency, đăng xuất
  [Thoát]  [Đăng xuất]    — Thoát giữ session, Đăng xuất xóa token

Stdout capture: `LogCapture` redirect sys.stdout -> RichLog widget during
worker execution so existing `print()` in services keep working unchanged
(per design: don't touch service code).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from typing import Any, Awaitable, Callable, Dict, List, Optional

from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    SelectionList,
    Static,
)
from textual.widgets.selection_list import Selection

from src.config import Config
from src.core.client import TLUClient
from src.models.course import Course
from src.models.user import User
from src.services.auth_service import AuthService
from src.services.calendar_service import CalendarService
from src.services.course_service import CourseService
from src.services.custom_service import CustomService
from src.services.register_service import RegisterService


# ---------- stdout capture ----------


class _StdoutToLog:
    """File-like that forwards `write()` to a RichLog widget (must be thread-safe)."""

    def __init__(self, log_widget: RichLog, real=sys.__stdout__):
        self.log_widget = log_widget
        self.real = real

    def write(self, data: str) -> int:
        if data and data.strip():
            try:
                self.log_widget.write(data.rstrip())
            except Exception:
                pass
        if self.real:
            try:
                self.real.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        if self.real:
            try:
                self.real.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False


@contextlib.contextmanager
def capture_stdout(log_widget: RichLog):
    """Replace sys.stdout with a RichLog-forwarding stream for the block."""
    original = sys.stdout
    sys.stdout = _StdoutToLog(log_widget, original)
    try:
        yield
    finally:
        sys.stdout = original


# ---------- custom toggle switch ----------


class ToggleSwitch(Static, can_focus=True):
    """iOS-style toggle: circle slides left (off) or right (on), track
    changes color. Click to toggle. No animation glitches.
    """

    DEFAULT_CSS = """
    ToggleSwitch {
        width: 5;
        height: 1;
        background: transparent;
    }
    ToggleSwitch:hover {
        background: transparent;
    }
    """

    value = reactive(False)

    def __init__(self, value: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.value = value

    def render(self) -> RichText:
        if self.value:
            return RichText("█░░", style="#a6da95")
        return RichText("░░█", style="#5b6078")

    def watch_value(self, value: bool) -> None:
        self.refresh()

    def on_click(self) -> None:
        self.value = not self.value


# ---------- log screen (reused across actions) ----------


class LogScreen(Screen):
    """Generic screen with a RichLog for live output + a Stop button."""

    BINDINGS = [
        Binding("ctrl+c", "stop", "Dừng", show=True),
    ]

    def __init__(self, title: str = "Logs"):
        super().__init__()
        self.title_text = title
        self.stop_event = asyncio.Event()
        self.worker_handle = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="log-container"):
            yield Label(self.title_text, id="log-title")
            yield RichLog(id="log", highlight=False, markup=False, wrap=False, max_lines=5000)
            with Horizontal(id="log-buttons"):
                yield Button("Dừng", id="stop-btn", variant="error")
                yield Button("Quay lại", id="back-btn", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#stop-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "stop-btn":
            self.action_stop()
        elif event.button.id == "back-btn":
            self.app.pop_screen()

    def action_stop(self) -> None:
        self.stop_event.set()
        self.query_one("#log", RichLog).write("[Đã yêu cầu dừng...]")

    def run_async(
        self,
        coro_factory: Callable[["LogCaptureContext"], Awaitable[Any]],
    ) -> None:
        """Run a coroutine in a Textual worker, capturing its stdout."""
        self.stop_event.clear()
        log_widget = self.query_one("#log", RichLog)

        async def _runner():
            ctx = LogCaptureContext(log_widget, self.stop_event)
            try:
                with capture_stdout(log_widget):
                    await coro_factory(ctx)
            except asyncio.CancelledError:
                self.stop_event.set()
                log_widget.write("[Đã hủy]")
            except Exception as e:  # noqa: BLE001
                log_widget.write(f"[ERROR] {e}")
            finally:
                self.query_one("#stop-btn", Button).label = "Đã xong"

        self.worker_handle = self.app.run_worker(_runner(), exclusive=False)


class LogCaptureContext:
    """Passed to worker coroutines: exposes log() and should_stop()."""

    def __init__(self, log_widget: RichLog, stop_event: asyncio.Event):
        self.log_widget = log_widget
        self.stop_event = stop_event

    def log(self, msg: str) -> None:
        self.log_widget.write(msg)

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def print(self, msg: str) -> None:
        self.log_widget.write(msg)


# ---------- login screen ----------


class LoginScreen(ModalScreen[Optional[Dict[str, Any]]]):
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Hủy", show=False),
    ]

    def __init__(
        self,
        default_user: Optional[str] = None,
        default_save: bool = True,
        default_password: Optional[str] = None,
    ):
        super().__init__()
        self._default_user = default_user or ""
        self._default_password = default_password or ""
        self._default_save = default_save

    def compose(self) -> ComposeResult:
        with Container(id="login-container"):
            yield Label("ĐĂNG NHẬP", id="login-title")
            yield Label("Mã sinh viên:")
            yield Input(value=self._default_user, id="username", placeholder="Mã sinh viên")
            yield Label("Mật khẩu:")
            yield Input(
                value=self._default_password,
                password=True,
                id="password",
                placeholder="Mật khẩu",
            )
            with Horizontal(id="save-login-row"):
                yield ToggleSwitch(value=self._default_save, id="save-login")
                yield Label("Lưu đăng nhập cho lần sau")
            yield Static("", id="login-error")
            with Horizontal(id="login-buttons"):
                yield Button("Đăng nhập", id="login-btn", variant="primary")
                yield Button("Thoát", id="cancel-btn", variant="default")

    def on_mount(self) -> None:
        if not self._default_user:
            self.query_one("#username", Input).focus()
        else:
            self.query_one("#password", Input).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
            return
        if event.button.id == "login-btn":
            await self._attempt_login()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("username", "password"):
            await self._attempt_login()

    async def _attempt_login(self) -> None:
        u = self.query_one("#username", Input).value.strip()
        p = self.query_one("#password", Input).value
        save = self.query_one("#save-login", ToggleSwitch).value
        err = self.query_one("#login-error", Static)
        if not u or not p:
            err.update("Thiếu tên đăng nhập hoặc mật khẩu.")
            return
        err.update("Đang đăng nhập...")
        self.query_one("#login-btn", Button).disabled = True
        try:
            client = TLUClient()
            auth = AuthService(client)
            try:
                user = await auth.login(u, p, save=save)
                self.dismiss({"user": user, "client": client})
            except Exception as e:  # noqa: BLE001
                err.update(f"Lỗi: {e}")
                self.query_one("#login-btn", Button).disabled = False
                try:
                    await client.close()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            err.update(f"Lỗi: {e}")
            self.query_one("#login-btn", Button).disabled = False


# ---------- menu screen ----------


class MenuScreen(Screen):
    BINDINGS = [
        Binding("1", "register", "Đăng ký nhanh"),
        Binding("2", "builder", "Tạo custom"),
        Binding("3", "profile", "Đăng ký profile"),
        Binding("4", "calendar", "Lịch"),
        Binding("5", "settings", "Settings"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="menu-container"):
            yield Label(
                f"Xin chào {self.user.full_name} ({self.user.student_id})",
                id="menu-greet",
            )
            yield Label("Chọn chức năng (phím số) hoặc click nút:", id="menu-hint")
            yield Button("1. Đăng ký nhanh (chọn nhiều môn)", id="b1", variant="primary")
            yield Button("2. Tạo danh sách custom", id="b2", variant="warning")
            yield Button("3. Đăng ký theo profile", id="b3")
            yield Button("4. Lịch (ICS / Google)", id="b4")
            yield Button("5. Settings", id="b5")
            with Horizontal(id="menu-footer"):
                yield Button("Thoát", id="exit-btn", variant="default")
                yield Button("Đăng xuất", id="logout-btn", variant="error")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "b1":
            self.app.push_screen(RegisterScreen(self.user, self.services))
        elif bid == "b2":
            self.app.push_screen(CustomBuilderScreen(self.user, self.services))
        elif bid == "b3":
            self.app.push_screen(ProfileScreen(self.user, self.services))
        elif bid == "b4":
            self.app.push_screen(CalendarScreen(self.user, self.services))
        elif bid == "b5":
            self.app.push_screen(SettingsScreen(self.services))
        elif bid == "exit-btn":
            self.app.exit()
        elif bid == "logout-btn":
            for f in (Config.TOKEN_FILE, Config.LOGIN_FILE, Config.GOOGLE_TOKEN_FILE):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass
            self.app.exit()

    def action_register(self) -> None:
        self.app.push_screen(RegisterScreen(self.user, self.services))

    def action_builder(self) -> None:
        self.app.push_screen(CustomBuilderScreen(self.user, self.services))

    def action_profile(self) -> None:
        self.app.push_screen(ProfileScreen(self.user, self.services))

    def action_calendar(self) -> None:
        self.app.push_screen(CalendarScreen(self.user, self.services))

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen(self.services))


# ---------- register screen (multi-select) ----------


class RegisterScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.courses: List[List[Course]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Label("ĐĂNG KÝ NHANH", id="reg-title")
            with Horizontal(id="reg-toolbar"):
                yield ToggleSwitch(id="summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Tải danh sách môn", id="load", variant="primary")
                yield Button("Chọn tất cả", id="select-all")
                yield Button("Bỏ chọn", id="deselect-all")
                yield Button("Đăng ký môn đã chọn", id="run", variant="success")
                yield Button("Quay lại", id="back")
            yield SelectionList[int](id="reg-selection")
        yield Footer()

    def _is_summer(self) -> bool:
        return self.query_one("#summer", ToggleSwitch).value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self.app.pop_screen()
            return
        if bid == "load":
            await self._load_courses()
            return
        if bid == "select-all":
            self.query_one(SelectionList).select_all()
            return
        if bid == "deselect-all":
            self.query_one(SelectionList).deselect_all()
            return
        if bid == "run":
            await self._run_register()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _load_courses(self) -> None:
        sel: SelectionList = self.query_one(SelectionList)
        sel.clear_options()
        self.courses = []
        is_summer = self._is_summer()
        try:
            self.courses, names = await self.services["course"].fetch_courses(
                self.user, is_summer
            )
            for i, name in enumerate(names):
                if not self.courses[i]:
                    continue
                c = self.courses[i][0]
                label = (
                    f"{i:>3}. {name[:40]:<40}  "
                    f"[{c.code}]  {c.current_students}/{c.max_students}"
                )
                sel.add_option(Selection(label, i))
        except Exception as e:  # noqa: BLE001
            self.notify(f"Lỗi tải môn: {e}", severity="error")
            print(f"[UI] Load courses FAILED: {e}")

    async def _run_register(self) -> None:
        sel: SelectionList = self.query_one(SelectionList)
        indices = list(sel.selected)
        if not indices or not self.courses:
            self.notify("Chưa chọn môn hoặc chưa tải dữ liệu.", severity="warning")
            return

        log_screen = LogScreen("Đăng ký nhanh")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]
            try:
                failed = await register.register_subjects(
                    self.user, indices, self.courses, self._is_summer()
                )
                if failed and Config.AUTO_SNIFF_FALLBACK and not ctx.should_stop():
                    ctx.log(f"[AUTO] {len(failed)} môn fail -> chuyển sang sniffing.")
                    await register.sniffing_loop(
                        self.user,
                        failed,
                        self._is_summer(),
                        interval=Config.SNIFF_INTERVAL,
                        jitter=Config.SNIFF_JITTER,
                        on_log=ctx.log,
                        should_stop=ctx.should_stop,
                    )
                elif failed and not Config.AUTO_SNIFF_FALLBACK:
                    ctx.log(f"[INFO] {len(failed)} môn fail. Tự fallback đã TẮT trong Settings.")
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen.run_async(_work)


# ---------- class picker modal (table with conflict-grey) ----------


class ClassPickerScreen(ModalScreen[Optional[Course]]):
    """Modal that lets the user pick one class for a subject.

    Renders a DataTable with one row per class option and the following
    columns: marker (○/●/✗), Tên lớp, Lịch (Thứ • Tiết), Giáo viên,
    Ngày, Sĩ số. Classes that conflict with already-selected classes
    are dimmed and cannot be picked (Enter/dblclick/button all refuse
    with a bell + notification).

    Enter priority-binding is used so the key always triggers pick
    (Textual 8.x's DataTable.RowActivated doesn't fire from Enter
    reliably in headless mode).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Hủy"),
        Binding("enter", "pick_cursor", "Chọn lớp đang trỏ", priority=True),
    ]

    def __init__(self, subject_name: str, options: List[Course],
                 other_selected: List[Course], current: Optional[Course] = None):
        super().__init__()
        self.subject_name = subject_name
        self.options = options
        self.other_selected = other_selected
        self.current = current
        self._conflict_idx: set[int] = set()
        self._populated = False

    def compose(self) -> ComposeResult:
        with Container(id="picker-container"):
            yield Label(f"Chọn lớp cho: {self.subject_name}", id="picker-title")
            yield DataTable(id="picker-table", zebra_stripes=True, cursor_type="row")
            with Horizontal(id="picker-buttons"):
                yield Button("Chọn lớp đang trỏ (Enter)", id="pick-btn", variant="primary")
                yield Button("Đóng (Esc)", id="close-btn", variant="default")

    def on_mount(self) -> None:
        # on_mount can fire more than once in some flows (priority-binding
        # double-trigger, screen re-push, etc.). Guard with a flag.
        if self._populated:
            return
        self._populated = True
        table = self.query_one("#picker-table", DataTable)
        # Explicit widths so all 4 columns fit on ~80-col screens.
        # Layout: ✓ N/M | Tên lớp | Lịch (all buổi) | GV
        # Date is dropped from the picker Lịch cell (still shown in the
        # builder's multi-line cell after a class is picked). Sĩ số is
        # folded into the marker column to save space.
        table.add_column("✓ N/M", width=7, key="col-pick")
        table.add_column("Tên lớp", width=24, key="col-name")
        table.add_column("Lịch (T2:1-3, T4:4-6)", width=20, key="col-lich")
        table.add_column("GV", width=22, key="col-gv")
        cursor_target = 0
        for i, opt in enumerate(self.options):
            conflict = any(opt.conflicts_with(o) for o in self.other_selected)
            chosen = opt == self.current
            if conflict:
                self._conflict_idx.add(i)
                style = "#5b6078 dim italic"
                mark = "✗"
            elif chosen:
                style = "#a6da95 bold"
                mark = "●"
            else:
                style = "#cad3f5"
                mark = "○"

            # Lịch cell: ALL buổi (no date here — date is in the builder
            # cell after a class is picked). e.g. "T2: 1-3, T4: 4-6, T6: 7-9".
            sessions = opt.sessions_summary or "—"
            if len(sessions) > 18:
                sessions = sessions[:17] + "…"
            name = opt.display_name
            if len(name) > 22:
                name = name[:21] + "…"
            sisos = f"{mark} {opt.current_students}/{opt.max_students}"
            gv = opt.teacher_name or "—"
            if len(gv) > 20:
                gv = gv[:19] + "…"

            table.add_row(
                RichText(sisos, style=style),
                RichText(name, style=style),
                RichText(sessions, style=style),
                RichText(gv, style=style),
                key=str(i),
            )
            if chosen:
                cursor_target = i
            elif cursor_target == 0 and i not in self._conflict_idx and not chosen:
                cursor_target = i

        table.focus()
        try:
            table.cursor_coordinate = (cursor_target, 0)
        except Exception:
            pass

    def on_data_table_row_activated(self, event) -> None:
        """Double-click on a row also tries to pick."""
        if event.data_table.id == "picker-table":
            self._try_pick(event.cursor_row)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss(None)
        elif event.button.id == "pick-btn":
            table = self.query_one("#picker-table", DataTable)
            self._try_pick(table.cursor_row)

    def action_pick_cursor(self) -> None:
        """Priority-bound: fires on Enter regardless of focus (within modal)."""
        table = self.query_one("#picker-table", DataTable)
        self._try_pick(table.cursor_row)

    def _try_pick(self, row: Optional[int]) -> None:
        if row is None or row < 0 or row >= len(self.options):
            return
        if row in self._conflict_idx:
            self.app.bell()
            self.notify(
                "Lớp này trùng lịch với lớp đã chọn — không thể chọn.",
                severity="warning",
            )
            return
        self.dismiss(self.options[row])

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------- custom builder screen ----------


class CustomBuilderScreen(Screen):
    """Build a custom course list with conflict detection (class picker
    greys out conflicting options). Save as JSON to res/custom/.
    """

    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
        Binding("enter", "pick_class", "Chọn lớp (Enter/dblclick)", priority=True),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.custom: CustomService = services["custom"]
        self.courses: List[List[Course]] = []
        self.names: List[str] = []
        self.selections: Dict[int, Course] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Label("TẠO DANH SÁCH CUSTOM", id="builder-title")
            with Horizontal(id="builder-toolbar"):
                yield ToggleSwitch(id="summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Tải môn", id="load", variant="primary")
                yield Button("Chọn lớp (subject đang trỏ)", id="pick", variant="warning")
                yield Button("Bỏ chọn lớp", id="clear-pick", variant="error")
                yield Button("Quay lại", id="back")
            yield DataTable(id="builder-table", zebra_stripes=True, cursor_type="row")
            with Horizontal(id="builder-save-row"):
                yield Input(placeholder="Tên file (rỗng = custom_{time}.json)", id="save-name")
                yield Button("Lưu danh sách", id="save", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#builder-table", DataTable)
        table.add_columns("STT", "Tên môn", "Lớp đã chọn", "Sĩ số")
        # Focus table so Enter/dblclick work without an extra click.
        self.call_after_refresh(table.focus)

    def _is_summer(self) -> bool:
        return self.query_one("#summer", ToggleSwitch).value

    def on_data_table_row_activated(self, event) -> None:
        """Double-click on a row → open the class picker for that subject."""
        if event.data_table.id == "builder-table":
            self._cursor_row = event.cursor_row
            asyncio.create_task(self._pick_class())

    def action_pick_class(self) -> None:
        """Priority-bound: Enter on the builder screen → open class picker
        for the row under the cursor (works without manually focusing
        the table first).
        """
        asyncio.create_task(self._pick_class())

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self.app.pop_screen()
            return
        if bid == "load":
            await self._load_courses()
            return
        if bid == "pick":
            await self._pick_class()
            return
        if bid == "clear-pick":
            self._clear_pick()
            return
        if bid == "save":
            self._save()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _load_courses(self) -> None:
        table = self.query_one("#builder-table", DataTable)
        table.clear()
        self.selections.clear()
        try:
            self.courses, self.names = await self.services["course"].fetch_courses(
                self.user, self._is_summer()
            )
            for i, name in enumerate(self.names):
                if not self.courses[i]:
                    continue
                c = self.courses[i][0]
                table.add_row(str(i), name, "---", f"{c.current_students}/{c.max_students}", key=str(i))
        except Exception as e:  # noqa: BLE001
            self.notify(f"Lỗi tải môn: {e}", severity="error")

    @staticmethod
    def _selected_cell_text(sel: Optional[Course]) -> str:
        """Cell text for 'Lớp đã chọn'. Two-line: name + sessions/dates detail.

        Multi-line so a class with many buổi is fully visible:
          '<display_name>\n  ↳ T2: 1-3, T4: 4-6\n  ↳ 13/04/2026 -> 04/05/2026'
        """
        if not sel:
            return "---"
        lines = [sel.display_name]
        sessions = sel.sessions_summary
        dates = sel.date_range
        if sessions:
            lines.append(f"  ↳ {sessions}")
        if dates:
            lines.append(f"  ↳ {dates}")
        return "\n".join(lines)

    def _refresh_table(self) -> None:
        table = self.query_one("#builder-table", DataTable)
        table.clear()
        for i, name in enumerate(self.names):
            if not self.courses[i]:
                continue
            sel = self.selections.get(i)
            sel_text = self._selected_cell_text(sel)
            c = self.courses[i][0]
            table.add_row(str(i), name, sel_text, f"{c.current_students}/{c.max_students}", key=str(i))

    async def _pick_class(self) -> None:
        # Guard: don't push a second picker if one is already on the stack
        # (Enter can fire both the priority binding AND DataTable.RowActivated).
        for screen in self.app.screen_stack:
            if isinstance(screen, ClassPickerScreen):
                return
        table = self.query_one("#builder-table")
        # Prefer row from a row_activated event if set, else use cursor.
        row = getattr(self, "_cursor_row", None)
        if row is None or row < 0:
            row = table.cursor_row
        self._cursor_row = None
        if row is None or row < 0 or not self.courses:
            self.notify("Chưa tải môn hoặc chưa chọn subject.", severity="warning")
            return
        # Map displayed row index back to subject index
        subject_idx = self._row_to_subject_idx(row)
        if subject_idx is None:
            return
        options = self.courses[subject_idx]
        if not options:
            return
        other = [c for k, c in self.selections.items() if k != subject_idx]
        current = self.selections.get(subject_idx)

        def _on_pick(picked: Optional[Course]) -> None:
            if picked is not None:
                self.selections[subject_idx] = picked
                self._refresh_table()

        self.app.push_screen(
            ClassPickerScreen(self.names[subject_idx], options, other, current),
            _on_pick,
        )

    def _row_to_subject_idx(self, row: int) -> Optional[int]:
        # Map visible row back to subject index (skip empty subjects)
        seen = 0
        for i, group in enumerate(self.courses):
            if not group:
                continue
            if seen == row:
                return i
            seen += 1
        return None

    def _clear_pick(self) -> None:
        table = self.query_one("#builder-table", DataTable)
        row = table.cursor_row
        if row is None or row < 0:
            return
        subject_idx = self._row_to_subject_idx(row)
        if subject_idx is not None and subject_idx in self.selections:
            del self.selections[subject_idx]
            self._refresh_table()

    def _save(self) -> None:
        if not self.selections:
            self.notify("Chưa chọn lớp nào.", severity="warning")
            return
        name = self.query_one("#save-name", Input).value
        filename = self.custom.save_named(list(self.selections.values()), name)
        self.notify(f"Đã lưu: {filename}", severity="information")
        self.query_one("#save-name", Input).value = ""


# ---------- profile screen (register from saved JSON) ----------


class ProfileScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.custom: CustomService = services["custom"]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Label("ĐĂNG KÝ THEO PROFILE", id="profile-title")
            with Horizontal():
                yield Button("Làm mới", id="refresh")
                yield Button("Đăng ký file đã chọn", id="run", variant="success")
                yield Button("Xóa file đã chọn", id="delete", variant="error")
                yield Button("Quay lại", id="back")
            yield DataTable(id="profile-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#profile-table", DataTable)
        table.add_columns("STT", "Tên file")
        self._refresh()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self.app.pop_screen()
            return
        if bid == "refresh":
            self._refresh()
            return
        if bid == "delete":
            self._delete_selected()
            return
        if bid == "run":
            await self._run_selected()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _refresh(self) -> None:
        table = self.query_one("#profile-table", DataTable)
        table.clear()
        for i, f in enumerate(self.custom.list_files()):
            table.add_row(str(i), f, key=f)

    def _delete_selected(self) -> None:
        table = self.query_one("#profile-table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Chưa chọn file.", severity="warning")
            return
        files = self.custom.list_files()
        if table.cursor_row >= len(files):
            return
        key = files[table.cursor_row]
        self.custom.delete_files([key])
        self._refresh()
        self.notify(f"Đã xóa {key}", severity="information")

    async def _run_selected(self) -> None:
        table = self.query_one("#profile-table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Chưa chọn file.", severity="warning")
            return
        files = self.custom.list_files()
        if table.cursor_row >= len(files):
            return
        key = files[table.cursor_row]
        path = os.path.join(Config.RES_DIR, "custom", key)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            target_courses = [Course(d) for d in data]
        except Exception as e:  # noqa: BLE001
            self.notify(f"Lỗi đọc file: {e}", severity="error")
            return

        log_screen = LogScreen(f"Profile: {key}")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]
            try:
                failed = await register.register_custom(self.user, target_courses)
                if failed and Config.AUTO_SNIFF_FALLBACK and not ctx.should_stop():
                    ctx.log(f"[AUTO] {len(failed)} môn fail -> chuyển sang sniffing.")
                    await register.sniffing_loop(
                        self.user,
                        failed,
                        False,
                        interval=Config.SNIFF_INTERVAL,
                        jitter=Config.SNIFF_JITTER,
                        on_log=ctx.log,
                        should_stop=ctx.should_stop,
                    )
                elif failed and not Config.AUTO_SNIFF_FALLBACK:
                    ctx.log(f"[INFO] {len(failed)} môn fail. Tự fallback đã TẮT trong Settings.")
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen.run_async(_work)


# ---------- calendar screen ----------


class CalendarScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Label("LỊCH", id="cal-title")
            with Horizontal():
                yield Button("Xuất ICS", id="ics", variant="primary")
                yield Button("Đồng bộ Google Calendar", id="google", variant="success")
                yield Button("Quay lại", id="back")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self.app.pop_screen()
            return
        if bid == "ics":
            await self._export_ics()
        elif bid == "google":
            await self._sync_google()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _export_ics(self) -> None:
        log_screen = LogScreen("Xuất ICS")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            cal: CalendarService = self.services["calendar"]
            try:
                path = await cal.export_ics(self.user)
                ctx.log(f"Đã tạo: {path}")
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen.run_async(_work)

    async def _sync_google(self) -> None:
        log_screen = LogScreen("Đồng bộ Google")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            cal: CalendarService = self.services["calendar"]
            try:
                events = await cal.get_tlu_events(self.user)
                await asyncio.to_thread(
                    cal.sync_to_google,
                    events,
                    initial_token=None,
                    on_token_update=None,
                    browser_callback=None,
                )
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen.run_async(_work)


# ---------- settings screen ----------


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, services: dict):
        super().__init__()
        self.services = services

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="settings-container"):
            yield Label("SETTINGS", id="set-title")
            with Horizontal(id="settings-row"):
                yield ToggleSwitch(value=Config.AUTO_SNIFF_FALLBACK, id="auto-sniff")
                yield Label("Tự fallback sang sniffing khi đăng ký fail")
            with Horizontal(id="settings-row"):
                yield ToggleSwitch(value=Config.DEBUG, id="debug")
                yield Label("Chế độ Debug")
            with Horizontal(id="settings-row"):
                yield Label("Số request song song / lần thử (BURST):")
                yield Input(value=str(Config.BURST_COUNT), id="burst")
            with Horizontal(id="settings-row"):
                yield Label("Giới hạn đồng thời (CONCURRENCY):")
                yield Input(value=str(Config.CONCURRENCY_LIMIT), id="concurrency")
            with Horizontal(id="settings-row"):
                yield Label("Interval sniff (giây):")
                yield Input(value=str(Config.SNIFF_INTERVAL), id="interval")
            with Horizontal(id="settings-row"):
                yield Label("Jitter sniff (giây, ±):")
                yield Input(value=str(Config.SNIFF_JITTER), id="jitter")
            with Horizontal(id="settings-buttons"):
                yield Button("Lưu", id="save", variant="primary")
                yield Button("Đăng xuất", id="logout", variant="error")
                yield Button("Quay lại", id="back")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self.app.pop_screen()
            return
        if bid == "save":
            self._save()
            return
        if bid == "logout":
            for f in (Config.TOKEN_FILE, Config.LOGIN_FILE, Config.GOOGLE_TOKEN_FILE):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass
            self.app.exit()

    def _parse_int(self, widget_id: str, label: str) -> Optional[int]:
        raw = self.query_one(f"#{widget_id}", Input).value.strip()
        try:
            v = int(raw)
            if v <= 0:
                raise ValueError
            return v
        except ValueError:
            self.notify(f"{label} phải là số nguyên dương.", severity="error")
            return None

    def _parse_float(self, widget_id: str, label: str) -> Optional[float]:
        raw = self.query_one(f"#{widget_id}", Input).value.strip()
        try:
            v = float(raw)
            if v < 0:
                raise ValueError
            return v
        except ValueError:
            self.notify(f"{label} phải là số không âm.", severity="error")
            return None

    def _save(self) -> None:
        auto_sniff = self.query_one("#auto-sniff", ToggleSwitch).value
        debug = self.query_one("#debug", ToggleSwitch).value
        burst = self._parse_int("burst", "Burst count")
        if burst is None:
            return
        conc = self._parse_int("concurrency", "Concurrency limit")
        if conc is None:
            return
        interval = self._parse_float("interval", "Sniff interval")
        if interval is None:
            return
        jitter = self._parse_float("jitter", "Jitter")
        if jitter is None:
            return

        Config.AUTO_SNIFF_FALLBACK = auto_sniff
        Config.DEBUG = debug
        Config.BURST_COUNT = burst
        Config.CONCURRENCY_LIMIT = conc
        Config.SNIFF_INTERVAL = interval
        Config.SNIFF_JITTER = jitter
        try:
            Config.save_settings()
            self.notify("Đã lưu vào res/settings.json.", severity="information")
        except OSError as e:
            self.notify(f"Lỗi ghi file: {e}", severity="error")


# ---------- main app ----------


class TLUApp(App):
    CSS = """
    /* Catppuccin Macchiato palette */
    Screen {
        background: #24273a;
    }

    /* Rows that contain a ToggleSwitch + Label */
    #save-login-row, #summer-row, #debug-row, #settings-row {
        height: 3;
        align-vertical: middle;
        padding: 1 0 0 0;
    }
    #settings-row Label {
        width: auto;
        padding: 0 1 0 0;
    }
    #settings-row Input {
        width: 16;
    }
    #settings-buttons {
        padding-top: 1;
        align-horizontal: center;
    }
    #settings-buttons Button {
        margin: 0 1;
    }

    /* Login screen — centered on screen, no Header/Footer */
    LoginScreen {
        align: center middle;
    }
    #login-container {
        padding: 1 2;
        width: 50;
        height: auto;
        background: #1e2030;
        border: round #5b6078;
    }
    #login-title {
        text-align: center;
        text-style: bold;
        color: #c6a0f6;
        padding: 0 0 1 0;
        width: 100%;
    }
    .login-field-label {
        padding: 1 0 0 0;
        color: #a5adcb;
    }
    #login-container Input {
        margin: 0;
    }
    #login-error {
        color: #ed8796;
        padding: 1 0 0 0;
        text-align: center;
    }
    #login-buttons {
        padding-top: 1;
        height: auto;
        width: 100%;
    }
    #login-buttons Horizontal {
        height: auto;
        align-horizontal: center;
    }
    #login-buttons Button {
        margin: 0 1;
    }

    /* Menu */
    #menu-container {
        align: center middle;
        padding: 1 2;
    }
    #menu-greet {
        text-align: center;
        text-style: bold;
        color: #a6da95;
        padding: 1;
    }
    #menu-hint {
        text-align: center;
        color: #a5adcb;
        padding-bottom: 1;
    }
    #menu-container Button {
        margin: 1 1;
        min-width: 36;
    }
    #menu-footer {
        align-horizontal: center;
        padding-top: 1;
    }
    #menu-footer Button {
        margin: 0 1;
        min-width: 16;
    }

    /* Register / Builder / Profile / Calendar screens */
    Label {
        color: #cad3f5;
    }
    DataTable {
        height: 1fr;
        margin: 1 0;
    }
    DataTable > .datatable--header {
        background: #363a4f;
        color: #c6a0f6;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #5b6078;
        color: #cad3f5;
    }
    DataTable > .datatable--hover {
        background: #494d64;
    }
    SelectionList {
        height: 1fr;
        margin: 1 0;
        background: #1e2030;
        border: round #5b6078;
    }
    #reg-toolbar, #builder-toolbar {
        height: auto;
        padding: 0 1;
    }
    #reg-toolbar Button, #builder-toolbar Button {
        margin: 0 1;
    }
    #builder-save-row {
        height: auto;
        padding: 1 0;
    }
    #builder-save-row Input {
        width: 1fr;
    }
    #builder-save-row Button {
        margin-left: 1;
    }

    /* Class picker modal */
    #picker-container {
        align: center middle;
        padding: 1 1;
        width: 98%;
        height: 90%;
        background: #1e2030;
        border: round #5b6078;
    }
    #picker-title {
        text-style: bold;
        color: #c6a0f6;
        text-align: center;
        padding-bottom: 1;
    }
    #picker-table {
        height: 1fr;
        margin: 1 0;
    }
    #picker-buttons {
        align-horizontal: center;
        padding-top: 1;
    }
    #picker-buttons Button {
        margin: 0 1;
    }

    /* Log screen */
    #log-container {
        padding: 1 2;
        height: 100%;
    }
    #log-title {
        text-style: bold;
        color: #c6a0f6;
        padding-bottom: 1;
    }
    #log {
        height: 1fr;
        border: round #5b6078;
        background: #181926;
    }
    #log-buttons {
        padding-top: 1;
        align-horizontal: center;
    }
    #log-buttons Button {
        margin: 0 1;
    }

    /* Buttons global */
    Button {
        margin: 0 1;
    }
    """

    def __init__(self):
        super().__init__()
        Config.ensure_dirs()
        self.client: Optional[TLUClient] = None
        self.services: dict = {}

    def on_mount(self) -> None:
        self.title = "AutoDangKiTin TLU"
        self._do_login()

    def _do_login(self) -> None:
        default_user = None
        default_password = None
        default_save = True
        if os.path.exists(Config.LOGIN_FILE):
            try:
                with open(Config.LOGIN_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                default_user = saved.get("username")
                default_password = saved.get("password")
                default_save = True
            except Exception:
                default_user = None
                default_password = None
                default_save = True
        else:
            default_save = False

        def _on_login(result) -> None:
            if not result:
                self.exit()
                return
            self.client = result["client"]
            user: User = result["user"]
            self.services = {
                "client": self.client,
                "auth": AuthService(self.client),
                "course": CourseService(self.client),
                "register": RegisterService(self.client),
                "calendar": CalendarService(self.client),
                "custom": CustomService(),
            }
            self.push_screen(MenuScreen(user, self.services))

        self.push_screen(
            LoginScreen(default_user, default_save, default_password),
            _on_login,
        )


def run_tui() -> None:
    TLUApp().run()
