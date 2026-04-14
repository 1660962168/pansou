import os
import requests
import re
from lxml import etree
import random
import time
import base64
import json
import logging
import urllib.parse
from filelock import FileLock
from requests.exceptions import RequestException
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ProxyManager:
    """代理生命周期与防并发脏读管理器"""
    def __init__(self):
        self.base_dir = os.path.abspath(os.path.dirname(__file__))
        self.cache_file = os.path.join(self.base_dir, 'proxy_cache.json')
        self.lock_file = self.cache_file + '.lock'
        self.api_url = "https://kps.kdlapi.com/api/getkps/?secret_id=ol6scirom0vt1kuokv8t&signature=qo1gd2tmj41ajnn4yxh107ygiophv7dn&num=1&sep=1"
        self.api_url_2 = "https://kps.kdlapi.com/api/getkps/?secret_id=od4j8lx9din0vhszoh6m&signature=nnfq99gzyqgh5uwnnc4mg9dg8yci1kzl&num=1&sep=1"
        self.proxy_user = "bdfishtn"
        self.proxy_pass = "vqz7axpt"
        self.logger = logging.getLogger(__name__)

    def _fetch_new_ip(self) -> str:
        res = requests.get(self.api_url, timeout=10)
        res.raise_for_status()
        ip_port = res.text.strip()
        if not re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', ip_port):
            raise ValueError(f"代理API返回异常数据: {ip_port}")
        return ip_port

    def get_proxy(self, force_refresh: bool = False) -> dict:
        """获取代理字典。通过文件排他锁保证多进程/线程下的获取安全"""
        with FileLock(self.lock_file, timeout=15):
            proxy_data = {}
            if not force_refresh and os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, 'r', encoding='utf-8') as f:
                        proxy_data = json.load(f)
                except Exception:
                    pass

            # 缓存有效且不强制刷新时，直接返回
            if proxy_data and not force_refresh:
                return self._format_proxy(proxy_data['ip'])

            # 强制刷新或缓存失效时，请求新 IP
            try:
                self.logger.info("[ProxyManager] 正在向快代理获取全新动态IP...")
                new_ip = self._fetch_new_ip()
                with open(self.cache_file, 'w', encoding='utf-8') as f:
                    json.dump({"ip": new_ip, "timestamp": time.time()}, f)
                return self._format_proxy(new_ip)
            except Exception as e:
                self.logger.error(f"[ProxyManager] 获取新代理失败: {e}")
                # 容错：若获取失败但有历史缓存，降级使用旧缓存
                if proxy_data:
                    return self._format_proxy(proxy_data['ip'])
                raise

    def _format_proxy(self, ip_port: str) -> dict:
        proxy_url = f"http://{self.proxy_user}:{self.proxy_pass}@{ip_port}"
        return {
            "http": proxy_url,
            "https": proxy_url
        }

