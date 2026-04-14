import requests

def get_ip_details(ip: str, app_code: str) -> dict:
    """
    请求阿里云 API 获取 IP 详情
    """
    url = f"https://api01.aliyun.venuscn.com/ip?ip={ip}"
    headers = {
        "Authorization": f"APPCODE {app_code}"
    }
    
    try:
        # 发送 GET 请求，verify=False 对应 PHP 中的 CURLOPT_SSL_VERIFYPEER => false
        response = requests.get(url, headers=headers, verify=False, timeout=10)
        
        # 对应 CURLOPT_FAILONERROR => true，如果状态码不是 200，将抛出异常
        response.raise_for_status()
        
        # 解析 JSON
        response_data = response.json()
        
        # 返回 data 字段，如果没有则返回空字典
        return response_data.get('data', {})
        
    except requests.exceptions.RequestException as e:
        raise Exception(f"API请求失败: {e}")
    except ValueError as e:
        raise Exception(f"JSON解析错误：{e}")

# 测试运行
if __name__ == "__main__":
    test_ip = "113.88.192.29" # 替换为你想要查询的IP
    app_code = "9d922d749a9640cba3e681e0b9d93196"
    
    # 忽略 requests 抛出的 InsecureRequestWarning 警告 (因为 verify=False)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        details = get_ip_details(test_ip, app_code)
        print("返回结果:", details)
    except Exception as err:
        print(err)