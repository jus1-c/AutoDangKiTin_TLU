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

login_url = "https://sinhvien1.tlu.edu.vn:443/education/oauth/token"
info_url = "https://sinhvien1.tlu.edu.vn:443/education/api/student/getstudentbylogin"
semester_url = "https://sinhvien1.tlu.edu.vn:443/education/api/semester/semester_info"
course_url = ""
register_url = ""
schedule_url = ""
calendar_url = "https://www.googleapis.com/auth/calendar"

course_array = []
course_array_addr = []

username = ""
password = ""
login_data = ""
name = ""
student_id = ""

starttime = 0
endtime = 0

cookies = ""
headers = ""

def main():
    internet_check()
    login_option()
    user_info()
    menu()

def clear():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

def internet_connection():
    try:
        response = httpx.get(login_url, timeout=30)
        return 0
    except httpx.ConnectTimeout:
        return 1
    except httpx.ConnectError:
        return 2
    except httpx.ReadTimeout:
        return 1

def internet_check():
    if internet_connection() == 1:
        print("Connection timeout")
        exit()
    elif internet_connection() == 2:
        print("Please check your internet connection and try again !")
        exit()

def login():
    global username, password
    username = input("Username: ")
    password = input("Password: ")
    login_data = {"client_id": "education_client", "grant_type": "password", "username": username, "password": password, "client_secret": "password"}
    r = httpx.post(login_url, data=login_data, timeout=30)
    if 'error' in r.text:
        print("Password or username is incorrect !\n")
        main()
    elif '502 Bad Gateway' in r.text:
        print("Bad gateway at server, please try again !")
        exit()
    else:
        print("Login successful !")
        time.sleep(1)
        clear()
        cookies_renew(r)

def login_option():
    clear()
    print("Login option:\n")
    print("1. Manual login")
    print("2. Login with JSON file\n")
    option = input("Option: ")
    if option == '1':
        clear()
        login()
    elif option == '2':
        clear()
        json_login()

def make_login_json():
    login = {
        "username": username,
        "password": password
    }
    with open("login.json", "w") as outfile:
        json.dump(login, outfile)
    print("Successful !")
    time.sleep(1)
    clear()
    menu()

def json_login():
    global username, password
    if os.path.exists("login.json") == False:
        print("You don't have a JSON login file !")
        time.sleep(1)
        main()
    f = open("login.json")
    login = json.load(f)
    username = login['username']
    password = login['password']
    login_data = {"client_id": "education_client", "grant_type": "password", "username": username, "password": password, "client_secret": "password"}
    r = httpx.post(login_url, data=login_data, timeout=30)
    if 'error' in r.text:
        print("Password or username is incorrect !\n")
        main()
    elif '502 Bad Gateway' in r.text:
        print("Bad gateway at server, please try again !")
        exit()
    else:
        print("Login successful !")
        time.sleep(1)
        clear()
        cookies_renew(r)

def user_info():
    global student_id, name, course_url, register_url, schedule_url
    r = httpx.get(info_url, headers=headers, cookies=cookies)
    name = json.loads(r.text)['displayName']
    student_id = json.loads(r.text)['id']
    r2 = httpx.get(semester_url, headers=headers, cookies=cookies)
    course_url = "https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/" + str(student_id) + "/" + str(json.loads(r2.text)['semesterRegisterPeriods'][0]['id'])
    register_url = "https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/" + str(student_id) + "/" + str(json.loads(r2.text)['semesterRegisterPeriods'][0]['id'])
    schedule_url = "https://sinhvien1.tlu.edu.vn/education/api/StudentCourseSubject/studentLoginUser/" + str(json.loads(r2.text)['id'])

def cookies_renew(r):
    global cookies, headers
    cookies = {"token": urllib.parse.quote_plus(r.text)}
    access_token = "Bearer " + json.loads(r.text)['access_token']
    headers = {"Authorization" : access_token}

