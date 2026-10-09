import sys
import io
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import base64
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Keep subprocess output readable on Windows and Linux.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from sub_module.Homework_module.Checkifanyonedidhw import check_if_anyone_did_hw
from sub_module.Homework_module.GETHWSpecificInfo import fetch_data_and_parse
from sub_module.Homework_module.POSTStartHW import start_assignment_request
from sub_module.Homework_module.PUTAnswers import submit_assignment
from sub_module.Homework_module.POSTSubmitHW import submit_hw
from sub_module.Homework_module.convertREFtoANSDOC import convert_assignment_json_to_json
from solve import solve_assignment, validate_submission_answers
from config import CSV_FILE_PATH, ANSWER_FILE_PATTERN, OUTPUT_JSON_PATTERN, DEFAULT_THREAD_COUNT, DEBUG_MODE
from studentdatabase import StudentDatabase


def decode_jwt_username(jwt_token: str) -> str:
    try:
        parts = jwt_token.split('.')
        if len(parts) != 3:
            return ''
        payload = parts[1] + '=' * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode('utf-8')).get('userName', '').strip()
    except Exception:
        return ''


class Logger:
    def __init__(self, filename='latest.log'):
        self.terminal = sys.stdout
        self.log = open(filename, 'w', encoding='utf-8')
        self.is_closed = False

    def write(self, message):
        self.terminal.write(message)
        if not self.is_closed:
            try:
                self.log.write(message)
                self.log.flush()
            except (ValueError, OSError):
                pass

    def flush(self):
        try:
            self.terminal.flush()
        except (ValueError, OSError):
            pass
        if not self.is_closed:
            try:
                self.log.flush()
            except (ValueError, OSError):
                pass

    def close(self):
        if not self.is_closed:
            self.log.close()
            self.is_closed = True


def _api_success(response):
    """Do not continue when the start-assignment API rejected the request."""
    if response is None:
        return False
    try:
        data = response.json()
        if data.get('success') is not False:
            return True
        if data.get('message') == 'Đang làm bài':
            return True
        return False
    except (ValueError, AttributeError):
        return False


def process_student_task(student, logid, reference_json_str, debug_mode):
    """Process exactly one selected student and fail unless answers were selected and uploaded."""
    name, token = student
    try:
        started = start_assignment_request(logid, token, debug_mode)
        if not _api_success(started):
            error_msg = f'Không thể bắt đầu bài tập (API từ chối yêu cầu).'
            if started is not None:
                error_msg += f" Status Code: {started.status_code}, Response: {started.text}"
            raise RuntimeError(error_msg)

        exam_data = fetch_assignment_data(token, logid)
        
        sample_file = "sample_answers.json"
        top = exam_data.get('data', {})
        pre_url = top.get('preSignedUrlAnswer', '')
        name_test = top.get('name', '')
        
        if os.path.exists(sample_file):
            from solve import build_payload_from_sample
            ready = build_payload_from_sample(sample_file, logid, name_test, pre_url)
        else:
            from solve import build_payload_from_reference
            ready = build_payload_from_reference(reference_json_str, logid, name_test, pre_url)
            
        validate_submission_answers(ready)

        payload_s3 = ready.get('payload_s3')
        if not payload_s3 or not payload_s3.get("data") or len(payload_s3["data"]) == 0:
            raise RuntimeError("Dữ liệu câu hỏi rỗng, hủy upload để tránh bị 0%!")

        # Truyền đúng format object { assignId, assignmentContentType, name, data } thay vì chỉ truyền mảng data
        uploaded = submit_assignment(ready['preSignedUrlAnswer'], payload_s3)
        if not isinstance(uploaded, tuple) or not uploaded[0]:
            message = uploaded[2] if isinstance(uploaded, tuple) and len(uploaded) > 2 else 'Upload đáp án thất bại.'
            raise RuntimeError(message)

        # Lấy logId từ payload_s3 (đã được solve.py parse)
        log_id = exam_data.get('data', {}).get('logId', exam_data.get('data', {}).get('_id', ''))
        if log_id:
            submitted = submit_hw(logid, log_id, token)
            if not submitted:
                print(f"[WARNING] {name}: API Nộp bài (status 3) phản hồi thất bại.")
        else:
            print(f"[WARNING] {name}: Không tìm thấy logId để gửi lệnh nộp bài cuối cùng.")

        print(f'[VERIFY] Đã chọn {len(ready["listAnswer"])} đáp án cho {name}')
        print(f'[SUCCESS] Đã nộp bài cho {name}')
        return name, True, None
    except Exception as exc:
        import traceback
        print(f'[FAIL] {name}: {exc}\n{traceback.format_exc()}')
        return name, False, str(exc)


