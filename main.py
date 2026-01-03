import asyncio
import sys
import json
import os
from src.config import Config
from src.core.client import TLUClient
from src.services.auth_service import AuthService
from src.services.course_service import CourseService
from src.services.register_service import RegisterService
from src.services.calendar_service import CalendarService
from src.services.custom_service import CustomService
from src.ui.tui import TUI
from src.models.course import Course
from src.core.exceptions import LoginError, NetworkError

async def main():
    Config.ensure_dirs()
    tui = TUI()
    
    # Initialize Services
    client = TLUClient()
    auth_service = AuthService(client)
    course_service = CourseService(client)
    register_service = RegisterService(client)
    calendar_service = CalendarService(client)
    custom_service = CustomService()

    user = None

    # --- Login Flow ---
    tui.clear()
    print("Đang khởi động...")
    try:
        user = await auth_service.load_saved_user()
    except Exception:
        pass

    if not user:
        while not user:
            tui.clear()
            print("--- ĐĂNG NHẬP ---")
            u = input("Username: ")
            p = input("Password: ") 
            try:
                user = await auth_service.login(u, p)
            except Exception as e:
                print(f"Lỗi: {e}")
                await asyncio.sleep(2)

    # --- Main Loop ---
    while True:
        try:
            debug_status = "ON" if Config.DEBUG else "OFF"
            options = [
                "1. Đăng ký tín chỉ (Theo danh sách)",
                "2. Tự động đăng ký (Custom Request)",
                "3. Xuất lịch học (.ics)",
                "4. Đồng bộ Google Calendar",
                f"5. Chế độ Debug: {debug_status}",
                "6. Đăng xuất",
                "0. Thoát"
            ]
            
            choice_idx = tui.menu_screen(f"AutoDangKiTin - {user.full_name}", options)
            
            if choice_idx == 0: # Register Normal
                sem_opts = ["1. Học kỳ chính", "2. Học kỳ hè", "0. Quay lại"]
                sem_idx = tui.menu_screen("CHỌN HỌC KỲ", sem_opts)
                
                if sem_idx == 2: continue
                is_summer = (sem_idx == 1)
                
                print("Đang tải dữ liệu...")
                courses, names = await course_service.fetch_courses(user, is_summer)
                
                tui.clear()
                print("Danh sách môn học:")
                for i, n in enumerate(names):
                    print(f"{i}. {n}")
                print("\nNhập các số thứ tự môn muốn đăng ký (cách nhau bởi dấu cách), hoặc 'all':")
                
                sel = input("Lựa chọn: ")
                indices = []
                if sel.strip() == 'all':
                    indices = list(range(len(names)))
                else:
                    try:
                        indices = [int(x) for x in sel.split() if x.strip().isdigit()]
                    except:
                        pass
                
                if indices:
                    await register_service.register_subjects(user, indices, courses, is_summer)
                input("Nhấn Enter để tiếp tục...")

            elif choice_idx == 1: # Custom Request
                while True:
                    action, payload = tui.custom_manager_screen(custom_service)
                    
                    if action == 'BACK':
                        break
                    
                    elif action == 'CREATE':
                        sem_opts = ["1. Học kỳ chính", "2. Học kỳ hè"]
                        s_idx = tui.menu_screen("CHỌN HỌC KỲ CHO FILE MỚI", sem_opts)
                        is_summer = (s_idx == 1)
                        
                        print("Đang tải dữ liệu...")
                        courses, names = await course_service.fetch_courses(user, is_summer)
                        
                        selected_courses = tui.course_creator_screen(courses, names)
                        
                        if selected_courses:
                            fname = custom_service.save_request(selected_courses)
                            print(f"Đã lưu file: {fname}")
                            await asyncio.sleep(1)
                            
                    elif action == 'RUN':
                        filename = payload
                        path = os.path.join(Config.RES_DIR, "custom", filename)
                        try:
                            with open(path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            target_courses = [Course(d) for d in data]
                            
                            print(f"Đang chạy file {filename} ({len(target_courses)} môn)...")
                            await register_service.register_custom(user, target_courses)
                            input("Hoàn tất. Nhấn Enter...")
                        except Exception as e:
                            print(f"Lỗi khi chạy file: {e}")
                            input()

            elif choice_idx == 2: # Export ICS
                print("Đang tạo lịch...")
                try:
                    p = await calendar_service.export_ics(user)
                    print(f"Đã tạo: {p}")
                except Exception as e:
                    print(f"Lỗi: {e}")
                input("Enter...")

            elif choice_idx == 3: # Sync Google
                print("Đang đồng bộ...")
                try:
                    await calendar_service.sync_schedule(user)
                    print("\n[THÀNH CÔNG] Đã thêm lịch mới vào tài khoản Google của bạn.")
                except Exception as e:
                    print(f"Lỗi khi đồng bộ: {e}")
                input("\nNhấn Enter để tiếp tục...")
            
            elif choice_idx == 4: # Toggle Debug
                Config.DEBUG = not Config.DEBUG
                # Force save to environment variable not really possible easily, 
                # but it persists in memory for this session.
                pass

            elif choice_idx == 5: # Logout
                if os.path.exists(Config.TOKEN_FILE): os.remove(Config.TOKEN_FILE)
                if os.path.exists(Config.LOGIN_FILE): os.remove(Config.LOGIN_FILE)
                if os.path.exists(Config.GOOGLE_TOKEN_FILE): os.remove(Config.GOOGLE_TOKEN_FILE)
                break
            
            elif choice_idx == 6: # Exit
                break

        except KeyboardInterrupt:
            break
        except Exception as e:
             print(f"Lỗi không mong muốn: {e}")
             import traceback
             traceback.print_exc()
             input("Enter to continue...")

    await client.close()
    print("Bye.")

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass