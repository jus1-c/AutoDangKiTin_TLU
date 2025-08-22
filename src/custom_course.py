from src.ui import clear
import os
import json
import time

def custom_json(course_array, course_name_array, file):
    registered = []
    tmp_lst = []
    while(1):
        duplicate = []
        clear()
        for i in range(len(course_array)):
            print(i, '.', course_name_array[i], '\n')
        option = input("Chọn môn để đăng kí (nhập 'ok' sau khi hoàn tất): ")
        if option == 'ok':
            break
        clear()
        try:
            option = int(option)
        except TypeError:
            print("Đối số không hợp lệ")
            time.sleep(1)
            custom_json(course_array, course_name_array)
        print(course_name_array[option], ':\n')
        while(1):
            for i in range(len(course_array[option])):
                print('------------------------------')
                print('Lựa chọn', str(i+1) + ':')
                print('Tên:', course_array[option][i]['displayName'], '\n')
                for j in range(len(course_array[option][i]['timetables'])):
                    tmp_dct = {
                        'week': course_array[option][i]['timetables'][j]["weekIndex"],
                        'start_code': course_array[option][i]['timetables'][j]['courseHourseStartCode'],
                        'end_code': course_array[option][i]['timetables'][j]['courseHourseEndCode']
                    }
                    if tmp_dct not in registered:
                        print('Thời gian:','Thứ' , course_array[option][i]['timetables'][j]["weekIndex"])
                        print(course_array[option][i]['timetables'][j]['start'] + ' - ' + course_array[option][i]['timetables'][j]['end'])
                    else:
                        if i not in duplicate:
                            duplicate.append(i)
                        print('Thời gian:','Thứ' , course_array[option][i]['timetables'][j]["weekIndex"], "(Đã trùng lịch)")
                        print(course_array[option][i]['timetables'][j]['start'] + ' - ' + course_array[option][i]['timetables'][j]['end'])
            while(1):
                try:
                    opt = input('\nLựa chọn (nhập back để quay về bảng chọn): ')
                    if opt == 'back':
                        break
                    opt = int(opt) - 1
                    if opt >= 0 and opt <= len(course_array[option]) and opt not in duplicate:
                        tmp_lst.append(course_array[option][opt])
                        for i in range(len(course_array[option][opt]['timetables'])):
                            regd_dct = {
                                'week': course_array[option][opt]['timetables'][i]["weekIndex"],
                                'start_code': course_array[option][opt]['timetables'][i]['courseHourseStartCode'],
                                'end_code': course_array[option][opt]['timetables'][i]['courseHourseEndCode']
                            }
                            registered.append(regd_dct)
                        break
                    else:
                        print("Đối số không hợp lệ")
                        time.sleep(1)
                except:
                    print("Đối số không hợp lệ")
                    time.sleep(1)
            break
        
    while(1):
        clear()
        filename = input("Nhập tên file muốn đặt: ")
        if os.path.exists('res/custom/'+ filename):
            print("Không thể sử dụng tên này!")
        else:
            break
    with open('res/custom/'+filename+'.json', 'w', encoding='utf8') as f:
        f.write(json.dumps(tmp_lst))
    with open("res/" + file, encoding="utf8") as f:
        time_get = json.load(f)
    with open('res/custom/'+filename+'.json.timer', 'w', encoding='utf8') as f:
        timer = {
            'courseRegisterViewObject': {
                'startDate': time_get['courseRegisterViewObject']['startDate'],
                'endDate': time_get['courseRegisterViewObject']['endDate']
            }
        }
        f.write(json.dumps(timer))