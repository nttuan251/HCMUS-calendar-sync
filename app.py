# requirements.txt
# flask==2.0.1
# google-auth==2.3.3
# google-auth-oauthlib==0.4.6
# google-auth-ttplib2==0.1.0
# google-api-python-client==2.33.0

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import os
import tempfile
import re
import json
import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import secrets
from werkzeug.utils import secure_filename
import pandas as pd
import random

# Thêm vào đầu file app.py (sau các import)
import json
import os

# Xử lý client_secret.json từ biến môi trường khi deploy
if os.environ.get('CLIENT_SECRET_JSON'):
    client_secret_data = json.loads(os.environ.get('CLIENT_SECRET_JSON'))
    
    # Tạm tạo file để sử dụng với OAuth flow
    with open('client_secret.json', 'w') as f:
        json.dump(client_secret_data, f)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(16)

# Thông tin xác thực Google OAuth
CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = ['https://www.googleapis.com/auth/calendar']
API_SERVICE_NAME = 'calendar'
API_VERSION = 'v3'

# Định nghĩa thời gian cho các tiết học
PERIOD_TIMES = {
    # Tiết lý thuyết (LT)
    "1": {"start": "07:30", "end": "08:20"},
    "2": {"start": "08:20", "end": "09:10"},
    "3": {"start": "09:10", "end": "10:00"},
    "4": {"start": "10:10", "end": "11:00"},
    "5": {"start": "11:00", "end": "11:50"},
    "6": {"start": "12:40", "end": "13:30"},
    "7": {"start": "13:30", "end": "14:20"},
    "8": {"start": "14:20", "end": "15:10"},
    "9": {"start": "15:20", "end": "16:10"},
    "10": {"start": "16:10", "end": "17:00"},
    
    # Tiết thực hành (TH) - sử dụng ký hiệu đặc biệt
    "1-2.5": {"start": "07:30", "end": "09:35"},  # Tiết 1 -> giữa tiết 3
    "3.5-5": {"start": "09:45", "end": "11:50"},  # Giữa tiết 3 -> tiết 5
    "6-7.5": {"start": "12:40", "end": "14:45"},  # Tiết 6 -> giữa tiết 8
    "8.5-10": {"start": "14:55", "end": "17:00"},  # Giữa tiết 8 -> tiết 10
}

def get_time_range(schedule_info):
    """Trích xuất thời gian bắt đầu và kết thúc từ thông tin lịch học"""
    if not schedule_info:
        return "", ""
    
    # Trích xuất thông tin về các tiết học từ lịch (ví dụ: T3(1-4))
    time_match = re.search(r'\(([^)]+)\)', schedule_info)
    if not time_match:
        return "", ""
    
    time_str = time_match.group(1)
    
    # Xử lý các trường hợp như "1-4", "6-9", "1-2.5", "8.5-10"
    if "-" in time_str:
        start_period, end_period = time_str.split("-")
        
        # Lấy thời gian bắt đầu và kết thúc từ dictionary
        if f"{start_period}-{end_period}" in PERIOD_TIMES:
            # Nếu có định nghĩa chính xác cho khoảng thời gian này (như các tiết TH)
            start_time = PERIOD_TIMES[f"{start_period}-{end_period}"]["start"]
            end_time = PERIOD_TIMES[f"{start_period}-{end_period}"]["end"]
        else:
            # Nếu không, lấy thời gian bắt đầu của tiết đầu và thời gian kết thúc của tiết cuối
            start_time = PERIOD_TIMES[start_period]["start"] if start_period in PERIOD_TIMES else ""
            end_time = PERIOD_TIMES[end_period]["end"] if end_period in PERIOD_TIMES else ""
    else:
        # Nếu chỉ có một tiết
        start_time = PERIOD_TIMES[time_str]["start"] if time_str in PERIOD_TIMES else ""
        end_time = PERIOD_TIMES[time_str]["end"] if time_str in PERIOD_TIMES else ""
    
    return start_time, end_time

def get_classroom(schedule_info):
    """Trích xuất thông tin phòng học từ lịch học"""
    if not schedule_info:
        return ""
    
    # Tìm thông tin phòng học sau dấu ":"
    classroom_match = re.search(r':([^\s]+)', schedule_info)
    if classroom_match:
        return classroom_match.group(1)  # Trả về phần sau dấu ":" (ví dụ: F208)
    
    # Nếu không tìm thấy dấu ":", thử tìm theo định dạng cũ
    old_format = re.search(r'P\.([^\s-]+)', schedule_info)
    if old_format:
        return old_format.group(1)  # Trả về phần sau "P."
    
    return ""

