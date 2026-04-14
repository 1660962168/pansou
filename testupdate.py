import logging
import requests

url = "https://se.tencenst.com/api/update_drama"
payload = {
    "auth_code": "sk-9f8d6a5c4e3f2", #授权码
    "title": "测试剧集标题", #标题（不可为空）
    "cover": "https://example.com/cover.jpg", #封面
    "baidu_link": "https://pan.baidu.com/s/1xxxx?pwd=abcd", #百度网盘
    "quark_link": "https://pan.quark.cn/s/1xxxx"#夸克网盘
}

response = requests.post(url, json=payload)
logging.info(response.json())