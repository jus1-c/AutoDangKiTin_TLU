import httpx
import json
import os
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError

calendar_url = "https://www.googleapis.com/auth/calendar"
calendar_name = "TLU Schedule"

def make_token(schedule_url, cookies, headers):
    creds = None
    credentials = {
        "installed": {
            "client_id": "751761844308-thsltjs8rp2l1r0tpqt13jan8981nenn.apps.googleusercontent.com",
            "project_id": "auto-schedule-433602",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": "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",
            "redirect_uris": ["http://localhost"]
        }
    }
    if os.path.exists("res/token_google.json"):
        creds = Credentials.from_authorized_user_file("res/token_google.json", calendar_url)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                os.remove("res/token_google.json")
                make_token(schedule_url, cookies, headers)
        else:
            flow = InstalledAppFlow.from_client_config(credentials, calendar_url)
            creds = flow.run_local_server(port=0, open_browser=False)
    with open("res/token_google.json", "w") as token:
        token.write(creds.to_json())
    cal = build('calendar', 'v3', credentials=creds)
    r = httpx.get(schedule_url, headers=headers, cookies=cookies, timeout=30, verify=False)
    schedule = json.loads(r.text)
    schedule_arr = make_schedule_arr(schedule)
    return cal, schedule_arr

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
            while startdate < enddate:
                start_datetime = startdate + (week_index_convert(week_index) * 86400)
                start_datetime_str = str(datetime.fromtimestamp(start_datetime))[0:10] + 'T' + starttime + ":00+07:00"
                end_datetime_str = str(datetime.fromtimestamp(start_datetime))[0:10] + 'T' + endtime + ":00+07:00"
                startdate += 7 * 86400
                event = {
                    'summary': title,
                    'description': desc,
                    'start': {'dateTime': start_datetime_str},
                    'end': {'dateTime': end_datetime_str},
                    'reminders': {
                        'useDefault': False,
                        'overrides': [{'method': 'popup', 'minutes': 30}]
                    }
                }
                temp_arr.append(event)
        schedule_arr.insert(i, temp_arr)
    return schedule_arr

def rm_and_insert_new_schedule(cal):
    page_token = None
    while True:
        calendar_list = cal.calendarList().list(pageToken=page_token).execute()
        for item in range(len(calendar_list['items'])):
            if calendar_name == calendar_list['items'][item]['summary']:
                cal.calendars().delete(calendarId=calendar_list['items'][item]['id']).execute()
                break
        page_token = calendar_list.get('nextPageToken')
        if not page_token:
            break
    
    calendar = {
        'summary': calendar_name,
        'timeZone': 'Asia/Ho_Chi_Minh'
    }
    created_calendar = cal.calendars().insert(body=calendar).execute()
    calendar_id = created_calendar['id']
    return calendar_id

def send_schedule(cal, schedule_arr, i, id):
    for j in range(len(schedule_arr[i])):
        event = cal.events().insert(calendarId=id, sendNotifications=True, body=schedule_arr[i][j]).execute()
        print('Sự kiện được thêm mới: %s' % (event.get('htmlLink')))

def week_index_convert(x):
    if x == 8: return 6
    elif x == 2: return 0
    elif x == 3: return 1
    elif x == 4: return 2
    elif x == 5: return 3
    elif x == 6: return 4
    elif x == 7: return 5