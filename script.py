import threading
import httpx
import urllib.parse
import json
import os
import sys
import time
import maskpass
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

login_url = "https://sinhvien1.tlu.edu.vn:443/education/oauth/token"
info_url = "https://sinhvien1.tlu.edu.vn:443/education/api/student/getstudentbylogin"
semester_url = "https://sinhvien1.tlu.edu.vn:443/education/api/semester/semester_info"
course_url = ""
register_url = ""
schedule_url = ""
calendar_url = "https://www.googleapis.com/auth/calendar"

course_array = []
course_name_array = []

offline_mode = False

username = ""
password = ""
login_data = ""
name = ""
student_id = ""

global_timeout = 30

cookies = ""
headers = ""

def main():
    internet_check()
    login()
    user_info()
    menu()

def clear():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

def internet_connection():
    try:
        response = httpx.get(login_url, timeout=global_timeout, verify=False)
        return 0
    except:
        return 1

def internet_check():
    global offline_mode
    status = internet_connection()
    if(status == 1 and os.path.exists("token.json")):
        option = input("Kết nối không ổn định, có muốn chuyển sang offline mode sử dụng token trước đó ?[Y/n]")
        if option == 'Y' or option == 'y':
            clear()
            offline_mode = True
            time.sleep(1)
            menu_offline()
        elif option == 'N' or option == 'n':
            clear()
            main()
        else:
            print("Đối số không hợp lệ, script đang thoát...")
            time.sleep(1)
            sys.exit()

def login_check(r):
    try:
        if 'error' in r.text:
            print("Tài khoản hoặc mật khẩu không đúng !\n")
            time.sleep(1)
            clear()
            login()
        elif '502 Bad Gateway' in r.text:
            print("Lỗi 502, vui lòng thử lại sau")
            sys.exit()
        else:
            print("\nĐăng nhập thành công !")
            time.sleep(1)
            clear()
            if os.path.exists("login.json") == False:
                option = input("Bạn muốn lưu mật khẩu cho lần đăng nhập tiếp theo ? [Y/n]")
                if option == 'Y' or option == 'y':
                    make_login_json()
                elif option == 'N' or option == 'n':
                    pass
                else:
                    print("Đối số không hợp lệ, script sẽ không lưu mật khẩu")
                    time.sleep(1)
    except httpx.ConnectTimeout:
        print("Thời gian chờ quá lâu, vui lòng thử lại")
        sys.exit()
    except httpx.ConnectError:
        print("Vui lòng kiểm tra internet của bạn và thử lại sau")
        sys.exit()