def get_day_of_week(schedule_info):
    """Trích xuất thông tin ngày trong tuần (T2, T3, ...) từ lịch học"""
    if not schedule_info:
        return None
    
    day_match = re.search(r'T(\d)', schedule_info)
    if day_match:
        return int(day_match.group(1))
    
    return None

def get_session_type(start_time):
    """Xác định buổi học (sáng/chiều) dựa trên thời gian bắt đầu"""
    if not start_time:
        return ""
    
    hour = int(start_time.split(":")[0])
    if 7 <= hour < 12:
        return "Sáng"
    elif 12 <= hour < 18:
        return "Chiều"
    else:
        return "Tối"

def process_text(input_text):
    """Xử lý text thời khóa biểu đầu vào"""
    # Tạo tệp tạm thời để lưu văn bản đầu vào
    temp_input = tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='UTF-8', suffix='.txt')
    temp_input.write(input_text)
    temp_input.close()
    
    # Đọc file
    content = []
    with open(temp_input.name, "r", encoding="UTF-8") as file:
        lines = file.readlines()
        
        for line in lines:
            row = line.rstrip().split("\t")
            # Loại bỏ các chuỗi rỗng trong hàng
            row = [x for x in row if x != ""]
            if row and len(row) >= 5:  # Đảm bảo có đủ thông tin cần thiết
                content.append(row)
    
    # Xóa file tạm sau khi đọc xong
    os.unlink(temp_input.name)
    
    # Xử lý các hàng dữ liệu
    courses = []
    for row in content:
        if len(row) >= 5:  # Kiểm tra lại để chắc chắn
            # Cố gắng trích xuất thông tin
            code = row[0].strip()           # Mã môn học
            name = row[1].strip()           # Tên môn học
            class_name = row[2].strip()     # Lớp/Nhóm 
            course_type = row[3].strip()    # Loại (LT/TH)
            schedule_info = row[4].strip()  # Lịch học (vd: T3(1-4)-P.cs2:G501)
            
            # Xử lý thông tin thời gian và phòng học
            start_time, end_time = get_time_range(schedule_info)
            classroom = get_classroom(schedule_info)
            day_of_week = get_day_of_week(schedule_info)
            session_type = get_session_type(start_time)
            
            # Thêm các thông tin đã xử lý vào hàng dữ liệu
            processed_row = row.copy()
            # Thêm tuần học (mặc định từ tuần 1)
            if len(processed_row) <= 5:
                processed_row.append("1")
            
            # Thêm các thông tin đã xử lý
            processed_row.append(start_time)
            processed_row.append(end_time)
            processed_row.append(classroom)
            processed_row.append(str(day_of_week) if day_of_week else "")
            processed_row.append(session_type)
            
            courses.append(processed_row)
    
    return courses

def credentials_to_dict(credentials):
    return {'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process():
    """Xử lý thời khóa biểu từ text đầu vào"""
    input_text = request.form['input_text']
    
    if not input_text.strip():
        flash('Vui lòng nhập thời khóa biểu')
        return redirect(url_for('index'))
    
    courses = process_text(input_text)
    
    if not courses:
        flash('Không thể nhận dạng thời khóa biểu. Vui lòng kiểm tra định dạng và thử lại.')
        return redirect(url_for('index'))
    
    # Lưu khóa học vào session để sử dụng sau khi xác thực Google
    session['courses'] = courses
    
    # Chuyển trực tiếp đến trang confirm thay vì qua preview
    return redirect(url_for('confirm'))

# Cập nhật đoạn OAuth để sử dụng URL động
@app.route('/authorize')
def authorize():
    # Tạo flow xác thực cho Google OAuth
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES)
    
    # Sử dụng URL động từ request
    base_url = request.url_root.rstrip('/')
    flow.redirect_uri = f"{base_url}/oauth2callback"
    
    # Code còn lại giữ nguyên
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=session['state'])
    
    # Sử dụng URL gốc động thay vì hardcode
    base_url = request.url_root.rstrip('/')
    flow.redirect_uri = f"{base_url}/oauth2callback"
    
    # Code còn lại giữ nguyên
    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    return redirect(url_for('create_events'))

# Dictionary lưu trữ màu sắc đã gán cho mỗi môn học
COURSE_COLORS = {}
# Google Calendar có 11 màu từ 1-11
AVAILABLE_COLORS = [str(i) for i in range(1, 12)]