def menu():
    print("Welcome back, " + name)
    print("Your id is: " + str(student_id))
    print("\n")
    print("1. Course register")
    print("2. Create a full course JSON file")
    print("3. List all course and ID")
    print("4. Auto register")
    print("5. Create a login JSON")
    print("6. Send your schedule to google calendar")
    print("0. Exit")
    option = input("\nOption: ")
    if option == '1':
        clear()
        manual_course_register()
    elif option == '2':
        clear()
        get_course_list()
    elif option == '3':
        clear()
        course_list()
    elif option == '4':
        clear()
        auto_register()
    elif option == '5':
        make_login_json()
    elif option == '6':
        clear()
        send_schedule_to_google()
    elif option == '0':
        print("See you again !")
        exit()
    else:
        print("Invalid argument")
        time.sleep(1)
        clear()
        menu()

def get_course_list():
    r = httpx.get(course_url, headers=headers, cookies=cookies)
    with open("all_course.json", "w", encoding="utf-8") as f:
        f.write(r.text)
    print("Successful !")
    time.sleep(1)
    clear()
    menu()

def course_list():
    if os.path.exists("all_course.json") == False:
        print("You must create a full course JSON file to continue")
        time.sleep(1)
        clear()
        menu()
    f = open('all_course.json', encoding="utf8")
    course_list = json.load(f)
    course_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'])
    course_count = 0
    course_count2 = 0
    for i in range(course_length):
        print('[', course_count2, ']')
        if course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'] is not None:
            subcourse_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'])
            for j in range(subcourse_length):
                sub_display_name = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['displayName']
                sub_start_date = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['timetables'][0]['startDate']
                sub_end_date = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['timetables'][0]['endDate']
                sub_start_hour = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['timetables'][0]['startHour']['startString']
                sub_end_hour = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['timetables'][0]['endHour']['endString']
                sub_week_index = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]['timetables'][0]['weekIndex']
                print(course_count, ".", sub_display_name)
                print(str(datetime.fromtimestamp(sub_start_date / 1000))[0:10], "->", str(datetime.fromtimestamp(sub_end_date / 1000))[0:10], end='')
                print(' ||', week_index_c(sub_week_index), end='')
                print(" ||" , sub_start_hour, "->", sub_end_hour)
                course_count+=1
        else:
            courseSubjectDtos_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'])
            for k in range(courseSubjectDtos_length):
                display_name = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['displayName']
                start_date = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['timetables'][0]['startDate']
                end_date = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['timetables'][0]['endDate']
                start_hour = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['timetables'][0]['startHour']['startString']
                end_hour = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['timetables'][0]['endHour']['endString']
                week_index = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]['timetables'][0]['weekIndex']
                print(course_count, ".", display_name)
                print(str(datetime.fromtimestamp(start_date / 1000))[0:10], "->", str(datetime.fromtimestamp(end_date / 1000))[0:10], end='')
                print(' ||', week_index_c(week_index), end='')
                print(" ||", start_hour, "->", end_hour)
                course_count+=1
        course_count2+=1
        print('')
    print("Press any key to continue...")
    input()
    clear()
    menu()

