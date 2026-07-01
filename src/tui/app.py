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
from textual.containers import Container, Horizontal, Vertical
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


# Status states for the in-progress registration table.
STATUS_PENDING = ("⏳", "#a5adcb", "Chờ")
STATUS_SENDING = ("⌛", "#f5a97f", "Đang gửi")
STATUS_SUCCESS = ("✓", "#a6da95", "Thành công")
STATUS_FAILED = ("✗", "#ed8796", "Sĩ số full")
STATUS_SNIFFING = ("⟳", "#8aadf4", "Đang săn")
STATUS_DONE = ("•", "#5b6078", "Sĩ số full, đã săn xong")

# Map key (string from caller) → current status tuple. The screen reads
# this on push so the worker can look up which row to update.
STATUS_KEYS: Dict[str, str] = {}


class LogScreen(Screen):
    """Live-progress screen with a status table (top) + RichLog (bottom).

    Top half: a DataTable showing each subject being registered, with a
    real-time status cell (⏳ pending, ⌛ sending, ✓ success, ✗ failed,
    ⟳ sniffing, • done). Rows are colored by status.

    Bottom half: a RichLog capturing stdout from the worker so existing
    service `print()` calls still show up unchanged.

    Worker coroutines receive a LogCaptureContext that exposes
    `update_status(key, status, message)` to drive the table.
    """

    BINDINGS = [
        Binding("ctrl+c", "stop", "Dừng", show=True),
    ]

    def __init__(
        self,
        title: str = "Logs",
        status_rows: Optional[List[Dict[str, Any]]] = None,
        on_mount_start: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.title_text = title
        self.stop_event = asyncio.Event()
        self.worker_handle = None
        # status_rows: list of {"key", "code", "lich"} dicts to seed the
        # status table on mount. The worker updates rows by key.
        self.status_rows = status_rows or []
        # Internal map: row_key → DataTable row_key
        self._row_keys: Dict[str, str] = {}
        # Callback chạy SAU on_mount (khi screen đã mount + table đã seed).
        # Dùng để start worker ngay khi screen sẵn sàng → tránh race
        # condition: nếu run_async gọi trước khi screen mount, query_one
        # bên trong worker sẽ fail → status không update.
        self._on_mount_start = on_mount_start

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="log-container"):
            yield Label(self.title_text, id="log-title")
            yield DataTable(
                id="status-table",
                zebra_stripes=True,
                cursor_type="row",
            )
            yield RichLog(id="log", highlight=False, markup=False, wrap=False, max_lines=5000)
            with Horizontal(id="log-buttons"):
                yield Button("Dừng", id="stop-btn", variant="error")
                yield Button("Quay lại", id="back-btn", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#status-table", DataTable)
        table.add_columns("STT", "Mã lớp", "Lịch", "Trạng thái", "Tin nhắn")
        # Pre-populate from status_rows
        for i, row in enumerate(self.status_rows, 1):
            icon, _color, label = STATUS_PENDING
            key = row["key"]
            row_key = f"r{i}"
            self._row_keys[key] = row_key
            table.add_row(
                str(i),
                RichText(row["code"] or "—", style="#cad3f5"),
                RichText(row["lich"] or "—", style="#cad3f5"),
                RichText(f"{icon} {label}", style="#a5adcb"),
                RichText("", style="#5b6078"),
                key=row_key,
            )
        # Set column widths so status is visible
        cols = list(table.columns.values())
        widths = [5, 22, 22, 18, None]  # None = auto-size
        for col, w in zip(cols, widths):
            if w is None:
                col.auto_width = True
            else:
                col.auto_width = False
                col.width = w
        self.query_one("#stop-btn", Button).focus()
        # Start worker SAU khi table đã seed (đảm bảo _row_keys đầy đủ).
        if self._on_mount_start is not None:
            try:
                self._on_mount_start()
            except Exception as e:
                print(f"[ERROR] on_mount_start: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "stop-btn":
            self.action_stop()
        elif event.button.id == "back-btn":
            self.app.pop_screen()

    def action_stop(self) -> None:
        self.stop_event.set()
        self.query_one("#log", RichLog).write("[Đã yêu cầu dừng...]")

    def update_status(self, key: str, status: tuple, message: str = "") -> None:
        """Update a status row. `status` is a STATUS_* tuple."""
        row_key_str = self._row_keys.get(key)
        if row_key_str is None:
            return
        try:
            table = self.query_one("#status-table", DataTable)
        except Exception:
            return
        icon, color, label = status
        # Locate the actual RowKey for this row + ColumnKey for each cell
        try:
            row_key = table.rows[row_key_str].key
        except KeyError:
            return
        # columns: list of (ColumnKey, Column) in declaration order
        col_keys = list(table.columns.keys())
        if len(col_keys) < 5:
            return
        status_col, msg_col = col_keys[3], col_keys[4]
        table.update_cell(row_key, status_col, RichText(f"{icon} {label}", style=color))
        if message:
            table.update_cell(row_key, msg_col, RichText(message, style=color))

    def run_async(
        self,
        coro_factory: Callable[["LogCaptureContext"], Awaitable[Any]],
    ) -> None:
        """Run a coroutine in a Textual worker, capturing its stdout."""
        self.stop_event.clear()
        log_widget = self.query_one("#log", RichLog)
        # Expose update_status via the context so workers can drive it
        # without holding a direct reference to the screen.
        ctx = LogCaptureContext(
            log_widget, self.stop_event, update_fn=self.update_status
        )

        async def _runner():
            try:
                with capture_stdout(log_widget):
                    await coro_factory(ctx)
            except asyncio.CancelledError:
                self.stop_event.set()
                log_widget.write("[Đã hủy]")
            except Exception as e:  # noqa: BLE001
                log_widget.write(f"[ERROR] {e}")
            finally:
                # m3 fix: nếu user pop screen trước khi worker xong, #stop-btn
                # không còn tồn tại → NoMatches. Suppress để tránh unhandled
                # worker error.
                try:
                    self.query_one("#stop-btn", Button).label = "Đã xong"
                except Exception:
                    pass

        self.worker_handle = self.app.run_worker(_runner(), exclusive=False)


class LogCaptureContext:
    """Passed to worker coroutines: exposes log/should_stop/update_status."""

    def __init__(self, log_widget: RichLog, stop_event: asyncio.Event,
                 update_fn: Optional[Callable[[str, tuple, str], None]] = None):
        self.log_widget = log_widget
        self.stop_event = stop_event
        self._update_status = update_fn

    def log(self, msg: str) -> None:
        self.log_widget.write(msg)

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def print(self, msg: str) -> None:
        self.log_widget.write(msg)

    def update_status(self, key: str, status: tuple, message: str = "") -> None:
        """Update a status row in the LogScreen table.

        `key` must match a key the screen was seeded with.
        `status` is one of the STATUS_* tuples defined above.
        """
        if self._update_status is not None:
            try:
                self._update_status(key, status, message)
            except Exception:
                pass


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
        # Event set khi user bấm "Thoát" giữa lúc đang bắn login liên tục.
        # Loop login_until_success check event này để thoát sạch.
        self._cancel_event = asyncio.Event()
        self._retrying = False

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
            yield Label("Tùy chọn:", id="login-options-title")
            with Vertical(id="login-options"):
                with Horizontal(id="save-login-row", classes="opt-row"):
                    yield ToggleSwitch(value=self._default_save, id="save-login")
                    yield Label("Lưu đăng nhập")
                yield Static("", classes="opt-spacer")
                with Horizontal(id="offline-mode-row", classes="opt-row"):
                    yield ToggleSwitch(value=False, id="offline-mode")
                    yield Label("Offline (dùng cache)")
                yield Static("", classes="opt-spacer")
                with Horizontal(id="continuous-login-row", classes="opt-row"):
                    yield ToggleSwitch(value=False, id="continuous-login")
                    yield Label("Bắn login liên tục đến khi có token")
            yield RichLog(
                id="login-log",
                highlight=True,
                markup=True,
                wrap=True,
                max_lines=200,
            )
            with Vertical(id="login-buttons"):
                yield Button("Đăng nhập", id="login-btn", variant="primary")
                yield Button("Thoát", id="cancel-btn", variant="default")

    def on_mount(self) -> None:
        if not self._default_user:
            self.query_one("#username", Input).focus()
        else:
            self.query_one("#password", Input).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            if self._retrying:
                # Đang bắn login liên tục → set event để loop thoát.
                # KHÔNG dismiss — user vẫn ở trên screen, có thể thử lại
                # hoặc tắt toggle "Bắn liên tục". Click "Thoát" lần nữa
                # mới dismiss.
                self._cancel_event.set()
                err = self.query_one("#login-log", RichLog)
                err.write("Đang hủy bắn request login...")
            else:
                self.dismiss(None)
            return
        if event.button.id == "login-btn":
            await self._attempt_login()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("username", "password"):
            await self._attempt_login()

    async def _attempt_login(self) -> None:
        # m4 fix: nếu đang retry, ignore trigger login mới (Enter trong
        # input khi _retrying=True) — tránh spawn worker thứ 2 leak client.
        if self._retrying:
            return
        offline = self.query_one("#offline-mode", ToggleSwitch).value
        continuous = self.query_one("#continuous-login", ToggleSwitch).value
        err = self.query_one("#login-log", RichLog)
        self.query_one("#login-btn", Button).disabled = True
        try:
            if offline:
                # Offline ưu tiên cao hơn continuous (offline = 0 API call).
                await self._attempt_offline_login()
            elif continuous:
                await self._attempt_continuous_login()
            else:
                await self._attempt_online_login()
        except Exception as e:  # noqa: BLE001
            err.write(f"Lỗi: {e}")
            self.query_one("#login-btn", Button).disabled = False

    async def _attempt_continuous_login(self) -> None:
        """Bắn request login liên tục cho đến khi thành công / user hủy /
        hết thời gian. Click "Thoát" giữa chừng sẽ set cancel event.
        Log progress vào RichLog — mỗi attempt 1 dòng, auto-scroll.
        Debug mode (Config.DEBUG=True) hiện thêm chi tiết.

        Retry loop chạy trong self.run_worker() để main event loop
        luôn free xử lý UI events (cancel button, repaint, ...).
        on_progress dùng call_later() để schedule write trên event
        loop tiếp theo — tránh race với retry loop đang chạy.
        """
        import time
        u = self.query_one("#username", Input).value.strip()
        p = self.query_one("#password", Input).value
        save = self.query_one("#save-login", ToggleSwitch).value
        log = self.query_one("#login-log", RichLog)
        if not u or not p:
            log.write("[red]Thiếu tên đăng nhập hoặc mật khẩu.[/red]")
            self.query_one("#login-btn", Button).disabled = False
            return
        self._cancel_event.clear()
        self._retrying = True
        log.write("[bold]Đang bắn request login liên tục (lần 1)...[/bold]")
        if Config.DEBUG:
            log.write(f"[dim]  endpoint: {Config.TLU_LOGIN_URL}[/dim]")
        start_ts = time.monotonic()
        client = TLUClient()

        def _write_progress(attempt: int, error_msg: Optional[str]) -> None:
            elapsed = time.monotonic() - start_ts
            ts = time.strftime("%H:%M:%S")
            if error_msg is None:
                log.write(
                    f"[green]✓ [{ts}] Thành công ở lần {attempt} "
                    f"(sau {elapsed:.1f}s)[/green]"
                )
            else:
                log.write(
                    f"[yellow]✗ [{ts}] Lần {attempt} ({elapsed:.1f}s): "
                    f"{error_msg[:80]}[/yellow]"
                )
                if Config.DEBUG:
                    log.write(f"[dim]  raw: {error_msg!r}[/dim]")

        def _do_on_progress(attempt: int, error_msg: Optional[str]) -> None:
            # call_later → schedule trên event loop tiếp theo, tránh
            # ghi RichLog sync trong khi retry loop đang chạy (gây
            # đơ UI trước đây).
            self.call_later(_write_progress, attempt, error_msg)

        async def _retry_worker() -> None:
            """Chạy trong worker — main event loop free cho UI."""
            auth = AuthService(client)
            try:
                user = await auth.login_until_success(
                    u,
                    p,
                    save=save,
                    on_progress=_do_on_progress,
                    should_stop=lambda: self._cancel_event.is_set(),
                )
                # call_later tự await return value của callback. Nếu
                # callback return AwaitComplete (từ dismiss()), Textual
                # raise "Can't await screen.dismiss() from message
                # handler". Fix: wrap trong function thường return None.
                def _do_dismiss(u=user, c=client):
                    self.dismiss({"user": u, "client": c, "offline": False})
                self.call_later(_do_dismiss)
            except Exception as e:  # noqa: BLE001
                if self._cancel_event.is_set():
                    self.call_later(
                        log.write, "[red]Đã hủy bắn request login.[/red]"
                    )
                else:
                    self.call_later(log.write, f"[red]Lỗi: {e}[/red]")
                try:
                    await client.close()
                except Exception:
                    pass
                def _reset_btn():
                    self.query_one("#login-btn", Button).disabled = False
                self.call_later(_reset_btn)
            finally:
                self._retrying = False

        # exclusive=True → không chạy song song với worker khác của screen
        self.run_worker(_retry_worker, exclusive=True)

    async def _attempt_offline_login(self) -> None:
        """Đăng nhập offline: 0 API call, dựng User từ res/user_info.json."""
        err = self.query_one("#login-log", RichLog)
        err.write("Đang tải dữ liệu offline...")
        client = TLUClient()
        try:
            auth = AuthService(client)
            user = await auth.load_offline_user()
            self.dismiss({"user": user, "client": client, "offline": True})
        except Exception as e:  # noqa: BLE001
            err.write(f"Offline lỗi: {e}")
            try:
                await client.close()
            except Exception:
                pass
            self.query_one("#login-btn", Button).disabled = False

    async def _attempt_online_login(self) -> None:
        """Đăng nhập online. Nếu mạng lỗi + có cache user_info.json →
        tự động fallback sang offline (TUI tự động theo đã chốt)."""
        u = self.query_one("#username", Input).value.strip()
        p = self.query_one("#password", Input).value
        save = self.query_one("#save-login", ToggleSwitch).value
        err = self.query_one("#login-log", RichLog)
        if not u or not p:
            err.write("Thiếu tên đăng nhập hoặc mật khẩu.")
            self.query_one("#login-btn", Button).disabled = False
            return
        err.write("Đang đăng nhập...")
        client = TLUClient()
        try:
            auth = AuthService(client)
            try:
                user = await auth.login(u, p, save=save)
                self.dismiss({"user": user, "client": client, "offline": False})
            except Exception as e:  # noqa: BLE001
                # Auto-fallback: lỗi mạng + có user_info.json → vào offline
                if AuthService._is_network_error(e) and os.path.exists(
                    Config.USER_INFO_FILE
                ):
                    err.write("Mất mạng — đang chuyển sang chế độ offline...")
                    try:
                        user = await auth.load_offline_user()
                        self.dismiss(
                            {"user": user, "client": client, "offline": True}
                        )
                        return
                    except Exception as off_e:  # noqa: BLE001
                        err.write(f"Offline fallback lỗi: {off_e}")
                else:
                    err.write(f"Lỗi: {e}")
                try:
                    await client.close()
                except Exception:
                    pass
                self.query_one("#login-btn", Button).disabled = False
        except Exception as e:  # noqa: BLE001
            err.write(f"Lỗi: {e}")
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

    def __init__(self, user: User, services: dict, offline: bool = False):
        super().__init__()
        self.user = user
        self.services = services
        self.offline = offline

    def _cache_age_text(self) -> str:
        """Hiển thị tuổi cache (mtime của all_course.json) — dùng cho banner offline."""
        path = os.path.join(Config.RES_DIR, "all_course.json")
        if not os.path.exists(path):
            return "(chưa có cache)"
        import time
        age_s = time.time() - os.path.getmtime(path)
        if age_s < 60:
            return "vừa tải"
        if age_s < 3600:
            return f"cache {int(age_s // 60)} phút trước"
        if age_s < 86400:
            return f"cache {int(age_s // 3600)} giờ trước"
        return f"cache {int(age_s // 86400)} ngày trước"

    def compose(self) -> ComposeResult:
        if self.offline:
            self.app.title = "AutoDangKiTin TLU [OFFLINE]"
        yield Header()
        with Container(id="menu-container"):
            if self.offline:
                yield Label(
                    f"--- CHẾ ĐỘ OFFLINE ({self._cache_age_text()}) ---",
                    id="offline-banner",
                )
            yield Label(
                f"Xin chào {self.user.full_name} ({self.user.student_id})",
                id="menu-greet",
            )
            yield Label("Chọn chức năng (phím số) hoặc click nút:", id="menu-hint")
            yield Button("1. Đăng ký nhanh (chọn nhiều môn)", id="b1", variant="primary")
            yield Button("2. Tạo danh sách custom", id="b2", variant="warning")
            yield Button("3. Đăng ký theo profile", id="b3")
            yield Button("4. Lịch (ICS / Google)", id="b4", disabled=self.offline)
            yield Button("5. Settings", id="b5")
            with Horizontal(id="menu-footer"):
                yield Button("Thoát", id="exit-btn", variant="default")
                yield Button("Đăng xuất", id="logout-btn", variant="error")
        yield Footer()

    def _notify_need_network(self) -> None:
        self.app.notify(
            "Chức năng này cần kết nối mạng. Đăng xuất và đăng nhập lại online.",
            severity="warning",
            timeout=6,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "b1":
            self.app.push_screen(RegisterScreen(self.user, self.services))
        elif bid == "b2":
            self.app.push_screen(CustomBuilderScreen(self.user, self.services))
        elif bid == "b3":
            self.app.push_screen(ProfileScreen(self.user, self.services))
        elif bid == "b4":
            if self.offline:
                self._notify_need_network()
            else:
                self.app.push_screen(CalendarScreen(self.user, self.services))
        elif bid == "b5":
            self.app.push_screen(SettingsScreen(self.services))
        elif bid == "exit-btn":
            self.app.exit()
        elif bid == "logout-btn":
            for f in (
                Config.TOKEN_FILE,
                Config.LOGIN_FILE,
                Config.GOOGLE_TOKEN_FILE,
                Config.USER_INFO_FILE,
            ):
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
        if self.offline:
            self._notify_need_network()
        else:
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

        # Build seed rows for the LogScreen status table. Key by the
        # subject_idx so the worker can look up rows by idx.
        # Đồng thời build map id(course) → subj_key để sniff status update
        # đúng row (M1: trước đây update dùng course_{id(course)} không khớp).
        status_rows: List[Dict[str, Any]] = []
        course_to_key: Dict[int, str] = {}
        for idx in indices:
            group = self.courses[idx]
            if not group:
                continue
            first = group[0]
            key = f"subj_{idx}"
            status_rows.append({
                "key": key,
                "code": first.code or first.display_name,
                "lich": first.sessions_summary or "—",
            })
            # Map cho tất cả lớp trong môn — sniff có thể trả về lớp bất kỳ
            for course in group:
                course_to_key[id(course)] = key
        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]
            is_summer = self._is_summer()

            def on_start(idx: int, course) -> None:
                key = f"subj_{idx}"
                ctx.update_status(key, STATUS_SENDING,
                                  f"Đang gửi {course.code if course else '?'}...")

            def on_progress(idx: int, success: bool, course) -> None:
                key = f"subj_{idx}"
                if success:
                    msg = f"Đã đăng ký (lớp {course.code if course else '?'})"
                    ctx.update_status(key, STATUS_SUCCESS, msg)
                else:
                    ctx.update_status(key, STATUS_FAILED, "Sĩ số full / lỗi")

            try:
                failed = await register.register_subjects(
                    self.user, indices, self.courses, is_summer,
                    on_start=on_start, on_progress=on_progress,
                )
                if failed and Config.AUTO_SNIFF_FALLBACK and not ctx.should_stop():
                    ctx.log(f"[AUTO] {len(failed)} môn fail -> chuyển sang sniffing.")
                    # M1 fix: dùng course_to_key thay vì course_{id(course)}
                    for course in failed:
                        key = course_to_key.get(id(course))
                        if key:
                            ctx.update_status(key, STATUS_SNIFFING, "Đang săn slot...")
                    sniff_failed = await register.sniffing_loop(
                        self.user,
                        failed,
                        is_summer,
                        interval=Config.SNIFF_INTERVAL,
                        jitter=Config.SNIFF_JITTER,
                        max_duration_min=Config.SNIFF_MAX_DURATION_MIN,
                        on_log=ctx.log,
                        should_stop=ctx.should_stop,
                    )
                    # M2 fix: logic ngược. Course thành công = không còn trong
                    # sniff_failed. Course fail = còn trong sniff_failed.
                    still_failed_ids = {id(c) for c in sniff_failed}
                    for course in failed:
                        key = course_to_key.get(id(course))
                        if not key:
                            continue
                        if id(course) in still_failed_ids:
                            ctx.update_status(key, STATUS_FAILED, "Vẫn fail sau săn")
                        else:
                            ctx.update_status(key, STATUS_SUCCESS, "Đã săn được!")
                elif failed and not Config.AUTO_SNIFF_FALLBACK:
                    ctx.log(f"[INFO] {len(failed)} môn fail. Tự fallback đã TẮT trong Settings.")
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        _push_register_flow(
            self.app, self, self.user, self.services, self._is_summer(),
            log_title="Đăng ký nhanh", status_rows=status_rows,
            work_factory=_work,
        )


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

        def _truncate(text: str, max_len: int) -> str:
            if len(text) <= max_len:
                return text
            if max_len <= 1:
                return text[:max_len]
            return text[: max_len - 1] + "…"

        # ---------- 1) Build all cell data first ----------
        # We need every cell's content before we can size the columns.
        rows_data: List[Dict[str, Any]] = []
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

            # Cell values — no client-side truncation; widths will scale
            # to fit instead so users always see the full info.
            sessions = opt.sessions_summary or "—"
            # Use the class code (e.g. "252061_SCSO232_65KTRB") instead of
            # the verbose displayName ("Chủ nghĩa xã hội khoa học-2-25
            # (65KTRB)"). The subject is already in the modal title; the
            # class code is the unique identifier users need.
            name = opt.code or opt.display_name
            sisos = f"{mark} {opt.current_students}/{opt.max_students}"
            gv = opt.teacher_name or "—"

            rows_data.append({
                "i": i,
                "raw": [sisos, name, sessions, gv],
                "style": style,
                "chosen": chosen,
                "conflict": conflict,
            })

        # ---------- 2) Truncate each cell to a per-column cap, then size ----------
        # Per-column hard caps (cells longer than this get truncated with
        # ellipsis). Order: [marker, name, lich, gv]
        # Larger caps so each column has more room; scaling below will
        # shrink columns if the total overflows the screen.
        caps = [8, 40, 36, 28]
        headers = ["✓ N/M", "Tên lớp", "Lịch", "GV"]
        for row in rows_data:
            row["truncated"] = [_truncate(val, caps[c]) for c, val in enumerate(row["raw"])]

        natural = [max(len(headers[c]), 4) for c in range(4)]
        for row in rows_data:
            for c, val in enumerate(row["truncated"]):
                if len(val) > natural[c]:
                    natural[c] = len(val)
        # Add 2 padding chars per column (DataTable reserves 1 on each side)
        natural = [w + 2 for w in natural]

        # ---------- 3) Scale to available width ----------
        # Picker modal: 98% width, padding 1, border 1, container padding 1
        # → 4 chars of chrome total. Subtract from screen width.
        screen_w = max(40, self.app.size.width) if self.app.size else 80
        available = max(40, int(screen_w) - 4)
        total = sum(natural)
        widths = list(natural)
        if total > available:
            # Tên lớp shrinks first (descriptive only). Lịch + GV are
            # info-dense — protect them. Marker is fixed.
            shrink_priority = [0, 3, 1, 1]
            order = sorted(range(4), key=lambda c: (-shrink_priority[c], -widths[c]))
            for c in order:
                if sum(widths) <= available:
                    break
                can_shrink = widths[c] - 4
                shrink = min(can_shrink, sum(widths) - available)
                if shrink > 0:
                    widths[c] -= shrink
        # Always distribute the FULL available width across columns,
        # even when content is small — avoids the "lots of empty space,
        # content still truncated" feel.
        slack = available - sum(widths)
        if slack > 0:
            # Give extra space to the columns that can benefit most
            # (Lịch and GV, which hold the most info-dense data).
            grow_order = [2, 3, 1, 0]  # lich, gv, name, marker
            for c in grow_order:
                if slack <= 0:
                    break
                give = min(slack, 6)  # cap per column to avoid one hog
                widths[c] += give
                slack -= give

        # ---------- 4) Add columns ----------
        # DataTable in Textual 8.2.7 doesn't respect fixed width hints —
        # it auto-sizes based on content. So we add columns WITHOUT width,
        # and pre-truncate cells to match our computed widths (so the
        # auto-sizer never sees longer content than we want). The result
        # is columns that fit exactly the truncated content + padding,
        # which is what the user actually wants to see.
        keys = ["col-pick", "col-name", "col-lich", "col-gv"]
        for header, w, k in zip(headers, widths, keys):
            table.add_column(header, key=k)

        # ---------- 5) Add rows (cells already truncated in step 2) ----------
        cursor_target = 0
        for row in rows_data:
            cells = [RichText(t, style=row["style"]) for t in row["truncated"]]
            table.add_row(*cells, key=str(row["i"]))
            if row["chosen"]:
                cursor_target = row["i"]
            elif cursor_target == 0 and not row["conflict"] and not row["chosen"]:
                cursor_target = row["i"]

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
        self.picks: Dict[int, Course] = {}

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
        self.picks.clear()
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
        """Cell text for 'Lớp đã chọn'. Multi-line: code + sessions + dates.

        Uses the class code (short) on line 1, then sessions and date
        range on the following lines. The full subject name is already
        shown in the subject row column, so it would be redundant here.
        """
        if not sel:
            return "---"
        head = sel.code or sel.display_name
        lines = [head]
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
            sel = self.picks.get(i)
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
        other = [c for k, c in self.picks.items() if k != subject_idx]
        current = self.picks.get(subject_idx)

        def _on_pick(picked: Optional[Course]) -> None:
            if picked is not None:
                self.picks[subject_idx] = picked
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
        if subject_idx is not None and subject_idx in self.picks:
            del self.picks[subject_idx]
            self._refresh_table()

    def _save(self) -> None:
        if not self.picks:
            self.notify("Chưa chọn lớp nào.", severity="warning")
            return
        name = self.query_one("#save-name", Input).value
        # Lưu kèm semester_id tương ứng với toggle HK hè đang bật, để
        # khi load profile biết đăng ký cho HK nào.
        is_summer = self._is_summer()
        sem_id = self.user.semester_summer_id if is_summer else self.user.semester_id
        filename = self.custom.save_named(list(self.picks.values()), name,
                                         semester_id=sem_id)
        hk = "HK hè" if is_summer else "HK chính"
        self.notify(f"Đã lưu: {filename} ({hk})", severity="information")
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
        table.add_columns("STT", "Tên file", "Học kỳ", "Số môn")
        # Sizing: Tên file dài nhất, Học kỳ ngắn, Số môn ngắn.
        cols = list(table.columns.values())
        widths = [5, 32, 14, 8]
        for col, w in zip(cols, widths):
            col.auto_width = False
            col.width = w
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

    @staticmethod
    def _format_hk_label(saved_sem_id: Optional[int], main_sem_id: int, summer_sem_id: int) -> str:
        """Format the HK cell: 'HK chính (66)' / 'HK hè (78)' / '? (123)' / 'không rõ'."""
        if saved_sem_id is None:
            return "không rõ"
        if saved_sem_id == main_sem_id:
            return f"HK chính ({main_sem_id})"
        if saved_sem_id == summer_sem_id:
            return f"HK hè ({summer_sem_id})"
        return f"? ({saved_sem_id})"

    def _refresh(self) -> None:
        table = self.query_one("#profile-table", DataTable)
        table.clear()
        for i, f in enumerate(self.custom.list_files()):
            path = os.path.join(Config.RES_DIR, "custom", f)
            try:
                saved_sem_id, courses = CustomService.load_profile(path)
            except Exception:
                saved_sem_id, courses = None, []
            hk_label = self._format_hk_label(
                saved_sem_id, self.user.semester_id, self.user.semester_summer_id
            )
            table.add_row(
                str(i), f, hk_label, str(len(courses)), key=f,
            )

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
            saved_sem_id, target_courses = CustomService.load_profile(path)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Lỗi đọc file: {e}", severity="error")
            return

        # Decide which semester to register into:
        # - If the profile saved a semester_id, use it.
        # - If no saved id (legacy file), use the current user's main semester.
        # - If saved id doesn't match any known semester, use the closest.
        if saved_sem_id == self.user.semester_summer_id:
            is_summer = True
        elif saved_sem_id == self.user.semester_id:
            is_summer = False
        elif saved_sem_id is None:
            is_summer = False
        else:
            # Unknown id — fall back to main and warn
            self.notify(
                f"⚠ Profile lưu semester_id={saved_sem_id} (không phải HK hiện tại). "
                f"Mặc định dùng HK chính.",
                severity="warning",
            )
            is_summer = False
        active_sem_id = saved_sem_id if saved_sem_id is not None else self.user.semester_id
        hk_label = "HK hè" if is_summer else "HK chính"
        self.notify(
            f"Đang đăng ký profile: {key} ({hk_label}, id={active_sem_id})",
            severity="information",
        )

        # Seed the LogScreen status table with one row per target course.
        status_rows: List[Dict[str, Any]] = [
            {
                "key": f"course_{id(c)}",
                "code": c.code or c.display_name,
                "lich": c.sessions_summary or "—",
            }
            for c in target_courses
        ]
        log_screen_title = f"Profile: {key} ({hk_label})"

        async def _work(ctx: LogCaptureContext):
            register: RegisterService = self.services["register"]

            def on_start(course) -> None:
                key = f"course_{id(course)}"
                ctx.update_status(key, STATUS_SENDING, f"Đang gửi {course.code}...")

            def on_progress(course, success: bool) -> None:
                key = f"course_{id(course)}"
                if success:
                    ctx.update_status(key, STATUS_SUCCESS, f"Đã đăng ký (lớp {course.code})")
                else:
                    ctx.update_status(key, STATUS_FAILED, "Sĩ số full / lỗi")

            try:
                failed = await register.register_custom_for_semester(
                    self.user, target_courses, semester_id=active_sem_id,
                    on_start=on_start, on_progress=on_progress,
                )
                if failed and Config.AUTO_SNIFF_FALLBACK and not ctx.should_stop():
                    ctx.log(f"[AUTO] {len(failed)} môn fail -> chuyển sang sniffing.")
                    for course in failed:
                        ctx.update_status(
                            f"course_{id(course)}", STATUS_SNIFFING, "Đang săn slot..."
                        )
                    sniff_failed = await register.sniffing_loop(
                        self.user,
                        failed,
                        is_summer=is_summer,
                        interval=Config.SNIFF_INTERVAL,
                        jitter=Config.SNIFF_JITTER,
                        max_duration_min=Config.SNIFF_MAX_DURATION_MIN,
                        on_log=ctx.log,
                        should_stop=ctx.should_stop,
                    )
                    # M2 fix: course KHÔNG còn trong sniff_failed = săn được →
                    # SUCCESS. Còn trong sniff_failed = vẫn fail → FAILED.
                    # Trước đây ternary ngược: sniff xong hết vẫn báo "vẫn fail".
                    still_failed_ids = {id(c) for c in sniff_failed}
                    for course in failed:
                        if id(course) in still_failed_ids:
                            ctx.update_status(
                                f"course_{id(course)}", STATUS_FAILED,
                                "Săn xong vẫn fail",
                            )
                        else:
                            ctx.update_status(
                                f"course_{id(course)}", STATUS_SUCCESS,
                                "Săn được slot!",
                            )
                elif failed and not Config.AUTO_SNIFF_FALLBACK:
                    ctx.log(f"[INFO] {len(failed)} môn fail. Tự fallback đã TẮT trong Settings.")
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        _push_register_flow(
            self.app, self, self.user, self.services, is_summer,
            log_title=log_screen_title, status_rows=status_rows,
            work_factory=_work,
        )


# ---------- countdown screen (schedule) ----------


def _push_register_flow(
    app,
    source_screen: Optional[Screen] = None,
    user: Optional[User] = None,
    services: Optional[dict] = None,
    is_summer: bool = False,
    log_title: str = "Logs",
    status_rows: Optional[List[Dict[str, Any]]] = None,
    work_factory: Optional[Callable[[LogCaptureContext, "TLUApp"], Awaitable[Any]]] = None,
) -> None:
    """Push đăng kí flow: nếu SCHEDULE_ENABLED + còn thời gian, hiện
    CountdownScreen trước rồi mới push LogScreen. Ngược lại, push
    LogScreen thẳng.

    `source_screen` dùng để check user còn ở flow đăng kí không (chưa
    pop về menu). Nếu user đã navigate away, KHÔNG push LogScreen.

    Chạy toàn bộ flow trong một worker (cần thiết cho push_screen_wait).
    `work_factory(ctx)` là coroutine sẽ chạy trong worker của LogScreen.
    """
    async def _worker():
        def _go_log():
            # Không push LogScreen nếu user đã navigate away khỏi flow
            # đăng kí (đã pop source_screen). Nếu cứ push, LogScreen sẽ
            # hiện đè lên menu hoặc các màn khác → UX lộn xộn.
            if source_screen is not None and source_screen not in app.screen_stack:
                print(f"[INFO] User navigated away, skip pushing LogScreen")
                return None
            # Push LogScreen (không await push_screen_wait — sẽ treo
            # vĩnh viễn vì LogScreen chỉ dismiss khi user click 'Quay
            # lại' hoặc khi worker kết thúc, mà worker chưa chạy).
            # Truyền on_mount_start = lambda chạy log_screen.run_async SAU
            # khi screen đã mount xong (table đã seed rows) → tránh race
            # condition: query_one trong worker sẽ không fail nữa.
            log_screen = LogScreen(
                log_title,
                status_rows=status_rows or [],
                on_mount_start=lambda: log_screen.run_async(work_factory),
            )
            app.push_screen(log_screen)
            return log_screen

        if not Config.SCHEDULE_ENABLED:
            _go_log()
            return

        course_svc: CourseService = services.get("course")
        try:
            target_ms = (
                await course_svc.get_registration_start(user, is_summer)
                if course_svc is not None
                else None
            )
        except Exception as e:
            print(f"[WARN] get_registration_start: {e}")
            target_ms = None
        if target_ms is None or target_ms <= 0:
            _go_log()
            return

        now_ms = int(_time_now() * 1000)
        lead = max(0, int(Config.SCHEDULE_LEAD_SECONDS))
        launch_ms = target_ms - lead * 1000
        if launch_ms <= now_ms:
            _go_log()
            return

        # CountdownScreen sẽ set fired[0]=True khi tới giờ, False khi hủy.
        fired = [False]

        def _on_done():
            fired[0] = True

        def _on_cancel():
            fired[0] = False

        cd = CountdownScreen(
            target_epoch_ms=target_ms,
            lead_seconds=lead,
            on_done=_on_done,
            on_cancel=_on_cancel,
            title=log_title,
        )
        app.push_screen(cd)
        # Chờ CountdownScreen dismiss (timer fire / user hủy / parent pop).
        # dismissed event được set trong _dismiss_with HOẶC on_unmount
        # → không bao giờ treo vĩnh viễn.
        await cd.dismissed.wait()
        # Nếu tới giờ → push LogScreen. Nếu user hủy → về menu.
        if fired[0]:
            _go_log()
        # else: cancelled, do nothing (back to menu)

    app.run_worker(_worker(), exclusive=False)


class CountdownScreen(ModalScreen):
    """Đếm ngược đến thời điểm mở đăng kí (lấy từ API).

    Hiển thị thời gian còn lại, target time, và lead time. Khi tới
    `target - lead_seconds`, sẽ tự gọi `on_done()` để push LogScreen.

    Có nút Hủy để thoát về menu. Có thể set thời gian mục tiêu qua
    tham số `target_epoch_ms` (epoch ms từ API).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Hủy"),
    ]

    def __init__(
        self,
        target_epoch_ms: int,
        lead_seconds: int,
        on_done: Callable[[], None],
        on_cancel: Optional[Callable[[], None]] = None,
        title: str = "HẸN GIỜ ĐĂNG KÝ",
    ):
        super().__init__()
        self.target_ms = int(target_epoch_ms)
        self.lead_seconds = int(lead_seconds)
        # Launch moment = target - lead (nếu <= now thì launch ngay)
        self.launch_ms = self.target_ms - self.lead_seconds * 1000
        self._on_done = on_done
        self._on_cancel = on_cancel
        self.title_text = title
        self._timer_handle = None
        self._fired = False
        # asyncio.Event set khi screen bị dismiss (timeout hoặc cancel).
        # Worker chờ event này thay vì dùng dismissed_event (không có
        # trong Textual 8.x).
        self.dismissed = asyncio.Event()

    def compose(self) -> ComposeResult:
        with Container(id="countdown-container"):
            yield Label(self.title_text, id="countdown-title")
            yield Label("—:—:—", id="countdown-clock")
            yield Label("", id="countdown-target")
            yield Label("", id="countdown-lead")
            with Horizontal(id="countdown-buttons"):
                yield Button("Hủy hẹn giờ (Esc)", id="countdown-cancel", variant="error")

    def on_mount(self) -> None:
        self._tick()
        # Auto-refresh mỗi 0.2s. Dùng timer Textual (sync) để không cần
        # tạo coroutine riêng — vẫn update UI realtime.
        self._timer_handle = self.set_interval(0.2, self._tick)

    def on_unmount(self) -> None:
        if self._timer_handle is not None:
            self._timer_handle.stop()
        # Đảm bảo worker không hang: nếu screen bị dismiss bởi parent
        # (user pop Register khi countdown đang chạy) thì custom event
        # dismissed cũng phải set. _dismiss_with chỉ set khi user click
        # Hủy hoặc timer fire; nếu parent pop thì Textual dismiss
        # screen mà không gọi _dismiss_with → worker sẽ chờ vĩnh viễn.
        self.dismissed.set()

    def _now_ms(self) -> int:
        return int(_time_now() * 1000)

    def _tick(self) -> None:
        now = self._now_ms()
        remaining = self.launch_ms - now
        clock = self.query_one("#countdown-clock")
        target_lbl = self.query_one("#countdown-target")
        lead_lbl = self.query_one("#countdown-lead")
        if remaining <= 0:
            clock.update("[bold #a6da95]ĐÃ ĐẾN GIỜ — bắt đầu đăng ký![/]")
            target_lbl.update("")
            lead_lbl.update("")
            if not self._fired:
                self._fired = True
                if self._timer_handle is not None:
                    self._timer_handle.stop()
                self._dismiss_with(self._on_done)
            return
        # Format HH:MM:SS
        total_sec = remaining // 1000
        hh, rem = divmod(total_sec, 3600)
        mm, ss = divmod(rem, 60)
        clock.update(f"[bold #f5a97f]{hh:02d}:{mm:02d}:{ss:02d}[/]")
        target_str = _format_epoch(self.target_ms)
        target_lbl.update(f"Mở đăng kí lúc: [cyan]{target_str}[/]  (target epoch: {self.target_ms})")
        lead_lbl.update(
            f"Lead time: [yellow]{self.lead_seconds}s[/]  →  "
            f"Auto-launch lúc: [cyan]{_format_epoch(self.launch_ms)}[/]"
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "countdown-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        if self._timer_handle is not None:
            self._timer_handle.stop()
        self._dismiss_with(self._on_cancel)

    def _dismiss_with(self, callback: Optional[Callable[[], None]]) -> None:
        """Dismiss + set dismissed event + invoke callback. Idempotent."""
        if self.dismissed.is_set():
            return
        self.dismiss()
        self.dismissed.set()
        if callback is not None:
            try:
                callback()
            except Exception as e:
                print(f"[ERROR] countdown callback: {e}")


def _time_now() -> float:
    """Wrapper for time.time() — easy to monkey-patch in tests."""
    import time as _t
    return _t.time()


def _format_epoch(ms: int) -> str:
    """Format epoch ms as 'YYYY-MM-DD HH:MM:SS' in local time."""
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"<{ms}>"


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
        # m5 fix: dùng on_mount_start để tránh race — run_async gọi
        # query_one("#log") có thể fail nếu screen chưa mount xong.
        async def _work(ctx: LogCaptureContext):
            cal: CalendarService = self.services["calendar"]
            try:
                path = await cal.export_ics(self.user)
                ctx.log(f"Đã tạo: {path}")
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[ERROR] {e}")

        log_screen = LogScreen(
            "Xuất ICS",
            on_mount_start=lambda: log_screen.run_async(_work),
        )
        self.app.push_screen(log_screen)

    async def _sync_google(self) -> None:
        # m5 fix: dùng on_mount_start để tránh race.
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

        log_screen = LogScreen(
            "Đồng bộ Google",
            on_mount_start=lambda: log_screen.run_async(_work),
        )
        self.app.push_screen(log_screen)


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
            with Horizontal(id="row-auto-sniff", classes="settings-row"):
                yield ToggleSwitch(value=Config.AUTO_SNIFF_FALLBACK, id="auto-sniff")
                yield Label("Tự fallback sang sniffing khi đăng ký fail")
            with Horizontal(id="row-debug", classes="settings-row"):
                yield ToggleSwitch(value=Config.DEBUG, id="debug")
                yield Label("Chế độ Debug")
            with Horizontal(id="row-burst", classes="settings-row"):
                yield Label("Số request song song / lần thử (BURST):")
                yield Input(value=str(Config.BURST_COUNT), id="burst")
            with Horizontal(id="row-concurrency", classes="settings-row"):
                yield Label("Giới hạn đồng thời (CONCURRENCY):")
                yield Input(value=str(Config.CONCURRENCY_LIMIT), id="concurrency")
            with Horizontal(id="row-interval", classes="settings-row"):
                yield Label("Interval sniff (giây):")
                yield Input(value=str(Config.SNIFF_INTERVAL), id="interval")
            with Horizontal(id="row-jitter", classes="settings-row"):
                yield Label("Jitter sniff (giây, ±):")
                yield Input(value=str(Config.SNIFF_JITTER), id="jitter")
            with Horizontal(id="row-max-duration", classes="settings-row"):
                yield Label("Giới hạn thời gian sniff (phút, 0 = vô hạn):")
                yield Input(value=str(Config.SNIFF_MAX_DURATION_MIN), id="max_duration")
            with Horizontal(id="row-schedule-enabled", classes="settings-row"):
                yield ToggleSwitch(value=Config.SCHEDULE_ENABLED, id="schedule-enabled")
                yield Label("Bật hẹn giờ (đếm ngược tới lúc mở đăng kí)")
            with Horizontal(id="row-schedule-lead", classes="settings-row"):
                yield Label("Lead time (giây trước khi auto-launch):")
                yield Input(value=str(Config.SCHEDULE_LEAD_SECONDS), id="schedule-lead")
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

    def _parse_int(self, widget_id: str, label: str, allow_zero: bool = False) -> Optional[int]:
        """Parse a positive int input. If allow_zero=True, 0 is also valid
        (used for SNIFF_MAX_DURATION_MIN where 0 = infinite)."""
        raw = self.query_one(f"#{widget_id}", Input).value.strip()
        try:
            v = int(raw)
            if v < 0 or (v == 0 and not allow_zero):
                raise ValueError
            return v
        except ValueError:
            if allow_zero:
                self.notify(
                    f"{label} phải là số nguyên >= 0 (0 = vô hạn).",
                    severity="error",
                )
            else:
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
        schedule_enabled = self.query_one("#schedule-enabled", ToggleSwitch).value
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
        max_dur = self._parse_int("max_duration", "Max sniff duration", allow_zero=True)
        if max_dur is None:
            return
        lead = self._parse_int("schedule-lead", "Schedule lead time", allow_zero=True)
        if lead is None:
            return

        Config.AUTO_SNIFF_FALLBACK = auto_sniff
        Config.DEBUG = debug
        Config.BURST_COUNT = burst
        Config.CONCURRENCY_LIMIT = conc
        Config.SNIFF_INTERVAL = interval
        Config.SNIFF_JITTER = jitter
        Config.SNIFF_MAX_DURATION_MIN = max_dur
        Config.SCHEDULE_ENABLED = schedule_enabled
        Config.SCHEDULE_LEAD_SECONDS = lead
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
    #save-login-row, #summer-row, #debug-row, .settings-row {
        height: 3;
        align-vertical: middle;
        padding: 1 0 0 0;
    }
    .settings-row Label {
        width: auto;
        padding: 0 1 0 0;
    }
    .settings-row Input {
        width: 24;
    }

    /* Countdown screen (schedule) */
    #countdown-container {
        align: center middle;
        padding: 2 4;
        width: 60%;
        height: auto;
        background: #1e2030;
        border: round #c6a0f6;
    }
    #countdown-title {
        text-style: bold;
        color: #c6a0f6;
        text-align: center;
        padding-bottom: 1;
    }
    #countdown-clock {
        text-align: center;
        text-style: bold;
        color: #f5a97f;
        padding: 1 0;
    }
    #countdown-target, #countdown-lead {
        text-align: center;
        color: #a5adcb;
    }
    #countdown-buttons {
        align-horizontal: center;
        padding-top: 1;
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
    #login-options-title {
        margin-top: 1;
        color: #a6adc8;
        text-style: bold;
    }
    #login-options {
        height: auto;
        margin-bottom: 1;
    }
    .opt-row {
        height: 1;
        margin: 0;
        padding: 0;
    }
    /* Spacer widget giữa các opt-row để gap đều tuyệt đối, không phụ
       thuộc margin collapse hay :last-child selector. */
    .opt-spacer {
        height: 1;
    }
    #login-buttons {
        height: auto;
        width: 100%;
    }
    #login-buttons Button {
        width: 100%;
        margin: 0 0 1 0;
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
    #login-log {
        height: 8;
        min-height: 5;
        width: 100%;
        border: round #5b6078;
        background: #181926;
        margin: 1 0;
        padding: 0 1;
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
    #status-table {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
        border: round #5b6078;
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

    async def on_unmount(self) -> None:
        """Close TLUClient khi app thoát để tránh leak httpx session
        (warning 'Unclosed client session / Unclosed connector').
        """
        if self.client is not None:
            try:
                await self.client.close()
            except Exception:
                pass

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
            offline: bool = result.get("offline", False)
            self.services = {
                "client": self.client,
                "auth": AuthService(self.client),
                "course": CourseService(self.client),
                "register": RegisterService(self.client),
                "calendar": CalendarService(self.client),
                "custom": CustomService(),
            }
            self.push_screen(MenuScreen(user, self.services, offline=offline))

        self.push_screen(
            LoginScreen(default_user, default_save, default_password),
            _on_login,
        )


def run_tui() -> None:
    TLUApp().run()
