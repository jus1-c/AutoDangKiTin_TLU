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
        print("\n1. Tự động đăng kí tín chỉ\n2. Đăng xuất\n0. Thoát")
    else:
        print(f"Xin chào, {name}\nID của bạn: {student_id}\n\n1. Tự động đăng kí tín chỉ\n2. Đồng bộ lịch của bạn với google\n3. Đăng xuất\n0. Thoát")
    return input("\nLựa chọn: ")

def schedule_menu(schedule_arr):
    clear()
    print("Lựa chọn đồng bộ:\n1. Đồng bộ khóa học cụ thể\n2. Đồng bộ tất cả khóa học\n3. Đăng xuất tài khoản google\n0. Trở về menu")
    option = input("\nLựa chọn: ")
    if option == '1':
        clear()
        for i in range(len(schedule_arr)):
            print(i, '.', schedule_arr[i][0]['summary'], '\n')
        return '1', input("Lựa chọn: ")
    return option, None

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