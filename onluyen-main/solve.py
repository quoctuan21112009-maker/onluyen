import json
import sys
import time
from typing import Dict, Any, List, Union
from sub_module.TIMESTAMPGen import generate_timestamp_sequence
from sub_module.utils import clean_content


def debug_print(message: str, debug_mode: bool):
    if debug_mode:
        print(f'[DEBUG] {message}')


def validate_submission_answers(payload: Dict[str, Any]) -> None:
    """Reject a submission that did not actually select any answer option."""
    answers = payload.get('listAnswer') if isinstance(payload, dict) else None
    if not isinstance(answers, list) or not answers:
        raise ValueError('Không có đáp án nào được chọn; không được báo thành công.')
    for index, answer in enumerate(answers, 1):
        if not isinstance(answer, dict) or answer.get('isSkip') is True:
            raise ValueError(f'Câu {index} chưa chọn đáp án.')
        selected = answer.get('optionText', answer.get('optionId'))
        if selected is None or selected == '' or selected == []:
            raise ValueError(f'Câu {index} chưa chọn đáp án.')


def solve_assignment(answer_data_str: str, question_data_str: str, debug_mode: bool = False) -> Dict[str, Union[List[Dict[str, Any]], str, int]]:
    try:
        answer_data = json.loads(answer_data_str) if isinstance(answer_data_str, str) else answer_data_str
        question_data = json.loads(question_data_str)
    except (json.JSONDecodeError, TypeError):
        raise ValueError('Dữ liệu bài hoặc đáp án không phải JSON hợp lệ.')

    top = question_data.get('data', {})
    pre_signed_url = top.get('preSignedUrlAnswer')
    time_server = top.get('timeServer', 'N/A')
    if not pre_signed_url:
        raise ValueError('Bài tập không có URL nộp đáp án hợp lệ.')

    id_map, content_map = {}, {}
    for item in answer_data.get('data', []):
        q_id = item.get('numberQuestion')
        kind = item.get('typeAnswer')
        content = item.get('content', [])
        raw = content[0] if content and kind != 1 else ''
        data = {'content': content, 'cleaned_content': clean_content(raw), 'type': kind}
        if q_id is not None:
            id_map[q_id] = data
        text = clean_content(item.get('content-dataStandard', ''))
        if text:
            content_map[text] = data

    questions = top.get('data', [])
    try:
        start = int(time_server) + 10
    except (TypeError, ValueError):
        start = int(time.time())
    times = iter(generate_timestamp_sequence(start=start, step=7, random_range=3, count=len(questions)))
    result = []
    misses = 0

    for item in questions:
        standard = item.get('dataStandard') or {}
        if not standard and item.get('dataMaterial', {}).get('data'):
            standard = item['dataMaterial']['data'][0]
        q_id = standard.get('numberQuestion')
        data = id_map.get(q_id) or content_map.get(clean_content(standard.get('content', '')))
        if not data:
            continue
        kind = data.get('type')
        selected, key = [], 'optionText'
        if kind == 0:
            target = data.get('cleaned_content')
            for position, option in enumerate(standard.get('options', [])):
                if clean_content(option.get('content')) == target:
                    selected, key = [position], 'optionId'
                    break
            if not selected:
                misses += 1
        elif kind == 1:
            selected = data.get('content') or []
        elif kind == 5:
            selected = [data.get('content', [''])[0]]
        if selected:
            result.append({key: selected, 'id': standard.get('stepId'), 'isSkip': False,
                           'studentDoRight': None, 'timeUpdate': next(times, int(time.time()))})

    if misses:
        print(f'[VERIFY] Không ghép được {misses} lựa chọn; các câu đó chưa được coi là thành công.')
    
    # Payload chuẩn gửi lên S3 KHÔNG ĐƯỢC để rỗng, bọc trong structure: { assignId, assignmentContentType, name, data }
    payload_s3 = {
        "assignId": top.get("assignId", top.get("_id", "")),
        "assignmentContentType": top.get("assignmentContentType", 0),
        "name": top.get("name", ""),
        "data": result
    }
    
    payload = {
        'listAnswer': result, 
        'preSignedUrlAnswer': pre_signed_url, 
        'timeServer': time_server,
        'payload_s3': payload_s3
    }
    validate_submission_answers(payload)
    print(f'[VERIFY] Đã tạo {len(result)}/{len(questions)} lựa chọn đáp án.')
    return payload


