import json
import urllib.parse
import re
import time
import random  # 引入随机数模块，用于生成随机延迟
from curl_cffi import requests
from lxml import html

# --- 增强版清洗函数 ---
def clean_data(text, is_date=False):
    if not text or text == "空":
        return "" if is_date else []
    
    if ":" in text:
        text = text.split(":", 1)[-1]
    
    if is_date:
        text = re.sub(r'\(.*?\)|（.*?）', '', text)
        parts = [item.strip() for item in text.split("/") if item.strip()]
        return parts[0] if parts else ""
    
    return [item.strip() for item in text.split("/") if item.strip()]


def run_spider():
    base_url = 'https://www.seedhub.cc/categories/1/movies/'
    
    # --- 步骤 1: 访问列表页 ---
    print("\n" + "="*50)
    print("🚀 [1/3] 正在访问列表页...")
    try:
        response = requests.get(base_url, impersonate="chrome", timeout=15)
        tree = html.fromstring(response.content)
        item_list = tree.xpath('/html/body/div/div/div[3]/div[1]/div/div')
        
        if not item_list:
            print("❌ 没找到数据，请检查 XPath。")
            return
            
        print(f"👉 成功突破！一共找到了 {len(item_list)} 部影视作品。")
    except Exception as e:
        print(f"❌ 列表页获取失败: {e}")
        return

    # 准备一个空列表，用来装所有爬取到的电影数据
    all_movies_data = []

    # --- 步骤 2: 遍历访问详情页 ---
    print("\n🚀 [2/3] 开始批量获取详情页数据...")
    
    # 使用 enumerate 可以在遍历时知道当前是第几个
    for index, item in enumerate(item_list, start=1):
        # 提取列表页的基础信息
        raw_title = item.xpath('./ul/li[1]/h2/text()')
        raw_url = item.xpath('./a/@href')
        raw_rating = item.xpath('./ul/li[4]/a/text()')
        
        title = raw_title[0].strip() if raw_title else "未知标题"
        detail_url = raw_url[0] if raw_url else ""
        rating = raw_rating[0].strip() if raw_rating else "空"
        
        if not detail_url:
            print(f"⚠️ 第 {index} 个项目没有链接，跳过...")
            continue
            
        full_url = urllib.parse.urljoin(base_url, detail_url)
        print(f"➤ 正在抓取 ({index}/{len(item_list)}): 【{title}】")
        
        detail_headers = {"Referer": base_url}
        try:
            # 访问详情页
            detail_response = requests.get(full_url, impersonate="chrome", headers=detail_headers, timeout=15)
            
            if detail_response.status_code != 200:
                print(f"  ❌ 被拦截或不存在 (状态码 {detail_response.status_code})，跳过...")
                continue
                
            detail_tree = html.fromstring(detail_response.content)
            
            # 定义提取小帮手
            def get_raw_text(xpath_rule):
                nodes = detail_tree.xpath(xpath_rule)
                return "".join(nodes).strip() if nodes else "空"

            # 提取详情
            cover_img_list = detail_tree.xpath('/html/body/div/div/div[3]/div[1]/div[1]/img/@src')
            cover_img = cover_img_list[0] if cover_img_list else "空"
            
            raw_type = get_raw_text('/html/body/div/div/div[3]/div[1]/div[1]/ul/li[5]//text()')
            raw_country = get_raw_text('/html/body/div/div/div[3]/div[1]/div[1]/ul/li[6]//text()')
            raw_language = get_raw_text('/html/body/div/div/div[3]/div[1]/div[1]/ul/li[7]//text()')
            raw_play_time = get_raw_text('/html/body/div/div/div[3]/div[1]/div[1]/ul/li[8]//text()')
            raw_synopsis = get_raw_text('/html/body/div/div/div[3]/div[1]/p[3]//text()')

            # 清洗并组装字典
            final_data = {
                "标题": title,
                "评分": rating, 
                "类型": clean_data(raw_type),
                "链接": full_url,
                "封面图": cover_img,
                "国家地区": clean_data(raw_country),
                "语言信息": clean_data(raw_language),
                "首播时间": clean_data(raw_play_time, is_date=True),
                "内容简介": raw_synopsis.replace('\n', '').replace('\r', '').strip()
            }
            
            # 🌟 把清洗好的单个电影数据，塞进大列表里
            all_movies_data.append(final_data)
            print(f"  ✅ 提取成功！")
            
        except Exception as e:
            print(f"  ❌ 请求报错: {e}，跳过...")
            continue
            
        # 🌟 核心防封逻辑：每次请求完，随机休息 1.5 到 3.5 秒
        if index < len(item_list): # 最后一个爬完就不用休息了
            sleep_time = random.uniform(1.5, 3.5)
            print(f"  ⏳ 随机休眠 {sleep_time:.2f} 秒，模拟人类浏览...")
            time.sleep(sleep_time)

    # --- 步骤 3: 保存至本地 JSON 文件 ---
    print("\n🚀 [3/3] 所有数据抓取完毕，正在保存至本地...")
    
    # 将字典列表写入到 seedhub_data.json 文件中
    file_name = 'seedhub_data_movies.json'
    with open(file_name, 'w', encoding='utf-8') as f:
        # ensure_ascii=False 保证中文正常写入，indent=4 让文件里的代码有整齐的缩进排版
        json.dump(all_movies_data, f, ensure_ascii=False, indent=4)
        
    print("-" * 40)
    print(f"🎉 大功告成！共成功保存 {len(all_movies_data)} 条数据！")
    print(f"📁 文件已保存在当前目录下：{file_name}")
    print("="*50 + "\n")


if __name__ == '__main__':
    run_spider()