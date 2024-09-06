import httpx
import urllib.parse
import json
import os
import time
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

username = ""
password = ""
login_data = ""
name = ""
student_id = ""

starttime = 0
endtime = 0
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
        response = httpx.get(login_url, timeout=global_timeout)
        return 0
    except httpx.ConnectTimeout:
        return 1
    except httpx.ReadTimeout:
        return 1
    except httpx.ConnectError:
        return 2

def internet_check():
    if internet_connection() == 1:
        print("Thời gian chờ quá lâu, vui lòng thử lại")
        exit()
    elif internet_connection() == 2:
        print("Vui lòng kiểm tra internet của bạn và thử lại sau")
        exit()

def login_check(r):
    try:
        if 'error' in r.text:
            print("Tài khoản hoặc mật khẩu không đúng !\n")
            time.sleep(1)
            clear()
            login()
        elif '502 Bad Gateway' in r.text:
            print("Phía server ngắt kết nối, vui lòng thử lại sau")
            exit()
        else:
            print("Đăng nhập thành công !")
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
        exit()
    except httpx.ConnectError:
        print("Vui lòng kiểm tra internet của bạn và thử lại sau")
        exit()

def login():
    global username, password
    if os.path.exists("login.json"):
        f = open("login.json")
        login = json.load(f)
        username = login['username']
        password = login['password']
    else:
        username = input("Username: ")
        password = input("Password: ")
    login_data = {"client_id": "education_client", "grant_type": "password", "username": username, "password": password, "client_secret": "password"}
    r = httpx.post(login_url, data=login_data, timeout=global_timeout)
    login_check(r)
    cookies_renew(r)

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
    r = httpx.get(info_url, headers=headers, cookies=cookies, timeout=global_timeout)
    name = json.loads(r.text)['displayName']
    student_id = json.loads(r.text)['id']
    r2 = httpx.get(semester_url, headers=headers, cookies=cookies, timeout=global_timeout)
    course_url = "https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/" + str(student_id) + "/" + str(json.loads(r2.text)['semesterRegisterPeriods'][0]['id'])
    register_url = "https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/" + str(student_id) + "/" + str(json.loads(r2.text)['semesterRegisterPeriods'][0]['id'])
    schedule_url = "https://sinhvien1.tlu.edu.vn/education/api/StudentCourseSubject/studentLoginUser/" + str(json.loads(r2.text)['id'])

def cookies_renew(r):
    global cookies, headers
    cookies = {"token": urllib.parse.quote_plus(r.text)}
    access_token = "Bearer " + json.loads(r.text)['access_token']
    headers = {"Authorization" : access_token}  

def get_course_list():
    try:
        r = httpx.get(course_url, headers=headers, cookies=cookies, timeout=global_timeout)
        print("Lấy dữ liệu thành công")
        with open("all_course.json", "w", encoding="utf-8") as f:
            f.write(r.text)
        time.sleep(1)
        clear()
        return
    except httpx.ConnectTimeout or httpx.ConnectError or OSError:
        if os.path.exists("all_course.json"):
            print("Không thể kết nối đến máy chủ, script sẽ sử dụng dữ liệu từ lần chạy trước")
            time.sleep(1)
            return
        else:
            print("Không thể kết nối đến máy chủ và không có dữ liệu từ lần chạy trước đó.\nScript sẽ tự ngắt sau 5 giây...")
            time.sleep(5)
            exit()

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
                    sub_start_hour = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['timetables'][0]['startHour']['startString']
                    temp_arr.append(subcourse)
                except TypeError:
                    continue
        else:
            courseSubjectDtos_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'])
            for k in range(courseSubjectDtos_length):
                try:
                    course = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]
                    start_hour = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['timetables'][0]['startHour']['startString']
                    temp_arr.append(course)
                except TypeError:
                    continue
        course_array.insert(i, temp_arr)
        course_name_array.append(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['subjectName'])