class BaiduTransfer:
    def __init__(self, bduss: str, bduss_bfess: str, stoken: str, save_path: str, user_agent: str = None):
        self.bduss = bduss
        self.bduss_bfess = bduss_bfess
        self.stoken = stoken
        self.save_path = save_path
        self.user_agent = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self.bdstoken = None 
        
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
            
        self.session = requests.Session()
        requests.utils.add_dict_to_cookiejar(self.session.cookies, {
            "PANWEB": "1",
            "BDUSS": self.bduss,
            "BDUSS_BFESS": self.bduss_bfess,
            "STOKEN": self.stoken
        })
        
        self.proxy_manager = ProxyManager()

    def _request_with_proxy(self, method: str, url: str, **kwargs) -> requests.Response:
        """全局 HTTP I/O 代理网关拦截器（双链路熔断与降级架构）"""
        timeout = kwargs.pop('timeout', 15)
        proxy_max_retries = 2
        local_max_retries = 1  # 降级后总计执行2次（1次首发 + 1次重试）
        force_refresh = False
        blacklist_status = {403, 502, 503, 504}

        # === 阶段一：代理链路与熔断拦截 ===
        for attempt in range(proxy_max_retries + 1):
            try:
                proxies = self.proxy_manager.get_proxy(force_refresh=force_refresh)
            except Exception as e:
                self.logger.error(f"[NetGateway-Meltdown] 代理源获取产生致命异常，强制熔断代理链路: {e}")
                break  # 获取代理直接崩溃，跳出循环进入降级链路

            try:
                self.session.proxies.update(proxies)
                res = self.session.request(method, url, timeout=timeout, **kwargs)
                
                if res.status_code in blacklist_status:
                    raise RequestException(f"代理响应命中状态码黑名单: {res.status_code}")
                
                res.raise_for_status()
                return res
            except RequestException as e:
                self.logger.warning(f"[NetGateway-Proxy] 代理链路异常 (Attempt {attempt+1}/{proxy_max_retries+1}) | URL: {url} | Err: {e}")
                if attempt < proxy_max_retries:
                    force_refresh = True
                    time.sleep(1.5 ** attempt)
                else:
                    self.logger.warning("[NetGateway-Proxy] 代理重试阈值耗尽，启动本地直连降级。")

        # === 阶段二：会话状态净化 ===
        self.session.proxies.clear()

        # === 阶段三：本地直连双重试链路 ===
        for attempt in range(local_max_retries + 1):
            try:
                res = self.session.request(method, url, timeout=timeout, **kwargs)
                
                if res.status_code in blacklist_status:
                    raise RequestException(f"本地响应命中状态码黑名单: {res.status_code}")
                
                res.raise_for_status()
                return res
            except RequestException as e:
                self.logger.warning(f"[NetGateway-Local] 本地直连异常 (Attempt {attempt+1}/{local_max_retries+1}) | URL: {url} | Err: {e}")
                if attempt < local_max_retries:
                    time.sleep(1.5 ** attempt)
                else:
                    self.logger.error("[NetGateway-Local] 双链路全线溃败，抛出最终异常。")
                    raise

    def _header(self, referer=None):
        header = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://pan.baidu.com',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'User-Agent': self.user_agent,
            'X-Requested-With': 'XMLHttpRequest',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        if referer:
            header["Referer"] = referer
        return header

    def _to_dict(self, text):
        data = {}
        try:
            text_list = text.split(",")
            for i in text_list:
                temp = i.split(":")
                temp[0] = temp[0].strip()
                temp[1] = temp[1].strip().split("\"")
                if len(temp[1]) == 3:
                    temp[1] = temp[1][1]
                else:
                    temp[1] = temp[1][0].split("\'")
                    if len(temp[1]) == 3:
                        temp[1] = temp[1][1]
                    else:
                        temp[1] = temp[1][0]
                data[temp[0]] = temp[1]
        except Exception as e:
            self.logger.error(f"YunData 解析失败: {e}")
        return data

    def _get_base_cookies(self):
        return {
            "PANWEB": "1",
            "BDUSS": self.bduss,
            "BDUSS_BFESS": self.bduss_bfess,
            "STOKEN": self.stoken
        }
    
    def _get_bdstoken(self):
        if self.bdstoken:
            return self.bdstoken
        try:
            url = 'https://pan.baidu.com/api/gettemplatevariable'
            params = {
                'clienttype': '0', 'app_id': '250528', 'web': '1', 
                'dp-logid': self._get_dp_logid(), 'fields': '["bdstoken","token","uk","isdocuser","servertime"]'
            }
            res = self._request_with_proxy('GET', url, params=params, headers=self._header(referer='https://pan.baidu.com/disk/main'))
            res_json = res.json()
            if res_json.get('errno') == 0:
                self.bdstoken = res_json.get('result', {}).get('bdstoken')
                return self.bdstoken
        except Exception as e:
            self.logger.error(f"获取 bdstoken 异常: {e}")
        return None
    
    def _clean_surl(self, raw_url: str) -> str:
        """强力剥离协议头与参数，提取纯净的 Share ID"""
        clean_id = raw_url
        if 'pan.baidu.com/s/' in clean_id:
            clean_id = clean_id.split('pan.baidu.com/s/')[-1]
        elif clean_id.startswith('/s/'):
            clean_id = clean_id[3:]
            
        # 强力剔除可能存在的 URL 参数 (如 ?pwd=xxx 或 #xxxx)
        clean_id = clean_id.split('?')[0].split('&')[0].split('#')[0]
        return clean_id

    def _get_dp_logid(self):
        session_id = str(random.randint(100000, 999999))
        user_id = "00" + str(random.randint(10000000, 99999999))
        count_id = str(random.randint(10, 9999)).zfill(4)
        return f"{session_id}{user_id}{count_id}"

    def transfer(self, surl: str, pwd: str) -> dict:
        try:
            surl_clean = self._clean_surl(surl)
            verify_surl = surl_clean[1:] if surl_clean.startswith('1') else surl_clean
            
            response = self._request_with_proxy('GET', f'https://pan.baidu.com/s/{surl_clean}', headers=self._header(), allow_redirects=False)
            cookies = {
                'BAIDUID': f'{self.session.cookies.get("BAIDUID")}',
                'BAIDUID_BFESS': f'{self.session.cookies.get("BAIDUID_BFESS")}',
            }
            logid = base64.b64encode(cookies["BAIDUID"].encode()).decode() if cookies.get("BAIDUID") and cookies.get("BAIDUID") != "None" else ""
            dp_logid = self._get_dp_logid()

            data = {'pwd': pwd, 'vcode': '', 'vcode_str': ''}
            verify_url = (f'https://pan.baidu.com/share/verify?t={int(time.time_ns() / 1000000)}&surl={verify_surl}'
                          f'&channel=chunlei&web=1&app_id=250528&bdstoken=&logid={logid}&clienttype=0&dp-logid={dp_logid}')
            
            self._request_with_proxy('POST', verify_url, cookies=cookies, headers=self._header(referer=f'https://pan.baidu.com/share/init?surl={surl_clean}&pwd={pwd}'), data=data)
            
            # 【核心修复】：从全局 Session 池中强制提取网盘下发的 BDCLND 令牌
            BDCLND = self.session.cookies.get("BDCLND")
            if not BDCLND:
                self.logger.error("[Transfer] 致命异常：未能从 Session 中提取到 BDCLND。")
                return {"status": False, "msg": "获取 BDCLND cookie 失败，提取码可能错误或已被风控", "data": None}

            cookies.update(self._get_base_cookies())
            cookies["BDCLND"] = BDCLND
            params = {'pwd': pwd, '_at_': f'{int(time.time_ns() / 1000000)}'}
            response = self._request_with_proxy('GET', f'https://pan.baidu.com/s/{surl_clean}', params=params, cookies=cookies, headers=self._header())
            
            tree = etree.HTML(response.text)
            script = tree.xpath('//body/script')
            if not script:
                self.logger.error("[Transfer] 转存页面解析失败：未找到 script 节点，可能触发了反爬滑块。")
                return {"status": False, "msg": "转存页面解析异常", "data": None}

            yun_text = script[-1].xpath("./text()")[0].split("window.yunData={")[-1].split("}")[0]
            login_text = script[-1].xpath("./text()")[0].split("locals.mset(")[-1].split(");")[0]
            
            yun_json = self._to_dict(yun_text)
            login_json = json.loads(login_text)

            transfer_params = {
                'shareid': yun_json.get("shareid"), 'from': yun_json.get("share_uk"),
                'sekey': urllib.parse.unquote(BDCLND), 'ondup': 'newcopy', 'async': '1',
                'channel': 'chunlei', 'web': '1', 'app_id': '250528',
                'bdstoken': yun_json.get("bdstoken"), 'logid': '', 'clienttype': '0', 'dp-logid': self._get_dp_logid(),
            }
            transfer_data = {
                'fsidlist': f'[{login_json.get("file_list")[0].get("fs_id")}]', 'path': f'{self.save_path}',
            }
            response = self._request_with_proxy('POST', 'https://pan.baidu.com/share/transfer', params=transfer_params, cookies=cookies, headers=self._header(referer=f'https://pan.baidu.com/s/{surl_clean}?pwd={pwd}'), data=transfer_data)
            res_json = response.json()
            
            if res_json.get("errno") == 0:
                list_data = res_json.get("extra", {}).get("list", [{}])
                file_info = list_data[0] if list_data else {}
                self.logger.info(f"[Transfer] 转存成功。目标ID: {file_info.get('to_fs_id')}")
                return {"status": True, "msg": "转存成功", "data": {"to_fs_id": file_info.get("to_fs_id"), "to": file_info.get("to")}}
            
            self.logger.error(f"[Transfer] 转存上游拒绝。错误码: {res_json.get('errno')} | 返回体: {res_json}")
            return {"status": False, "msg": f"转存失败，errno: {res_json.get('errno')}", "data": res_json}
        except Exception as e:
            self.logger.error(f"转存异常: {e}")
            return {"status": False, "msg": f"转存过程发生异常: {str(e)}", "data": None}

    def delete_file(self, file_list: list) -> dict:
        try:
            formatted_list = [f"/{p.lstrip('/')}" for p in file_list]
            token = self._get_bdstoken() 
            params = {
                'async': '2', 'onnest': 'fail', 'opera': 'delete', 'newVerify': '1',
                'clienttype': '0', 'app_id': '250528', 'web': '1', 'dp-logid': self._get_dp_logid(), 'bdstoken': token
            }
            data = {'filelist': json.dumps(formatted_list, ensure_ascii=False)}
            response = self._request_with_proxy('POST', 'https://pan.baidu.com/api/filemanager', params=params, headers=self._header(referer='https://pan.baidu.com/disk/main'), data=data)
            res_json = response.json()
            errno = res_json.get("errno")
            return {"status": errno == 0, "msg": "删除成功" if errno == 0 else f"删除失败 (errno: {errno})", "data": res_json}
        except Exception as e:
            self.logger.error(f"删除异常: {e}")
            return {"status": False, "msg": f"删除过程发生异常: {str(e)}", "data": None}

    def share_file(self, fid_list: list, pwd: str = "ugqy", period: int = 1) -> dict:
        try:
            cookies = self._get_base_cookies()
            # 【核心修复】：挂载官方要求强制校验的 bdstoken
            token = self._get_bdstoken() 
            params = {
                'channel': 'chunlei', 'clienttype': '0', 'app_id': '250528', 
                'web': '1', 'dp-logid': self._get_dp_logid(), 'bdstoken': token
            }
            data = {
                'is_knowledge': '0', 'public': '0', 'period': str(period), 'pwd': pwd, 
                'eflag_disable': 'true', 'linkOrQrcode': 'link', 'channel_list': '[]', 
                'schannel': '4', 'fid_list': json.dumps(fid_list)
            }
            response = self._request_with_proxy('POST', 'https://pan.baidu.com/share/pset', params=params, cookies=cookies, headers=self._header(referer='https://pan.baidu.com/disk/main'), data=data)
            res_json = response.json()
            
            if res_json.get("errno") == 0:
                self.logger.info(f"[Share] 二次分享成功。")
                return {"status": True, "msg": "分享请求完成", "data": res_json}
                
            self.logger.error(f"[Share] 二次分享上游拒绝。错误码: {res_json.get('errno')} | 返回体: {res_json}")
            return {"status": False, "msg": f"分享失败 (errno: {res_json.get('errno')})", "data": res_json}
        except Exception as e:
            self.logger.error(f"分享异常: {e}")
            return {"status": False, "msg": f"分享过程发生异常: {str(e)}", "data": None}

    def get_file_list(self, dir_path: str = "/") -> dict:
        try:
            cookies = self._get_base_cookies()
            params = {'clienttype': '0', 'app_id': '250528', 'web': '1', 'dp-logid': self._get_dp_logid(), 'order': 'time', 'desc': '1', 'dir': dir_path, 'num': '100', 'page': '1'}
            response = self._request_with_proxy('GET', 'https://pan.baidu.com/api/list', params=params, cookies=cookies, headers=self._header(referer='https://pan.baidu.com/disk/main'))
            res_json = response.json()
            return {"status": res_json.get("errno") == 0, "msg": "获取列表完成" if res_json.get("errno") == 0 else "获取列表失败", "data": res_json}
        except Exception as e:
            self.logger.error(f"获取文件列表异常: {e}")
            return {"status": False, "msg": f"获取列表异常: {str(e)}", "data": None}
    
    def check_resource_health(self, surl: str, pwd: str = "") -> dict:
        try:
            surl_clean = self._clean_surl(surl)
            url = f'https://pan.baidu.com/s/{surl_clean}'
            init_response = self._request_with_proxy('GET', url, headers=self._header(), allow_redirects=False)
            cookies = {'BAIDUID': f'{self.session.cookies.get("BAIDUID")}', 'BAIDUID_BFESS': f'{self.session.cookies.get("BAIDUID_BFESS")}'}
            cookies.update(self._get_base_cookies())

            params = {}
            if pwd:
                params['pwd'] = pwd
                params['_at_'] = f'{int(time.time_ns() / 1000000)}'

            response = self._request_with_proxy('GET', url, params=params, cookies=cookies, headers=self._header())
            response.encoding = 'utf-8'
            html_text = response.text

            if any(k in html_text for k in ["啊哦，你所访问的页面不存在了", "页面不存在", "error-404"]): return {"status": False, "msg": "链接不存在或已失效", "data": None}
            if any(k in html_text for k in ["分享已取消", "被取消"]): return {"status": False, "msg": "分享已被取消", "data": None}
            if "提取码错误" in html_text: return {"status": False, "msg": "提取码错误", "data": None}

            tree = etree.HTML(html_text)
            title_element = tree.xpath('//title/text()')
            if title_element and any(k in title_element[0] for k in ["不存在", "失效", "错误"]): return {"status": False, "msg": f"链接失效 ({title_element[0]})", "data": None}
            script_list = tree.xpath('//body/script')
            if script_list:
                for script in script_list:
                    texts = script.xpath("./text()")
                    if texts and any(k in texts[0] for k in ["window.yunData={", "window.yunData ="]): return {"status": True, "msg": "资源正常", "data": None}
            return {"status": False, "msg": "未找到资源数据(页面可能被风控或结构有变更)", "data": None}
        except Exception as e:
            return {"status": False, "msg": f"页面解析异常: {str(e)}", "data": None}
    
    def verify_pwd(self, surl: str, pwd: str) -> dict:
        try:
            surl_clean = self._clean_surl(surl)
            response = self._request_with_proxy('GET', f'https://pan.baidu.com/s/{surl_clean}', headers=self._header(), allow_redirects=False)
            cookies = {'BAIDUID': f'{self.session.cookies.get("BAIDUID")}', 'BAIDUID_BFESS': f'{self.session.cookies.get("BAIDUID_BFESS")}'}
            
            if not cookies.get("BAIDUID") or cookies.get("BAIDUID") == "None":
                return {"status": False, "msg": "获取不到 BAIDUID，链接可能存在异常", "data": None}
                
            logid = base64.b64encode(cookies["BAIDUID"].encode()).decode()
            verify_surl = surl_clean[1:] if surl_clean.startswith('1') else surl_clean
            verify_url = (f'https://pan.baidu.com/share/verify?t={int(time.time_ns() / 1000000)}&surl={verify_surl}'
                          f'&channel=chunlei&web=1&app_id=250528&bdstoken=&logid={logid}&clienttype=0&dp-logid={self._get_dp_logid()}')
            data = {'pwd': pwd, 'vcode': '', 'vcode_str': ''}
            verify_res = self._request_with_proxy('POST', verify_url, cookies=cookies, headers=self._header(referer=f'https://pan.baidu.com/share/init?surl={verify_surl}'), data=data)
            res_json = verify_res.json()
            errno = res_json.get("errno")
            if errno == 0: return {"status": True, "msg": "提取码正确", "data": res_json}
            elif errno == -9: return {"status": False, "msg": "提取码错误", "data": res_json}
            elif errno == -12: return {"status": False, "msg": "提取码为空或不合法", "data": res_json}
            return {"status": False, "msg": f"验证失败, 错误码: {errno}", "data": res_json}
        except Exception as e:
            return {"status": False, "msg": f"解析验证接口结果异常: {str(e)}", "data": None}
        
    def count_share_files(self, surl: str, pwd: str) -> int:
        try:
            surl_clean = self._clean_surl(surl)
            verify_surl = surl_clean[1:] if surl_clean.startswith('1') else surl_clean
            url = f'https://pan.baidu.com/s/{surl_clean}'
            
            init_response = self._request_with_proxy('GET', url, headers=self._header(), allow_redirects=False)
            cookies = {'BAIDUID': f'{init_response.cookies.get("BAIDUID")}', 'BAIDUID_BFESS': f'{init_response.cookies.get("BAIDUID_BFESS")}'}
            logid = base64.b64encode(cookies["BAIDUID"].encode()).decode() if cookies.get("BAIDUID") else ""
            dp_logid = self._get_dp_logid()

            if pwd:
                verify_url = (f'https://pan.baidu.com/share/verify?t={int(time.time_ns() / 1000000)}&surl={verify_surl}'
                              f'&channel=chunlei&web=1&app_id=250528&bdstoken=&logid={logid}&clienttype=0&dp-logid={dp_logid}')
                data = {'pwd': pwd, 'vcode': '', 'vcode_str': ''}
                verify_res = self._request_with_proxy('POST', verify_url, cookies=cookies, headers=self._header(referer=f'https://pan.baidu.com/share/init?surl={surl_clean}'), data=data)
                if verify_res.cookies.get("BDCLND"): cookies["BDCLND"] = verify_res.cookies.get("BDCLND")

            cookies.update(self._get_base_cookies())
            params = {'pwd': pwd, '_at_': f'{int(time.time_ns() / 1000000)}'} if pwd else {}
            res = self._request_with_proxy('GET', url, params=params, cookies=cookies, headers=self._header())
            
            tree = etree.HTML(res.text)
            script_list = tree.xpath('//body/script')
            if not script_list: return 0
            
            yun_text, login_text = "", ""
            for script in script_list:
                texts = script.xpath("./text()")
                if not texts: continue
                text = texts[0]
                if "window.yunData" in text:
                    if "window.yunData={" in text: yun_text = text.split("window.yunData={")[-1].split("}")[0]
                    elif "window.yunData = {" in text: yun_text = text.split("window.yunData = {")[-1].split("}")[0]
                    if "locals.mset(" in text: login_text = text.split("locals.mset(")[-1].split(");")[0]
                    break
            
            if not yun_text or not login_text: return 0

            yun_json = self._to_dict(yun_text)
            login_json = json.loads(login_text)

            total_files = 0
            for item in login_json.get("file_list", []):
                if item.get("isdir") == 1: total_files += self._count_share_dir(item.get("path"), yun_json, cookies.get("BDCLND"), cookies, dp_logid, surl_clean, pwd)
                else: total_files += 1
            return total_files
        except Exception:
            return 0

    def _count_share_dir(self, dir_path: str, yun_json: dict, bdclnd: str, cookies: dict, dp_logid: str, surl: str, pwd: str) -> int:
        url = 'https://pan.baidu.com/share/list'
        params = {
            'uk': yun_json.get("share_uk"), 'shareid': yun_json.get("shareid"), 'sekey': urllib.parse.unquote(bdclnd) if bdclnd else "",
            'dir': dir_path, 'page': '1', 'num': '1000', 'channel': 'chunlei', 'web': '1',
            'app_id': '250528', 'clienttype': '0', 'dp-logid': dp_logid, 'order': 'other', 'desc': '1', 'showempty': '0'
        }
        try:
            response = self._request_with_proxy('GET', url, params=params, cookies=cookies, headers=self._header(referer=f'https://pan.baidu.com/s/{surl}?pwd={pwd}'))
            res_json = response.json()
            folder_file_count = 0
            if res_json.get("errno") == 0:
                for item in res_json.get("list", []):
                    if item.get("isdir") == 1: folder_file_count += self._count_share_dir(item.get("path"), yun_json, bdclnd, cookies, dp_logid, surl, pwd)
                    else: folder_file_count += 1
            return folder_file_count
        except Exception:
            return 0