import httpx
import json
import os
import sys
import time
from src.ui import clear

global_timeout = 30

def get_course_list(course_url, cookies, headers, name):
    try:
        r = httpx.get(course_url, headers=headers, cookies=cookies, timeout=global_timeout, verify=False)
        print("Lấy dữ liệu thành công")
        with open("res/" + name, "w", encoding="utf-8") as f:
            f.write(r.text)
        time.sleep(1)
        clear()
    except:
        if os.path.exists("res/" + name):
            print("Không thể kết nối đến máy chủ, script sẽ sử dụng dữ liệu từ lần chạy trước")
            time.sleep(1)
        else:
            print("Không thể kết nối đến máy chủ và không có dữ liệu từ lần chạy trước đó.\nScript sẽ tự ngắt sau 5 giây...")
            time.sleep(5)
            sys.exit()

def make_course_array(course_url, cookies, headers, name):
    course_array = []
    course_name_array = []
    get_course_list(course_url, cookies, headers, name)
    with open('res/' + name, encoding="utf8") as f:
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
    return course_array, course_name_array