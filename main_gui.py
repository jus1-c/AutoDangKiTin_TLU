# --- HOTFIX for Python 3.12+ and vbuild/nicegui compatibility ---
import pkgutil
import importlib.util
if not hasattr(pkgutil, 'find_loader'):
    def find_loader(fullname):
        spec = importlib.util.find_spec(fullname)
        return spec.loader if spec else None
    pkgutil.find_loader = find_loader
# ----------------------------------------------------------------

# Explicitly import webview to ensure PyInstaller bundles it
try:
    import webview
except ImportError:
    pass

from nicegui import ui, app, run
import asyncio
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import List, Set, Dict

# Import from src (Local/File-based)
from src.config import Config
from src.core.client import TLUClient
from src.services.auth_service import AuthService
from src.services.course_service import CourseService
from src.services.register_service import RegisterService
from src.services.calendar_service import CalendarService
from src.models.course import Course
from src.core.exceptions import LoginError, NetworkError

# --- 1. Stream Redirector ---
class UILogger(logging.Handler):
    def __init__(self):
        super().__init__()
        self.log_element = None
        self.terminal_stdout = sys.__stdout__
        self.terminal_stderr = sys.__stderr__

    def set_element(self, element):
        self.log_element = element

    def emit(self, record):
        try:
            msg = self.format(record)
            if self.log_element:
                self.log_element.push(msg)
        except:
            self.handleError(record)

    def write(self, message):
        self.terminal_stdout.write(message)
        if self.log_element and message.strip():
            try:
                self.log_element.push(message.rstrip())
            except:
                pass

    def flush(self):
        self.terminal_stdout.flush()

    def isatty(self):
        return self.terminal_stdout.isatty()

ui_logger = UILogger()
sys.stdout = ui_logger
sys.stderr = ui_logger

logging.basicConfig(level=logging.INFO)
root_logger = logging.getLogger()
root_logger.addHandler(ui_logger)

# --- Global Services (Singleton for GUI Local) ---
client = TLUClient()
auth_service = AuthService(client)
course_service = CourseService(client)
register_service = RegisterService(client)
calendar_service = CalendarService(client)

user = None
is_summer_sem = False
courses_cache = []

