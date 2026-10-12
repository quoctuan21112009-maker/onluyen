import requests
import json

def submit_hw(assign_id: str, log_id: str, token: str) -> bool:
    url = "https://assignments.onluyen.vn/api/assign/submit"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://app.onluyen.vn",
        "Referer": "https://app.onluyen.vn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
    }
    payload = {
        "assignId": assign_id,
        "logId": log_id,
        "status": 3,
        "totalTimeDoing": 1428
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("success") is not False:
            return True
        else:
            print(f"[FAIL] POSTSubmitHW: {data.get('message')}")
            return False
    except Exception as e:
        print(f"[ERROR] POSTSubmitHW exception: {e}")
        return False