def build_payload_from_sample(sample_answers_file: str, assign_id: str, name: str, pre_signed_url: str):
    # Đọc file đáp án mẫu thật
    with open(sample_answers_file, "r", encoding="utf-8") as f:
        answers = json.load(f)

    # Cập nhật thời gian làm từng câu cho tự nhiên
    current_time = int(time.time()) - (len(answers) * 35)
    for ans in answers:
        current_time += 30  # Mỗi câu làm cách nhau 30 giây
        ans["timeUpdate"] = current_time
        # Đảm bảo configId khớp với bài thi hiện tại nếu cần
        ans["configId"] = assign_id

    # Đóng gói chuẩn template gửi lên AWS S3
    payload_s3 = {
        "assignId": assign_id,
        "assignmentContentType": 0,
        "name": name,
        "data": answers
    }

    return {
        "payload_s3": payload_s3,
        "preSignedUrlAnswer": pre_signed_url,
        "listAnswer": answers
    }


def build_payload_from_reference(reference_json_str: str, assign_id: str, name: str, pre_signed_url: str):
    """
    Trích xuất đáp án chuẩn 100% từ JSON detail của một học sinh đã hoàn thành (reference student).
    Server trả về isAnswer, answerFreeText trong file này.
    """
    try:
        ref_data = json.loads(reference_json_str) if isinstance(reference_json_str, str) else reference_json_str
        if isinstance(ref_data, str):
            ref_data = json.loads(ref_data) # Giải quyết double encoding nếu có
    except Exception:
        raise ValueError('Reference JSON không hợp lệ.')

    if not isinstance(ref_data, dict):
        ref_data = {}

    questions = ref_data.get('data', [])
    if isinstance(ref_data.get('data'), dict):
        questions = ref_data['data'].get('data', [])

    result = []
    misses = 0

    current_time = int(time.time()) - (len(questions) * 35)
    
    for item in questions:
        standard = item.get('dataStandard') or {}
        if not standard and item.get('dataMaterial', {}).get('data'):
            standard = item['dataMaterial']['data'][0]
            
        step_id = standard.get('stepId')
        kind = standard.get('typeAnswer')
        if step_id is None or kind is None:
            continue
            
        current_time += 30
        selected, key = [], 'optionText'
        
        if kind == 0:
            # Trắc nghiệm 1 đáp án: option có isAnswer = True
            for position, option in enumerate(standard.get('options', [])):
                if option.get('isAnswer') is True:
                    selected, key = [position], 'optionId'
                    break
            if not selected:
                misses += 1
                
        elif kind == 1:
            # Đúng/Sai: từ options[i].isAnswer hoặc answerFreeText
            if standard.get('answerFreeText'):
                selected = [str(x).lower() for x in standard.get('answerFreeText')]
            else:
                selected = [str(opt.get('isAnswer', False)).lower() for opt in standard.get('options', [])]
                
        elif kind == 5:
            # Trả lời ngắn / Điền số
            if standard.get('answerFreeText'):
                selected = [str(standard['answerFreeText'][0])]
            elif standard.get('options') and len(standard['options']) > 0:
                selected = [str(standard['options'][0].get('content', ''))]
                
        if selected:
            result.append({
                key: selected,
                'id': step_id,
                'isSkip': False,
                'studentDoRight': None,
                'timeUpdate': current_time,
                'configId': assign_id
            })

    if misses:
        print(f'[VERIFY] Không tìm thấy đáp án chuẩn cho {misses} câu trong file reference.')

    payload_s3 = {
        "assignId": assign_id,
        "assignmentContentType": 0,
        "name": name,
        "data": result
    }

    return {
        "payload_s3": payload_s3,
        "preSignedUrlAnswer": pre_signed_url,
        "listAnswer": result
    }