# --- GUI Logic ---
def run_gui():
    @ui.page('/')
    async def main_page():
        global user, is_summer_sem, courses_cache
        
        # Task Management
        active_tasks: Set[asyncio.Task] = set()

        def cleanup_tasks():
            for t in active_tasks: t.cancel()
            # client.close() explicitly removed
        app.on_disconnect(cleanup_tasks)

        async def run_safe(coro):
            task = asyncio.create_task(coro)
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)
            try:
                return await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[ERROR] {e}")
                ui.notify(f"Lỗi: {e}", type='negative')

        # --- Timer Logic ---
        async def wait_until(target_time: datetime):
            while True:
                now = datetime.now()
                if now >= target_time: break
                diff = target_time - now
                seconds = int(diff.total_seconds())
                if seconds > 60:
                    if seconds % 30 == 0: print(f"⏳Còn {seconds} giây...")
                    await asyncio.sleep(1)
                else:
                    print(f"⏳ Đếm ngược: {seconds}s")
                    await asyncio.sleep(1)
            print("🚀 Bắt đầu chạy!")

        # --- UI Layout ---
        with ui.header().classes('bg-blue-700 text-white shadow-lg'):
            ui.label('AutoDangKiTin TLU (GUI Local)').classes('text-h6 font-bold')
            ui.space()
            user_label = ui.label('Chưa đăng nhập').classes('mr-4')
            logout_btn = ui.button('Đăng xuất', on_click=lambda: logout()).props('flat color=white').classes('hidden')

        with ui.tabs().classes('w-full shadow-sm sticky top-0 bg-white z-10') as tabs:
            tab_login = ui.tab('Đăng nhập', icon='login')
            tab_register = ui.tab('Đăng ký Nhanh', icon='flash_on')
            tab_custom = ui.tab('Custom (Local)', icon='save')
            tab_utils = ui.tab('Tiện ích', icon='build')
            tab_logs = ui.tab('Logs', icon='terminal')

        # Tab State
        def update_tabs_state():
            is_logged_in = user is not None
            tab_register.enabled = is_logged_in
            tab_custom.enabled = is_logged_in
            tab_utils.enabled = is_logged_in
            tab_logs.enabled = is_logged_in

        update_tabs_state()

        with ui.tab_panels(tabs, value=tab_login).classes('w-full p-4'):
            
            # ================= TAB: ĐĂNG NHẬP =================
            with ui.tab_panel(tab_login):
                with ui.card().classes('w-full max-w-md mx-auto p-6'):
                    ui.label('Thông tin sinh viên').classes('text-h5 mb-4')
                    username_input = ui.input('Mã sinh viên').classes('w-full mb-2')
                    password_input = ui.input('Mật khẩu', password=True, password_toggle_button=True).classes('w-full mb-4')
                    remember_switch = ui.switch('Ghi nhớ đăng nhập', value=True).classes('mb-4')
                    
                    async def handle_login():
                        global user
                        if not username_input.value or not password_input.value:
                            ui.notify('Thiếu thông tin!', type='warning')
                            return
                        try:
                            print(">>> Đang thực hiện đăng nhập...")
                            user = await auth_service.login(username_input.value, password_input.value)
                            
                            if remember_switch.value:
                                creds = {'u': username_input.value, 'p': password_input.value}
                                ui.run_javascript(f"localStorage.setItem('autotlu_creds', '{json.dumps(creds)}');")
                            else:
                                ui.run_javascript("localStorage.removeItem('autotlu_creds');")

                            on_login_success()
                        except Exception as e:
                            ui.notify(f'Lỗi: {e}', type='negative')
                            print(f"[ERROR] Login failed: {e}")
                    
                    def on_login_success():
                        user_label.text = f'{user.full_name} ({user.student_id})'
                        logout_btn.classes(remove='hidden')
                        ui.notify(f'Xin chào {user.full_name}', type='positive')
                        update_tabs_state()
                        tabs.value = tab_register
                        refresh_saved_custom_list()

                    ui.button('Đăng nhập', on_click=handle_login).classes('w-full bg-blue-600')
                    
                    def toggle_debug(e):
                        Config.DEBUG = e.value
                        print(f"Debug Mode: {e.value}")
                    ui.switch('Chế độ Debug', on_change=toggle_debug).classes('mt-4')

                    # Auto-fill
                    async def check_browser_creds():
                        try:
                            json_str = await ui.run_javascript("return localStorage.getItem('autotlu_creds');", timeout=5.0)
                            if json_str:
                                creds = json.loads(json_str)
                                username_input.value = creds.get('u', '')
                                password_input.value = creds.get('p', '')
                                ui.notify('Đã tải thông tin từ trình duyệt', type='info')
                        except Exception as e:
                            print(f"Error reading local storage: {e}")
                    ui.timer(0.1, check_browser_creds, once=True)

            # Helper: Ensure Courses Loaded
            async def ensure_courses_loaded():
                global courses_cache
                if not user: return False
                if not courses_cache:
                    ui.notify('Đang tải danh sách môn...', type='info')
                    try:
                        raw, names = await run_safe(course_service.fetch_courses(user, is_summer_sem))
                        courses_cache = raw
                        update_register_table(raw, names)
                        return True
                    except Exception as e:
                        print(f"Error fetching courses: {e}")
                        ui.notify(f"Lỗi tải môn: {e}", type='negative')
                        return False
                return True

            def update_register_table(raw_courses, names):
                rows = []
                for i, name in enumerate(names):
                    if raw_courses[i]:
                        c = raw_courses[i][0]
                        rows.append({
                            'id': i,
                            'name': name,
                            'code': c.code,
                            'info': f"{c.current_students}/{c.max_students}"
                        })
                reg_table.rows = rows
                reg_table.update()

            async def open_timer_dialog(callback):
                # Ensure meta data
                if not course_service.last_meta:
                    await ensure_courses_loaded()

                with ui.dialog() as dlg, ui.card().classes('w-96'):
                    ui.label('Hẹn giờ chạy:').classes('text-lg font-bold')
                    
                    ts = course_service.last_meta.get('startDate')
                    if ts:
                        dt = datetime.fromtimestamp(ts/1000)
                    else:
                        dt = datetime.now() + timedelta(minutes=1)
                    
                    # --- NEW UI: Date & Time Pickers ---
                    with ui.row().classes('w-full items-center justify-between'):
                        d_input = ui.input('Ngày', value=dt.strftime('%Y-%m-%d'))
                        with d_input.add_slot('append'):
                            ui.icon('event').classes('cursor-pointer').on('click', lambda: date_menu.open())
                            with ui.menu() as date_menu:
                                ui.date().bind_value(d_input)
                        
                        t_input = ui.input('Giờ', value=dt.strftime('%H:%M'))
                        with t_input.add_slot('append'):
                            ui.icon('access_time').classes('cursor-pointer').on('click', lambda: time_menu.open())
                            with ui.menu() as time_menu:
                                ui.time().bind_value(t_input)

                    async def start():
                        try:
                            target = datetime.strptime(f"{d_input.value} {t_input.value}", '%Y-%m-%d %H:%M')
                            if target < datetime.now():
                                ui.notify('Thời gian này đã qua!', type='warning')
                                return
                                
                            dlg.close()
                            tabs.value = tab_logs
                            print(f"⏰ Đã hẹn giờ chạy vào: {target.strftime('%d/%m/%Y %H:%M:%S')}")
                            await wait_until(target)
                            await callback()
                        except ValueError:
                            ui.notify('Sai định dạng ngày giờ!', type='negative')

                    ui.button('Bắt đầu đếm ngược', on_click=start).classes('w-full bg-indigo-600')
                dlg.open()

            # ================= TAB: ĐĂNG KÝ NHANH =================
            with ui.tab_panel(tab_register):
                with ui.row().classes('items-center mb-4'):
                    def on_sem_change(e): global is_summer_sem; is_summer_sem = e.value
                    ui.switch('Học kỳ Hè', on_change=on_sem_change)
                    
                    async def fetch_courses_ui():
                        global courses_cache
                        courses_cache = [] 
                        await ensure_courses_loaded()
                        ui.notify('Đã cập nhật dữ liệu mới nhất', type='positive')

                    ui.button('Tải danh sách môn', on_click=fetch_courses_ui).props('icon=download')

                reg_table = ui.table(
                    columns=[
                        {'name': 'name', 'label': 'Tên môn', 'field': 'name', 'align': 'left'},
                        {'name': 'code', 'label': 'Mã môn', 'field': 'code', 'align': 'left'},
                        {'name': 'info', 'label': 'Sĩ số', 'field': 'info'},
                    ],
                    rows=[], selection='multiple', row_key='id', pagination=10
                ).classes('w-full')

                async def do_register_fast():
                    if not await ensure_courses_loaded(): return
                    
                    selected = reg_table.selected
                    if not selected:
                        ui.notify('Chưa chọn môn!', type='warning')
                        return
                    
                    indices = [r['id'] for r in selected]
                    tabs.value = tab_logs
                    print(f"\n--- BẮT ĐẦU ĐĂNG KÝ NHANH ({len(indices)} môn) ---")
                    try:
                        failed = await run_safe(register_service.register_subjects(user, indices, courses_cache, is_summer_sem))
                        ui.notify('Hoàn tất. Kiểm tra Logs.', type='positive')
                        if failed:
                            start_sniffing(failed)
                    except Exception as e:
                        print(f"[ERROR] {e}")

                with ui.row():
                    ui.button('Đăng ký ngay', on_click=do_register_fast).classes('mt-4 bg-green-600')
                    ui.button('⏳ Hẹn giờ', on_click=lambda: open_timer_dialog(do_register_fast)).classes('mt-4 ml-2 bg-orange-600')

            # ================= TAB: CUSTOM (LOCAL) =================
            with ui.tab_panel(tab_custom):
                ui.label('Quản lý hồ sơ đăng ký (Lưu trên trình duyệt)').classes('text-h6 mb-2')
                
                custom_selections = {} 

                with ui.splitter(value=30).classes('w-full h-full border rounded') as splitter:
                    
                    # --- LEFT: Saved Lists ---
                    with splitter.before:
                        ui.label('Hồ sơ đã lưu').classes('p-2 font-bold bg-gray-100 block')
                        saved_list_container = ui.column().classes('p-2 w-full')

                        async def refresh_saved_custom_list():
                            saved_list_container.clear()
                            js_code = """
                            const items = [];
                            for (let i = 0; i < localStorage.length; i++) {
                                const key = localStorage.key(i);
                                if (key.startsWith('autotlu_profile_')) {
                                    items.push(key.replace('autotlu_profile_', ''));
                                }
                            }
                            return items;
                            """
                            keys = await ui.run_javascript(js_code, timeout=5.0)
                            
                            if not keys:
                                with saved_list_container:
                                    ui.label('(Trống)').classes('text-gray-400 italic')
                                return

                            for k in keys:
                                with saved_list_container:
                                    with ui.card().classes('w-full mb-2 p-2'):
                                        ui.label(k).classes('font-bold')
                                        with ui.row().classes('w-full justify-between mt-2'):
                                            # Run button
                                            ui.button('Chạy', on_click=lambda key=k: run_custom_profile(key)).props('size=sm color=green icon=play_arrow')
                                            # Timer button
                                            ui.button(on_click=lambda key=k: open_timer_dialog(lambda: run_custom_profile(key))).props('size=sm color=orange icon=timer')
                                            # Delete button
                                            ui.button(on_click=lambda key=k: delete_custom_profile(key)).props('size=sm color=red icon=delete').classes('px-2')

                        async def run_custom_profile(name):
                            if not user: 
                                ui.notify('Chưa đăng nhập!', type='warning')
                                return
                            tabs.value = tab_logs
                            print(f"\n--- CHẠY CUSTOM PROFILE: {name} ---")
                            
                            data = await ui.run_javascript(f'return localStorage.getItem("autotlu_profile_{name}")', timeout=5.0)
                            if not data: return
                            
                            try:
                                courses_json = json.loads(data)
                                target_courses = [Course(d) for d in courses_json]
                                print(f"Đã tải {len(target_courses)} môn từ bộ nhớ trình duyệt.")
                                
                                failed = await run_safe(register_service.register_custom(user, target_courses))
                                ui.notify('Hoàn tất profile.', type='positive')
                                if failed:
                                    start_sniffing(failed)
                            except Exception as e:
                                print(f"[ERROR] Run profile failed: {e}")

                        async def delete_custom_profile(name):
                            # Do not await removeItem
                            ui.run_javascript(f'localStorage.removeItem("autotlu_profile_{name}")')
                            await asyncio.sleep(0.1) # Yield
                            await refresh_saved_custom_list()
                            ui.notify('Đã xóa hồ sơ', type='info')

                        ui.button('Làm mới danh sách', on_click=refresh_saved_custom_list).classes('m-2 w-full')
                        ui.timer(0.5, refresh_saved_custom_list, once=True)

                    # --- RIGHT: Creator ---
                    with splitter.after:
                        ui.label('Tạo hồ sơ mới').classes('p-2 font-bold bg-gray-100 block')
                        
                        creator_container = ui.column().classes('p-4 w-full')
                        
                        def render_creator():
                            creator_container.clear()
                            with creator_container:
                                with ui.row().classes('w-full items-center mb-4'):
                                    prof_name = ui.input('Tên hồ sơ mới (VD: Sang_Thu_3)').classes('flex-grow mr-2')
                                    
                                    async def load_btn():
                                        if await ensure_courses_loaded():
                                            custom_selections.clear()
                                            update_creator_table()
                                            ui.notify('Đã tải môn', type='positive')
                                    
                                    ui.button('Load Môn', on_click=load_btn).props('outline icon=download')

                                    async def save_btn():
                                        name = prof_name.value
                                        if not name or not custom_selections:
                                            ui.notify('Thiếu thông tin!', type='warning')
                                            return
                                        
                                        data = [c.data for c in custom_selections.values()]
                                        js_str = json.dumps(data).replace("'", "\\'")
                                        ui.run_javascript(f"localStorage.setItem('autotlu_profile_{name}', '{js_str}')")
                                        
                                        ui.notify(f'Đã lưu profile: {name}', type='positive')
                                        prof_name.value = ''
                                        custom_selections.clear()
                                        update_creator_table()
                                        refresh_saved_custom_list()

                                    ui.button('Lưu Hồ Sơ', on_click=save_btn).classes('bg-blue-600')

                                creator_table = ui.table(
                                    columns=[
                                        {'name': 'name', 'label': 'Môn học', 'field': 'name', 'align': 'left'},
                                        {'name': 'selected', 'label': 'Lớp đã chọn', 'field': 'selected', 'align': 'left'},
                                        {'name': 'action', 'label': 'Chọn lớp', 'field': 'action'},
                                    ],
                                    rows=[],
                                    row_key='id',
                                    pagination=10
                                ).classes('w-full')
                                
                                creator_table.add_slot('body-cell-action', '''
                                    <q-td :props="props">
                                        <q-btn size="sm" color="indigo" label="Chọn lớp" @click="$parent.$emit('open_dialog', props.row)" />
                                        <q-btn v-if="props.row.has_selection" size="sm" color="red" flat icon="close" @click="$parent.$emit('clear_selection', props.row)" />
                                    </q-td>
                                ''')

                                def update_creator_table():
                                    if not courses_cache: return
                                    rows = []
                                    for i, group in enumerate(courses_cache):
                                        if not group: continue
                                        
                                        sel_course = custom_selections.get(i)
                                        sel_text = f"{sel_course.display_name}" if sel_course else "---"
                                        
                                        rows.append({
                                            'id': i,
                                            'name': group[0].display_name.split('(')[0],
                                            'selected': sel_text,
                                            'has_selection': (sel_course is not None)
                                        })
                                    creator_table.rows = rows
                                    creator_table.update()

                                creator_table.on('open_dialog', lambda e: open_class_dialog(e.args['id']))
                                creator_table.on('clear_selection', lambda e: clear_subject_selection(e.args['id']))
                                
                                def clear_subject_selection(idx):
                                    if idx in custom_selections:
                                        del custom_selections[idx]
                                        update_creator_table()

                                def open_class_dialog(subject_idx):
                                    if not courses_cache or subject_idx >= len(courses_cache): return
                                    options = courses_cache[subject_idx]
                                    subject_name = options[0].display_name.split('(')[0]
                                    
                                    other_courses = [c for idx, c in custom_selections.items() if idx != subject_idx]

                                    with ui.dialog() as dlg, ui.card().classes('w-full max-w-4xl'):
                                        ui.label(f'Chọn lớp cho: {subject_name}').classes('text-h6 font-bold')
                                        with ui.scroll_area().classes('h-96 w-full border rounded p-2'):
                                            for opt in options:
                                                conflict = False
                                                for existing in other_courses:
                                                    if opt.conflicts_with(existing):
                                                        conflict = True
                                                        break
                                                
                                                card_classes = 'w-full mb-2 p-2 border-l-4 '
                                                if conflict:
                                                    card_classes += 'border-red-500 bg-gray-100 opacity-60'
                                                elif custom_selections.get(subject_idx) == opt:
                                                    card_classes += 'border-green-500 bg-green-50'
                                                else:
                                                    card_classes += 'border-gray-300 hover:bg-blue-50 cursor-pointer'

                                                with ui.card().classes(card_classes):
                                                    with ui.row().classes('w-full items-center justify-between'):
                                                        with ui.column():
                                                            ui.label(opt.display_name).classes('font-bold')
                                                            ui.label(f"Sĩ số: {opt.current_students}/{opt.max_students}").classes('text-xs')
                                                        
                                                        if conflict:
                                                            ui.label('TRÙNG LỊCH').classes('text-red-600 font-bold text-xs')
                                                        else:
                                                            def pick(c=opt):
                                                                custom_selections[subject_idx] = c
                                                                dlg.close()
                                                                update_creator_table()
                                                            
                                                            if custom_selections.get(subject_idx) == opt:
                                                                ui.icon('check_circle', color='green').props('size=sm')
                                                            else:
                                                                ui.button('Chọn', on_click=pick).props('size=sm flat color=indigo')

                                        ui.button('Đóng', on_click=dlg.close).classes('w-full mt-2')
                                    
                                    dlg.open()

                        render_creator()

            # --- Browser Opening Bridge for Google ---
            pending_url = []
            def browser_cb(url):
                pending_url.append(url)

            async def check_bridges():
                if pending_url:
                    url = pending_url.pop(0)
                    ui.notify('Mở trình duyệt xác thực...', type='info')
                    ui.run_javascript(f'window.open("{url}", "_blank")')
            
            ui.timer(1.0, check_bridges)

            # ================= TAB: TIỆN ÍCH =================
            with ui.tab_panel(tab_utils):
                ui.label('Tiện ích').classes('text-h6 mb-4')
                with ui.row().classes('w-full justify-center gap-4'):
                    # Nút 1: Xuất ICS
                    with ui.card().classes('w-64 p-4 text-center cursor-pointer hover:shadow-lg transition'):
                        ui.icon('calendar_today', size='4em').classes('text-blue-500 mx-auto')
                        ui.label('Xuất File .ICS').classes('font-bold text-lg mt-2')
                        ui.label('Dùng cho Calendar, Outlook...').classes('text-sm text-gray-500')
                        
                        async def do_export():
                            if not user: 
                                ui.notify('Chưa đăng nhập!', type='warning')
                                return
                            try:
                                content = await run_safe(calendar_service.get_ics_content(user))
                                import datetime
                                fname = f"TLU_{datetime.datetime.now().strftime('%d%m%y')}.ics"
                                ui.download(content.encode('utf-8'), fname)
                                ui.notify('Đang tải...', type='positive')
                            except Exception as e: ui.notify(f"Lỗi: {e}", type='negative')
                        
                        ui.button('Thực hiện', on_click=do_export).classes('w-full mt-4 bg-blue-600')

                    # Nút 2: Google Calendar Sync
                    with ui.card().classes('w-64 p-4 text-center cursor-pointer hover:shadow-lg transition'):
                        ui.icon('sync', size='4em').classes('text-green-500 mx-auto')
                        ui.label('Đồng bộ Google Calendar').classes('font-bold text-lg mt-2')
                        ui.label('Tự động thêm vào GG Calendar').classes('text-sm text-gray-500')
                        
                        async def do_google_sync():
                            if not user:
                                ui.notify('Chưa đăng nhập!', type='warning')
                                return
                            
                            tabs.value = tab_logs 
                            print("\n--- BẮT ĐẦU ĐỒNG BỘ GOOGLE CALENDAR ---")
                            
                            try:
                                # 1. Get token from LocalStorage
                                token_json = await ui.run_javascript("return localStorage.getItem('autotlu_google_token');", timeout=5.0)
                                events = await run_safe(calendar_service.get_tlu_events(user))
                                
                                await run.io_bound(
                                    calendar_service.sync_to_google, 
                                    events, 
                                    initial_token=token_json,
                                    on_token_update=None, # No saving back to local for now or use bridge if needed
                                    browser_callback=browser_cb
                                )
                                
                                ui.notify('Đồng bộ thành công!', type='positive')
                            except Exception as e:
                                print(f"[ERROR] Sync failed: {e}")
                                ui.notify('Lỗi đồng bộ (Xem Logs)', type='negative')

                        ui.button('Thực hiện', on_click=do_google_sync).classes('w-full mt-4 bg-green-600')

            # ================= TAB: LOGS =================
            with ui.tab_panel(tab_logs):
                # Sniffing UI
                row_sniff = ui.row().classes('items-center hidden')
                with row_sniff:
                    ui.spinner('dots').classes('text-green-500')
                    lbl_sniff = ui.label('Đang săn môn (Sniffing)...').classes('font-bold text-green-500 mr-4')
                    ui.button('DỪNG LẠI (STOP)', on_click=lambda: stop_sniffing()).classes('bg-red-600')

                ui.label('Logs').classes('text-h6')
                log_box = ui.log(max_lines=5000).classes('w-full h-96 bg-gray-900 text-green-400 font-mono p-2')
                ui_logger.set_element(log_box)
                ui.button('Xóa logs', on_click=log_box.clear)

        # --- SNIFFING LOGIC ---
        sniff_task = None
        is_sniffing = False

        def start_sniffing(failed):
            nonlocal sniff_task, is_sniffing
            if is_sniffing: return
            
            is_sniffing = True
            row_sniff.classes(remove='hidden')
            print(f"--- BẮT ĐẦU SĂN {len(failed)} MÔN ---")
            
            async def loop():
                targets = failed
                while is_sniffing and targets:
                    print(f"Đang thử lại {len(targets)} môn...")
                    
                    tasks = []
                    url = user.register_summer_url if is_summer_sem else user.register_url
                    
                    async def attempt(c):
                        success = await register_service.register_single_subject(url, [c], [])
                        return (success, c)

                    for c in targets:
                        tasks.append(attempt(c))
                    
                    results = await asyncio.gather(*tasks)
                    
                    new_failed = []
                    for success, course in results:
                        if success: 
                            print(f"[SNIFFED] Săn thành công: {course.display_name}")
                            ui.notify(f"Săn được: {course.display_name}", type='positive')
                        else: 
                            new_failed.append(course)
                    
                    targets = new_failed
                    if not targets:
                        print("ĐÃ SĂN HẾT!")
                        stop_sniffing()
                        break
                    
                    await asyncio.sleep(2)
            
            sniff_task = asyncio.create_task(loop())
            active_tasks.add(sniff_task)

        def stop_sniffing():
            nonlocal is_sniffing, sniff_task
            is_sniffing = False
            if sniff_task: sniff_task.cancel()
            row_sniff.classes(add='hidden')
            print("--- ĐÃ DỪNG SĂN ---")

        async def logout():
            global user
            user = None
            stop_sniffing()
            update_tabs_state()
            tabs.value = t_login
            ui.notify('Đã đăng xuất')
            ui.run_javascript("localStorage.removeItem('autotlu_creds');")

    app.on_shutdown(client.close)
    
    # Check if running as frozen executable (PyInstaller)
    is_frozen = getattr(sys, 'frozen', False)
    
    ui.run(
        title='AutoDangKiTin TLU', 
        port=8090, 
        reload=False, 
        favicon='🎓',
        native=is_frozen,
        window_size=(1200, 800) # Kích thước cửa sổ mặc định cho Native Mode
    )

if __name__ in {"__main__", "__mp_main__"}:
    run_gui()
