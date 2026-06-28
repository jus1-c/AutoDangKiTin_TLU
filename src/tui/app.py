"""
AutoDangKiTin TLU - Textual TUI

Async-native terminal interface (replaces the hand-rolled ANSI TUI).

Use cases (5):
  1. Đăng ký nhanh        — multi-select → burst register → auto-sniff fails
  2. Sniffing riêng       — chọn môn săn → vòng GET-gated check-then-register
  3. Custom profile       — quản lý hồ sơ JSON (res/custom/*.json)
  4. Lịch                 — export ICS + sync Google Calendar
  5. Settings             — debug, interval sniff, đăng xuất

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
from typing import Any, Awaitable, Callable, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

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


from rich.text import Text as RichText
from textual.reactive import reactive


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
            # Green track, circle on RIGHT
            return RichText("  ●  ", style="black on #a6da95")
        else:
            # Gray track, circle on LEFT
            return RichText("  ○  ", style="#cad3f5 on #5b6078")

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


class LoginScreen(ModalScreen[Optional[User]]):
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Hủy", show=False),
    ]

    def __init__(self, default_user: Optional[str] = None, default_save: bool = True):
        super().__init__()
        self._default_user = default_user or ""
        self._default_save = default_save

    def compose(self) -> ComposeResult:
        with Container(id="login-container"):
            yield Label("ĐĂNG NHẬP", id="login-title")
            yield Label("Mã sinh viên:")
            yield Input(value=self._default_user, id="username", placeholder="Mã sinh viên")
            yield Label("Mật khẩu:")
            yield Input(password=True, id="password", placeholder="Mật khẩu")
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
        Binding("2", "sniff", "Sniffing"),
        Binding("3", "custom", "Custom"),
        Binding("4", "calendar", "Lịch"),
        Binding("5", "settings", "Settings"),
        Binding("0", "logout", "Đăng xuất"),
        Binding("q", "logout", "Thoát"),
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
            yield Button("1. Đăng ký nhanh (theo danh sách)", id="b1", variant="primary")
            yield Button("2. Sniffing riêng (săn môn fail)", id="b2", variant="warning")
            yield Button("3. Custom profile (hồ sơ JSON)", id="b3")
            yield Button("4. Lịch (ICS / Google)", id="b4")
            yield Button("5. Settings (debug, interval, đăng xuất)", id="b5")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "b1":
            self.app.push_screen(RegisterScreen(self.user, self.services))
        elif bid == "b2":
            self.app.push_screen(SniffScreen(self.user, self.services))
        elif bid == "b3":
            self.app.push_screen(CustomScreen(self.user, self.services))
        elif bid == "b4":
            self.app.push_screen(CalendarScreen(self.user, self.services))
        elif bid == "b5":
            self.app.push_screen(SettingsScreen(self.services))

    def action_register(self) -> None:
        self.app.push_screen(RegisterScreen(self.user, self.services))

    def action_sniff(self) -> None:
        self.app.push_screen(SniffScreen(self.user, self.services))

    def action_custom(self) -> None:
        self.app.push_screen(CustomScreen(self.user, self.services))

    def action_calendar(self) -> None:
        self.app.push_screen(CalendarScreen(self.user, self.services))

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen(self.services))

    def action_logout(self) -> None:
        for f in (Config.TOKEN_FILE, Config.LOGIN_FILE, Config.GOOGLE_TOKEN_FILE):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        self.app.exit()


# ---------- register screen ----------


class RegisterScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.courses: List[List[Course]] = []
        self.names: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Label("ĐĂNG KÝ NHANH", id="reg-title")
            with Horizontal():
                yield ToggleSwitch(id="summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Tải danh sách môn", id="load", variant="primary")
                yield Button("Đăng ký môn đã chọn", id="run", variant="success")
                yield Button("Quay lại", id="back")
            yield DataTable(id="courses-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#courses-table", DataTable)
        table.add_columns("STT", "Tên môn", "Mã", "Lớp đầu", "Sĩ số")

    def _is_summer(self) -> bool:
        return self.query_one("#summer", ToggleSwitch).value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "load":
            await self._load_courses()
        elif event.button.id == "run":
            await self._run_sniff()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _load_courses(self) -> None:
        table = self.query_one("#courses-table", DataTable)
        table.clear()
        try:
            self.courses, self.names = await self.services["course"].fetch_courses(
                self.user, self._is_summer()
            )
            for i, name in enumerate(self.names):
                if not self.courses[i]:
                    continue
                c = self.courses[i][0]
                table.add_row(
                    str(i),
                    name,
                    c.code,
                    c.display_name,
                    f"{c.current_students}/{c.max_students}",
                    key=str(i),
                )
        except Exception as e:  # noqa: BLE001
            self.notify(f"Lỗi tải môn: {e}", severity="error")

    async def _run_register(self) -> None:
        table = self.query_one("#courses-table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is None or cursor_row < 0 or not self.courses:
            self.notify("Chưa chọn môn hoặc chưa tải dữ liệu.", severity="warning")
            return
        if cursor_row >= len(self.courses):
            return
        indices = [cursor_row]

        log_screen = LogScreen("Đăng ký nhanh")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]
            try:
                failed = await register.register_subjects(
                    self.user, indices, self.courses, self._is_summer()
                )
                if failed and not ctx.should_stop():
                    ctx.log(f"[AUTO] {len(failed)} môn fail -> chuyển sang sniffing.")
                    register.sniffing_loop(
                        self.user,
                        failed,
                        self._is_summer(),
                        interval=Config.SNIFF_INTERVAL,
                        on_log=ctx.log,
                        should_stop=ctx.should_stop,
                    )
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen.run_async(_work)


# ---------- sniff screen ----------


class SniffScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.courses: List[List[Course]] = []
        self.names: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Label("SNIFFING RIÊNG", id="sniff-title")
            with Horizontal():
                yield ToggleSwitch(id="summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Tải danh sách môn", id="load", variant="primary")
                yield Button("Săn môn đã chọn", id="run", variant="warning")
                yield Button("Quay lại", id="back")
            yield DataTable(id="courses-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#courses-table", DataTable)
        table.add_columns("STT", "Tên môn", "Mã", "Lớp đầu", "Sĩ số")

    def _is_summer(self) -> bool:
        return self.query_one("#summer", ToggleSwitch).value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "load":
            await self._load_courses()
        elif event.button.id == "run":
            await self._run_sniff()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _load_courses(self) -> None:
        table = self.query_one("#courses-table", DataTable)
        table.clear()
        try:
            self.courses, self.names = await self.services["course"].fetch_courses(
                self.user, self._is_summer()
            )
            for i, name in enumerate(self.names):
                if not self.courses[i]:
                    continue
                c = self.courses[i][0]
                table.add_row(
                    str(i),
                    name,
                    c.code,
                    c.display_name,
                    f"{c.current_students}/{c.max_students}",
                    key=str(i),
                )
        except Exception as e:  # noqa: BLE001
            self.notify(f"Lỗi tải môn: {e}", severity="error")

    async def _run_sniff(self) -> None:
        table = self.query_one("#courses-table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is None or cursor_row < 0 or not self.courses:
            self.notify("Chưa chọn môn hoặc chưa tải dữ liệu.", severity="warning")
            return
        targets = self.courses[cursor_row]

        log_screen = LogScreen("Sniffing")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]
            try:
                await register.sniffing_loop(
                    self.user,
                    targets,
                    self._is_summer(),
                    interval=Config.SNIFF_INTERVAL,
                    on_log=ctx.log,
                    should_stop=ctx.should_stop,
                )
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen.run_async(_work)


# ---------- custom profile screen ----------


class CustomScreen(Screen):
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
            yield Label("CUSTOM PROFILE", id="cust-title")
            with Horizontal():
                yield Button("Làm mới", id="refresh")
                yield Button("Chạy file đã chọn", id="run", variant="success")
                yield Button("Xóa file đã chọn", id="delete", variant="error")
                yield Button("Quay lại", id="back")
            yield DataTable(id="files-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#files-table", DataTable)
        table.add_columns("STT", "Tên file")
        self._refresh()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "refresh":
            self._refresh()
        elif event.button.id == "delete":
            self._delete_selected()
        elif event.button.id == "run":
            await self._run_selected()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _refresh(self) -> None:
        table = self.query_one("#files-table", DataTable)
        table.clear()
        for i, f in enumerate(self.custom.list_files()):
            table.add_row(str(i), f, key=f)

    def _delete_selected(self) -> None:
        table = self.query_one("#files-table", DataTable)
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
        table = self.query_one("#files-table", DataTable)
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

        log_screen = LogScreen(f"Custom: {key}")
        self.app.push_screen(log_screen)

        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]
            try:
                failed = await register.register_custom(self.user, target_courses)
                if failed and not ctx.should_stop():
                    ctx.log(f"[AUTO] {len(failed)} môn fail -> chuyển sang sniffing.")
                    await register.sniffing_loop(
                        self.user,
                        failed,
                        False,
                        interval=Config.SNIFF_INTERVAL,
                        on_log=ctx.log,
                        should_stop=ctx.should_stop,
                    )
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
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "ics":
            await self._export_ics()
        elif event.button.id == "google":
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
                # Blocking call -> run in thread to not block event loop
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
        with Container():
            yield Label("SETTINGS", id="set-title")
            with Horizontal():
                yield ToggleSwitch(value=Config.DEBUG, id="debug")
                yield Label("Chế độ Debug")
            with Horizontal():
                yield Label("Interval sniff (giây):")
                yield Input(
                    value=str(Config.SNIFF_INTERVAL),
                    id="interval",
                    placeholder="2.0",
                )
            with Horizontal():
                yield Button("Lưu", id="save", variant="primary")
                yield Button("Đăng xuất", id="logout", variant="error")
                yield Button("Quay lại", id="back")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "save":
            self._save()
        elif event.button.id == "logout":
            for f in (Config.TOKEN_FILE, Config.LOGIN_FILE, Config.GOOGLE_TOKEN_FILE):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass
            self.app.exit()

    def _save(self) -> None:
        dbg = self.query_one("#debug", ToggleSwitch).value
        try:
            interval = float(self.query_one("#interval", Input).value.strip() or "2.0")
            if interval <= 0:
                raise ValueError
        except ValueError:
            self.notify("Interval phải là số dương.", severity="error")
            return
        Config.DEBUG = dbg
        Config.SNIFF_INTERVAL = interval
        self.notify("Đã lưu.", severity="information")


# ---------- main app ----------


class TLUApp(App):
    CSS = """
    /* Catppuccin Macchiato palette */
    Screen {
        background: #24273a;
    }

    /* Rows that contain a ToggleSwitch + Label */
    #save-login-row, #summer-row, #debug-row {
        height: 3;
        align-vertical: middle;
        padding: 1 0 0 0;
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

    /* Register / Sniff / Custom / Calendar screens */
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
        default_save = True
        if os.path.exists(Config.LOGIN_FILE):
            try:
                with open(Config.LOGIN_FILE, "r", encoding="utf-8") as f:
                    default_user = json.load(f).get("username")
                default_save = True
            except Exception:
                default_user = None
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

        self.push_screen(LoginScreen(default_user, default_save), _on_login)


def run_tui() -> None:
    TLUApp().run()