def map_answers_to_student_exam(reference_json_str: str, exam_data: dict, assign_id: str):
    """
    Khớp đáp án đúng từ bài mẫu (reference) sang đúng stepId của học sinh đang làm bài.
    """
    try:
        ref_data = json.loads(reference_json_str) if isinstance(reference_json_str, str) else reference_json_str
        if isinstance(ref_data, str):
            ref_data = json.loads(ref_data)
    except Exception:
        raise ValueError('Reference JSON không hợp lệ.')

    # Lấy danh sách câu hỏi của bài mẫu
    ref_top = ref_data.get('data', {}) if isinstance(ref_data, dict) else {}
    if isinstance(ref_top, dict) and 'dataDetail' in ref_top and isinstance(ref_top['dataDetail'], dict):
        ref_questions = ref_top['dataDetail'].get('data', [])
    elif isinstance(ref_top, dict) and 'data' in ref_top and isinstance(ref_top['data'], list):
        ref_questions = ref_top.get('data', [])
    elif isinstance(ref_top, list):
        ref_questions = ref_top
    else:
        ref_questions = []

    # Tạo bảng tra cứu đáp án đúng theo numberQuestion từ bài mẫu
    ans_map = {}
    for item in ref_questions:
        std = item.get('dataStandard') or {}
        if not std and item.get('dataMaterial', {}).get('data'):
            std = item['dataMaterial']['data'][0]
        q_num = std.get('numberQuestion')
        kind = std.get('typeAnswer')
        if q_num is None:
            continue

        selected = []
        target_content = None
        key = 'optionText'
        if kind == 0:
            key = 'optionId'
            for idx, opt in enumerate(std.get('options', [])):
                if opt.get('isAnswer') is True:
                    selected = [idx]
                    target_content = opt.get('content')
                    break
            if not selected and std.get('userOptionId'):
                selected = std.get('userOptionId')
                if selected and isinstance(selected[0], int) and selected[0] < len(std.get('options', [])):
                    target_content = std.get('options')[selected[0]].get('content')
        elif kind == 1:
            key = 'optionText'
            if std.get('userOptionText') and len(std.get('userOptionText')) > 0:
                selected = [str(x).lower() for x in std.get('userOptionText')]
            elif std.get('answerFreeText'):
                selected = [str(x).lower() for x in std.get('answerFreeText')]
            else:
                selected = [str(opt.get('isAnswer', False)).lower() for opt in std.get('options', [])]
        elif kind == 5:
            key = 'optionText'
            if std.get('userOptionText') and len(std.get('userOptionText')) > 0:
                selected = [str(std['userOptionText'][0])]
            elif std.get('answerFreeText'):
                selected = [str(std['answerFreeText'][0])]
            elif std.get('options') and len(std['options']) > 0:
                selected = [str(std['options'][0].get('content', ''))]

        if selected and selected != [''] and selected != []:
            ans_map[q_num] = (key, selected, kind, target_content)

    # Lấy danh sách câu hỏi của chính học sinh hiện tại đang thi
    curr_top = exam_data.get('data', {}) if isinstance(exam_data, dict) else {}
    if isinstance(curr_top, dict) and 'dataDetail' in curr_top and isinstance(curr_top['dataDetail'], dict):
        curr_questions = curr_top['dataDetail'].get('data', [])
    elif isinstance(curr_top, dict) and 'data' in curr_top and isinstance(curr_top['data'], list):
        curr_questions = curr_top.get('data', [])
    elif isinstance(curr_top, list):
        curr_questions = curr_top
    else:
        curr_questions = []

    pre_url = curr_top.get('preSignedUrlAnswer', '')
    if not pre_url and isinstance(curr_top.get('dataDetail'), dict):
        pre_url = curr_top['dataDetail'].get('preSignedUrlAnswer', '')

    result = []
    current_time = int(time.time()) - (len(curr_questions) * 35)

    for item in curr_questions:
        std = item.get('dataStandard') or {}
        if not std and item.get('dataMaterial', {}).get('data'):
            std = item['dataMaterial']['data'][0]

        step_id = std.get('stepId')
        q_num = std.get('numberQuestion')
        if not step_id or q_num not in ans_map:
            continue

        current_time += 30
        key, selected, kind, target_content = ans_map[q_num]

        # Đối với trắc nghiệm kind == 0, nếu có nội dung đáp án thì kiểm tra đảo phương án
        final_selected = selected
        if kind == 0 and target_content is not None:
            clean_target = clean_content(target_content)
            for idx, opt in enumerate(std.get('options', [])):
                if clean_content(opt.get('content', '')) == clean_target:
                    final_selected = [idx]
                    break

        ans_item = {
            'id': step_id,  # DÙNG CHÍNH stepId CỦA HỌC SINH HIỆN TẠI
            'isSkip': False,
            'studentDoRight': None,
            'timeUpdate': current_time,
            'lang': 'vi'
        }
        ans_item[key] = final_selected
        result.append(ans_item)

    return {
        'listAnswer': result,
        'preSignedUrlAnswer': pre_url
    }


if __name__ == '__main__':
    print('Use main.py to run an assignment.')
