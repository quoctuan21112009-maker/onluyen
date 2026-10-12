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

    # --- SỬA ĐOẠN NÀY ĐỂ BÓC TÁCH CHUẨN DATA ---
    data_obj = ref_data.get('data', {})
    if isinstance(data_obj, dict) and 'dataDetail' in data_obj and isinstance(data_obj['dataDetail'], dict):
        questions = data_obj['dataDetail'].get('data', [])
    elif isinstance(data_obj, dict) and 'data' in data_obj and isinstance(data_obj['data'], list):
        questions = data_obj.get('data', [])
    elif isinstance(data_obj, list):
        questions = data_obj
    elif isinstance(ref_data.get('dataDetail'), dict):
        questions = ref_data['dataDetail'].get('data', [])
    else:
        questions = ref_data.get('data', [])
        if isinstance(questions, dict):
            questions = questions.get('data', [])

    if not isinstance(questions, list):
        questions = []
    # -------------------------------------------

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
        selected = []
        key = 'optionText'
        
        if kind == 0:
            key = 'optionId'
            # 1. Thử tìm qua isAnswer = True
            for position, option in enumerate(standard.get('options', [])):
                if option.get('isAnswer') is True:
                    selected = [position]
                    break
            # 2. Nếu server không trả isAnswer, lấy userOptionId từ bài làm của học sinh mẫu
            if not selected and standard.get('userOptionId'):
                selected = standard.get('userOptionId')
            if not selected:
                misses += 1
                
        elif kind == 1:
            key = 'optionText'
            opts = standard.get('options', [])
            if opts and any('isAnswer' in opt for opt in opts):
                selected = [str(opt.get('isAnswer', False)).lower() for opt in opts]
            elif standard.get('userOptionText') and len(standard.get('userOptionText')) > 0:
                selected = [str(x).lower() if x != '' else 'true' for x in standard.get('userOptionText')]
            elif standard.get('answerFreeText'):
                selected = [str(x).lower() for x in standard.get('answerFreeText')]
                
        elif kind == 5:
            key = 'optionText'
            opts = standard.get('options', [])
            correct_opt = None
            for opt in opts:
                if opt.get('isAnswer') is True:
                    correct_opt = str(opt.get('content', '')).strip()
                    break
            if not correct_opt and opts and opts[0].get('content'):
                correct_opt = str(opts[0].get('content', '')).strip()

            if correct_opt:
                selected = [correct_opt]
            elif standard.get('answerFreeText'):
                selected = [str(standard['answerFreeText'][0]).strip()]
            elif standard.get('userOptionText') and len(standard.get('userOptionText')) > 0:
                selected = [str(standard['userOptionText'][0]).strip()]
                
        if selected and selected != [''] and selected != []:
            ans_dict = {
                'id': step_id,
                'isSkip': False,
                'studentDoRight': None,
                'timeUpdate': current_time,
                'lang': 'vi'
            }
            ans_dict[key] = selected
            result.append(ans_dict)

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
    Khớp đáp án chuẩn 100% từ System Answer Key (ngân hàng đáp án của đề thi)
    sang đúng stepId của học sinh đang làm bài, chống đảo phương án và đảo mệnh đề Đúng/Sai.
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

    # Xây dựng Ngân hàng đáp án chuẩn (System Answer Bank) theo numberQuestion
    ans_map = {}
    for item in ref_questions:
        std = item.get('dataStandard') or {}
        if not std and item.get('dataMaterial', {}).get('data'):
            std = item['dataMaterial']['data'][0]
        q_num = std.get('numberQuestion')
        kind = std.get('typeAnswer')
        if q_num is None:
            continue

        opts = std.get("languagesData", {}).get("vi", {}).get("options", []) or std.get('options', [])

        # 1. Trắc nghiệm 1 đáp án (Phần I)
        if kind == 0:
            target_content = None
            fallback_idx = 0
            for idx, opt in enumerate(opts):
                if opt.get('isAnswer') is True:
                    fallback_idx = idx
                    target_content = clean_content(opt.get('content', ''))
                    break
            # Nếu server không trả isAnswer trong options, fallback sang userOptionId bài mẫu
            if not target_content and std.get('userOptionId'):
                u_ids = std.get('userOptionId')
                if u_ids and isinstance(u_ids[0], int) and u_ids[0] < len(opts):
                    fallback_idx = u_ids[0]
                    target_content = clean_content(opts[fallback_idx].get('content', ''))

            ans_map[q_num] = {
                'kind': 0,
                'key': 'optionId',
                'target_content': target_content,
                'fallback_idx': fallback_idx
            }

        # 2. Đúng / Sai (Phần II) - Chống đảo mệnh đề & khắc phục câu học sinh mẫu bỏ trống
        elif kind == 1:
            sub_map = {}
            default_list = []
            has_is_answer = any('isAnswer' in opt for opt in opts)

            for idx, opt in enumerate(opts):
                txt = clean_content(opt.get('content', ''))
                if has_is_answer:
                    val = "true" if opt.get('isAnswer') else "false"
                else:
                    u_texts = std.get('userOptionText', [])
                    if idx < len(u_texts) and u_texts[idx] != '':
                        val = str(u_texts[idx]).lower()
                    else:
                        val = "true"
                sub_map[txt] = val
                default_list.append(val)

            ans_map[q_num] = {
                'kind': 1,
                'key': 'optionText',
                'sub_map': sub_map,
                'default_list': default_list
            }

        # 3. Trả lời ngắn / Điền số (Phần III) - Luôn bốc đáp án chuẩn từ hệ thống, bỏ qua lỗi sai của học sinh mẫu
        elif kind == 5:
            correct_val = None
            for opt in opts:
                if opt.get('isAnswer') is True:
                    correct_val = clean_content(opt.get('content', ''))
                    break
            if not correct_val and opts and opts[0].get('content'):
                correct_val = clean_content(opts[0].get('content', ''))
            if not correct_val and std.get('answerFreeText'):
                correct_val = clean_content(str(std['answerFreeText'][0]))
            if not correct_val and std.get('userOptionText') and len(std.get('userOptionText')) > 0:
                correct_val = clean_content(str(std['userOptionText'][0]))

            correct_val = str(correct_val).strip() if correct_val else ""

            ans_map[q_num] = {
                'kind': 5,
                'key': 'optionText',
                'correct_val': correct_val
            }

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
        q_info = ans_map[q_num]
        kind = q_info['kind']
        key = q_info['key']
        curr_opts = std.get("languagesData", {}).get("vi", {}).get("options", []) or std.get('options', [])

        final_selected = []

        # Ghép Phần I: Trắc nghiệm (so khớp content để chống đảo vị trí A, B, C, D)
        if kind == 0:
            target_content = q_info['target_content']
            fallback_idx = q_info['fallback_idx']
            matched_idx = None
            if target_content:
                for idx, opt in enumerate(curr_opts):
                    if clean_content(opt.get('content', '')) == target_content:
                        matched_idx = idx
                        break
            if matched_idx is None:
                matched_idx = fallback_idx if fallback_idx < len(curr_opts) else 0
            final_selected = [matched_idx]

        # Ghép Phần II: Đúng / Sai (tra sub_map từng mệnh đề theo content để chống đảo ý a, b, c, d)
        elif kind == 1:
            sub_map = q_info['sub_map']
            default_list = q_info['default_list']
            answers_for_curr = []
            for idx, opt in enumerate(curr_opts):
                curr_txt = clean_content(opt.get('content', ''))
                if curr_txt in sub_map:
                    answers_for_curr.append(sub_map[curr_txt])
                else:
                    matched = None
                    for ref_txt, val in sub_map.items():
                        if curr_txt and (curr_txt in ref_txt or ref_txt in curr_txt):
                            matched = val
                            break
                    if matched is not None:
                        answers_for_curr.append(matched)
                    elif idx < len(default_list):
                        answers_for_curr.append(default_list[idx])
                    else:
                        answers_for_curr.append("true")
            final_selected = answers_for_curr

        # Ghép Phần III: Điền số (gửi trực tiếp đáp án chuẩn)
        elif kind == 5:
            correct_val = q_info['correct_val']
            final_selected = [correct_val]

        if final_selected and final_selected != [''] and final_selected != []:
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

