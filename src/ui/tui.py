import sys
import os
import time
from typing import List, Optional, Dict, Tuple
from src.models.course import Course
from src.services.custom_service import CustomService

# Handle platform specific imports
if sys.platform == 'win32':
    import msvcrt
else:
    import tty
    import termios

# ANSI Colors
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BG_SELECTED = "\033[44m" 
    GREY = "\033[90m"

class TUI:
    def __init__(self):
        self.width = 80
        # Enable ANSI colors on Windows 10+
        if sys.platform == 'win32':
            os.system('') 
        
    def clear(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def get_key(self):
        """Reads a single keypress (Cross-platform)."""
        if sys.platform == 'win32':
            # Windows Implementation
            key = msvcrt.getch()
            if key == b'\xe0': # Special keys (arrows)
                key = msvcrt.getch()
                if key == b'H': return 'UP'
                if key == b'P': return 'DOWN'
                if key == b'M': return 'RIGHT'
                if key == b'K': return 'LEFT'
            elif key == b'\r': return 'ENTER'
            elif key == b' ':
                return 'SPACE'
            elif key == b'\x03': return 'CTRL_C'
            elif key == b'\x1b': return '\x1b' # Esc
            return key.decode('utf-8', errors='ignore')
        else:
            # Unix Implementation
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
                if ch == '\x1b': 
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        ch3 = sys.stdin.read(1)
                        if ch3 == 'A': return 'UP'
                        if ch3 == 'B': return 'DOWN'
                        if ch3 == 'C': return 'RIGHT'
                        if ch3 == 'D': return 'LEFT'
                if ch == '\r' or ch == '\n': return 'ENTER'
                if ch == ' ':
                    return 'SPACE'
                if ch == '\x03': return 'CTRL_C' 
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def print_center(self, text, color=Colors.WHITE):
        padding = (self.width - len(text)) // 2
        print(f"{ ' ' * padding}{color}{text}{Colors.RESET}")

    def menu_screen(self, title: str, options: List[str]) -> int:
        selected_idx = 0
        while True:
            self.clear()
            print(f"{Colors.CYAN}{'='*self.width}{Colors.RESET}")
            self.print_center(title, Colors.BOLD + Colors.GREEN)
            print(f"{Colors.CYAN}{'='*self.width}{Colors.RESET}\n")

            for i, option in enumerate(options):
                prefix = "  > " if i == selected_idx else "    "
                color = Colors.GREEN if i == selected_idx else Colors.WHITE
                print(f"{prefix}{color}{option}{Colors.RESET}")

            print(f"\n{Colors.CYAN}{'-'*self.width}{Colors.RESET}")
            print("Use Arrow Keys to move, Enter to select.")
            
            key = self.get_key()
            if key == 'UP':
                selected_idx = (selected_idx - 1) % len(options)
            elif key == 'DOWN':
                selected_idx = (selected_idx + 1) % len(options)
            elif key == 'ENTER':
                return selected_idx
            elif key == 'CTRL_C':
                sys.exit()

    def custom_manager_screen(self, service: CustomService) -> Tuple[str, Optional[str]]:
        marked_files = set()
        selected_idx = 0
        
        while True:
            files = service.list_files()
            list_len = len(files)
            total_items = list_len + 2 
            
            self.clear()
            self.print_center("QUẢN LÝ FILE CUSTOM", Colors.BOLD + Colors.YELLOW)
            print("-" * self.width)
            
            if not files:
                print(" (Trống)")
            
            for i, f in enumerate(files):
                is_selected = (i == selected_idx)
                is_marked = (f in marked_files)
                
                cursor = " >" if is_selected else "  "
                mark = "[x]" if is_marked else "[ ]"
                color = Colors.CYAN if is_selected else Colors.WHITE
                
                print(f"{cursor} {color}{mark} {f}{Colors.RESET}")
            
            print("-" * self.width)
            
            btn_idx_del = list_len
            btn_idx_new = list_len + 1
            
            if selected_idx == btn_idx_del:
                print(f" > {Colors.RED}[ Xoá File Đã Chọn ]{Colors.RESET}")
            else:
                print(f"   [ Xoá File Đã Chọn ]")

            if selected_idx == btn_idx_new:
                print(f" > {Colors.GREEN}[ + Tạo Mới ]{Colors.RESET}")
            else:
                print(f"   [ + Tạo Mới ]")
            
            print("\nArrows: Move/Mark. Enter: Action. Esc: Back")

            key = self.get_key()
            if key == 'UP':
                selected_idx = max(0, selected_idx - 1)
            elif key == 'DOWN':
                selected_idx = min(total_items - 1, selected_idx + 1)
            elif key == 'RIGHT' and selected_idx < list_len:
                f = files[selected_idx]
                if f in marked_files: marked_files.remove(f)
                else: marked_files.add(f)
            elif key == 'ENTER':
                if selected_idx == btn_idx_new:
                    return 'CREATE', None
                elif selected_idx == btn_idx_del:
                    if marked_files:
                        service.delete_files(list(marked_files))
                        marked_files.clear()
                        selected_idx = 0 
                elif selected_idx < list_len:
                     return 'RUN', files[selected_idx]
            elif key == '\x1b': 
                return 'BACK', None

    def course_creator_screen(self, all_courses: List[List[Course]], subject_names: List[str]) -> Optional[List[Course]]:
        selections = [-1] * len(all_courses)
        current_subj_idx = 0
        
        while True:
            self.clear()
            self.print_center("TẠO CUSTOM REQUEST MỚI", Colors.BOLD + Colors.GREEN)
            print(f"{Colors.WHITE}Enter: Chọn lớp, Space: Bỏ chọn, Lên/Xuống: Di chuyển{Colors.RESET}")
            print("-" * self.width)
            
            # 1. Render Subject List
            for i, name in enumerate(subject_names):
                is_focused = (i == current_subj_idx)
                selected_opt_idx = selections[i]
                
                cursor = ">>" if is_focused else "  "
                
                if selected_opt_idx == -1:
                    status_color = Colors.WHITE
                    disp_text = "---"
                else:
                    course = all_courses[i][selected_opt_idx]
                    status_color = Colors.GREEN
                    disp_text = f"{course.display_name} (OK)"

                line_color = Colors.BG_SELECTED if is_focused else ""
                display_name = (name[:35] + '..') if len(name) > 35 else name
                print(f"{cursor} {line_color}{display_name:<40} | {status_color}{disp_text}{Colors.RESET}")

            print("-" * self.width)
            
            btn_save_idx = len(subject_names)
            btn_cancel_idx = len(subject_names) + 1
            
            if current_subj_idx == btn_save_idx:
                print(f" > {Colors.GREEN}[ LƯU FILE ]{Colors.RESET}")
            else:
                print(f"   [ LƯU FILE ]")
                
            if current_subj_idx == btn_cancel_idx:
                print(f" > {Colors.RED}[ HUỶ BỎ ]{Colors.RESET}")
            else:
                print(f"   [ HUỶ BỎ ]")

            # 2. Input Handling
            key = self.get_key()
            
            if key == 'UP':
                current_subj_idx = max(0, current_subj_idx - 1)
            
            elif key == 'DOWN':
                current_subj_idx = min(btn_cancel_idx, current_subj_idx + 1)
            
            elif key == 'ENTER':
                if current_subj_idx < len(subject_names):
                    # Gather OTHER selected courses to check conflict
                    other_selected_courses = []
                    for i, idx in enumerate(selections):
                        if idx != -1 and i != current_subj_idx:
                            other_selected_courses.append(all_courses[i][idx])
                    
                    chosen_idx = self.course_option_screen(
                        subject_names[current_subj_idx], 
                        all_courses[current_subj_idx], 
                        selections[current_subj_idx],
                        other_selected_courses
                    )
                    
                    if chosen_idx is not None:
                        selections[current_subj_idx] = chosen_idx
                
                elif current_subj_idx == btn_save_idx:
                    # Collect selections
                    final_courses = []
                    for i, idx in enumerate(selections):
                        if idx != -1:
                            final_courses.append(all_courses[i][idx])
                    
                    if not final_courses:
                         print(f"\n{Colors.RED}Chưa chọn môn nào!{Colors.RESET}")
                         time.sleep(1)
                         continue
                         
                    return final_courses

                elif current_subj_idx == btn_cancel_idx:
                    return None
            
            elif key == 'SPACE':
                if current_subj_idx < len(subject_names):
                    selections[current_subj_idx] = -1

    def course_option_screen(self, subject_name: str, options: List[Course], current_selection: int, other_selections: List[Course]) -> Optional[int]:
        """
        Sub-screen to select a specific class/section.
        Checks conflict against 'other_selections'.
        """
        current_idx = 0 if current_selection == -1 else current_selection
        
        while True:
            self.clear()
            self.print_center(f"CHỌN LỚP CHO MÔN: {subject_name}", Colors.BOLD + Colors.YELLOW)
            print("-" * self.width)
            
            if not options:
                print("Không có lớp học nào.")
                self.get_key()
                return None

            # Calculate disabled status for current view
            options_status = [] # List of (is_disabled, message)
            for opt in options:
                conflict = False
                for existing in other_selections:
                    if opt.conflicts_with(existing):
                        conflict = True
                        break
                
                if conflict:
                    options_status.append((True, "(Đã trùng lịch)"))
                else:
                    options_status.append((False, ""))

            # Render
            for i, opt in enumerate(options):
                is_focused = (i == current_idx)
                is_picked = (i == current_selection)
                is_disabled, conflict_msg = options_status[i]
                
                cursor = ">>" if is_focused else "  "
                
                # Colors
                line_color = Colors.BG_SELECTED if is_focused else ""
                
                if is_disabled:
                    text_color = Colors.RED
                    mark = conflict_msg
                else:
                    text_color = Colors.GREEN if is_picked else Colors.WHITE
                    mark = "(Đang chọn)" if is_picked else "" 
                
                info = f"{opt.display_name} | {opt.current_students}/{opt.max_students}"
                # If too long, truncate
                info = (info[:50] + '..') if len(info) > 50 else info
                
                print(f"{cursor} {line_color}{text_color}{info:<55} {mark}{Colors.RESET}")
            
            print("-" * self.width)
            print("Enter: Chọn. Esc: Quay lại.")

            key = self.get_key()
            
            if key == 'UP':
                current_idx = max(0, current_idx - 1)
            elif key == 'DOWN':
                current_idx = min(len(options) - 1, current_idx + 1)
            elif key == 'ENTER':
                # Prevent selection if disabled
                if options_status[current_idx][0]:
                    # Disabled
                    # Maybe flash screen or do nothing
                    pass
                else:
                    return current_idx
            elif key == '\x1b': # Esc
                return None 