# Keep the original function name available without relying on a wildcard import.
def fetch_assignment_data(token, logid):
    from sub_module.Homework_module.GETHWQuestDoing import get_assignment_data
    return get_assignment_data(token, logid)


def student_matches(student, account):
    raw = student[1] or ''
    return (decode_jwt_username(raw) or raw.strip()).lower() == account.strip().lower()


def main():
    parser = argparse.ArgumentParser(description='Run the ONLUYEN assignment process.')
    parser.add_argument('--debug', '-d', action='store_true')
    parser.add_argument('--logid', '-l')
    parser.add_argument('--account', '-a')
    args = parser.parse_args()
    debug = args.debug or DEBUG_MODE
    sys.stdout = Logger()

    try:
        db = StudentDatabase(CSV_FILE_PATH)
        logid_input = args.logid or input('Please input the logid:').strip()
        while True:
            logid, students_done, students_not_done = check_if_anyone_did_hw(db, logid_input, debug)
            if students_done:
                break
            print('Chưa có học sinh nào làm xong bài này để lấy đáp án tham chiếu. Đang chờ (5s)...')
            time.sleep(5)

        if args.account:
            target = [s for s in students_not_done if student_matches(s, args.account)]
            if not target:
                if any(student_matches(s, args.account) for s in students_done):
                    raise RuntimeError(f'Tài khoản {args.account} đã làm bài này.')
                raise RuntimeError(f'Tài khoản {args.account} không có trong danh sách chưa làm bài.')
            # Web requests must never fan out to all students: one account, one assignment.
            students_not_done = target[:1]

        if not students_not_done:
            print('Tất cả học sinh đã làm bài.')
            return

        reference = students_done[0]
        raw = fetch_data_and_parse(logid, reference[1], True, debug,
                                   OUTPUT_JSON_PATTERN.format(student_name=reference[0], logid=logid))
        if not raw:
            raise RuntimeError('Không lấy được dữ liệu đáp án tham chiếu.')
        
        # Bỏ qua việc convert bóc tách đáp án (có thể sai) của học sinh, 
        # dùng thẳng JSON đề gốc để trích xuất đáp án chuẩn 100%.
        raw_json_str = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        with open(ANSWER_FILE_PATTERN.format(student_name=reference[0], logid=logid), 'w', encoding='utf-8') as fh:
            fh.write(raw_json_str)

        # With --account this list contains exactly one student. Batch CLI mode remains supported.
        failures = []
        with ThreadPoolExecutor(max_workers=1 if args.account else DEFAULT_THREAD_COUNT) as executor:
            jobs = [executor.submit(process_student_task, student, logid, raw_json_str, debug)
                    for student in students_not_done]
            for job in as_completed(jobs):
                name, ok, error = job.result()
                if not ok:
                    failures.append(f'{name}: {error}')
        if failures:
            raise RuntimeError('Không hoàn tất: ' + '; '.join(failures))
        print(f'Hoàn tất {len(students_not_done)} tài khoản cho đúng bài {logid}.')
    finally:
        if isinstance(sys.stdout, Logger):
            sys.stdout.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'[ERROR] {exc}')
        sys.exit(1)
# In mã lỗi HTTP và nội dung response từ server
print(f"Status Code: {response.status_code}")
print(f"Server Response: {response.text}")