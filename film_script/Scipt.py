# -*- coding=utf-8
import urllib.parse
import re
import time
from curl_cffi import requests
import requests as std_requests  # 隔离 curl_cffi，供图片下载专用
from lxml import html
from concurrent.futures import ThreadPoolExecutor
from qcloud_cos import CosConfig
from qcloud_cos import CosS3Client
from models import SystemConfig
import random

# 延迟初始化变量，隔离应用上下文
_cos_client = None
_COS_BUCKET = None

def _init_cos():
    global _cos_client, _COS_BUCKET
    if _cos_client is None:
        try:
            sys_config = SystemConfig.query.first()
            if sys_config and sys_config.cos_secret_id:
                cos_config = CosConfig(
                    Region=sys_config.cos_region,
                    SecretId=sys_config.cos_secret_id,
                    SecretKey=sys_config.cos_secret_key
                )
                _cos_client = CosS3Client(cos_config)
                _COS_BUCKET = sys_config.cos_bucket
        except Exception:
            pass

def clean_release_time(text):
    """提取合法的年份整数和完整日期，摒弃非法的00-00格式"""
    if not text or text == "空": return None, None
    match = re.search(r'(\d{4})(-\d{2}-\d{2})?', text)
    if not match: return None, None
    year = int(match.group(1))
    full_date = f"{year}{match.group(2)}" if match.group(2) else None
    return year, full_date

def process_cover_image(image_url):
    """处理封面图：下载并直传 COS。包含重试机制，彻底失败则降级返回原外链"""
    if not image_url or image_url == "空":
        return ""
        
    filename = image_url.split('/')[-1]
    cos_key = f"images/{filename}"
    max_retries = 1
    
    # 防御拦截：未配置 COS 时直接降级回传外链
    if not _cos_client or not _COS_BUCKET:
        return image_url

    for attempt in range(max_retries + 1):
        try:
            res = std_requests.get(image_url, timeout=10)
            res.raise_for_status()
            
            _cos_client.put_object(
                Bucket=_COS_BUCKET,
                Body=res.content,
                Key=cos_key,
                ContentType=res.headers.get('Content-Type', 'image/jpeg')
            )
            return cos_key  # 成功静默
        except Exception as e:
            if attempt == max_retries:
                print(f"❌ COS 上传失败 [降级保留原图] -> {image_url} | 错误: {e}")
                return image_url
            time.sleep(1)
            
    return image_url
def _fetch_detail(task, base_url):
    # 随机抖动延迟 (0.5 到 1.5 秒)：打破绝对并发带来的瞬间高并发尖峰，伪装成人类点击
    # time.sleep(random.uniform(0.5, 1))
    title, raw_url, rating = task
    full_url = urllib.parse.urljoin(base_url, raw_url)
    try:
        res = requests.get(full_url, impersonate="chrome", timeout=15)
        tree = html.fromstring(res.content)

        def get_text(xpath):
            nodes = tree.xpath(xpath)
            return "".join(nodes).strip() if nodes else "空"

        def get_text_by_label(label_name):
            # 健壮的 XPath 特征锚定提取引擎：无视节点错位滑坡
            xpath_expr = f'/html/body/div/div/div[3]/div[1]/div[1]/ul/li[contains(text(), "{label_name}")]//text()'
            nodes = tree.xpath(xpath_expr)
            if not nodes:
                return "空"
            
            full_text = "".join(nodes).replace('\n', '').strip()
            
            # 切除键名(如 "类型: ")，只保留值
            if ':' in full_text:
                return full_text.split(':', 1)[-1].strip()
            elif '：' in full_text:
                return full_text.split('：', 1)[-1].strip()
            return full_text.strip()

        # 抛弃绝对位置，动态按名索骥
        raw_type = get_text_by_label('类型')
        raw_country = get_text_by_label('制片国家/地区')
        raw_lang = get_text_by_label('语言')
        raw_time = get_text_by_label('首播')

        # 结构畸形拦截兜底：全空说明不是标准详情页，直接丢弃
        if raw_type == "空" and raw_lang == "空" and raw_time == "空":
            return None

        synopsis = get_text('/html/body/div/div/div[3]/div[1]/p[3]//text()')
        
        cover = tree.xpath('/html/body/div/div/div[3]/div[1]/div[1]/img/@src')
        raw_cover_url = cover[0] if cover else ""
        final_cover = process_cover_image(raw_cover_url)

        return {
            "title": title,
            "score": float(rating) if rating != "空" else 0.0,
            "source_url": full_url,
            "cover_url": final_cover,
            # 注意：因为 get_text_by_label 已经切掉了冒号前缀，这里直接用 "/" 分割即可
            "categories": [i.strip() for i in raw_type.split("/") if i.strip()],
            "regions": [i.strip() for i in raw_country.split("/") if i.strip()],
            "languages": [i.strip() for i in raw_lang.split("/") if i.strip()],
            "release_year": clean_release_time(raw_time)[0],
            "release_date": clean_release_time(raw_time)[1],
            # 剔除导致 MySQL 报错的 4字节 Emoji 字符
            "description": re.sub(r'[^\u0000-\uFFFF]', '', synopsis.replace('\n', '')).strip() 
        }
    except Exception as e:
        print(f"解析详情页失败: {full_url} - {e}")
        return None

def run_spider(base_url, existing_urls, start_page=1, end_page=1, max_workers=5):
    """流式爬虫引擎 (Generator)"""
    _init_cos()
    total_pages = abs(end_page - start_page) + 1
    processed_pages = 0
    
    # 倒序翻页
    for page in range(end_page, start_page - 1, -1):
        page_url = f"{base_url}?page={page}" if '?' not in base_url else f"{base_url}&page={page}"
        
        try:
            response = requests.get(page_url, impersonate="chrome", timeout=15)
            tree = html.fromstring(response.content)
            items = tree.xpath('/html/body/div/div/div[3]/div[1]/div/div')
        except Exception as e:
            yield {"status": "error", "page": page, "msg": str(e)}
            continue

        tasks = []
        for item in items:
            raw_url = item.xpath('./a/@href')
            if not raw_url: continue
            full_url = urllib.parse.urljoin(base_url, raw_url[0])
            
            if full_url in existing_urls:
                continue
                
            title = item.xpath('./ul/li[1]/h2/text()')
            rating = item.xpath('./ul/li[4]/a/text()')
            tasks.append((title[0].strip() if title else "未知", raw_url[0], rating[0].strip() if rating else "空"))

        # 倒序单页列表
        tasks.reverse()
        page_results = []
        
        # 阻塞式线程池执行
        for t in tasks:
            res = _fetch_detail(t, base_url)
            if res: 
                page_results.append(res)
                existing_urls.append(res['source_url'])
        
        processed_pages += 1
        
        # Stream 抛出单页数据
        yield {
            "status": "ok",
            "page": page,
            "count": len(page_results),
            "remaining": total_pages - processed_pages,
            "data": page_results
        }