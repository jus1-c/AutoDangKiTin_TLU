import os
import sys
import time

def clear():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

def menu(name, student_id, offline_mode=False):
    clear()
    if offline_mode:
        print("---OFFLINE MODE---")
        print("1. Tự động đăng kí tín chỉ")
        print("2. Đăng xuất")
        print("0. Thoát")
    else:
        print(f"Xin chào, {name}")
        print(f"ID của bạn: {student_id}")
        print("\n1. Tự động đăng kí tín chỉ")
        print("2. Đồng bộ lịch của bạn với Google")
        print("3. Đăng xuất")
        print("0. Thoát")
    return input("\nLựa chọn: ")

def register_menu():
    clear()
    print("1. Học kì chính")
    print("2. Học kì hè")
    return input("\nLựa chọn: ")

def schedule_menu(schedule_arr):
    clear()
    print("Lựa chọn đồng bộ:")
    print("1. Đồng bộ tất cả khoá học")
    print("2. Đăng xuất tài khoản Google")
    print("0. Trở về menu")
    return input("\nLựa chọn: ")

def internet_check():
    option = input("Kết nối không ổn định, có muốn chuyển sang offline mode sử dụng token trước đó ?[Y/n]")
    if option.lower() == 'y':
        return True
    elif option.lower() == 'n':
        return False
    else:
        print("Đối số không hợp lệ, script đang thoát...")
        time.sleep(1)
        sys.exit()