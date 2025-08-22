import httpx
import json
import threading
import time
import os
from datetime import datetime

thread_count = 20

def valid_time_checking(filename):
    with open("res/" + filename, encoding="utf8") as f:
        time_get = json.load(f)
    starttime = time_get['courseRegisterViewObject']['startDate']
    endtime = time_get['courseRegisterViewObject']['endDate']
    try:
        str_starttime = datetime.fromtimestamp(starttime / 1000)
        str_endtime = datetime.fromtimestamp(endtime / 1000)
    except TypeError:
        print('Không thể lấy thời gian, không thể thực hiện tự đăng kí')
        return False
    current_time = datetime.fromtimestamp(int(time.time()))
    print("Hiện tại:     ", current_time)
    print("Bắt đầu:      ", str_starttime)
    print("Kết thúc:     ", str_endtime, '\n')
    if current_time >= str_endtime:
        print("Đã hết thời gian đăng kí!")
        time.sleep(1)
        input("\nNhấn phím bất kì để tiếp tục...")
        return False
    else:
        for x in range(int(starttime/1000) - int(time.time()) - 5, 0, -1):
            sec = x % 60
            min = int(x/60) % 60
            hrs = x / 3600
            times = f"{int(hrs):02}:{min:02}:{sec:02}"
            print("Bắt đầu chế độ tự động, " + times + " còn lại.", end='\r')
            time.sleep(1)
        return True

def send_request(val, i, register_url, cookies, headers, thread_check):
    try:
        r = httpx.post(register_url, headers=headers, cookies=cookies, json=val, verify=False)
        response = json.loads(r.text)
        if response['status'] == 0:
            print("[" + "Thread " + str(i) + "]", "Debug:", response['message'])
            thread_check[i] = 'True'
        elif response['status'] < 0:
            print("[" + "Thread " + str(i) + "]", response['message'])
            thread_check[i] = 'Error'
        else:
            print("[" + "Thread " + str(i) + "]", response['message'])
            thread_check[i] = 'False'
    except Exception as err:
        print("[" + "Thread " + str(i) + "]", "Exception Error:", err)
        thread_check[i] = 'Error'

def auto_send_request(val, course_array, register_url, cookies, headers):
    thread_check = ['' for _ in range(thread_count)]
    for i in range(len(course_array[val])):
        for j in range(thread_count):
            thread = threading.Thread(target=send_request, args=(course_array[val][i], j, register_url, cookies, headers, thread_check))
            thread.start()
        while True:
            if 'True' in thread_check:
                while '' in thread_check:
                    pass
                return True
            elif '' not in thread_check:
                if 'Error' in thread_check:
                    for k in range(len(thread_check)):
                        if thread_check[k] == 'Error':
                            thread_check[k] = ''
                            thread = threading.Thread(target=send_request, args=(course_array[val][i], k, register_url, cookies, headers, thread_check))
                            thread.start()
                else:
                    if(i == len(course_array[val])):
                        return False
                    else:
                        break
            time.sleep(0.1)

def auto_register(course_array, course_name_array, register_url, cookies, headers, filename):
    for i in range(len(course_array)):
        print(i, '.', course_name_array[i], '\n')
    option = input("Chọn môn để đăng kí (nhập 'all' để chọn tất cả)\nBạn có thể nhập nhiều môn 1 lúc bằng dấu cách: ")
    opt_list = option.split()
    print("Đang tiến hành đăng kí, vui lòng đợi...\n")
    time.sleep(2)
    print("Tips: Chỉ nên chọn những môn thực sự quan trọng vì quá trình đăng kí sẽ rất lâu.\nÀ quên, môn nào nhập trước đăng kí trước nhé :3\n")
    time.sleep(2)
    if not valid_time_checking(filename):
        return
    for opt in opt_list:
        if opt == 'all':
            for j in range(len(course_array)):
                if auto_send_request(j, course_array, register_url, cookies, headers):
                    print("\nThành công: " + course_name_array[j])
                else:
                    print("\nKhông thành công: " + course_name_array[j])
        elif 0 <= int(opt) < len(course_array):
            if auto_send_request(int(opt), course_array, register_url, cookies, headers):
                print("\nThành công: " + course_name_array[int(opt)])
            else:
                print("\nKhông thành công: " + course_name_array[int(opt)])
    input("\nNhấn phím bất kì để tiếp tục...")

def send_custom_rq(register_url, cookies, headers):
    ls = os.listdir('src/custom')
    ls = [x for x in ls if "timer" not in x.split('.')]
    print('Các file custom: ')
    for i in range(len(ls)):
        print(i, ls[i])
    opt = input('\nLựa chọn: ')
    while(1):
        try:
            opt = int(opt)
            if opt >= 0 and opt < len(ls):
                filename = ls[opt]
                break
            else:
                print('Đối số không hợp lệ')
        except ValueError or TypeError:
            print('Đối số không hợp lệ')
    if not valid_time_checking(filename+'.timer'):
        return
    with open('src/custom/'+filename, 'r', encoding='utf8') as f:
        custom_array = json.load(f)
    thread_check = ['' for _ in range(custom_array)]
    for i in range(len(custom_array)):
        thread = threading.Thread(target=send_request, args=(custom_array[i], i, register_url, cookies, headers, thread_check))
        thread.start()
    while True:
        if '' not in thread_check:
            if 'Error' in thread_check:
                for k in range(len(thread_check)):
                    if thread_check[k] == 'Error':
                        thread_check[k] = ''
                        thread = threading.Thread(target=send_request, args=(custom_array[i], k, register_url, cookies, headers, thread_check))
                        thread.start()
            else:
                true_lst = []
                false_lst = []
                for i in range(len(custom_array)):
                    if thread_check[i] == 'True':
                        true_lst.append(custom_array[i]['displayName'])
                    else:
                        false_lst.append(custom_array[i]['displayName'])
                    print("Thành Công:", ", ".join(true_lst))
                    print("Thất bại:", ", ".join(false_lst))
                    break
        input("\nNhấn phím bất kì để tiếp tục...")