def auto_register():
    make_course_array()
    for i in range(len(course_array)):
        print(i, '.', course_name_array[i], '\n')
    option = input("Chọn môn để đăng kí (nhập 'all' để  chọn tất cả)\nBạn có thể nhập nhiều môn 1 lúc bằng dấu cách: ")
    opt_list = option.split()
    try:
        for i in range(len(opt_list)):
            if opt_list[0] == 'all':
                for i in range(len(course_array)):
                    auto_send_request(i)
            if int(opt_list[i]) >= 0 and int(opt_list[i]) < len(course_array):
                auto_send_request(i)
    except TypeError:
        print("Lỗi đầu vào, vui lòng nhập lại")
        time.sleep(1)
        clear()
        auto_register()
            
def auto_send_request(val):
    global course_array
    while(1):
        for i in range(len(course_array[val])):
            try:
                r = httpx.post(register_url, headers=headers, cookies=cookies, json=course_array[val][i])
                response = json.loads(r.text)
                if course_array[i] == None:
                    continue
                elif response['status'] == 0:
                    course_array[i] = None
                    return
            except httpx.ConnectError:
                pass
            except httpx.ConnectTimeout:
                pass

def send_schedule_to_google():
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
                send_schedule_to_google()
        else:
            flow = InstalledAppFlow.from_client_config(credentials, calendar_url)
            creds = flow.run_local_server(port=0, open_browser=False)
    with open("token.json", "w") as token:
        token.write(creds.to_json())
    cal = build('calendar', 'v3', credentials=creds)
    try:
        r = httpx.get(schedule_url, headers=headers, cookies=cookies)
        schedule = json.loads(r.text)
        schedule_arr = make_schedule_arr(schedule)
        clear()
        time.sleep(1)
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
                for i in range(len(schedule_arr[int(sub_option_1)])):
                    ev = cal.events().list(calendarId='primary',
                            timeMin = schedule_arr[int(sub_option_1)][i]['start']['dateTime'],
                            timeMax = schedule_arr[int(sub_option_1)][i]['end']['dateTime']).execute()
                    try:
                        if ev['items'][0]['summary'] == schedule_arr[int(sub_option_1)][i]['summary'] and ev['items'][0]['description'] == schedule_arr[int(sub_option_1)][i]['description']:
                            continue
                        else:
                            cal.events().delete(calendarId='primary', eventId=ev['items'][0]['id']).execute()
                            event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[int(sub_option_1)][i]).execute()
                            print('Sự kiện đã được thêm: %s' % (event.get('htmlLink')))
                            time.sleep(0.5)
                    except IndexError:
                        event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[int(sub_option_1)][i]).execute()
                        print('Sự kiện đã được thêm: %s' % (event.get('htmlLink')))
                print("\nNhấn phím bất kì để tiếp tục...")
                input()
                send_schedule_to_google()
        elif option == '2':
            clear()
            for i in range(len(schedule_arr)):
                for j in range(len(schedule_arr[i])):
                    ev = cal.events().list(calendarId='primary',
                        timeMin = schedule_arr[i][j]['start']['dateTime'],
                        timeMax = schedule_arr[i][j]['end']['dateTime']).execute()
                    try:
                        if ev['items'][0]['summary'] == schedule_arr[i][j]['summary'] and ev['items'][0]['description'] == schedule_arr[i][j]['description']:
                            continue
                        else:
                            cal.events().delete(calendarId='primary', eventId=ev['items'][0]['id']).execute()
                            event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[i][j]).execute()
                            print('Sự kiện đã được thêm: %s' % (event.get('htmlLink')))
                            time.sleep(0.5)
                    except IndexError:
                        event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[i][j]).execute()
                        print('Sự kiện đã được thêm: %s' % (event.get('htmlLink')))
                        time.sleep(0.5)
            print("\nNhấn phím bất kì để tiếp tục...")
            input()
            menu()
        elif option == '3':
            print("Đăng xuất thành công !")
            os.remove("token.json")
            time.sleep(1)
            menu()
        else:
            print("Đối số không hợp lệ")
            time.sleep(1)
            clear()
            send_schedule_to_google()
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
            while(startdate <= enddate + 86400):
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
        send_schedule_to_google()
    elif option == '3':
        os.remove("login.json")
        print("Đăng xuất thành công !")
        time.sleep(1)
        menu()
    elif option == '0':
        print("Gặp lại sau !")
        exit()
    else:
        print("Đối số không hợp lệ")
        time.sleep(1)
        menu()

main()
