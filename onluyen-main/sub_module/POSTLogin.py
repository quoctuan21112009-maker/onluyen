import json
import sys
from typing import Any, Dict, Optional
import requests


def make_login_request(username: str, userpass: str, debug: bool = False) -> Optional[Dict[str, Any]]:
    """
    Executes a POST request to the onluyen.vn login API.
    """
    url = "https://oauth.onluyen.vn/api/account/login"
    
    data_payload = {
        "phoneNumber": username,
        "password": userpass,
        "rememberMe": True,
        "userName": username,
        "socialType": "Email"
    }
    
    # Giữ lại các header tiêu chuẩn cần thiết nhất
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi,en;q=0.9",
        "Origin": "https://app.onluyen.vn",
        "Referer": "https://app.onluyen.vn/",
        "Content-Type": "application/json",
        "sec-ch-ua": '"Chromium";v="142", "Microsoft Edge";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site"
    }

    try:
        if debug:
            print(f"[DEBUG] Gửi POST request tới: {url}")
            
        response = requests.post(url, headers=headers, json=data_payload, timeout=10)

        if debug:
            print(f"[DEBUG] Status Code: {response.status_code}")

        if response.status_code == 200:
            try:
                parsed_json = response.json()
                if debug:
                    print("[DEBUG] Response JSON:")
                    print(json.dumps(parsed_json, indent=4, ensure_ascii=False))

                # Kiểm tra cờ nghiệp vụ của Onluyen (nếu có trả về status -1 khi sai pass)
                if isinstance(parsed_json, dict) and parsed_json.get("status") == -1:
                    print(f"Đăng nhập thất bại: Sai tài khoản hoặc mật khẩu.", file=sys.stderr)
                    return None

                return parsed_json

            except requests.exceptions.JSONDecodeError:
                if debug:
                    print("[DEBUG] Không thể parse JSON từ response:", response.text[:200])
                return None
        else:
            if debug:
                print(f"[DEBUG] Request lỗi với mã: {response.status_code}")
                print(f"[DEBUG] Nội dung trả về: {response.text[:300]}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Lỗi kết nối mạng: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    example_username = "anhnv13@c3ltk.hanam.edu.vn"
    example_password = "******"  # Khuyến cáo đổi mật khẩu và không lưu trực tiếp lên file code
    
    result = make_login_request(example_username, example_password, debug=True)
    
    if result and "access_token" in result:
        token = result["access_token"]
        print(f"\n=> Đăng nhập thành công! Token: {token[:25]}...")
        # Sau khi có token, các request lấy đề thi/bài tập tiếp theo chỉ cần thêm:
        # headers["Authorization"] = f"Bearer {token}"
    else:
        print("\n=> Không lấy được access token.")
