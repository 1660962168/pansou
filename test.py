#coding=utf-8
import requests
import base64
import time
import random
import traceback
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= 核心配置区 =================
# 1. 测试用的百度网盘链接和提取码
TEST_URL = "12RQHHuhOznj0VSNr8T8TCA" 
TEST_PWD = "yyds"

# 2. 代理平台 API 提取链接
tiquApiUrl = 'http://proxy.siyetian.com/apis_get2.html?token=gWi90T4dTS11iTENmeOp2Yy4EVJRTTR1STqFUeOpWQ61kanBzTUF0MNR1Zw4EVRNjTql0M.AO5QTM5YDN3cTM&limit=1&type=1&time=&data_format=json' 

# 3. 代理账号和密码
proxy_user = 'uI7xOObZ'
proxy_pass = '7b328e9a3b54' # 注意：如果总是报错 407 (代理认证失败)，请换成之前那个长密码 7b328e9a3b5438039c521e49c2dca158
# ==============================================

def get_dp_logid():
    """生成百度所需的 dp-logid"""
    session_id = str(random.randint(100000, 999999))
    user_id = "00" + str(random.randint(10000000, 99999999))
    count_id = str(random.randint(10, 9999)).zfill(4)
    return f"{session_id}{user_id}{count_id}"

def get_dynamic_proxy():
    """请求 API 提取动态 IP，并组装成 proxies 字典"""
    print(f"[*] 正在请求 API 提取动态代理 IP...")
    try:
        apiRes = requests.get(tiquApiUrl, timeout=5)
        try:
            res_json = apiRes.json()
            if res_json.get("code") == 1 and res_json.get("data"):
                proxy_info = res_json["data"][0]
                ip = f"{proxy_info['ip']}:{proxy_info['port']}"
            else:
                print(f"[-] 提取代理失败，接口返回: {res_json}")
                return None
        except requests.exceptions.JSONDecodeError:
            ip = apiRes.text.strip()
            
        print(f"[+] 成功提取动态代理 IP: {ip}")
        
        # 组装完整的代理 URL (注意这里是 http 协议)
        proxy_url = f"http://{proxy_user}:{proxy_pass}@{ip}"
        return {
            "http": proxy_url,
            "https": proxy_url
        }
    except Exception as e:
        print(f"[-] 请求代理 API 发生错误: {e}")
        return None

def test_baidu_verify(surl, pwd):
    # 1. 获取动态代理
    proxies = get_dynamic_proxy()
    if not proxies:
        print("[-] 无法获取代理，程序终止。")
        return

    session = requests.Session()
    
    # 2. 为 Session 增加重试机制 (应对动态代理网络不稳定的情况)
    retry_strategy = Retry(
        total=3, # 总共重试 3 次
        backoff_factor=1, 
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # 3. 将提取到的动态代理全局应用到 Session
    session.proxies.update(proxies)
    
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    def get_headers(referer=None):
        headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://pan.baidu.com',
            'User-Agent': user_agent,
            'X-Requested-With': 'XMLHttpRequest'
        }
        if referer:
            headers['Referer'] = referer
        return headers

    try:
        print(f"\n[*] =======================================")
        print(f"[*] 开始测试百度网盘链接: {surl} | 提取码: {pwd}")
        print(f"[*] =======================================")
        
        surl_clean = surl[3:] if surl.startswith('/s/') else surl
        real_share_url = f'https://pan.baidu.com/s/{surl_clean}'
        
        print(f"[*] 1. 请求基础页面，获取初始 Cookie (BAIDUID)...")
        # 超时时间设为 15 秒
        response = session.get(real_share_url, headers=get_headers(), allow_redirects=False, timeout=15) 
        
        baiduid = response.cookies.get("BAIDUID") or session.cookies.get("BAIDUID")
        if not baiduid:
            print("[-] 失败: 无法获取 BAIDUID。说明当前提取的动态 IP 无法连通百度，或者直接被百度拦截。")
            return
        
        print(f"[+] 成功获取 BAIDUID: {baiduid}")
        
        logid = base64.b64encode(baiduid.encode()).decode()
        verify_surl = surl_clean[1:] if surl_clean.startswith('1') else surl_clean
        
        verify_url = (
            f'https://pan.baidu.com/share/verify?t={int(time.time_ns() / 1000000)}&surl={verify_surl}'
            f'&channel=chunlei&web=1&app_id=250528&bdstoken=&logid={logid}&clienttype=0&dp-logid={get_dp_logid()}'
        )
        data = {'pwd': pwd, 'vcode': '', 'vcode_str': ''}
        
        print(f"[*] 2. 伪造 Referer 发送提取码验证请求...")
        verify_res = session.post(
            verify_url, 
            headers=get_headers(referer=real_share_url), 
            data=data, 
            timeout=15
        )
        
        print(f"[*] 响应 HTTP 状态码: {verify_res.status_code}")
        
        if verify_res.status_code != 200:
            print(f"[-] 请求被拦截！")
            if "nginx" in verify_res.text.lower() and verify_res.status_code == 404:
                print("\n🚨 [诊断结论]: 遭遇 Nginx 404 拦截！当前动态 IP 已被百度 WAF 拉黑。")
            return
            
        try:
            res_json = verify_res.json()
            print(f"[+] 接口成功返回 JSON 数据: {res_json}")
            if res_json.get("errno") == 0:
                print("\n✅ [诊断结论]: 验证成功！提取码正确，且当前动态 IP 状态正常。")
            elif res_json.get("errno") == -9:
                print("\n✅ [诊断结论]: 提取码错误，但接口访问正常，说明当前动态 IP 状态正常。")
            else:
                print(f"\n⚠️ [诊断结论]: 接口通畅，但百度返回了其他错误码: {res_json.get('errno')}")
        except Exception as e:
            print("\n🚨 [诊断结论]: 百度未返回 JSON 数据（通常是被重定向去滑滑块了）。当前动态 IP 已触发百度风控！")

    except Exception as e:
        print(f"\n[-] 发生网络层面的异常 (可能是该动态代理连接失败或严重超时):\n{traceback.format_exc()}")

if __name__ == '__main__':
    test_baidu_verify(TEST_URL, TEST_PWD)
