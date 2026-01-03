import json
import os
import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from src.config import Config
from src.core.client import TLUClient
from src.models.user import User

class CalendarService:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]
    
    def __init__(self, client: TLUClient):
        self.client = client

    def get_credentials(self):
        creds = None
        if os.path.exists(Config.GOOGLE_TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(Config.GOOGLE_TOKEN_FILE, self.SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    if os.path.exists(Config.GOOGLE_TOKEN_FILE):
                        os.remove(Config.GOOGLE_TOKEN_FILE)
                    return self.get_credentials()
            else:
                # Create config from env
                config = {
                    "installed": {
                        "client_id": Config.GOOGLE_CLIENT_ID,
                        "project_id": "auto-schedule-tlu",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "client_secret": Config.GOOGLE_CLIENT_SECRET,
                        "redirect_uris": ["http://localhost"]
                    }
                }
                flow = InstalledAppFlow.from_client_config(config, self.SCOPES)
                creds = flow.run_local_server(port=0, open_browser=False)
                
            with open(Config.GOOGLE_TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
                
        return creds

    async def sync_schedule(self, user: User):
        print("Đang xác thực Google Calendar...")
        creds = self.get_credentials()
        service = build('calendar', 'v3', credentials=creds)

        # Fetch schedule from TLU
        print("Đang tải dữ liệu lịch học từ TLU...")
        res = await self.client.request("GET", user.schedule_url)
        if res.status_code != 200:
            print("Failed to fetch schedule from TLU")
            return

        tlu_schedule = res.json()
        events = self._parse_schedule(tlu_schedule)

        # Create NEW calendar
        current_date_str = datetime.datetime.now().strftime("%d/%m/%Y")
        calendar_name = f"TLU Schedule {current_date_str}"
        
        print(f"Đang tạo lịch mới: '{calendar_name}'...")
        cal_id = self._create_new_calendar(service, calendar_name)
        
        # Insert events
        print(f"Đang đồng bộ {len(events)} sự kiện...")
        batch = service.new_batch_http_request()
        
        for event in events:
            # Using batch for better performance (though legacy code was serial)
            # Batch limit is usually 50-100, but for simplicity let's do serial for now to avoid batch logic complexity
            # or just do serial printing
            service.events().insert(calendarId=cal_id, body=event).execute()
            print(f"+ Đã thêm: {event['summary']}")
            
        print("Đồng bộ hoàn tất!")

    def _create_new_calendar(self, service, name):
        calendar = {
            'summary': name,
            'timeZone': 'Asia/Ho_Chi_Minh'
        }
        created_calendar = service.calendars().insert(body=calendar).execute()
        return created_calendar['id']

    def _week_index_convert(self, x):
        # 2->0 (Mon), 3->1 ... 8->6 (Sun)
        if x == 8: return 6
        return x - 2

    def _parse_schedule(self, schedule_data):
        events = []
        for subject in schedule_data:
            timetables = subject['courseSubject']['timetables']
            for tt in timetables:
                title = f"[{tt['room']['name']}] {subject['courseSubject']['displayName']}"
                desc = f"{tt['startHour']['name']} -> {tt['endHour']['name']} || {tt['room']['name']}"
                
                start_time_str = tt['startHour']['startString'] # "07:00"
                end_time_str = tt['endHour']['endString']
                
                start_date_ts = tt['startDate'] / 1000
                end_date_ts = tt['endDate'] / 1000
                
                week_index = tt['weekIndex'] # Day of week (2-8)
                day_offset = self._week_index_convert(week_index)
                
                current_date = start_date_ts
                while current_date < end_date_ts:
                    event_date = current_date + (day_offset * 86400)
                    dt_object = datetime.datetime.fromtimestamp(event_date)
                    date_str = dt_object.strftime('%Y-%m-%d')
                    
                    start_dt = f"{date_str}T{start_time_str}:00+07:00"
                    end_dt = f"{date_str}T{end_time_str}:00+07:00"
                    
                    events.append({
                        'summary': title,
                        'description': desc,
                        'start': {'dateTime': start_dt},
                        'end': {'dateTime': end_dt},
                         'reminders': {
                            'useDefault': False,
                            'overrides': [{'method': 'popup', 'minutes': 30}]
                        }
                    })
                    
                    current_date += 7 * 86400 # Next week
        return events
