import httpx
import urllib.parse
import json
import os
import sys
import time

login_url = "https://sinhvien1.tlu.edu.vn:443/education/oauth/token"
info_url = "https://sinhvien1.tlu.edu.vn:443/education/api/student/getstudentbylogin"
semester_url = "https://sinhvien1.tlu.edu.vn:443/education/api/semester/semester_info"

global_timeout = 30

def internet_connection():
    try:
        r = httpx.get(login_url, timeout=global_timeout, verify=False)
        print(r)
        if r.status_code > 399 and not 401:
            raise Exception
        return 0
    except Exception as e:
        print(e)
        return 1

def login_check(r):
    try:
        if 'error' in r.text:
            print("Tài khoản hoặc mật khẩu không đúng !\n")
            time.sleep(1)
            return False
        elif r.status_code == 502:
            print("Lỗi 502, vui lòng thử lại sau")
            sys.exit()
        else:
            print("\nĐăng nhập thành công !")
            return True
    except httpx.ConnectTimeout:
        print("Thời gian chờ quá lâu, vui lòng thử lại")
        sys.exit()
    except httpx.ConnectError:
        print("Vui lòng kiểm tra internet của bạn và thử lại sau")
        sys.exit()

def login(username, password):
    cookies = {}
    headers = {}
    if os.path.exists("res/token.json"):
        with open("res/token.json") as f:
            token = json.load(f)
        cookies = {"token": token['token']}
        headers = {"Authorization": token['Authorization']}
        while(1):
            try:
                r = httpx.get(info_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
                break
            except Exception as e:
                print("Có lỗi không mong muốn xảy ra:", e, "\nĐang thử lại...\r")
        if "error" not in r.text:
            print("Đang đăng nhập tự động bằng token...")
            time.sleep(1)
            return cookies, headers
    login_data = {"client_id": "education_client", "grant_type": "password", "username": username, "password": password, "client_secret": "password"}
    while(1):
        try:
            r = httpx.post(login_url, data=login_data, timeout=global_timeout, verify=False)
            break
        except Exception as e:
            print("Có lỗi không mong muốn xảy ra:", e, "\nĐang thử lại...\r")
    if login_check(r):
        cookies = {"token": urllib.parse.quote_plus(r.text)}
        headers = {"Authorization": "Bearer " + json.loads(r.text)['access_token']}
        with open("res/token.json", "w") as outfile:
            json.dump({"token": cookies["token"], "Authorization": headers["Authorization"]}, outfile)
        with open("res/login.json", "w") as outfile:
            json.dump({"username": username, "password": password}, outfile)
    return cookies, headers

def get_user_info(cookies, headers, offline_mode):
    if(offline_mode == True):
        data = json.load(open("res/user_info.json"))
        name = data["name"]
        student_id = data["student_id"]
        course_url = data["course_url"]
        register_url = data["register_url"]
        course_summer_url = data["course_summer_url"]
        register_summer_url = data["register_summer_url"]
        schedule_url = data["schedule_url"]
        return name, student_id, course_url, course_summer_url, register_url, register_summer_url, schedule_url
    else:
        while(1):
            try:
                r = httpx.get(info_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
                r2 = httpx.get(semester_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
                break
            except Exception as e:
                print("Có lỗi không mong muốn xảy ra:", e, "\nĐang thử lại...\r")
        name = json.loads(r.text)['displayName']
        student_id = json.loads(r.text)['id']
        semester_id = json.loads(r2.text)['semesterRegisterPeriods'][0]['id']
        semester_summer_id = json.loads(r2.text)['semesterRegisterPeriods'][6]['id']
        course_url = f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/{student_id}/{semester_id}"
        register_url = f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/{student_id}/{semester_id}"
        course_summer_url = f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/findByPeriod/{student_id}/{semester_summer_id}"
        register_summer_url = f"https://sinhvien1.tlu.edu.vn:443/education/api/cs_reg_mongo/add-register/{student_id}/{semester_summer_id}"
        schedule_url = f"https://sinhvien1.tlu.edu.vn/education/api/StudentCourseSubject/studentLoginUser/{json.loads(r2.text)['id']}"
        with open("res/user_info.json", "w") as outfile:
            json.dump({"name": name, "student_id": student_id, "course_url": course_url, "register_url": register_url, "course_summer_url": course_summer_url, "register_summer_url": register_summer_url, "schedule_url": schedule_url}, outfile)
        return name, student_id, course_url, course_summer_url, register_url, register_summer_url, schedule_url