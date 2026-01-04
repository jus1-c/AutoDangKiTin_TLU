import json
import os
import datetime
import webbrowser
from ics import Calendar, Event
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

    def get_credentials(self, initial_token_json=None, on_token_update=None, open_browser_callback=None):
        creds = None
        
        # 1. Try loading from provided JSON (from Browser LocalStorage)
        if initial_token_json:
            try:
                token_data = json.loads(initial_token_json)
                creds = Credentials.from_authorized_user_info(token_data, self.SCOPES)
            except Exception as e:
                print(f"[DEBUG] Failed to load initial google token: {e}")

        # 2. Validate and Refresh
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    if on_token_update:
                        on_token_update(creds.to_json())
                except Exception:
                    # If refresh fails, we need new login
                    creds = None
            
            if not creds:
                # 3. New Login Flow
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
                
                original_get = webbrowser.get
                if open_browser_callback:
                    class MockBrowser:
                        def open(self, url, new=0, autoraise=True):
                            open_browser_callback(url)
                            return True
                    webbrowser.get = lambda x=None: MockBrowser()
                
                try:
                    creds = flow.run_local_server(
                        port=0, 
                        open_browser=True,
                        authorization_prompt_message="\n=== LINK XÁC THỰC GOOGLE ===\n{url}\n============================\n"
                    )
                    # Notify update
                    if on_token_update:
                        on_token_update(creds.to_json())
                finally:
                    if open_browser_callback:
                        webbrowser.get = original_get
                
        return creds

    async def get_tlu_events(self, user: User):
        """Fetches schedule from TLU and parses it into Google Calendar events format."""
        print("Đang tải dữ liệu lịch học từ TLU...")
        res = await self.client.request("GET", user.schedule_url)
        if res.status_code != 200:
            raise Exception(f"Failed to fetch schedule from TLU: {res.status_code}")

        tlu_schedule = res.json()
        return self._parse_schedule(tlu_schedule)

    def sync_to_google(self, events, initial_token=None, on_token_update=None, browser_callback=None):
        """Blocking function to sync events to Google Calendar."""
        if not events:
            print("Không có sự kiện nào để đồng bộ.")
            return

        print("Đang xác thực Google Calendar...")
        creds = self.get_credentials(
            initial_token_json=initial_token, 
            on_token_update=on_token_update, 
            open_browser_callback=browser_callback
        )
        service = build('calendar', 'v3', credentials=creds)

        # Create NEW calendar
        current_date_str = datetime.datetime.now().strftime("%d/%m/%Y")
        calendar_name = f"TLU Schedule {current_date_str}"
        
        print(f"Đang tạo lịch mới: '{calendar_name}'...")
        cal_id = self._create_new_calendar(service, calendar_name)
        
        # Insert events
        print(f"Đang đồng bộ {len(events)} sự kiện...")
        
        for event in events:
            service.events().insert(calendarId=cal_id, body=event).execute()
            print(f"+ Đã thêm: {event['summary']}")
            
        print("Đồng bộ hoàn tất!")

    async def get_ics_content(self, user: User) -> str:
        """Returns ICS content as string."""
        events = await self.get_tlu_events(user)
        c = Calendar()
        for ev in events:
            e = Event()
            e.name = ev['summary']
            e.description = ev['description']
            e.begin = ev['start']['dateTime']
            e.end = ev['end']['dateTime']
            c.events.add(e)
        return str(c)

    async def export_ics(self, user: User):
        """Legacy: Generates ICS file locally (server-side)."""
        content = await self.get_ics_content(user)
        filepath = os.path.join(Config.RES_DIR, "schedule.ics")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return os.path.abspath(filepath)

    def _create_new_calendar(self, service, name):
        calendar = {
            'summary': name,
            'timeZone': 'Asia/Ho_Chi_Minh'
        }
        created_calendar = service.calendars().insert(body=calendar).execute()
        return created_calendar['id']

    def _week_index_convert(self, x):
        if x == 8: return 6
        return x - 2

    def _parse_schedule(self, schedule_data):
        events = []
        for subject in schedule_data:
            timetables = subject['courseSubject']['timetables']
            for tt in timetables:
                title = f"[{tt['room']['name']}] {subject['courseSubject']['displayName']}"
                desc = f"{tt['startHour']['name']} -> {tt['endHour']['name']} || {tt['room']['name']}"
                
                start_time_str = tt['startHour']['startString'] 
                end_time_str = tt['endHour']['endString']
                
                start_date_ts = tt['startDate'] / 1000
                end_date_ts = tt['endDate'] / 1000
                
                week_index = tt['weekIndex']
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
                    
                    current_date += 7 * 86400 
        return events