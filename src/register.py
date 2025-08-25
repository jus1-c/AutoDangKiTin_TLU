import httpx
import json
import threading
import time
import os
from datetime import datetime
from src.ui import clear

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
            print("[" + "Thread " + str(i) + "]", 'status_code:', response['status'], "Debug:", response['message'])
            thread_check[i] = 'True'
        elif response['status'] == -6:  # Full slot
            print("[" + "Thread " + str(i) + "]", 'status_code:', response['status'], response['message'])
            thread_check[i] = 'False'
        elif response['status'] == -2:  # Trùng lịch
            print("[" + "Thread " + str(i) + "]", 'status_code:', response['status'], response['message'])
            thread_check[i] = 'False'
        else:
            print("[" + "Thread " + str(i) + "]", 'status_code:', response['status'], response['message'])
            thread_check[i] = 'Error'
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
                    return False

def sniff_send_rq(val, i, course_array, register_url, cookies, headers):
    thread_check = ['' for _ in range(thread_count)]
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
                return False


def sniff_send_rq_custom(course_array, register_url, cookies, headers):
    thread_check = ['' for _ in range(thread_count)]
    for j in range(thread_count):
        thread = threading.Thread(target=send_request, args=(course_array, j, register_url, cookies, headers, thread_check))
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
                        thread = threading.Thread(target=send_request, args=(course_array, k, register_url, cookies, headers, thread_check))
                        thread.start()
            else:
                return False

def auto_register(course_array, course_name_array, register_url, cookies, headers, filename, course_url):
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
    fail_opt = []
    for opt in opt_list:
        if opt == 'all':
            for j in range(len(course_array)):
                if auto_send_request(j, course_array, register_url, cookies, headers):
                    print("\nThành công: " + course_name_array[j])
                else:
                    fail_opt.append(int(opt))
                    print("\nKhông thành công: " + course_name_array[j])
        elif 0 <= int(opt) < len(course_array):
            if auto_send_request(int(opt), course_array, register_url, cookies, headers):
                print("\nThành công: " + course_name_array[int(opt)])
            else:
                fail_opt.append(int(opt))
                print("\nKhông thành công: " + course_name_array[int(opt)])
    if fail_opt != []:
        opt_2 = input("Bạn có muốn kích hoạt sniffing mode cho những môn đăng kí không thành công không [Y/n]?")
        while len(fail_opt) == 0:
            if opt_2 == 'Y' or opt_2 == 'y':
                code_lst = []
                for i in range(len(fail_opt)):
                    for j in range(len(course_array[fail_opt[i]])):
                        code_lst.append(course_array[fail_opt[i]][j]['code'])
                    code = sniffing_mode(course_url, headers, cookies, code_lst)
                    idx_i, idx_j = find_index_by_code(course_array, code)
                    if sniff_send_rq(idx_i, idx_j, course_array, register_url, cookies, headers):
                        print("Đăng kí thành công mã môn học", code)
                    else:
                        print("Đăng kí không thành công mã môn học", code)
                    fail_opt.pop(i)
                    break
            elif opt_2 == 'N' or opt_2 == 'n':
                break
            else:
                print("Đối số không hợp lệ")
    input("\nNhấn phím bất kì để tiếp tục...")

def auto_send_custom_rq(custom_array, register_url, cookies, headers, name, result,  code):
    thread_check = ['' for _ in range(len(thread_count))]
    for i in range(len(thread_count)):
        thread = threading.Thread(target=send_request, args=(custom_array, i, register_url, cookies, headers, thread_check))
        thread.start()
    while True:
        if '' not in thread_check:
            if 'Error' in thread_check:
                for k in range(len(thread_check)):
                    if thread_check[k] == 'Error':
                        thread_check[k] = ''
                        thread = threading.Thread(target=send_request, args=(custom_array, k, register_url, cookies, headers, thread_check))
                        thread.start()
            else:
                if thread_check[i] == 'True':
                    while '' in thread_check:
                        pass
                    result = True
                    break
                else:
                    result = False
                    code = custom_array['code']
                    break
    name = custom_array['displayName']

