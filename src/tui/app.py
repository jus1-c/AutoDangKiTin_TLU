"""
AutoDangKiTin TLU - Textual TUI

Async-native terminal interface (replaces the hand-rolled ANSI TUI).

Menu (6 items + 2 footer buttons):
  1. Đăng ký nhanh        — multi-select (SelectionList) → burst register
                              → auto-sniff fails if AUTO_SNIFF_FALLBACK on
  2. Tạo danh sách custom — chọn lớp, lớp trùng lịch bị xám + không pick,
                              lưu file res/custom/{name}.json
                              (tên rỗng → custom_{time}.json)
  3. Đăng ký theo profile — load file JSON đã lưu → đăng ký
  4. Lịch                 — export ICS + sync Google Calendar
  5. Settings             — debug, interval, jitter, fallback toggle,
                              burst count, concurrency, đăng xuất
  6. Multi-account (tạo)  — tạo file multireg (chỉ tạo — chạy bằng CLI:
                              `autodktin multireg run <file>`)
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
    Rule,
    Select,
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
from src.services.multireg_service import (
    MultiRegAccount,
    MultiRegConfig,
    MultiRegService,
)
from src.services.register_service import RegisterService
from src.services.transfer_service import TransferService


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
        container = Container(id="log-container")
        container.border_title = self.title_text
        with container:
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
        container = Container(id="login-container")
        container.border_title = "ĐĂNG NHẬP"
        with container:
            yield Label("Mã sinh viên:", classes="login-field-label")
            yield Input(value=self._default_user, id="username", placeholder="Mã sinh viên")
            yield Label("Mật khẩu:", classes="login-field-label")
            yield Input(
                value=self._default_password,
                password=True,
                id="password",
                placeholder="Mật khẩu",
            )
            yield Rule(line_style="dashed")
            with Vertical(id="login-options"):
                with Horizontal(id="save-login-row", classes="opt-row"):
                    yield ToggleSwitch(value=self._default_save, id="save-login")
                    yield Label("Lưu đăng nhập")
                with Horizontal(id="offline-mode-row", classes="opt-row"):
                    yield ToggleSwitch(value=False, id="offline-mode")
                    yield Label("Offline (dùng cache)")
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
        Binding("6", "multireg", "Multi-account"),
        Binding("7", "transfer", "Chuyển lớp"),
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
        container = Container(id="menu-container")
        container.border_title = "AutoDangKiTin TLU"
        with container:
            if self.offline:
                yield Label(
                    f"⚠ OFFLINE ({self._cache_age_text()})",
                    id="offline-banner",
                )
            yield Label(
                f"Xin chào {self.user.full_name} ({self.user.student_id})",
                id="menu-greet",
            )
            yield Label("Đăng ký", classes="menu-section")
            yield Button("1. Đăng ký nhanh (chọn nhiều môn)", id="b1", variant="primary")
            yield Button("2. Tạo danh sách custom", id="b2", variant="warning")
            yield Button("3. Đăng ký theo profile", id="b3")
            yield Rule()
            yield Label("Công cụ", classes="menu-section")
            yield Button("4. Lịch (ICS / Google)", id="b4", disabled=self.offline)
            yield Button("5. Settings", id="b5")
            yield Rule()
            yield Label("Nâng cao", classes="menu-section")
            yield Button("6. Multi-account (tạo file, chạy bằng CLI)", id="b6")
            yield Button("7. Chuyển lớp giữa 2 account", id="b7")
            yield Rule()
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
        elif bid == "b6":
            self.app.push_screen(MultiRegListScreen(self.user, self.services))
        elif bid == "b7":
            self.app.push_screen(TransferScreen(self.user, self.services))
        elif bid == "b7":
            self.app.push_screen(TransferScreen(self.user, self.services))
        elif bid == "b7":
            self.app.push_screen(TransferScreen(self.user, self.services))
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

    def action_multireg(self) -> None:
        self.app.push_screen(MultiRegListScreen(self.user, self.services))

    def action_transfer(self) -> None:
        self.app.push_screen(TransferScreen(self.user, self.services))


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
        container = Container(id="reg-container")
        container.border_title = "ĐĂNG KÝ NHANH"
        with container:
            with Horizontal(id="reg-toolbar"):
                yield ToggleSwitch(id="summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Tải danh sách môn", id="load", variant="primary")
            with Horizontal(id="reg-toolbar2"):
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
        container = Container(id="picker-container")
        container.border_title = f"Chọn lớp: {self.subject_name}"
        with container:
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
        container = Container()
        container.border_title = "TẠO DANH SÁCH CUSTOM"
        with container:
            with Horizontal(id="builder-toolbar"):
                yield ToggleSwitch(id="summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Tải môn", id="load", variant="primary")
            with Horizontal(id="builder-toolbar2"):
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
        container = Container(id="profile-container")
        container.border_title = "ĐĂNG KÝ THEO PROFILE"
        with container:
            with Horizontal(id="profile-toolbar"):
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


# ---------- multireg screens (chỉ tạo/quản lý file — CHẠY BẰNG CLI) ----------


class MultiRegListScreen(Screen):
    """List file multireg trong res/multireg/. Tạo mới / xóa / xem chi tiết.

    KHÔNG chạy được từ TUI theo yêu cầu — user phải dùng CLI:
        autodktin multireg run <file>
    """

    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.svc = MultiRegService()

    def compose(self) -> ComposeResult:
        yield Header()
        container = Container()
        container.border_title = "MULTI-ACCOUNT REGISTER"
        with container:
            yield Label(
                "Chọn file rồi bấm Chạy (hoặc dùng CLI: autodktin multireg run <file>).",
                id="multireg-hint", markup=False,
            )
            with Horizontal(id="multireg-toolbar"):
                yield Button("Chạy file đã chọn", id="run", variant="success")
                yield Button("Tạo file mới", id="new", variant="primary")
                yield Button("Làm mới", id="refresh")
                yield Button("Xóa file đã chọn", id="delete", variant="error")
                yield Button("Quay lại", id="back")
            yield DataTable(
                id="multireg-table", zebra_stripes=True, cursor_type="row"
            )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#multireg-table", DataTable)
        table.add_columns("STT", "Tên file", "Đợt", "Số acc", "Shared profile")
        cols = list(table.columns.values())
        widths = [5, 28, 20, 8, 24]
        for col, w in zip(cols, widths):
            col.auto_width = False
            col.width = w
        self._refresh()

    def _refresh(self) -> None:
        table = self.query_one("#multireg-table", DataTable)
        table.clear()
        for i, f in enumerate(self.svc.list_files()):
            try:
                cfg = self.svc.load(f)
                table.add_row(
                    str(i), f, cfg.name or "?", str(len(cfg.accounts)),
                    cfg.shared_profile or "—", key=f,
                )
            except Exception as e:  # noqa: BLE001
                table.add_row(str(i), f, f"lỗi: {e}", "?", "—", key=f)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self.app.pop_screen()
        elif bid == "refresh":
            self._refresh()
        elif bid == "new":
            self.app.push_screen(
                MultiRegBuilderScreen(self.user, self.services),
                self._on_builder_done,
            )
        elif bid == "delete":
            self._delete_selected()
        elif bid == "run":
            self._run_selected()

    def _run_selected(self) -> None:
        """Load file đã chọn → mở MultiRegRunScreen chạy trong TUI."""
        table = self.query_one("#multireg-table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Chưa chọn file để chạy.", severity="warning")
            return
        files = self.svc.list_files()
        if table.cursor_row >= len(files):
            return
        key = files[table.cursor_row]
        try:
            cfg = self.svc.load(key)
        except Exception as e:  # noqa: BLE001
            self.notify(f"Đọc file lỗi: {e}", severity="error")
            return
        if not cfg.accounts:
            self.notify("File không có account nào.", severity="warning")
            return
        self.app.push_screen(
            MultiRegRunScreen(cfg, key, self.services)
        )

    def _on_builder_done(self, result: Optional[str]) -> None:
        """Callback khi builder scren dismiss. result = filename hoặc None."""
        if result:
            self.notify(f"Đã lưu: {result}", severity="information")
        self._refresh()

    def _delete_selected(self) -> None:
        table = self.query_one("#multireg-table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Chưa chọn file.", severity="warning")
            return
        files = self.svc.list_files()
        if table.cursor_row >= len(files):
            return
        key = files[table.cursor_row]
        self.svc.delete(key)
        self._refresh()
        self.notify(f"Đã xóa {key}", severity="information")

    def action_back(self) -> None:
        self.app.pop_screen()


class MultiRegRunScreen(Screen):
    """Chạy 1 file multireg TRONG TUI (song song N account).

    Layout:
      - DataTable (trên): mỗi account 1 row — STT | MSV | Trạng thái | Kết quả.
        Trạng thái cập nhật realtime từ on_progress; Kết quả điền khi xong.
      - RichLog (giữa): capture stdout của service (mọi print `[user] ...`).
      - Hàng nút (dưới): Dừng (set stop, ở lại) / Quay lại (set stop + pop).

    Cả 2 nút set stop_event → truyền should_stop vào svc.run → cắt sniffing.
    """

    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
        Binding("ctrl+c", "stop", "Dừng", show=True),
    ]

    # Map event_type từ on_progress → STATUS_* tuple cho cột Trạng thái.
    _EV_STATUS = {
        "start": STATUS_PENDING,
        "login": ("🔑", "#8aadf4", "Đang login"),
        "register": STATUS_SENDING,
        "sniff": STATUS_SNIFFING,
        "done": STATUS_SUCCESS,
        "error": STATUS_FAILED,
        "stopped": ("⏹", "#a5adcb", "Đã dừng"),
    }

    def __init__(self, cfg: MultiRegConfig, filename: str, services: dict):
        super().__init__()
        self.cfg = cfg
        self.filename = filename
        self.services = services
        self.svc = MultiRegService()
        self.stop_event = asyncio.Event()
        self.worker_handle = None
        # username → row_key (str) trong DataTable
        self._row_keys: Dict[str, str] = {}
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header()
        container = Container(id="mrun-container")
        container.border_title = f"CHẠY MULTIREG: {self.cfg.name or self.filename}"
        with container:
            yield DataTable(
                id="mrun-table", zebra_stripes=True, cursor_type="row",
            )
            yield RichLog(
                id="mrun-log", highlight=False, markup=False,
                wrap=False, max_lines=5000,
            )
            with Horizontal(id="mrun-buttons"):
                yield Button("Dừng", id="mrun-stop", variant="error")
                yield Button("Quay lại", id="mrun-back", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#mrun-table", DataTable)
        table.add_columns("STT", "MSV", "Trạng thái", "Kết quả")
        cols = list(table.columns.values())
        widths = [5, 18, 20, None]  # None = auto (kết quả)
        for col, w in zip(cols, widths):
            if w is None:
                col.auto_width = True
            else:
                col.auto_width = False
                col.width = w
        # Seed 1 row / account, key theo username.
        icon, color, label = STATUS_PENDING
        for i, acc in enumerate(self.cfg.accounts, 1):
            row_key = f"acc{i}"
            self._row_keys[acc.username] = row_key
            table.add_row(
                str(i),
                RichText(acc.username, style="#cad3f5"),
                RichText(f"{icon} {label}", style=color),
                RichText("—", style="#5b6078"),
                key=row_key,
            )
        # Start worker sau khi table seed xong (tránh race query_one).
        self._start_run()

    # ---------- realtime updates ----------

    def _update_status(self, username: str, ev: str, msg: str) -> None:
        """Cập nhật cột Trạng thái theo event. Chạy trên main thread
        (on_progress được gọi trong worker cùng event loop nên OK)."""
        row_key_str = self._row_keys.get(username)
        if row_key_str is None:
            return
        status = self._EV_STATUS.get(ev)
        if status is None:
            return
        try:
            table = self.query_one("#mrun-table", DataTable)
            row_key = table.rows[row_key_str].key
        except (KeyError, Exception):  # noqa: BLE001
            return
        col_keys = list(table.columns.keys())
        if len(col_keys) < 3:
            return
        icon, color, label = status
        table.update_cell(row_key, col_keys[2], RichText(f"{icon} {label}", style=color))

    def _fill_result(self, username: str, res: Dict[str, Any]) -> None:
        """Điền cột Kết quả khi account xong (registered/total hoặc lỗi)."""
        row_key_str = self._row_keys.get(username)
        if row_key_str is None:
            return
        try:
            table = self.query_one("#mrun-table", DataTable)
            row_key = table.rows[row_key_str].key
        except (KeyError, Exception):  # noqa: BLE001
            return
        col_keys = list(table.columns.keys())
        if len(col_keys) < 4:
            return
        err = res.get("error")
        if err:
            text = RichText(f"✗ {err[:40]}", style="#ed8796")
            st = STATUS_FAILED
        else:
            reg = res.get("registered", 0)
            failed = res.get("failed", [])
            sniffed = res.get("sniffed", [])
            total = reg + len(failed)
            parts = [f"{reg}/{total} lớp"]
            if sniffed:
                parts.append(f"(săn {len(sniffed)})")
            text = RichText(" ".join(parts),
                            style="#a6da95" if not failed else "#f5a97f")
            st = STATUS_SUCCESS if not failed else STATUS_DONE
        table.update_cell(row_key, col_keys[3], text)
        # Đồng bộ cột trạng thái cuối
        icon, color, label = st
        table.update_cell(row_key, col_keys[2], RichText(f"{icon} {label}", style=color))

    # ---------- worker ----------

    def _start_run(self) -> None:
        log_widget = self.query_one("#mrun-log", RichLog)

        def _on_progress(username: str, ev: str, msg: str) -> None:
            # Cập nhật bảng — schedule trên event loop để an toàn với worker.
            self.call_later(self._update_status, username, ev, msg)

        async def _runner():
            try:
                with capture_stdout(log_widget):
                    results = await self.svc.run(
                        self.cfg,
                        on_progress=_on_progress,
                        should_stop=lambda: self.stop_event.is_set(),
                    )
                # Điền kết quả cuối cho từng account
                for username, res in results.items():
                    self.call_later(self._fill_result, username, res)
                ok = sum(1 for r in results.values() if r.get("success"))
                log_widget.write(
                    f"\n=== HOÀN TẤT: {ok}/{len(results)} account thành công ==="
                )
            except asyncio.CancelledError:
                self.stop_event.set()
                log_widget.write("[Đã hủy]")
            except Exception as e:  # noqa: BLE001
                log_widget.write(f"[ERROR] {e}")
            finally:
                self._done = True
                try:
                    btn = self.query_one("#mrun-stop", Button)
                    btn.label = "Đã xong"
                    btn.disabled = True
                except Exception:  # noqa: BLE001
                    pass

        self.worker_handle = self.app.run_worker(_runner(), exclusive=False)

    # ---------- buttons ----------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mrun-stop":
            self.action_stop()
        elif event.button.id == "mrun-back":
            self.action_back()

    def action_stop(self) -> None:
        if self._done:
            return
        self.stop_event.set()
        self.query_one("#mrun-log", RichLog).write(
            "[Đã yêu cầu dừng — cắt sniffing, chờ account hiện tại thoát...]"
        )

    def action_back(self) -> None:
        # Set stop trước khi pop (dừng mềm). Worker bị cancel khi screen
        # unmount → finally trong _run_one đóng client.
        self.stop_event.set()
        self.app.pop_screen()


class SubjectPickerScreen(ModalScreen[Optional[Dict[str, Any]]]):
    """Login bằng creds nhập vào → fetch_courses → tick MÔN (giống menu #1).

    Dùng cho subject-mode của multireg: account không gắn profile mà chọn
    danh sách môn, lúc chạy sẽ thử các lớp trong mỗi môn (như Đăng ký nhanh).

    Dismiss với {"is_summer": bool, "subjects": [{"id": int, "name": str}]}
    khi bấm Xong, hoặc None khi Hủy.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Hủy"),
    ]

    def __init__(self, username: str, password: str, services: dict):
        super().__init__()
        self._username = username
        self._password = password
        self.services = services
        self.courses: List[List[Course]] = []
        # index option → subjectId + name (để build kết quả khi Xong)
        self._id_by_value: Dict[int, int] = {}   # value → subjectId
        self._name_by_value: Dict[int, str] = {}  # value → subjectName
        self._loaded = False

    def compose(self) -> ComposeResult:
        container = Container(id="subpick-container")
        container.border_title = f"CHỌN MÔN — {self._username}"
        with container:
            with Horizontal(classes="mb-row"):
                yield ToggleSwitch(id="subpick-summer", value=False)
                yield Label("Học kỳ hè")
                yield Button("Đăng nhập + Tải môn", id="subpick-load", variant="primary")
                yield Button("Chọn tất cả", id="subpick-all")
                yield Button("Bỏ chọn", id="subpick-none")
            yield Label("Bấm 'Đăng nhập + Tải môn' để lấy danh sách.", id="subpick-status", markup=False)
            yield SelectionList[int](id="subpick-selection")
            with Horizontal(id="subpick-buttons"):
                yield Button("Xong", id="subpick-done", variant="success")
                yield Button("Hủy", id="subpick-cancel")

    def _is_summer(self) -> bool:
        return self.query_one("#subpick-summer", ToggleSwitch).value

    def _set_status(self, msg: str) -> None:
        self.query_one("#subpick-status", Label).update(msg)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "subpick-cancel":
            self.dismiss(None)
        elif bid == "subpick-load":
            self.run_worker(self._load(), exclusive=True)
        elif bid == "subpick-all":
            self.query_one(SelectionList).select_all()
        elif bid == "subpick-none":
            self.query_one(SelectionList).deselect_all()
        elif bid == "subpick-done":
            self._done()

    def action_cancel(self) -> None:
        self.dismiss(None)

    async def _load(self) -> None:
        """Login bằng creds → fetch_courses → populate SelectionList.

        Client riêng, đóng ngay sau khi fetch xong (chỉ cần danh sách môn).
        """
        sel: SelectionList = self.query_one(SelectionList)
        sel.clear_options()
        self.courses = []
        self._id_by_value.clear()
        self._name_by_value.clear()
        self._loaded = False
        is_summer = self._is_summer()
        self._set_status("Đang đăng nhập...")
        client = TLUClient()
        try:
            auth = AuthService(client)
            user = await auth.login(self._username, self._password, save=False)
            self._set_status(
                f"Login OK: {user.full_name}. Đang tải môn "
                f"({'HK hè' if is_summer else 'HK chính'})..."
            )
            course_svc = CourseService(client)
            self.courses, names = await course_svc.fetch_courses(user, is_summer)
            count = 0
            for i, name in enumerate(names):
                if not self.courses[i]:
                    continue
                first = self.courses[i][0]
                sid = first.data.get("subjectId")
                if sid is None:
                    continue
                # value = subject index i (unique trong màn này); map sang
                # subjectId + name để build kết quả.
                self._id_by_value[i] = int(sid)
                self._name_by_value[i] = name
                n_classes = len(self.courses[i])
                label = f"{name[:44]:<44}  ({n_classes} lớp)"
                sel.add_option(Selection(label, i))
                count += 1
            self._loaded = True
            self._set_status(
                f"Đã tải {count} môn. Tick môn cần đăng ký rồi bấm 'Xong'."
            )
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Lỗi: {e}")
            self.notify(f"Login/tải môn lỗi: {e}", severity="error")
        finally:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    def _done(self) -> None:
        if not self._loaded:
            self.notify("Chưa tải môn. Bấm 'Đăng nhập + Tải môn' trước.", severity="warning")
            return
        sel: SelectionList = self.query_one(SelectionList)
        values = list(sel.selected)
        if not values:
            self.notify("Chưa tick môn nào.", severity="warning")
            return
        subjects = [
            {"id": self._id_by_value[v], "name": self._name_by_value.get(v, "")}
            for v in values
            if v in self._id_by_value
        ]
        self.dismiss({"is_summer": self._is_summer(), "subjects": subjects})


