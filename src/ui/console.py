import os
import sys

class ConsoleUI:
    @staticmethod
    def clear():
        os.system('cls' if os.name == 'nt' else 'clear')

    @staticmethod
    def print_header():
        print("========================================")
        print("      AutoDangKiTin TLU - AsyncIO       ")
        print("========================================")

    @staticmethod
    def main_menu(user_name: str) -> str:
        ConsoleUI.print_header()
        print(f"Xin chào, {user_name}!")
        print("1. Đăng ký tín chỉ")
        print("2. Tự động đăng ký (Custom)")
        print("3. Đồng bộ Google Calendar (Tạo lịch mới)")
        print("4. Đăng xuất")
        print("0. Thoát")
        return input("Lựa chọn: ")

    @staticmethod
    def register_menu() -> str:
        print("\n--- Menu Đăng Ký ---")
        print("1. Học kỳ chính")
        print("2. Học kỳ hè")
        print("3. Custom Request")
        print("0. Quay lại")
        return input("Lựa chọn: ")

    @staticmethod
    def get_login_input() -> tuple:
        print("\n--- Đăng Nhập ---")
        u = input("Username: ")
        p = input("Password: ")
        return u, p

    @staticmethod
    def select_courses(subject_names: list) -> list:
        """Returns list of INDICES to register."""
        print("\nDanh sách môn học:")
        for i, name in enumerate(subject_names):
            print(f"{i}. {name}")
            
        choice = input("\nChọn môn (nhập số, cách nhau bởi dấu cách, hoặc 'all'): ")
        if choice.strip() == 'all':
            return list(range(len(subject_names)))
            
        try:
            return [int(x) for x in choice.split() if x.strip().isdigit()]
        except:
            return []
