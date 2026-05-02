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
from datetime import datetime, timedelta
from requests.exceptions import RequestException
import urllib3
from exts import db
from models import ProxyNode
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ProxyManager:
    """基于MySQL行锁与10分钟冷却的代理生命周期管理器"""
    def __init__(self):
        self.api_url = "https://kps.kdlapi.com/api/getkps/?secret_id=ol6scirom0vt1kuokv8t&signature=qo1gd2tmj41ajnn4yxh107ygiophv7dn&num=1&sep=1"
        self.proxy_user = "bdfishtn"
        self.proxy_pass = "vqz7axpt"
        self.logger = logging.getLogger(__name__)

    def _fetch_new_ip(self, level: int = 1) -> str:
        # 统一主链路 API 请求
        res = requests.get(self.api_url, timeout=10)
        res.raise_for_status()
        # 兼容 \r\n 与 \n 分割边界，剔除潜在空行
        ip_list = [ip.strip() for ip in re.split(r'\r?\n', res.text.strip()) if ip.strip()]
        if len(ip_list) < 2:
            raise ValueError(f"代理API返回节点数量不足2个: {res.text}")
        # 映射读取架构：level 1 摘取 [0]，level 2 摘取 [1]
        ip_port = ip_list[0] if level == 1 else ip_list[1]
        if not re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', ip_port):
            raise ValueError(f"代理API({level})返回异常数据: {ip_port}")
        return ip_port
    
    def get_proxy(self, level: int) -> dict:
        """获取指定层级代理，含10分钟故障冷却与每日00:10跨日比对更新"""
        try:
            node = ProxyNode.query.filter_by(level=level).with_for_update().first()
            if not node:
                raise ValueError(f"数据库缺失 ProxyNode Level {level} 初始化数据")

            now = datetime.now()
            cooldown_period = timedelta(minutes=10)
            
            # 锁定今天的 00:10:00 临界点
            today_update_time = now.replace(hour=0, minute=10, second=0, microsecond=0)
            
            # 跨日准点更新检测: 当前越过 00:10，且最后刷新时间在 00:10 之前
            is_daily_update = now >= today_update_time and node.last_refresh_time < today_update_time
            
            # 故障恢复检测: 处于失效状态且已熬过 10 分钟冷却
            is_recovery = node.is_failed and (now - node.last_refresh_time) >= cooldown_period
            
            # 初始空库检测
            is_initial = not node.ip_port

            # 1. 拦截层：已失效且处于10分钟惩罚期内，且未触发跨日更新 -> 静默跳过，交由下游 API 2 或本地接管
            if node.is_failed and not is_recovery and not is_daily_update and not is_initial:
                db.session.rollback()
                return None

            # 2. 触发获取层
            if is_initial or is_recovery or is_daily_update:
                reason = "初始化" if is_initial else "跨日准点更新" if is_daily_update else "故障恢复"
                self.logger.info(f"[ProxyManager] 触发 API {level} 刷新机制 (引擎: {reason})...")
                
                new_ip = self._fetch_new_ip(level=level)
                
                # 3. 动态比对与状态愈合引擎 (兼容静态IP与动态IP)
                if new_ip != node.ip_port:
                    self.logger.info(f"[ProxyManager] API {level} 获取到新IP，执行更迭。")
                    node.ip_port = new_ip
                else:
                    self.logger.info(f"[ProxyManager] API {level} IP未变(源自静态IP或上游未轮转)。")

                # 核心修复：只要触发故障恢复或例行更新，无条件解除失效状态并刷新时间戳
                node.is_failed = False
                node.last_refresh_time = now
                
                db.session.commit()
                return self._format_proxy(node.ip_port)

            db.session.commit()
            return self._format_proxy(node.ip_port)

        except Exception as e:
            db.session.rollback()
            raise RuntimeError(f"ProxyManager异常: {str(e)}")

    def mark_failed(self, level: int):
        """将指定层级代理标记为失效"""
        try:
            node = ProxyNode.query.filter_by(level=level).with_for_update().first()
            if node and not node.is_failed:
                node.is_failed = True
                node.last_refresh_time = datetime.now()
                db.session.commit()
                self.logger.warning(f"[ProxyManager] 已将 API {level} 标记为失效，进入10分钟冷却。")
            else:
                db.session.rollback()
        except Exception as e:
            db.session.rollback()
            self.logger.error(f"[ProxyManager] 标记代理失效异常: {e}")

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
        """全局 HTTP I/O 代理网关拦截器（三级级联降级架构，全局异常兜底）"""
        timeout = kwargs.pop('timeout', 15)
        blacklist_status = {403, 502, 503, 504}

        def _sniff_business_risk(res: requests.Response):
            """深层探针：穿透 200 状态码嗅探业务风控特征"""
            if res.status_code == 200:
                body = res.text[:1024] 
                if re.search(r'"errno"\s*:\s*9019', body) or 'need verify' in body:
                    raise RuntimeError("命中业务层反爬风控 (200状态码伪装)")

        # === Phase 1: API 1 链路 ===
        try:
            proxies = self.proxy_manager.get_proxy(level=1)
            if proxies:
                self.session.proxies.update(proxies)
                res = self.session.request(method, url, timeout=timeout, **kwargs)
                if res.status_code in blacklist_status:
                    raise RuntimeError(f"命中状态码黑名单: {res.status_code}")
                _sniff_business_risk(res)
                return res
        except Exception as e:
            self.logger.warning(f"[NetGateway] API 1 链路阻断(异常拦截): {e} | 触发降级 -> API 2")
            self.proxy_manager.mark_failed(level=1)

        # === Phase 2: API 2 链路 ===
        try:
            proxies = self.proxy_manager.get_proxy(level=2)
            if proxies:
                self.session.proxies.update(proxies)
                res = self.session.request(method, url, timeout=timeout, **kwargs)
                if res.status_code in blacklist_status:
                    raise RuntimeError(f"命中状态码黑名单: {res.status_code}")
                _sniff_business_risk(res)
                return res
        except Exception as e:
            self.logger.warning(f"[NetGateway] API 2 链路阻断(异常拦截): {e} | 触发降级 -> 本地直连")
            self.proxy_manager.mark_failed(level=2)

        # === Phase 3: 本地直连链路 ===
        self.session.proxies.clear()
        try:
            res = self.session.request(method, url, timeout=timeout, **kwargs)
            if res.status_code in blacklist_status:
                raise RuntimeError(f"命中状态码黑名单: {res.status_code}")
            _sniff_business_risk(res)
            return res
        except Exception as e:
            self.logger.error(f"[NetGateway] 本地直连异常或全线熔断: {e}")
            raise RuntimeError("所有网络代理节点及本地网络均已熔断，请10分钟后重试")

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
            print(res_json)
            
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