class MultiRegBuilderScreen(ModalScreen[Optional[str]]):
    """Form tạo file multireg mới. Dismiss với filename khi lưu OK, None khi hủy.

    2 chế độ thêm account:
      - Profile-mode: chọn custom profile (pick lớp cụ thể).
      - Subject-mode: bấm "Chọn môn (đăng nhập)" → login → tick môn (giống
        menu #1, pick môn thử các lớp). Không cần profile.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Hủy"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        self.svc = MultiRegService()
        self.custom_svc: CustomService = services["custom"]
        # Danh sách account đang build (list of dict)
        self._accounts: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        profiles = self.custom_svc.list_files()
        # Combobox options: (label, value). Value=None → "(dùng shared)".
        # Nếu chưa có profile nào thì Select vẫn cần ít nhất 1 option
        # → thêm option placeholder "—" giá trị "" để form không crash.
        profile_options: List[tuple] = [("(dùng shared)", "")]
        for f in profiles:
            profile_options.append((f, f))
        shared_options: List[tuple] = [("(không dùng shared)", "")]
        for f in profiles:
            shared_options.append((f, f))

        # KHÔNG có nút HK / quick per-account:
        # - HK chính/hè: profile v2 đã lưu sẵn semester_id trong envelope
        #   → _run_one đọc trực tiếp.
        # - Sniff fallback khi lớp đầy: là hành vi MẶC ĐỊNH global
        #   (Config.AUTO_SNIFF_FALLBACK ở Settings), giống menu "1. Đăng ký
        #   nhanh". Account chỉ cần username/password/profile.
        container = Container(id="multireg-builder-container")
        container.border_title = "TẠO FILE MULTIREG"
        with container:
            yield Label("Cấu hình đợt", classes="mb-section")
            with Horizontal(classes="mb-row"):
                yield Label("Tên đợt:", classes="mb-lbl")
                yield Input(placeholder="vd: dot1_thang7", id="mb-name")
            with Horizontal(classes="mb-row"):
                yield Label("Shared profile:", classes="mb-lbl")
                yield Select(
                    shared_options, allow_blank=False, value="",
                    id="mb-shared",
                )
            if not profiles:
                yield Label(
                    "⚠ Chưa có custom profile nào. Tạo trước ở menu \"2. Tạo danh sách custom\".",
                    id="mb-hint", markup=False,
                )

            yield Rule()
            yield Label("Account đã thêm", classes="mb-section")
            yield DataTable(
                id="mb-table", zebra_stripes=True, cursor_type="row",
            )

            yield Rule()
            yield Label("Thêm account", classes="mb-section")
            with Horizontal(classes="mb-row"):
                yield Label("Username:", classes="mb-lbl-narrow")
                yield Input(placeholder="mã SV", id="mb-user")
                yield Label("Password:", classes="mb-lbl-narrow")
                yield Input(placeholder="mật khẩu", password=True, id="mb-pass")
            with Horizontal(classes="mb-row"):
                yield Label("Profile:", classes="mb-lbl-narrow")
                yield Select(
                    profile_options, allow_blank=False, value="",
                    id="mb-profile",
                )
            yield Label(
                "Profile = pick LỚP cụ thể. Hoặc bỏ trống profile + bấm "
                "\"Chọn môn\" để pick MÔN (giống Đăng ký nhanh, thử các lớp).",
                id="mb-mode-hint", markup=False,
            )
            with Horizontal(id="mb-buttons"):
                yield Button("+ Thêm (profile)", id="add-acc", variant="primary")
                yield Button("+ Thêm (chọn môn)", id="add-acc-subj", variant="primary")
                yield Button("Xóa acc đã chọn", id="del-acc", variant="warning")
                yield Button("Lưu file", id="save", variant="success")
                yield Button("Hủy", id="cancel")

    def on_mount(self) -> None:
        table = self.query_one("#mb-table", DataTable)
        table.add_columns("STT", "Username", "Chế độ", "Chi tiết")
        cols = list(table.columns.values())
        widths = [5, 18, 12, 30]
        for col, w in zip(cols, widths):
            col.auto_width = False
            col.width = w
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#mb-table", DataTable)
        table.clear()
        for i, a in enumerate(self._accounts):
            if a.get("subjects"):
                mode = "chọn môn"
                names = [s.get("name") or f"id={s.get('id')}" for s in a["subjects"]]
                detail = f"{len(names)} môn: " + ", ".join(names)
            elif a.get("profile"):
                mode = "profile"
                detail = a["profile"]
            else:
                mode = "profile"
                detail = "(shared)"
            table.add_row(
                str(i),
                a["username"],
                mode,
                detail[:60],
                key=str(i),
            )

    def _add_account(self) -> None:
        u = self.query_one("#mb-user", Input).value.strip()
        p = self.query_one("#mb-pass", Input).value
        # Select value = "" khi user chọn "(dùng shared)".
        prof_raw = self.query_one("#mb-profile", Select).value
        prof = str(prof_raw).strip() if prof_raw else ""
        if not u or not p:
            self.notify("Thiếu username hoặc password.", severity="warning")
            return
        # Check trùng username
        if any(a["username"] == u for a in self._accounts):
            self.notify(f"Username {u} đã có trong danh sách.", severity="warning")
            return
        acc: Dict[str, Any] = {"username": u, "password": p}
        if prof:
            acc["profile"] = prof
        self._accounts.append(acc)
        # Clear inputs (Select giữ nguyên)
        self.query_one("#mb-user", Input).value = ""
        self.query_one("#mb-pass", Input).value = ""
        try:
            self.query_one("#mb-profile", Select).value = ""
        except Exception:  # noqa: BLE001
            pass
        self._refresh_table()

    def _delete_selected_account(self) -> None:
        table = self.query_one("#mb-table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self.notify("Chưa chọn account.", severity="warning")
            return
        if table.cursor_row >= len(self._accounts):
            return
        removed = self._accounts.pop(table.cursor_row)
        self._refresh_table()
        self.notify(f"Đã xóa {removed['username']}", severity="information")

    def _save(self) -> None:
        name = self.query_one("#mb-name", Input).value.strip()
        shared_raw = self.query_one("#mb-shared", Select).value
        shared = (str(shared_raw).strip() or None) if shared_raw else None
        if not name:
            self.notify("Thiếu tên đợt.", severity="warning")
            return
        if not self._accounts:
            self.notify("Chưa có account nào.", severity="warning")
            return
        raw = {
            "version": 1,
            "name": name,
            "shared_profile": shared,
            "accounts": self._accounts,
        }
        try:
            cfg = MultiRegConfig.from_dict(raw)
        except ValueError as e:
            self.notify(f"Config lỗi: {e}", severity="error")
            return
        try:
            filename = self.svc.save(cfg)
        except OSError as e:
            self.notify(f"Ghi file lỗi: {e}", severity="error")
            return
        self.dismiss(filename)

    def _add_account_subject(self) -> None:
        """Subject-mode: đọc user/pass, push SubjectPickerScreen (login →
        tick môn), khi Xong lưu account với field 'subjects'."""
        u = self.query_one("#mb-user", Input).value.strip()
        p = self.query_one("#mb-pass", Input).value
        if not u or not p:
            self.notify(
                "Nhập username + password trước khi bấm 'Chọn môn' (cần login).",
                severity="warning",
            )
            return
        if any(a["username"] == u for a in self._accounts):
            self.notify(f"Username {u} đã có trong danh sách.", severity="warning")
            return

        def _on_picked(result: Optional[Dict[str, Any]]) -> None:
            if not result or not result.get("subjects"):
                return
            acc: Dict[str, Any] = {
                "username": u,
                "password": p,
                "subjects": result["subjects"],
            }
            if result.get("is_summer"):
                acc["is_summer"] = True
            self._accounts.append(acc)
            # Clear input sau khi thêm thành công
            self.query_one("#mb-user", Input).value = ""
            self.query_one("#mb-pass", Input).value = ""
            self._refresh_table()
            self.notify(
                f"Đã thêm {u} ({len(result['subjects'])} môn).",
                severity="information",
            )

        self.app.push_screen(
            SubjectPickerScreen(u, p, self.services),
            _on_picked,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cancel":
            self.dismiss(None)
        elif bid == "add-acc":
            self._add_account()
        elif bid == "add-acc-subj":
            self._add_account_subject()
        elif bid == "del-acc":
            self._delete_selected_account()
        elif bid == "save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------- transfer screen (Feature 2) ----------


class TransferScreen(Screen):
    """Chuyển lớp giữa 2 account. 2 form login trái/phải + 2 bảng lớp đã ĐK.

    Mô hình 2 luồng độc lập:
    - Tick lớp bên A → A nhả, B chụp (A cho B).
    - Tick lớp bên B → B nhả, A chụp (B cho A).
    Give 1 chiều = tick 1 bên. Swap = tick cả 2. Khác môn OK.

    Cross-check eligible: lớp bên cho bị GREY nếu bên nhận không đủ điều
    kiện (subjectId không nằm trong eligible list của bên nhận).

    2 TLUClient riêng, TẮT auto-renew (tránh renew nhầm creds login.json
    của account khác). Close cả 2 khi thoát screen.
    """

    BINDINGS = [
        Binding("escape", "back", "Quay lại"),
    ]

    def __init__(self, user: User, services: dict):
        super().__init__()
        self.user = user
        self.services = services
        # State mỗi bên. period_id = active period (main/summer, cái nào
        # đang mở). enrolled = List[Course]. eligible = set(subjectId).
        self._sides: Dict[str, Dict[str, Any]] = {
            "a": self._blank_side(),
            "b": self._blank_side(),
        }

    @staticmethod
    def _blank_side() -> Dict[str, Any]:
        return {
            "client": None, "user": None, "register": None,
            "period_id": None, "is_summer": False,
            "enrolled": [], "eligible": set(), "logged": False,
        }

    def compose(self) -> ComposeResult:
        yield Header()
        container = Container(id="transfer-container")
        container.border_title = "CHUYỂN LỚP GIỮA 2 ACCOUNT"
        with container:
            yield Label(
                "Tick lớp bên nào = bên đó NHẢ, bên kia CHỤP. "
                "Lớp xám = bên nhận không đủ điều kiện học.",
                id="transfer-hint", markup=False,
            )
            with Horizontal(id="transfer-cols"):
                with Vertical(classes="transfer-panel", id="panel-a"):
                    yield Label("A — Chưa đăng nhập", id="a-header")
                    with Horizontal(classes="tf-row"):
                        yield Input(placeholder="Mã SV A", id="a-user")
                        yield Input(placeholder="Mật khẩu A", password=True, id="a-pass")
                    with Horizontal(classes="tf-row"):
                        yield Button("Đăng nhập", id="a-login", variant="primary")
                        yield Button("Dùng user hiện tại", id="a-login-current")
                    yield SelectionList[int](id="a-sel")
                with Vertical(classes="transfer-panel", id="panel-b"):
                    yield Label("B — Chưa đăng nhập", id="b-header")
                    with Horizontal(classes="tf-row"):
                        yield Input(placeholder="Mã SV B", id="b-user")
                        yield Input(placeholder="Mật khẩu B", password=True, id="b-pass")
                    with Horizontal(classes="tf-row"):
                        yield Button("Đăng nhập", id="b-login", variant="primary")
                    yield SelectionList[int](id="b-sel")
            yield Rule()
            with Horizontal(id="transfer-buttons"):
                yield Button("Chuyển lớp", id="transfer-run", variant="success")
                yield Button("Quay lại", id="transfer-back")
        yield Footer()

    # ---------- client factory (tắt auto-renew) ----------

    @staticmethod
    def _make_client() -> TLUClient:
        client = TLUClient()

        async def _no_renew() -> bool:
            # Tắt auto-renew: với 2 account, renew qua login.json sẽ dùng
            # NHẦM creds của account khác. Transfer nhanh nên session
            # sống đủ; nếu 401 giữa chừng → báo lỗi, user login lại.
            return False

        client._try_renew_token = _no_renew  # type: ignore[assignment]
        return client

    # ---------- button dispatch ----------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "transfer-back":
            self.app.pop_screen()
        elif bid == "a-login":
            u = self.query_one("#a-user", Input).value.strip()
            p = self.query_one("#a-pass", Input).value
            self.run_worker(self._login_side("a", u, p), exclusive=False)
        elif bid == "a-login-current":
            self._login_current()
        elif bid == "b-login":
            u = self.query_one("#b-user", Input).value.strip()
            p = self.query_one("#b-pass", Input).value
            self.run_worker(self._login_side("b", u, p), exclusive=False)
        elif bid == "transfer-run":
            await self._do_transfer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _login_current(self) -> None:
        """Điền + login account hiện tại từ login.json vào form A."""
        if not os.path.exists(Config.LOGIN_FILE):
            self.notify("Không có login.json (chưa lưu đăng nhập).", severity="warning")
            return
        try:
            with open(Config.LOGIN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            u = data.get("username") or ""
            p = data.get("password") or ""
        except (json.JSONDecodeError, OSError) as e:
            self.notify(f"Đọc login.json lỗi: {e}", severity="error")
            return
        if not u or not p:
            self.notify("login.json thiếu username/password.", severity="warning")
            return
        self.query_one("#a-user", Input).value = u
        self.query_one("#a-pass", Input).value = p
        self.run_worker(self._login_side("a", u, p), exclusive=False)

    # ---------- login + fetch enrolled/eligible ----------

    async def _login_side(self, side: str, username: str, password: str) -> None:
        header = self.query_one(f"#{side}-header", Label)
        if not username or not password:
            self.notify(f"Thiếu username/password bên {side.upper()}.", severity="warning")
            return
        # Chặn login trùng account 2 bên (drop+grab cùng account vô nghĩa).
        other = "b" if side == "a" else "a"
        other_user = self._sides[other].get("user")
        if other_user is not None and str(other_user.student_id) and \
                other_user.username == username:
            self.notify("2 bên không được cùng 1 account.", severity="error")
            return

        header.update(f"{side.upper()} — đang đăng nhập...")
        # Đóng client cũ nếu login lại
        old = self._sides[side].get("client")
        if old is not None:
            try:
                await old.close()
            except Exception:  # noqa: BLE001
                pass
        self._sides[side] = self._blank_side()

        client = self._make_client()
        try:
            auth = AuthService(client)
            user = await auth.login(username, password, save=False)
            register = RegisterService(client)
            periods = await register.get_active_periods(user)
            if not periods:
                header.update(f"{side.upper()} — {user.full_name}: KHÔNG có kỳ mở ĐK/hủy")
                self.notify(
                    f"{side.upper()}: không có học kỳ nào đang mở đăng ký/hủy.",
                    severity="warning",
                )
                await client.close()
                return
            # Gom enrolled + eligible từ TẤT CẢ period active (thường 1).
            # period_id chính = period đầu (dùng cho URL drop/register).
            primary = periods[0]
            enrolled: List[Course] = []
            eligible: set = set()
            seen_codes: set = set()
            for pd in periods:
                view = pd["view"]
                for c in register.get_enrolled_courses(view):
                    if c.code not in seen_codes:
                        seen_codes.add(c.code)
                        # Tag period cho course (drop/register đúng kỳ)
                        c.data["_transfer_period_id"] = pd["period_id"]
                        enrolled.append(c)
                eligible |= register.get_eligible_subject_ids(view)
            self._sides[side] = {
                "client": client, "user": user, "register": register,
                "period_id": primary["period_id"],
                "is_summer": primary["is_summer"],
                "enrolled": enrolled, "eligible": eligible, "logged": True,
            }
            header.update(
                f"{side.upper()} — {user.full_name} ({user.student_id}) | "
                f"{len(enrolled)} lớp đã ĐK"
            )
            # Populate cả 2 bên (grey phụ thuộc eligible bên kia)
            self._populate("a")
            self._populate("b")
        except Exception as e:  # noqa: BLE001
            header.update(f"{side.upper()} — lỗi đăng nhập")
            self.notify(f"{side.upper()} login lỗi: {e}", severity="error")
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    def _populate(self, side: str) -> None:
        """Đổ enrolled vào SelectionList, grey lớp bên nhận không đủ ĐK."""
        s = self._sides[side]
        if not s["logged"]:
            return
        try:
            sel: SelectionList = self.query_one(f"#{side}-sel", SelectionList)
        except Exception:  # noqa: BLE001
            return
        sel.clear_options()
        other = "b" if side == "a" else "a"
        other_side = self._sides[other]
        other_eligible = other_side["eligible"] if other_side["logged"] else None
        for i, c in enumerate(s["enrolled"]):
            sid = c.data.get("subjectId")
            disabled = False
            suffix = ""
            if other_eligible is not None:
                if sid is None or int(sid) not in other_eligible:
                    disabled = True
                    suffix = "  ✗ bên nhận không đủ ĐK"
            label = (
                f"{c.code} | {(c.display_name or '')[:28]} | "
                f"{c.sessions_summary or '?'}{suffix}"
            )
            sel.add_option(Selection(label, i, disabled=disabled))

    def _selected_courses(self, side: str) -> List[Course]:
        s = self._sides[side]
        if not s["logged"]:
            return []
        try:
            sel: SelectionList = self.query_one(f"#{side}-sel", SelectionList)
        except Exception:  # noqa: BLE001
            return []
        out: List[Course] = []
        for i in list(sel.selected):
            if 0 <= i < len(s["enrolled"]):
                out.append(s["enrolled"][i])
        return out

    # ---------- transfer execution ----------

    async def _do_transfer(self) -> None:
        if not (self._sides["a"]["logged"] and self._sides["b"]["logged"]):
            self.notify("Cả 2 account phải đăng nhập trước.", severity="warning")
            return
        a_gives = self._selected_courses("a")
        b_gives = self._selected_courses("b")
        if not a_gives and not b_gives:
            self.notify("Chưa tick lớp nào để chuyển.", severity="warning")
            return

        plan = TransferService.plan(
            a_gives, self._sides["a"]["enrolled"],
            b_gives, self._sides["b"]["enrolled"],
        )
        # α/γ error → KHÔNG thực thi gì, buộc user sửa selection.
        if plan["errors"]:
            for e in plan["errors"]:
                self.notify(e, severity="error", timeout=8)
            self.notify(
                "Có xung đột lịch với lớp đang GIỮ — sửa lựa chọn rồi thử lại. "
                "KHÔNG thực thi gì.",
                severity="error", timeout=8,
            )
            return

        n_beta = len(plan["beta_pairs"])
        if n_beta:
            self.notify(
                f"⚠ {n_beta} cặp swap cùng slot: phải DROP cả 2 trước khi giành "
                f"lại — rủi ro mất lớp nếu người ngoài chụp trước!",
                severity="warning", timeout=8,
            )

        ctx_a = {
            "register": self._sides["a"]["register"],
            "user": self._sides["a"]["user"],
            "period_id": self._sides["a"]["period_id"],
        }
        ctx_b = {
            "register": self._sides["b"]["register"],
            "user": self._sides["b"]["user"],
            "period_id": self._sides["b"]["period_id"],
        }
        engine = TransferService()

        n_simple = len(plan["simple_a_to_b"]) + len(plan["simple_b_to_a"])
        title = f"Chuyển lớp: {n_beta} swap + {n_simple} give"

        async def _work(ctx: LogCaptureContext):
            ctx.log(f"[TRANSFER] Bắt đầu: {n_beta} β swap, {n_simple} simple give.")
            try:
                results = await engine.execute(
                    plan, ctx_a, ctx_b,
                    on_log=ctx.log, should_stop=ctx.should_stop,
                )
            except Exception as e:  # noqa: BLE001
                ctx.log(f"[TRANSFER] ✗ LỖI: {type(e).__name__}: {e}")
                return
            # Tổng kết trạng thái cuối
            ctx.log("[TRANSFER] === KẾT QUẢ ===")
            for r in results.get("beta", []):
                ctx.log(
                    f"  SWAP {r['x_code']}↔{r['y_code']}: "
                    f"A drop X={r['a_dropped_x']} B drop Y={r['b_dropped_y']} | "
                    f"A chụp Y={r['a_grabbed_y']} B chụp X={r['b_grabbed_x']}"
                )
            for r in results.get("simple", []):
                ctx.log(
                    f"  GIVE {r.get('dir', '?')} {r['code']}: "
                    f"nhả={r['dropped']} chụp={r['grabbed']}"
                )
            ctx.log("[TRANSFER] Xong. Kiểm tra lại lịch mỗi account để xác nhận.")

        log_screen = LogScreen(
            title,
            status_rows=[],
            on_mount_start=lambda: log_screen.run_async(_work),
        )
        self.app.push_screen(log_screen)

    # ---------- cleanup ----------

    async def on_unmount(self) -> None:
        for side in ("a", "b"):
            client = self._sides[side].get("client")
            if client is not None:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass


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
        container = Container(id="cal-container")
        container.border_title = "LỊCH"
        with container:
            yield Label(
                "Xuất thời khóa biểu ra file ICS hoặc đồng bộ lên Google Calendar.",
                id="cal-hint", markup=False,
            )
            with Horizontal(id="cal-toolbar"):
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
        container = Container(id="settings-container")
        container.border_title = "SETTINGS"
        with container:
            yield Label("Đăng ký", classes="set-section")
            with Horizontal(id="row-auto-sniff", classes="settings-row"):
                yield ToggleSwitch(value=Config.AUTO_SNIFF_FALLBACK, id="auto-sniff")
                yield Label("Tự fallback sang sniffing khi đăng ký fail")
            with Horizontal(id="row-burst", classes="settings-row"):
                yield Label("Số request song song / lần thử (BURST):")
                yield Input(value=str(Config.BURST_COUNT), id="burst")
            with Horizontal(id="row-concurrency", classes="settings-row"):
                yield Label("Giới hạn đồng thời (CONCURRENCY):")
                yield Input(value=str(Config.CONCURRENCY_LIMIT), id="concurrency")
            yield Rule()
            yield Label("Sniff", classes="set-section")
            with Horizontal(id="row-interval", classes="settings-row"):
                yield Label("Interval sniff (giây):")
                yield Input(value=str(Config.SNIFF_INTERVAL), id="interval")
            with Horizontal(id="row-jitter", classes="settings-row"):
                yield Label("Jitter sniff (giây, ±):")
                yield Input(value=str(Config.SNIFF_JITTER), id="jitter")
            with Horizontal(id="row-max-duration", classes="settings-row"):
                yield Label("Giới hạn thời gian sniff (phút, 0 = vô hạn):")
                yield Input(value=str(Config.SNIFF_MAX_DURATION_MIN), id="max_duration")
            yield Rule()
            yield Label("Lịch", classes="set-section")
            with Horizontal(id="row-schedule-enabled", classes="settings-row"):
                yield ToggleSwitch(value=Config.SCHEDULE_ENABLED, id="schedule-enabled")
                yield Label("Bật hẹn giờ (đếm ngược tới lúc mở đăng kí)")
            with Horizontal(id="row-schedule-lead", classes="settings-row"):
                yield Label("Lead time (giây trước khi auto-launch):")
                yield Input(value=str(Config.SCHEDULE_LEAD_SECONDS), id="schedule-lead")
            yield Rule()
            yield Label("Debug", classes="set-section")
            with Horizontal(id="row-debug", classes="settings-row"):
                yield ToggleSwitch(value=Config.DEBUG, id="debug")
                yield Label("Chế độ Debug")
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
    /* Catppuccin Macchiato palette.
       Spacing scale: sm=1 md=2. Colors:
       bg-base #24273a | bg-panel #1e2030 | bg-inset #181926
       border #363a4f | focus/accent #c6a0f6
       text #cad3f5 | dim #a5adcb | ok #a6da95 | warn #f5a97f | err #ed8796 */
    Screen {
        background: #24273a;
    }

    /* Panel border-title (thay Label title cũ) — accent mauve, đậm. */
    Container {
        border-title-color: #c6a0f6;
        border-title-style: bold;
        border-title-align: center;
    }

    /* Section label trong Settings (phân nhóm sau Rule). */
    .set-section {
        color: #c6a0f6;
        text-style: bold;
        padding: 1 0 0 0;
    }
    #cal-hint {
        color: #a5adcb;
        padding: 0 0 1 0;
    }
    Rule {
        color: #363a4f;
        margin: 0;
    }

    /* Rows that contain a ToggleSwitch + Label */
    #summer-row, #debug-row, .settings-row {
        height: auto;
        margin: 1 0 0 0;
    }
    .settings-row Label {
        width: auto;
        padding: 0 1 0 0;
    }
    .settings-row Input {
        width: 24;
    }

    /* Multireg screens */
    #multireg-title {
        text-style: bold;
        color: #c6a0f6;
        padding: 1 0;
    }
    #multireg-hint {
        color: #a5adcb;
        padding-bottom: 1;
    }
    #multireg-builder-container {
        padding: 1 2;
        width: 90%;
        height: 90%;
        background: #1e2030;
        border: round #c6a0f6;
    }
    .mb-section {
        color: #c6a0f6;
        text-style: bold;
    }
    .mb-row {
        height: auto;
        margin: 0 0 1 0;
    }
    .mb-lbl {
        width: 16;
        padding: 0 1 0 0;
    }
    .mb-lbl-narrow {
        width: auto;
        padding: 0 1 0 1;
    }
    .mb-row Input {
        width: 32;
    }
    #mb-hint {
        color: #a5adcb;
        padding: 0 0 1 0;
    }
    #mb-buttons {
        padding-top: 1;
    }
    #mb-buttons Button {
        margin: 0 1;
    }
    #mb-mode-hint {
        color: #a5adcb;
        padding: 1 0 0 0;
    }

    /* Subject picker (multireg subject-mode) */
    #subpick-container {
        padding: 1 2;
        width: 90%;
        height: 90%;
        background: #1e2030;
        border: round #c6a0f6;
    }
    #subpick-title {
        text-style: bold;
        color: #c6a0f6;
        text-align: center;
        padding-bottom: 1;
    }
    #subpick-status {
        color: #a5adcb;
        padding: 1 0;
    }
    #subpick-selection {
        height: 1fr;
    }
    #subpick-buttons {
        padding-top: 1;
    }
    #subpick-buttons Button {
        margin: 0 1;
    }

    /* Transfer screen (Feature 2) */
    #transfer-container {
        padding: 1 2;
        width: 98%;
        height: 98%;
        background: #1e2030;
        border: round #c6a0f6;
    }
    #transfer-title {
        text-style: bold;
        color: #c6a0f6;
        text-align: center;
        padding-bottom: 1;
    }
    #transfer-hint {
        color: #a5adcb;
        padding: 0 0 1 0;
    }
    #transfer-cols {
        height: 1fr;
    }
    .transfer-panel {
        width: 1fr;
        height: 100%;
        padding: 0 1;
        border: round #5b6078;
    }
    #a-header, #b-header {
        text-style: bold;
        color: #a6da95;
        padding-bottom: 1;
    }
    .tf-row {
        height: auto;
        margin: 0 0 1 0;
    }
    .tf-row Input {
        width: 1fr;
    }
    .tf-row Button {
        margin: 0 1 0 0;
    }
    #a-sel, #b-sel {
        height: 1fr;
    }
    #transfer-buttons {
        padding-top: 1;
        align-horizontal: center;
    }
    #transfer-buttons Button {
        margin: 0 1;
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
    #settings-container {
        height: 1fr;
        padding: 1 2;
        overflow-y: auto;
    }
    #settings-buttons {
        height: auto;
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
        height: 3;
        align-vertical: middle;
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
    MenuScreen {
        align: center middle;
    }
    #menu-container {
        width: 56;
        height: auto;
        padding: 1 2;
        background: #1e2030;
        border: round #c6a0f6;
    }
    #offline-banner {
        text-align: center;
        color: #f5a97f;
        text-style: bold;
        padding-bottom: 1;
    }
    #menu-greet {
        text-align: center;
        text-style: bold;
        color: #a6da95;
        padding-bottom: 1;
    }
    .menu-section {
        color: #a5adcb;
        text-style: bold;
        padding: 0 0 0 1;
    }
    #menu-container Button {
        width: 100%;
        margin: 0 0 1 0;
    }
    #menu-container Rule {
        margin: 0;
    }
    #menu-footer {
        height: auto;
        align-horizontal: center;
    }
    #menu-footer Button {
        width: auto;
        min-width: 16;
        margin: 0 1;
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
    #reg-toolbar, #reg-toolbar2, #builder-toolbar, #builder-toolbar2, #multireg-toolbar {
        height: 3;
        align-vertical: middle;
        padding: 0 1;
    }
    #reg-toolbar Button, #reg-toolbar2 Button, #builder-toolbar Button,
    #builder-toolbar2 Button, #multireg-toolbar Button {
        margin: 0 1;
    }
    /* Toggle + label trong toolbar hàng 1 — switch/label cao 1, nút cao 3.
       align-vertical:middle không đẩy được vì nút đã lấp đầy container.
       Dùng margin-top:1 đẩy switch+label xuống dòng giữa (y=2) khớp text nút. */
    #reg-toolbar Label, #builder-toolbar Label {
        margin-top: 1;
        padding: 0 1 0 0;
    }
    #reg-toolbar ToggleSwitch, #builder-toolbar ToggleSwitch {
        margin-top: 1;
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

    /* Multireg run screen (chạy multireg trong TUI) */
    #mrun-container {
        padding: 1 2;
        height: 100%;
    }
    #mrun-table {
        height: auto;
        max-height: 14;
        margin-bottom: 1;
        border: round #5b6078;
    }
    #mrun-log {
        height: 1fr;
        border: round #5b6078;
        background: #181926;
    }
    #mrun-buttons {
        padding-top: 1;
        align-horizontal: center;
    }
    #mrun-buttons Button {
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