def get_color_for_course(course_name):
    """Gán màu ngẫu nhiên nhưng nhất quán cho mỗi tên môn học"""
    # Google Calendar có màu từ 1-11
    # 1: Lavender, 2: Sage, 3: Grape, 4: Flamingo, 5: Banana
    # 6: Tangerine, 7: Peacock, 8: Graphite, 9: Blueberry, 10: Basil, 11: Tomato
    
    # Nếu môn học đã được gán màu trước đó, trả về màu đó
    if course_name in COURSE_COLORS:
        return COURSE_COLORS[course_name]
    
    # Nếu chưa được gán, chọn ngẫu nhiên một màu
    # Ưu tiên chọn màu chưa được sử dụng nếu còn
    used_colors = set(COURSE_COLORS.values())
    unused_colors = list(set(AVAILABLE_COLORS) - used_colors)
    
    if unused_colors:
        # Nếu còn màu chưa sử dụng, chọn ngẫu nhiên một màu từ các màu chưa dùng
        new_color = random.choice(unused_colors)
    else:
        # Nếu tất cả màu đã được sử dụng, chọn ngẫu nhiên từ tất cả các màu
        new_color = random.choice(AVAILABLE_COLORS)
    
    # Gán và trả về màu mới
    COURSE_COLORS[course_name] = new_color
    return new_color

@app.route('/create_events')
def create_events():
    # Kiểm tra xem có thông tin đăng nhập không
    if 'credentials' not in session or 'courses' not in session:
        return redirect(url_for('index'))
    
    # Lấy thông tin đăng nhập
    credentials = Credentials(**session['credentials'])
    
    # Tạo service Google Calendar
    service = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
    
    # Lấy danh sách khóa học và thông tin tuần học từ session
    courses = session['courses']
    course_weeks = session.get('course_weeks', {})
    
    # Xác định ngày bắt đầu học kỳ
    if 'semester_start_date' in session and session['semester_start_date']:
        try:
            start_of_semester = datetime.datetime.strptime(session['semester_start_date'], '%Y-%m-%d').date()
            # Tìm ngày Thứ Hai của tuần chứa ngày này
            start_of_week = start_of_semester - datetime.timedelta(days=start_of_semester.weekday())
        except:
            # Nếu có lỗi, sử dụng tuần hiện tại
            today = datetime.date.today()
            start_of_week = today - datetime.timedelta(days=today.weekday())
    else:
        # Mặc định bắt đầu từ tuần hiện tại
        today = datetime.date.today()
        start_of_week = today - datetime.timedelta(days=today.weekday())
    
    # Tạo sự kiện cho từng khóa học
    created_events = []
    
    # Đếm số lần xuất hiện của mỗi mã môn học
    course_code_count = {}
    for course in courses:
        if len(course) >= 10:
            code = course[0]  # Mã môn học
            if code in course_code_count:
                course_code_count[code] += 1
            else:
                course_code_count[code] = 1
    
    # Sau đó tiếp tục với vòng lặp hiện tại
    for i, course in enumerate(courses):
        if len(course) >= 10:
            code = course[0]          # Mã môn học
            name = course[1]          # Tên môn học
            class_name = course[2]    # Lớp/Nhóm
            course_type = course[3]   # Loại (LT/TH)
            schedule = course[4]      # Lịch học (vd: T3(1-4)-P.cs2:G501)
            start_time = course[6]    # Giờ bắt đầu
            end_time = course[7]      # Giờ kết thúc
            classroom = course[8]     # Phòng học
            day_of_week = int(course[9]) if course[9] else None  # Ngày trong tuần (2-7)
            
            # Lấy thông tin tuần học của môn này (nếu có)
            course_index = str(i)
            start_week = 1  # Mặc định là tuần 0
            end_week = 12   # Mặc định là tuần 12
            
            if course_index in course_weeks:
                if 'start' in course_weeks[course_index]:
                    start_week = course_weeks[course_index]['start']
                if 'end' in course_weeks[course_index]:
                    end_week = course_weeks[course_index]['end']
            
            if not day_of_week or not start_time or not end_time:
                continue
            
            # Tính ngày bắt đầu: tuần bắt đầu học kỳ + (tuần bắt đầu môn học - 1) * 7 ngày
            days_to_add = day_of_week - 2  # Thứ 2 = 0, Thứ 3 = 1, etc.
            week_offset = (start_week - 1) * 7  # Số ngày cần thêm vào để đến tuần bắt đầu môn học
            
            course_start_date = start_of_week + datetime.timedelta(days=week_offset + days_to_add)
            
            # Tạo ngày giờ DateTime cho Google Calendar
            start_hour, start_minute = map(int, start_time.split(':'))
            end_hour, end_minute = map(int, end_time.split(':'))
            
            event_start = datetime.datetime.combine(
                course_start_date, 
                datetime.time(hour=start_hour, minute=start_minute)
            ).isoformat()
            
            event_end = datetime.datetime.combine(
                course_start_date, 
                datetime.time(hour=end_hour, minute=end_minute)
            ).isoformat()
            
            # Quyết định hiển thị loại (LT/TH) dựa vào số lần xuất hiện của mã môn
            show_course_type = course_code_count[code] > 1
            
            # Tính số tuần của môn học
            weeks_count = end_week - start_week + 1
            
            # Lấy màu sắc cho môn học dựa trên tên môn học
            color_id = get_color_for_course(name)

            # Tạo sự kiện lịch với màu sắc
            event = {
                'summary': f"{name} ({course_type})" if show_course_type and course_type in ["LT", "TH"] else name,
                'location': classroom,
                'description': f"Mã môn: {code}\nLớp: {class_name}\nLoại: {course_type}\nLịch học: {schedule}\nTuần: {start_week} - {end_week}",
                'colorId': color_id,
                'start': {
                    'dateTime': event_start,
                    'timeZone': 'Asia/Ho_Chi_Minh',
                },
                'end': {
                    'dateTime': event_end,
                    'timeZone': 'Asia/Ho_Chi_Minh',
                },
                'recurrence': [
                    f'RRULE:FREQ=WEEKLY;COUNT={weeks_count}'  # Số lần lặp lại dựa vào số tuần học
                ],
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 30},
                    ],
                }
            }
            
            try:
                # Thêm sự kiện vào Google Calendar
                event = service.events().insert(calendarId='primary', body=event).execute()
                created_events.append({
                    'name': name,
                    'status': 'success',
                    'link': event.get('htmlLink')
                })
            except Exception as e:
                created_events.append({
                    'name': name,
                    'status': 'error',
                    'error': str(e)
                })
    
    # Xóa dữ liệu khóa học khỏi session sau khi tạo xong sự kiện
    session.pop('courses', None)
    session.pop('course_weeks', None)
    session.pop('semester_start_date', None)
    
    return render_template('result.html', events=created_events)