def send_custom_rq(register_url, course_url, cookies, headers):
    ls = os.listdir('res/custom')
    ls = [x for x in ls if "timer" not in x.split('.')]
    clear()
    print('Các file custom: ')
    for i in range(len(ls)):
        print(i,'.', ls[i])
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
    if not valid_time_checking('custom/'+filename+'.timer'):
        return
    with open('res/custom/'+filename, 'r', encoding='utf8') as f:
        custom_array = json.load(f)
    name = ['' for _ in range(len(custom_array))]
    result = ['' for _ in range(len(custom_array))]
    code_lst = ['' for _ in range(len(custom_array))]
    for i in range(len(custom_array)):
        thread = threading.Thread(target=auto_send_custom_rq, args=(custom_array[i], register_url, cookies, headers, name[i], result[i], code_lst[i]))
        thread.start()
    while '' in result:
        pass
    while(1):
        for i in range(len(code_lst)):
            if code_lst[i] == '':
                code_lst.pop(i)
                break
        break
    true_lst = []
    false_lst = []
    for i in range(len(result)):
        if result[i] == True:
            true_lst.append(name[i])
        else:
            false_lst.append(name[i])
    print("Thành Công:", ", ".join(true_lst))
    print("Thất bại:", ", ".join(false_lst))
    if false_lst != []:
        opt_2 = input("\nBạn có muốn kích hoạt sniffing mode cho những môn đăng kí không thành công không [Y/n]?")
        while code_lst == []:
            if opt_2 == 'Y' or opt_2 == 'y':
                for i in range(len(code_lst)):
                    code = sniffing_mode(course_url, headers, cookies, code_lst)
                    idx = find_index_by_code_custom(custom_array, code)
                    if sniff_send_rq_custom(custom_array[idx], register_url, cookies, headers):
                        print("Đăng kí thành công mã môn học", code)
                    else:
                        print("Đăng kí không thành công mã môn học", code)
                    code_lst.pop(i)
                    break
            elif opt_2 == 'N' or opt_2 == 'n':
                break
            else:
                print("Đối số không hợp lệ")
    input("\nNhấn phím bất kì để tiếp tục...")

def find_index_by_code(course_array, target_code):
    for i, sublist in enumerate(course_array):      # i: index môn
        for j, course in enumerate(sublist):        # j: index lớp trong môn
            if course.get("code") == target_code:
                return i, j
    return None, None

def find_index_by_code_custom(courses, code):
    for i, course in enumerate(courses):
        if course.get("code") == code:
            return i
    return -1

def find_course_info(data, target_code):
    # Đi vào courseRegisterViewObject -> listSubjectRegistrationDtos -> courseSubjectDtos
    crvo = data.get("courseRegisterViewObject", {})
    for subj in crvo.get("listSubjectRegistrationDtos", []):
        for course in subj.get("courseSubjectDtos", []):
            if course.get("code") == target_code:
                # Trả về các thông tin hay dùng
                return {
                    "code": course.get("code"),
                    "displayName": course.get("displayName"),
                    "numberStudent": course.get("numberStudent"),
                    "maxStudent": course.get("maxStudent"),
                    "isFullClass": course.get("isFullClass"),
                }
    return None

def sniffing_mode(course_url, headers, cookies, code):
    while(1):
        while(1):
            try:
                r = httpx.get(course_url, headers=headers, cookies=cookies, verify=False)
                data = json.loads(r.text)
                break
            except Exception as e:
                print("[DEBUG]: Error:", e)
        for c in code:
            course_info = find_course_info(data, c)
            print("[DEBUG]:", "class_code:",c,"isFullClass:", course_info['isFullClass'])
            if course_info['isFullClass'] == False:
                return c
