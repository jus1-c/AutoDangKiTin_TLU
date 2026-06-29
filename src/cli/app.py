# -*- coding: utf-8 -*-
"""
AutoDangKiTin TLU - CLI (Typer)

Usage:
  autodktin                     # (handled by main.py) launch TUI
  autodktin login
  autodktin register --index 0 --index 1
  autodktin register --all
  autodktin register --profile NAME
  autodktin sniff --index 0
  autodktin export-ics
  autodktin sync-calendar
  autodktin profile list
  autodktin profile run NAME
  autodktin profile delete NAME
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from src.config import Config
from src.core.client import TLUClient
from src.models.course import Course
from src.services.auth_service import AuthService
from src.services.calendar_service import CalendarService
from src.services.course_service import CourseService
from src.services.custom_service import CustomService
from src.services.register_service import RegisterService

app = typer.Typer(
    name="autodktin",
    help="AutoDangKiTin TLU - CLI đăng ký tín chỉ tự động",
    add_completion=False,
)
console = Console()

profile_app = typer.Typer(help="Quản lý hồ sơ custom (res/custom/*.json).")
app.add_typer(profile_app, name="profile")


async def _ensure_user(client: TLUClient, offline: bool = False):
    """Returns (User, services, offline_flag).

    offline=True → gọi AuthService.load_offline_user() (0 API call).
    offline=False → load session bình thường. Nếu lỗi mạng + có
    cache user_info.json → prompt [Y/n] dùng offline.
    """
    auth = AuthService(client)
    used_offline = False

    if offline:
        try:
            user = await auth.load_offline_user()
            used_offline = True
            console.print(f"[yellow]OFFLINE:[/yellow] Xin chào {user.full_name} ({user.student_id})")
        except Exception as e:
            console.print(f"[red]Offline load lỗi: {e}[/red]")
            raise typer.Exit(1)
    else:
        try:
            user = await auth.load_saved_user()
            console.print(f"[green]Đã đăng nhập:[/green] {user.full_name}")
        except Exception as e:
            # Auto-prompt offline nếu lỗi mạng + có cache
            if AuthService._is_network_error(e) and os.path.exists(
                Config.USER_INFO_FILE
            ):
                if typer.confirm("Mất mạng. Dùng offline mode với dữ liệu đã lưu?", default=True):
                    try:
                        user = await auth.load_offline_user()
                        used_offline = True
                        console.print(
                            f"[yellow]OFFLINE:[/yellow] Xin chào {user.full_name} ({user.student_id})"
                        )
                    except Exception as off_e:
                        console.print(f"[red]Offline fallback lỗi: {off_e}[/red]")
                        raise typer.Exit(1)
                else:
                    raise typer.Exit(1)
            else:
                if not os.path.exists(Config.LOGIN_FILE):
                    console.print("[red]Chưa đăng nhập. Chạy:[/red] autodktin login")
                    raise typer.Exit(1)
                console.print("[yellow]Phiên hết hạn. Đăng nhập lại...[/yellow]")
                with open(Config.LOGIN_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                try:
                    user = await auth.login(data["username"], data["password"])
                except Exception as login_e:
                    console.print(f"[red]Login thất bại: {login_e}[/red]")
                    raise typer.Exit(1)

    services = {
        "client": client,
        "auth": auth,
        "course": CourseService(client),
        "register": RegisterService(client),
        "calendar": CalendarService(client),
        "custom": CustomService(),
    }
    return user, services, used_offline


@app.command()
def login(
    username: Optional[str] = typer.Option(None, "--user", "-u", help="Mã sinh viên"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Mật khẩu", hide_input=True),
    save: bool = typer.Option(
        True, "--save/--no-save", help="Lưu mật khẩu vào res/login.json để auto-login lần sau"
    ),
):
    """Đăng nhập và lưu session."""
    Config.ensure_dirs()

    async def _run():
        client = TLUClient()
        try:
            auth = AuthService(client)
            if not username:
                username = typer.prompt("Mã sinh viên")
            if not password:
                password = typer.prompt("Mật khẩu", hide_input=True)
            user = await auth.login(username, password, save=save)
            if save:
                console.print(f"[green]OK[/green] Xin chào {user.full_name} ({user.student_id}) — đã lưu đăng nhập.")
            else:
                console.print(f"[green]OK[/green] Xin chào {user.full_name} ({user.student_id}) — KHÔNG lưu đăng nhập.")
        finally:
            await client.close()

    asyncio.run(_run())


@app.command()
def register(
    indices: List[int] = typer.Option([], "--index", "-i", help="Chỉ số môn (lặp lại được)"),
    all_subjects: bool = typer.Option(False, "--all", help="Đăng ký tất cả"),
    summer: bool = typer.Option(False, "--summer", help="Học kỳ hè"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Chạy từ file custom profile"),
    auto_sniff: bool = typer.Option(
        Config.AUTO_SNIFF_FALLBACK,
        "--auto-sniff/--no-auto-sniff",
        help="Tự sniff môn fail (mặc định theo Settings)",
    ),
    interval: float = typer.Option(Config.SNIFF_INTERVAL, "--sniff-interval", help="Interval sniff (giây)"),
    offline: bool = typer.Option(
        False, "--offline", help="Bỏ qua 3 API call lúc khởi động, dùng cache user_info.json"
    ),
):
    """Đăng ký môn theo chỉ số / tất cả / từ custom profile."""
    Config.ensure_dirs()

    async def _run():
        client = TLUClient()
        try:
            user, services, _ = await _ensure_user(client, offline=offline)
            register_svc: RegisterService = services["register"]
            course_svc: CourseService = services["course"]
            custom_svc: CustomService = services["custom"]

            if profile:
                files = custom_svc.list_files()
                if profile not in files:
                    console.print(f"[red]Không tìm thấy profile: {profile}[/red]")
                    raise typer.Exit(1)
                with open(os.path.join(Config.RES_DIR, "custom", profile), "r", encoding="utf-8") as f:
                    data = json.load(f)
                target_courses = [Course(d) for d in data]
                console.print(f"[blue]Chạy profile[/blue] {profile} ({len(target_courses)} môn)")
                failed = await register_svc.register_custom(user, target_courses)
                if failed and auto_sniff:
                    console.print(f"[yellow]{len(failed)} môn fail -> sniffing...[/yellow]")
                    await register_svc.sniffing_loop(
                        user, failed, False, interval=interval, on_log=print
                    )
                return

            courses, names = await course_svc.fetch_courses(user, summer)
            if all_subjects:
                sel = list(range(len(names)))
            else:
                sel = indices
            if not sel:
                console.print("[red]Cần --index/-i hoặc --all hoặc --profile[/red]")
                raise typer.Exit(1)
            for i in sel:
                if 0 <= i < len(names):
                    console.print(f"  {i}. {names[i]}")
            failed = await register_svc.register_subjects(user, sel, courses, summer)
            if failed and auto_sniff:
                console.print(f"[yellow]{len(failed)} môn fail -> sniffing...[/yellow]")
                await register_svc.sniffing_loop(
                    user, failed, summer, interval=interval, on_log=print
                )
        finally:
            await client.close()

    asyncio.run(_run())


@app.command()
def sniff(
    indices: List[int] = typer.Option([], "--index", "-i", help="Chỉ số môn cần săn (lặp lại được)"),
    summer: bool = typer.Option(False, "--summer", help="Học kỳ hè"),
    interval: float = typer.Option(Config.SNIFF_INTERVAL, "--interval", help="Giây giữa các lần poll"),
    offline: bool = typer.Option(
        False, "--offline", help="Bỏ qua 3 API call lúc khởi động, dùng cache user_info.json"
    ),
):
    """Săn môn (check-then-register) cho tới khi hết hoặc Ctrl-C."""
    Config.ensure_dirs()

    async def _run():
        client = TLUClient()
        try:
            user, services, _ = await _ensure_user(client, offline=offline)
            course_svc: CourseService = services["course"]
            register_svc: RegisterService = services["register"]
            if not indices:
                console.print("[red]Cần --index/-i (lặp lại được) để chỉ định môn săn[/red]")
                raise typer.Exit(1)
            courses, names = await course_svc.fetch_courses(user, summer)
            targets: List[Course] = []
            for i in indices:
                if 0 <= i < len(courses) and courses[i]:
                    targets.extend(courses[i])
                    console.print(f"  + {names[i]}")
            if not targets:
                console.print("[red]Không có môn hợp lệ để săn[/red]")
                raise typer.Exit(1)
            console.print(f"[blue]Săn {len(targets)} môn, interval {interval}s (Ctrl-C để dừng)[/blue]")
            await register_svc.sniffing_loop(
                user, targets, summer, interval=interval, on_log=print
            )
        finally:
            await client.close()

    asyncio.run(_run())


@app.command("export-ics")
def export_ics(
    offline: bool = typer.Option(
        False, "--offline", help="Bị reject — chức năng này cần mạng", hidden=True
    ),
):
    """Xuất lịch học ra file .ics."""
    if offline:
        console.print("[red]Export-ics cần kết nối mạng.[/red]")
        raise typer.Exit(1)
    Config.ensure_dirs()

    async def _run():
        client = TLUClient()
        try:
            user, services, _ = await _ensure_user(client)
            cal: CalendarService = services["calendar"]
            path = await cal.export_ics(user)
            console.print(f"[green]Đã tạo:[/green] {path}")
        finally:
            await client.close()

    asyncio.run(_run())


@app.command("sync-calendar")
def sync_calendar(
    offline: bool = typer.Option(
        False, "--offline", help="Bị reject — chức năng này cần mạng", hidden=True
    ),
):
    """Đồng bộ lịch học lên Google Calendar."""
    if offline:
        console.print("[red]Sync-calendar cần kết nối mạng.[/red]")
        raise typer.Exit(1)
    Config.ensure_dirs()

    async def _run():
        client = TLUClient()
        try:
            user, services, _ = await _ensure_user(client)
            cal: CalendarService = services["calendar"]
            events = await cal.get_tlu_events(user)
            await asyncio.to_thread(
                cal.sync_to_google, events, initial_token=None,
                on_token_update=None, browser_callback=None,
            )
            console.print("[green]Đồng bộ xong![/green]")
        finally:
            await client.close()

    asyncio.run(_run())


@profile_app.command("list")
def profile_list():
    """Liệt kê các custom profile."""
    Config.ensure_dirs()
    custom = CustomService()
    files = custom.list_files()
    if not files:
        console.print("(Trống)")
        return
    table = Table("STT", "File")
    for i, f in enumerate(files):
        table.add_row(str(i), f)
    console.print(table)


@profile_app.command("run")
def profile_run(
    name: str = typer.Argument(..., help="Tên file profile"),
    auto_sniff: bool = typer.Option(
        Config.AUTO_SNIFF_FALLBACK,
        "--auto-sniff/--no-auto-sniff",
        help="Tự sniff môn fail (mặc định theo Settings)",
    ),
    interval: float = typer.Option(Config.SNIFF_INTERVAL, "--sniff-interval"),
    offline: bool = typer.Option(
        False, "--offline", help="Bỏ qua 3 API call lúc khởi động, dùng cache user_info.json"
    ),
):
    """Chạy một custom profile (đăng ký + sniff nếu có fail)."""
    Config.ensure_dirs()
    custom = CustomService()
    if name not in custom.list_files():
        console.print(f"[red]Không tìm thấy profile: {name}[/red]")
        raise typer.Exit(1)

    async def _run():
        client = TLUClient()
        try:
            user, services, _ = await _ensure_user(client, offline=offline)
            with open(os.path.join(Config.RES_DIR, "custom", name), "r", encoding="utf-8") as f:
                data = json.load(f)
            target_courses = [Course(d) for d in data]
            register_svc: RegisterService = services["register"]
            failed = await register_svc.register_custom(user, target_courses)
            if failed and auto_sniff:
                console.print(f"[yellow]{len(failed)} môn fail -> sniffing...[/yellow]")
                await register_svc.sniffing_loop(
                    user, failed, False, interval=interval, on_log=print
                )
        finally:
            await client.close()

    asyncio.run(_run())


@profile_app.command("delete")
def profile_delete(
    name: str = typer.Argument(..., help="Tên file profile"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Bỏ qua xác nhận"),
):
    """Xóa một custom profile."""
    custom = CustomService()
    if name not in custom.list_files():
        console.print(f"[red]Không tìm thấy profile: {name}[/red]")
        raise typer.Exit(1)
    if not yes:
        if not typer.confirm(f"Xóa {name}?"):
            raise typer.Abort()
    custom.delete_files([name])
    console.print(f"[green]Đã xóa {name}[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