@app.route('/confirm', methods=['GET', 'POST'])
def confirm():
    """Cho phép người dùng chọn tuần bắt đầu và kết thúc cho các môn học"""
    if 'courses' not in session:
        flash('Không tìm thấy dữ liệu khóa học. Vui lòng nhập lại thời khóa biểu.')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        # Lấy ngày bắt đầu học kỳ nếu được cung cấp
        semester_start_date = request.form.get('semester_start_date')
        if semester_start_date:
            # Lưu vào session để sử dụng sau này
            session['semester_start_date'] = semester_start_date
    else:
        # Khi truy cập bằng GET, set semester_start_date thành ngày thứ 2 hiện tại
        today = datetime.date.today()
        start_of_week = today - datetime.timedelta(days=today.weekday())
        semester_start_date = start_of_week.strftime('%Y-%m-%d')
    
    return render_template('confirm.html', 
                          courses=session['courses'], 
                          semester_start_date=semester_start_date or '')

@app.route('/save_course_weeks', methods=['POST'])
def save_course_weeks():
    """Lưu thông tin tuần học và chuyển hướng đến xác thực Google"""
    if 'courses' not in session:
        flash('Không tìm thấy dữ liệu khóa học. Vui lòng nhập lại thời khóa biểu.')
        return redirect(url_for('index'))
    
    # Đây là nơi lấy thông tin về tuần bắt đầu/kết thúc của mỗi môn học
    course_weeks = {}
    
    for key, value in request.form.items():
        if key.startswith('start_week_'):
            course_index = key.split('_')[-1]
            if course_index not in course_weeks:
                course_weeks[course_index] = {}
            course_weeks[course_index]['start'] = int(value)
        elif key.startswith('end_week_'):
            course_index = key.split('_')[-1]
            if course_index not in course_weeks:
                course_weeks[course_index] = {}
            course_weeks[course_index]['end'] = int(value)
    
    # Lưu thông tin tuần học vào session
    session['course_weeks'] = course_weeks
    
    # Lấy ngày bắt đầu học kỳ từ form
    semester_start_date = request.form.get('semester_start_date')
    if semester_start_date:
        session['semester_start_date'] = semester_start_date
    
    # Chuyển hướng đến xác thực Google
    return redirect(url_for('authorize'))

if __name__ == '__main__':
    # Khi chạy ứng dụng ở chế độ phát triển
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(debug=True)
else:
    # Khi chạy trên PythonAnywhere
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '0'