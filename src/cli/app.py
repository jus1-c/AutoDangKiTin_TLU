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
  autodktin multireg list
  autodktin multireg create
  autodktin multireg run FILE
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
from src.services.multireg_service import MultiRegService
from src.services.register_service import RegisterService

app = typer.Typer(
    name="autodktin",
    help="AutoDangKiTin TLU - CLI đăng ký tín chỉ tự động",
    add_completion=False,
)
console = Console()

profile_app = typer.Typer(help="Quản lý hồ sơ custom (res/custom/*.json).")
app.add_typer(profile_app, name="profile")

multireg_app = typer.Typer(
    help="Đăng ký nhiều account song song (res/multireg/*.json)."
)
app.add_typer(multireg_app, name="multireg")


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
        # nonlocal: nếu không có, Python coi `username = typer.prompt(...)`
        # là gán biến local mới → dòng `if not username:` phía trên đọc
        # local chưa gán → UnboundLocalError. Bug này khiến `autodktin login`
        # luôn crash khi thiếu --user hoặc --password.
        nonlocal username, password
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
                profile_path = os.path.join(Config.RES_DIR, "custom", profile)
                # load_profile xử lý cả v1 (list) và v2 (envelope {version,
                # semester_id, courses}). Cũ dùng `[Course(d) for d in data]`
                # với data là dict v2 → lặp qua KEYS → Course("version"). Sai.
                try:
                    sem_id, target_courses = CustomService.load_profile(profile_path)
                except (json.JSONDecodeError, OSError) as e:
                    console.print(f"[red]Đọc profile lỗi: {e}[/red]")
                    raise typer.Exit(1)
                # Xác định học kỳ từ profile. Ưu tiên semester_id trong file;
                # nếu file v1 (không có) thì --summer từ command line.
                if sem_id is not None:
                    is_summer_profile = (sem_id == user.semester_summer_id)
                    active_sem_id = sem_id
                else:
                    is_summer_profile = summer
                    active_sem_id = user.semester_summer_id if summer else user.semester_id
                hk_label = "HK hè" if is_summer_profile else "HK chính"
                console.print(
                    f"[blue]Chạy profile[/blue] {profile} "
                    f"({len(target_courses)} môn, {hk_label}, semester_id={active_sem_id})"
                )
                failed = await register_svc.register_custom_for_semester(
                    user, target_courses, semester_id=active_sem_id,
                )
                if failed and auto_sniff:
                    console.print(f"[yellow]{len(failed)} môn fail -> sniffing...[/yellow]")
                    await register_svc.sniffing_loop(
                        user, failed, is_summer_profile,
                        interval=interval,
                        max_duration_min=Config.SNIFF_MAX_DURATION_MIN,
                        on_log=print,
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
            # Filter index out-of-range TRƯỚC khi gọi register_subjects.
            # register_subjects sẽ `all_courses[idx]` → IndexError nếu bad.
            original_sel = sel
            sel = [i for i in original_sel if 0 <= i < len(names)]
            invalid = [i for i in original_sel if not (0 <= i < len(names))]
            if invalid:
                console.print(
                    f"[yellow]Bỏ qua index không hợp lệ (ngoài 0..{len(names)-1}): "
                    f"{invalid}[/yellow]"
                )
            if not sel:
                console.print(
                    f"[red]Không có index hợp lệ. Có {len(names)} môn (0..{len(names)-1}).[/red]"
                )
                raise typer.Exit(1)
            for i in sel:
                console.print(f"  {i}. {names[i]}")
            failed = await register_svc.register_subjects(user, sel, courses, summer)
            if failed and auto_sniff:
                console.print(f"[yellow]{len(failed)} môn fail -> sniffing...[/yellow]")
                await register_svc.sniffing_loop(
                    user, failed, summer,
                    interval=interval,
                    max_duration_min=Config.SNIFF_MAX_DURATION_MIN,
                    on_log=print,
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


# ---------- multireg commands ----------


@multireg_app.command("list")
def multireg_list():
    """Liệt kê file multireg có trong res/multireg/."""
    Config.ensure_dirs()
    svc = MultiRegService()
    files = svc.list_files()
    if not files:
        console.print("(Trống — chưa có file multireg)")
        return
    table = Table("STT", "File", "Tên", "Số acc")
    for i, f in enumerate(files):
        try:
            cfg = svc.load(f)
            table.add_row(str(i), f, cfg.name or "?", str(len(cfg.accounts)))
        except Exception as e:  # noqa: BLE001
            table.add_row(str(i), f, f"[red]lỗi: {e}[/red]", "?")
    console.print(table)


@multireg_app.command("create")
def multireg_create(
    name: str = typer.Option(..., "--name", "-n", help="Tên đợt (dùng làm filename)"),
    shared_profile: Optional[str] = typer.Option(
        None, "--shared-profile", help="Custom profile dùng chung cho các account không có profile riêng"
    ),
    account: List[str] = typer.Option(
        [], "--account", "-a",
        help="username:password[:profile[:summer[:quick]]] — lặp lại được. "
             "summer=summer|main, quick=quick|noquick. VD: sv001:pass:file.json:main:quick",
    ),
):
    """Tạo file multireg từ CLI (wizard prompt hoặc --account nhiều lần)."""
    Config.ensure_dirs()
    svc = MultiRegService()
    accounts: List[dict] = []

    # Parse --account flags (mỗi flag = 1 account)
    for spec in account:
        parts = spec.split(":")
        if len(parts) < 2:
            console.print(f"[red]--account cần format username:password[:profile[:summer[:quick]]]: {spec}[/red]")
            raise typer.Exit(1)
        acc: dict = {"username": parts[0], "password": parts[1]}
        if len(parts) > 2 and parts[2]:
            acc["profile"] = parts[2]
        if len(parts) > 3:
            acc["is_summer"] = parts[3].lower() in ("summer", "he", "hè", "true", "1")
        if len(parts) > 4:
            acc["quick"] = parts[4].lower() in ("quick", "true", "1")
        accounts.append(acc)

    # Nếu không có flag → interactive wizard
    if not accounts:
        console.print("[blue]Wizard tạo multireg. Bấm Enter với username trống để kết thúc.[/blue]")
        while True:
            u = typer.prompt("Username", default="", show_default=False)
            if not u:
                break
            p = typer.prompt("Password", hide_input=True)
            prof = typer.prompt("Profile file (Enter=dùng shared)", default="", show_default=False)
            is_summer = typer.confirm("Học kỳ hè?", default=False)
            quick = typer.confirm("Đăng ký nhanh (burst+sniff nếu fail)?", default=True)
            acc = {"username": u, "password": p, "is_summer": is_summer, "quick": quick}
            if prof:
                acc["profile"] = prof
            accounts.append(acc)

    if not accounts:
        console.print("[red]Không có account nào. Hủy.[/red]")
        raise typer.Exit(1)

    from src.services.multireg_service import MultiRegConfig
    raw = {
        "version": 1,
        "name": name,
        "accounts": accounts,
    }
    if shared_profile:
        raw["shared_profile"] = shared_profile
    try:
        cfg = MultiRegConfig.from_dict(raw)
    except ValueError as e:
        console.print(f"[red]Config không hợp lệ: {e}[/red]")
        raise typer.Exit(1)

    filename = svc.save(cfg)
    console.print(f"[green]Đã lưu:[/green] {filename} ({len(accounts)} account)")


@multireg_app.command("run")
def multireg_run(
    file: str = typer.Argument(..., help="Tên file multireg (trong res/multireg/)"),
):
    """Chạy file multireg — login + đăng ký N account song song. CHỈ CLI chạy được."""
    Config.ensure_dirs()
    Config.load_settings()  # áp settings.json làm mặc định
    svc = MultiRegService()

    async def _run():
        try:
            cfg = svc.load(file)
        except FileNotFoundError:
            console.print(f"[red]Không tìm thấy file: {file}[/red]")
            raise typer.Exit(1)
        except ValueError as e:
            console.print(f"[red]File không hợp lệ: {e}[/red]")
            raise typer.Exit(1)

        console.print(f"[blue]Chạy multireg[/blue] {cfg.name or '?'} ({len(cfg.accounts)} account, song song)")

        def _on_progress(username: str, event: str, msg: str) -> None:
            console.print(f"[{username}][{event}] {msg}", markup=False, highlight=False)

        results = await svc.run(cfg, on_progress=_on_progress)

        # Summary table — results là Dict[username, result_dict]
        console.print()
        table = Table("Username", "Kết quả", "Đăng ký", "Sniffed", "Còn fail", "Log", "Lỗi")
        ok_all = True
        for username, r in results.items():
            status = "[green]✓[/green]" if r["success"] else "[red]✗[/red]"
            reg = str(r.get("registered", 0))
            sniffed = str(len(r.get("sniffed", [])))
            failed = str(len(r.get("failed", [])))
            log_short = os.path.basename(r.get("log_file", "")) if r.get("log_file") else "-"
            err = (r.get("error") or "")[:60]
            table.add_row(username, status, reg, sniffed, failed, log_short, err)
            if not r["success"]:
                ok_all = False
        console.print(table)
        if not ok_all:
            raise typer.Exit(2)

    asyncio.run(_run())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