def login():
    global username, password, cookies, headers
    if os.path.exists("token.json"):
        f = open("token.json")
        token = json.load(f)
        cookies = {"token": token['token']}
        headers = {"Authorization": token['Authorization']}
        r = httpx.get(info_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
        if "error" not in r.text:
            print("Đang đăng nhập tự động bằng token...")
            time.sleep(1)
            return
    if os.path.exists("login.json"):
        f = open("login.json")
        login = json.load(f)
        username = login['username']
        password = login['password']
    else:
        username = input("Username: ")
        password = maskpass.askpass(prompt="Password: ", mask="*")
    login_data = {"client_id": "education_client", "grant_type": "password", "username": username, "password": password, "client_secret": "password"}
    r = httpx.post(login_url, data=login_data, timeout=global_timeout, verify=False)
    login_check(r)
    get_token(r)

def make_login_json():
    login = {
        "username": username,
        "password": password
    }
    with open("login.json", "w") as outfile:
        json.dump(login, outfile)
    time.sleep(1)

def user_info():
    global student_id, name, course_url, register_url, schedule_url
    r = httpx.get(info_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
    name = json.loads(r.text)['displayName']
    student_id = json.loads(r.text)['id']
    r2 = httpx.get(semester_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
    course_url = "https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/" + str(student_id) + "/" + str(json.loads(r2.text)['semesterRegisterPeriods'][0]['id'])
    register_url = "https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/" + str(student_id) + "/" + str(json.loads(r2.text)['semesterRegisterPeriods'][0]['id'])
    schedule_url = "https://sinhvien1.tlu.edu.vn/education/api/StudentCourseSubject/studentLoginUser/" + str(json.loads(r2.text)['id'])
    u_info = {
        "course_url": course_url,
        "register_url": register_url,
        "schedule_url": schedule_url
    }
    with open("user_info.json", "w") as outfile:
        json.dump(u_info, outfile)

def get_token(r):
    global cookies, headers
    cookies = {"token": urllib.parse.quote_plus(r.text)}
    access_token = "Bearer " + json.loads(r.text)['access_token']
    headers = {"Authorization": access_token}
    token = {
        "token": urllib.parse.quote_plus(r.text),
        "Authorization": access_token
    }
    with open("token.json", "w") as outfile:
        json.dump(token, outfile)

def get_course_list():
    try:
        r = httpx.get(course_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
        print("Lấy dữ liệu thành công")
        with open("all_course.json", "w", encoding="utf-8") as f:
            f.write(r.text)
        time.sleep(1)
        clear()
    except:
        if os.path.exists("all_course.json"):
            print("Không thể kết nối đến máy chủ, script sẽ sử dụng dữ liệu từ lần chạy trước")
            time.sleep(1)
            clear()
        else:
            print("Không thể kết nối đến máy chủ và không có dữ liệu từ lần chạy trước đó.\nScript sẽ tự ngắt sau 5 giây...")
            time.sleep(5)
            sys.exit()

def make_course_array():
    global course_array, course_name_array
    get_course_list()
    if len(course_array) > 0:
        return
    f = open('all_course.json', encoding="utf8")
    course_list = json.load(f)
    course_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'])
    for i in range(course_length):
        temp_arr = []
        if course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'] is not None:
            subcourse_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'])
            for j in range(subcourse_length):
                try:
                    subcourse = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]
                    temp_arr.append(subcourse)
                except TypeError:
                    continue
        else:
            courseSubjectDtos_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'])
            for k in range(courseSubjectDtos_length):
                try:
                    course = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]
                    temp_arr.append(course)
                except TypeError:
                    continue
        course_array.insert(i, temp_arr)
        course_name_array.append(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['subjectName'])

def valid_time_checking():
    f = open("all_course.json", encoding="utf8")
    time_get = json.load(f)
    starttime = time_get['courseRegisterViewObject']['startDate']
    endtime = time_get['courseRegisterViewObject']['endDate']
    str_starttime = datetime.fromtimestamp(starttime / 1000)
    str_endtime = datetime.fromtimestamp(endtime / 1000)
    current_time = datetime.fromtimestamp(int(time.time()))
    print("Hiện tại:     ", current_time)
    print("Bắt đầu:      ", str_starttime)
    print("Kết thúc:     ", str_endtime, '\n')

    if current_time >= str_endtime:
        print("Đã hết thời gian đăng kí!")
        time.sleep(3)
        clear()
        menu()
    else:
        for x in range(int(starttime/1000) - int(time.time()), 0, -1):
            sec = x % 60
            min = int(x/60) % 60
            hrs = x / 3600
            times = f"{int(hrs):02}:{min:02}:{sec:02}"
            print("Bắt đầu chế độ tự động, " + times + " còn lại.", end='\r')
            time.sleep(1)

def auto_register():
    make_course_array()
    for i in range(len(course_array)):
        print(i, '.', course_name_array[i], '\n')
    option = input("Chọn môn để đăng kí (nhập 'all' để chọn tất cả)\nBạn có thể nhập nhiều môn 1 lúc bằng dấu cách: ")
    opt_list = option.split()
    clear()
    try:
        print("Đang tiến hành đăng kí, vui lòng đợi...\n")
        time.sleep(2)
        print("Tips: Chỉ nên chọn những môn thực sự quan trọng vì quá trình đăng kí sẽ rất lâu.\nÀ quên, môn nào nhập trước đăng kí trước nhé :3\n")
        time.sleep(3)
        clear()
        time.sleep(2)
        #valid_time_checking()
        for _ in range(len(opt_list)):
            if opt_list[0] == 'all':
                for j in range(len(course_array)):
                    if(auto_send_request(j)):
                        print("\nThành công: " + course_name_array[j])
                    else:
                        print("\nKhông thành công: " + course_name_array[j])
            if int(opt_list[_]) >= 0 and int(opt_list[_]) < len(course_array):
                if(auto_send_request(int(opt_list[_]))):
                    print("\nThành công: " + course_name_array[int(opt_list[_])])
                else:
                    print("\nKhông thành công: " + course_name_array[int(opt_list[_])])
        print("\nNhấn phím bất kì để tiếp tục...")
        input()
        if(offline_mode):
            menu_offline()
        else:
            menu()
    except TypeError or ValueError:
        print("Lỗi đầu vào, vui lòng nhập lại")
        time.sleep(1)
        clear()
        auto_register()

def send_request(val, i):
    global thread_check
    try:
        r = httpx.post(register_url, headers=headers, cookies=cookies, json=course_array[val][i], verify=False)
        response = json.loads(r.text)
        if response['status'] == 0:
            thread_check[i] = 'True'
        else:
            thread_check[i] = 'False'
    except Exception as err:
        #print(err)
        thread_check[i] = 'Error'

def auto_send_request(val):
    global thread_check
    thread_check = []
    for i in range(len(course_array[val])):
        thread_check.append('')
        thread = threading.Thread(target=send_request, args=(val, i))
        thread.start()
    print("Số thread hiện tại: ", len(course_array[val]))
    while(1):
        print(thread_check, end='\r')
        if 'True' in thread_check:
            return True
        elif '' not in thread_check:
            if 'Error' in thread_check:
                err_threads = []
                for i in range(len(thread_check)):
                    if thread_check[i] == 'Error':
                        thread_check[i] == ''
                        thread = threading.Thread(target=send_request, args=(val, i))
                        err_threads.append(thread)
                        thread.start()
                for thread in err_threads:
                    thread.join()
            else:
                return False
        time.sleep(0.1)

def make_token():
    creds = None
    credentials = {
        "installed":{
        "client_id":"751761844308-thsltjs8rp2l1r0tpqt13jan8981nenn.apps.googleusercontent.com",
                                "project_id":"auto-schedule-433602",
                                "auth_uri":"https://accounts.google.com/o/oauth2/auth",
                                "token_uri":"https://oauth2.googleapis.com/token",
                                "auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",
                                "client_secret":"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",
                                "redirect_uris":["http://localhost"]}}
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", calendar_url)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                os.remove("token.json")
                make_token()
        else:
            flow = InstalledAppFlow.from_client_config(credentials, calendar_url)
            creds = flow.run_local_server(port=0, open_browser=False)
    with open("token.json", "w") as token:
        token.write(creds.to_json())
    cal = build('calendar', 'v3', credentials=creds)
    try:
        r = httpx.get(schedule_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
        schedule = json.loads(r.text)
        schedule_arr = make_schedule_arr(schedule)
        clear()
        time.sleep(1)
        schedule_menu(cal, schedule_arr)
    except HttpError as err:
            print(err)

def make_schedule_arr(schedule):
    schedule_arr = []
    for i in range(len(schedule)):
        temp_arr = []
        title = schedule[i]['courseSubject']['displayName']
        timetables_length = len(schedule[i]['courseSubject']['timetables'])
        for j in range(timetables_length):
            desc = schedule[i]['courseSubject']['timetables'][j]['startHour']['name'] + "->" + schedule[i]['courseSubject']['timetables'][j]['endHour']['name'] + " || " + schedule[i]['courseSubject']['timetables'][j]['room']['name']
            starttime = schedule[i]['courseSubject']['timetables'][j]['startHour']['startString']
            endtime = schedule[i]['courseSubject']['timetables'][j]['endHour']['endString']
            startdate = schedule[i]['courseSubject']['timetables'][j]['startDate'] / 1000
            enddate = schedule[i]['courseSubject']['timetables'][j]['endDate'] / 1000
            week_index = schedule[i]['courseSubject']['timetables'][j]['weekIndex']
            while(startdate < enddate):
                start_datetime = startdate + (week_index_convert(week_index) * 86400)
                start_datetime_str = str(datetime.fromtimestamp(start_datetime))[0:10] + 'T' + starttime + ":00+07:00"
                end_datetime_str = str(datetime.fromtimestamp(start_datetime))[0:10] + 'T' + endtime + ":00+07:00"
                startdate += 7 * 86400
                event = {
                        'summary': title,
                        'description': desc,
                        'start': {
                            'dateTime': start_datetime_str,
                        },
                        'end': {
                            'dateTime': end_datetime_str,
                        },
                        'reminders': {
                            'useDefault': False,
                            'overrides': [
                                {'method': 'popup', 'minutes': 30},
                                ],
                            },
                        }
                temp_arr.append(event)
        schedule_arr.insert(i, temp_arr)
    return schedule_arr

def send_schedule(cal, schedule_arr, i):
    for j in range(len(schedule_arr[i])):
        ev = cal.events().list(calendarId='primary',
            timeMin = schedule_arr[i][j]['start']['dateTime'],
            timeMax = schedule_arr[i][j]['end']['dateTime']).execute()
        try:
            for k in range(len(ev)):
                if ev['items'][k]['summary'] == schedule_arr[i][j]['summary']:
                    cal.events().delete(calendarId='primary', eventId=ev['items'][k]['id']).execute()
                    event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[i][j]).execute()
                    print('Sự kiện được ghi đè: %s' % (event.get('htmlLink')))
                    break
        except IndexError:
            event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[i][j]).execute()
            print('Sự kiện được thêm mới: %s' % (event.get('htmlLink')))

def schedule_menu(cal, schedule_arr):
        print("Lựa chọn đồng bộ:\n")
        print("1. Đồng bộ khóa học cụ thể")
        print("2. Đồng bộ tất cả khóa học")
        print("3. Đăng xuất tài khoản google")
        print("0. Trở về menu")
        option = input("\nLựa chọn: ")
        if option == '0':
            menu()
        elif option == '1':
            clear()
            for i in range(len(schedule_arr)):
                print(i, '.', schedule_arr[i][0]['summary'], '\n')
            sub_option_1 = input("Lựa chọn: ")
            clear()
            if int(sub_option_1) >= 0 and int(sub_option_1) < len(schedule_arr):
                send_schedule(cal, schedule_arr, int(sub_option_1))
                print("\nNhấn phím bất kì để tiếp tục...")
                input()
                clear()
                schedule_menu(cal, schedule_arr)
        elif option == '2':
            clear()
            for i in range(len(schedule_arr)):
                print(i, '.', schedule_arr[i][0]['summary'])
                send_schedule(cal, schedule_arr, i)
                print()
            print("Nhấn phím bất kì để tiếp tục...")
            clear()
            schedule_menu(cal, schedule_arr)
        elif option == '3':
            print("Đăng xuất thành công !")
            os.remove("token.json")
            time.sleep(1)
            menu()
        else:
            print("Đối số không hợp lệ")
            time.sleep(1)
            clear()
            make_token()

def week_index_convert(x):
    if x == 1:
        return 6
    elif x == 2:
        return 0
    elif x == 3:
        return 1
    elif x == 4:
        return 2
    elif x == 5:
        return 3
    elif x == 6:
        return 4
    elif x == 7:
        return 5
    
def menu():
    clear()
    print("Xin chào, " + name)
    print("ID của bạn: " + str(student_id))
    print("\n")
    print("1. Tự động đăng kí tín chỉ")
    print("2. Đồng bộ lịch của bạn với google")
    print("3. Đăng xuất")
    print("0. Thoát")

    option = input("\nLựa chọn: ")
    if option == '1':
        clear()
        auto_register()
    elif option == '2':
        clear()
        make_token()
    elif option == '3':
        os.remove("login.json")
        os.remove("token.json")
        print("Đăng xuất thành công !")
        time.sleep(1)
        menu()
    elif option == '0':
        print("Gặp lại sau !")
        sys.exit()
    else:
        print("Đối số không hợp lệ")
        time.sleep(1)
        menu()

def offline_feature():
    global course_url, register_url, schedule_url, cookies, headers
    f = open("user_info.json")
    url = json.load(f)
    course_url = url['course_url']
    register_url = url['register_url']
    schedule_url = url['schedule_url']
    f = open("token.json")
    token = json.load(f)
    cookies = {"token": token['token']}
    headers = {"Authorization": token['Authorization']}

def menu_offline():
    clear()
    offline_feature()
    print("---OFFLINE MODE---")
    print("\n")
    print("1. Tự động đăng kí tín chỉ")
    print("2. Đăng xuất")
    print("0. Thoát")

    option = input("\nLựa chọn: ")
    if option == '1':
        clear()
        auto_register()
    elif option == '2':
        os.remove("login.json")
        os.remove("token.json")
        print("Đăng xuất thành công !")
        time.sleep(1)
        menu()
    elif option == '0':
        print("Gặp lại sau !")
        sys.exit()
    else:
        print("Đối số không hợp lệ")
        time.sleep(1)
        menu()

if __name__ == "__main__":
    main()