import requests
import base64
import hashlib
from cryptography.fernet import Fernet
from flask import current_app
from models import SystemConfig

class PanSouClient:
    _token = None

    @classmethod
    def get_fernet(cls):
        # 强制将任意长度的 SECRET_KEY 转换为 Fernet 兼容的 32 字节 url-safe base64
        secret = current_app.config['SECRET_KEY'].encode('utf-8')
        hashed = hashlib.sha256(secret).digest()
        return Fernet(base64.urlsafe_b64encode(hashed))

    @classmethod
    def encrypt_data(cls, text):
        if not text:
            return ""
        return cls.get_fernet().encrypt(text.encode('utf-8')).decode('utf-8')

    @classmethod
    def decrypt_data(cls, encrypted_text):
        if not encrypted_text:
            return ""
        try:
            return cls.get_fernet().decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
        except Exception:
            return ""

    @classmethod
    def _get_config(cls):
        config = SystemConfig.query.first()
        if not config or not getattr(config, 'search_api_url', None):
            raise ValueError("系统配置中未找到 search_api_url 或记录不存在")
        # 剔除末尾斜杠防双斜杠
        return config.search_api_url.rstrip('/'), config.search_api_token

    @classmethod
    def authenticate(cls):
        api_url, password = cls._get_config()
        login_url = f"{api_url}/api/auth/login"
        
        # 依据规范：username固定为admin，password取自SystemConfig
        res = requests.post(login_url, json={"username": "admin", "password": password}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if 'token' in data:
                cls._token = data['token']
                return
        raise Exception(f"认证失败 HTTP {res.status_code}: {res.text}")

    @classmethod
    def get_valid_token(cls):
        if not cls._token:
            cls.authenticate()
        else:
            api_url, _ = cls._get_config()
            verify_url = f"{api_url}/api/auth/verify"
            try:
                res = requests.post(verify_url, headers={"Authorization": f"Bearer {cls._token}"}, timeout=5)
                if res.status_code != 200 or not res.json().get('valid'):
                    cls.authenticate()
            except requests.RequestException:
                cls.authenticate()
        return cls._token

    @classmethod
    def search(cls, keyword, **kwargs):
        api_url, api_token = cls._get_config()
        search_url = f"{api_url}/api/search"
        
        # 剥离旧版强绑定的 "res": "merge"，基于新规范封包
        payload = {"kw": keyword, **kwargs}
        headers = {"Content-Type": "application/json"}
        
        # 动态鉴权分流：存在 token 则挂载 Auth 标头，否则直连
        if api_token:
            token = cls.get_valid_token()
            headers["Authorization"] = f"Bearer {token}"
            
        res = requests.post(search_url, headers=headers, json=payload, timeout=30)
        
        # 401 Token 失效兜底重试机制 (仅鉴权模式下触发)
        if res.status_code == 401 and api_token:
            cls._token = None
            token = cls.get_valid_token()
            headers["Authorization"] = f"Bearer {token}"
            res = requests.post(search_url, headers=headers, json=payload, timeout=30)
            
        if res.status_code != 200:
            raise Exception(f"API搜索异常 HTTP {res.status_code}: {res.text}")
            
        data = res.json()
        
        # 上游状态码挟持：强转 0 为 200 抹平上下游差异
        if data.get("code") == 0:
            data["code"] = 200
        
        # 提取真正的 data 负载进行加密操作
        payload_data = data.get("data", data)
        
        # 维持加密管线：针对 merged_by_type 敏感字段进行 Fernet 混淆
        if isinstance(payload_data, dict) and "merged_by_type" in payload_data:
            for cloud_type, items in payload_data["merged_by_type"].items():
                for item in items:
                    if "url" in item:
                        item["url"] = cls.encrypt_data(item["url"])
                    if "password" in item:
                        item["password"] = cls.encrypt_data(item["password"])
                        
        return data