def make_course_array():
    global course_array
    if len(course_array) > 0:
        return
    f = open('all_course.json', encoding="utf8")
    course_list = json.load(f)
    course_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'])
    course_count = 0
    for i in range(course_length):
        if course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'] is not None:
            subcourse_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'])
            for j in range(subcourse_length):
                subcourse = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][0]['subCourseSubjects'][j]
                course_array.append(subcourse)
                course_count+=1
        else:
            courseSubjectDtos_length = len(course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'])
            for k in range(courseSubjectDtos_length):
                course = course_list['courseRegisterViewObject']['listSubjectRegistrationDtos'][i]['courseSubjectDtos'][k]
                course_array.append(course)
                course_count+=1
        course_array_addr.append(course_count)

def manual_course_register():
    make_course_array()
    option = input("Enter your course order in list option (press a charater to escape): ")
    try:
        val = int(option)
        if val < 0 or val >= len(course_array):
            print("Please enter a valid number !")
            time.sleep(1)
            manual_course_register()
        else:
            r = httpx.post(register_url, headers=headers, cookies=cookies, json=course_array[val])
            response = json.loads(r.text)
            if response['status'] == 0:
                print(response['message'])
                time.sleep(1)
                clear()
                menu()
            else:
                print(response['message'])
                time.sleep(1)
                clear()
                manual_course_register()
    except ValueError:
        print("Invalid argument")
        time.sleep(1)
        clear()
        menu()

def auto_course_register(val):
    r = httpx.post(register_url, headers=headers, cookies=cookies, json=course_array[val])
    response = json.loads(r.text)
    try:
        if response['status'] == 0:
            return True
        else:
            return False
    except httpx.ConnectError:
        pass
    except httpx.ConnectTimeout:
        pass

def auto_register():
    print("Selected feature is in maintainance, try again later !")
    time.sleep(1)
    menu()
    '''
    if os.path.exists("all_course.json") == False:
        print("You must create a full course JSON file to continue")
        time.sleep(1)
        clear()
        menu()
    global starttime, endtime
    f = open('all_course.json')
    time_get = json.load(f)
    starttime = time_get['courseRegisterViewObject']['startDate']
    endtime = time_get['courseRegisterViewObject']['endDate']
    print("Current time: ", datetime.fromtimestamp(int(time.time())))
    print("Start date:   ", datetime.fromtimestamp(starttime / 1000))
    print("End date:     ", datetime.fromtimestamp(endtime / 1000), '\n')
    print("1. ")
'''
def countdown():
    for x in range(int(starttime/1000) - int(time.time()), 0, -1):
        sec = x % 60
        min = int(x/60) % 60
        hrs = x / 3600
        times = f"{int(hrs):02}:{min:02}:{sec:02}"
        print("Schedule started, " + times + " remaining.", end='\r')
        time.sleep(1)

def send_schedule_to_google():
  creds = None
  if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", calendar_url)
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", calendar_url)
        creds = flow.run_local_server(port=0, open_browser=False)
    with open("token.json", "w") as token:
      token.write(creds.to_json())
  cal = build('calendar', 'v3', credentials=creds)
  schedule_arr = []
  try:
    r = httpx.get(schedule_url, headers=headers, cookies=cookies)
    schedule = json.loads(r.text)
    for i in range(len(schedule)):
        title = schedule[i]['courseSubject']['displayName']
        timetables_length = len(schedule[i]['courseSubject']['timetables'])
        for j in range(timetables_length):
            desc = schedule[i]['courseSubject']['timetables'][j]['startHour']['name'] + " -> " + schedule[i]['courseSubject']['timetables'][j]['endHour']['name'] + " || " + schedule[i]['courseSubject']['timetables'][j]['room']['name']
            starttime = schedule[i]['courseSubject']['timetables'][j]['startHour']['startString']
            endtime = schedule[i]['courseSubject']['timetables'][j]['endHour']['endString']
            startdate = schedule[i]['courseSubject']['timetables'][j]['startDate'] / 1000
            enddate = schedule[i]['courseSubject']['timetables'][j]['endDate'] / 1000
            week_index = schedule[i]['courseSubject']['timetables'][j]['weekIndex']
            while(startdate <= enddate):
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
                schedule_arr.append(event)
    for i in range(len(schedule_arr)):
        event = cal.events().insert(calendarId='primary', sendNotifications=True, body=schedule_arr[i]).execute()
        print('Event created: %s' % (event.get('htmlLink')))
        time.sleep(0.5)
    print("\nPress any key to continue...")
    input()
    menu()
  except HttpError as err:
      print(err)

def week_index_c(x):
    if x == 1:
        return "Sun"
    elif x == 2:
        return "Mon"
    elif x == 3:
        return "Tue"
    elif x == 4:
        return "Wed"
    elif x == 5:
        return "Thu"
    elif x == 6:
        return "Fri"
    elif x == 7:
        return "Sat"

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

main()
