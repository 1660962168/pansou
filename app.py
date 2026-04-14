import sys
import logging

# 初始化全局日志配置 (交由 uWSGI/宝塔 守护进程接管)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    stream=sys.stdout
)

from flask import Flask, session, g, render_template,request, jsonify
import config
from exts import db
from blueprints.admin import bp as admin_bp
from livereload import Server # 测试用
from models import *
import requests
from datetime import datetime, timedelta
import json
from apscheduler.schedulers.background import BackgroundScheduler
import os
from search_service import PanSouClient
from BaiduTransfer import BaiduTransfer
import re
import threading
from quark_client import QuarkClient
from quark_client.exceptions import APIError, ShareLinkError
from datetime import date
from apscheduler.executors.pool import ThreadPoolExecutor


app = Flask(__name__)
app.config.from_object(config)
app.register_blueprint(admin_bp)
db.init_app(app)


def get_real_ip():
    """穿透反代获取真实客户端IP"""
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    return request.remote_addr

@app.before_request
def check_maintenance_mode():
    path = request.path
    # 放行后台管理路由和静态文件路由，确保管理员不会被锁在外面
    if path.startswith('/admin') or path.startswith('/static') or path == '/favicon.ico':
        return

    try:
        sys_config = SystemConfig.query.first()
        # 检查维护开关是否开启
        if sys_config and sys_config.maintenance_mode:
            client_ip = get_real_ip()
            # 获取白名单，支持按逗号或换行分割
            whitelist_str = sys_config.maintenance_whitelist or ''
            whitelist = [ip.strip() for ip in whitelist_str.replace(',', '\n').split('\n') if ip.strip()]
            
            # 如果当前访问IP不在白名单中，则拦截并返回维护页面
            if client_ip not in whitelist:
                # 503 Service Unavailable 对SEO比较友好
                return render_template('maintenance.html'), 503
    except Exception as e:
        logging.error(f"维护模式拦截器异常: {e}")

@app.before_request
def protect_drainage_files():
    path = request.path
    # 阻断外部对引流配置目录的直接 GET 访问，确保仅供底层网盘拼装使用
    if path.startswith('/static/drainage/'):
        from flask import abort
        abort(403)

@app.before_request
def record_page_view():
    path = request.path
    # 拦截并放行非前台页面
    if path.startswith('/admin') or path.startswith('/api') or path.startswith('/static') or path == '/favicon.ico':
        return
    
    try:
        today = datetime.now().date()
        client_ip = get_real_ip()
        
        # IP 级去重探针 (利用数据库联合索引)
        if VisitorIPRecord.query.filter_by(ip_address=client_ip, visit_date=today).first():
            return # 当前IP今日已记录，抛弃统计动作直接放行
            
        # 写入新IP黑匣子
        new_ip_record = VisitorIPRecord(ip_address=client_ip, visit_date=today)
        db.session.add(new_ip_record)

        stat = SiteStat.query.filter_by(date=today).first()
        if not stat:
            stat = SiteStat(date=today, page_views=1)
            db.session.add(stat)
        else:
            stat.page_views += 1
            
        db.session.commit()
    except Exception as e:
        db.session.rollback()

def record_frontend_transfer():
    """复用：记录前台解密触发的转存数量"""
    try:
        today = datetime.now().date()
        stat = SiteStat.query.filter_by(date=today).first()
        if not stat:
            stat = SiteStat(date=today, frontend_transfers=1)
            db.session.add(stat)
        else:
            stat.frontend_transfers += 1
        db.session.commit()
    except Exception:
        db.session.rollback()

def async_fetch_ip_location(ip_address):
    """异步获取IP归属地并缓存"""
    def task(app_context, ip):
        with app_context:
            if not db.session.get(IpLocationCache, ip):
                try:
                    import ip as ip_tool
                    # 注意：如需传app_code，请在此处传入配置值，或修改ip.py设为默认值
                    res = ip_tool.get_ip_details(ip, app_code="9d922d749a9640cba3e681e0b9d93196")
                    region = res.get('region', '')
                    city = res.get('city', '')
                    cache = IpLocationCache(ip_address=ip, region=region, city=city)
                    db.session.add(cache)
                    db.session.commit()
                except Exception as e:
                    logging.error(f"IP归属地获取失败: {e}")
    
    thread = threading.Thread(target=task, args=(app.app_context(), ip_address))
    thread.daemon = True
    thread.start()

def record_user_transfer(client_ip, resource_name):
    """记录用户转存流水并触发IP缓存"""
    try:
        record = TransferRecord(ip_address=client_ip, resource_name=resource_name)
        db.session.add(record)
        db.session.commit()
        async_fetch_ip_location(client_ip)
    except Exception as e:
        db.session.rollback()
        logging.error(f"记录转存流水失败: {e}")

def init_db_data():
    # 1. 初始化管理员
    admin = Admin.query.filter_by(username='admin').first()
    if not admin:
        new_admin = Admin(username='admin')
        new_admin.password = 'admin123'
        db.session.add(new_admin)
        logging.info("Created default admin.")

    # 2. 初始化网站配置
    site_config = SiteConfig.query.first()
    if not site_config:
        default_config = SiteConfig()
        db.session.add(default_config)
        logging.info("Created default site config.")
        
    # 3. 初始化系统配置 (二维码等核心数据)
    sys_config = SystemConfig.query.first()
    if not sys_config:
        default_sys = SystemConfig()
        db.session.add(default_sys)
        logging.info("Created default system config.")
    
    db.session.commit()

def sync_external_drama(app_instance):
    """同步外部短剧API数据，根据title去重合并"""
    with app_instance.app_context():
        try:
            logging.info("[Scheduler] 开始获取 External Drama API...")
            res = requests.get('https://api.uuuka.com/api/contents/post/all', timeout=30)
            if res.status_code != 200:
                logging.info(f"[Scheduler] External Drama API 异常, 状态码: {res.status_code}")
                return
                
            data = res.json()
            if not data.get('success'):
                logging.info("[Scheduler] External Drama API 返回失败状态")
                return

            items = data.get('data', {}).get('items', [])
            for item in items:
                title = item.get('title', '').strip()
                source_link = item.get('source_link', '').strip()
                update_time_str = item.get('update_time', '')
                
                if not title or not source_link:
                    continue
                    
                # 判定网盘归属，非指定网盘直接丢弃
                link_lower = source_link.lower()
                is_baidu = 'baidu' in link_lower
                is_quark = 'quark' in link_lower
                if not is_baidu and not is_quark:
                    continue
                
                # 解析时间戳
                try:
                    update_time = datetime.strptime(update_time_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    update_time = datetime.now()

                # 去重与合并逻辑
                record = ExternalDrama.query.filter_by(title=title).first()
                if record:
                    if is_baidu:
                        record.baidu_link = source_link
                    if is_quark:
                        record.quark_link = source_link
                    record.update_time = update_time
                else:
                    new_record = ExternalDrama(
                        title=title,
                        baidu_link=source_link if is_baidu else None,
                        quark_link=source_link if is_quark else None,
                        update_time=update_time
                    )
                    db.session.add(new_record)
            
            db.session.commit()
            logging.info(f"[Scheduler] External Drama 同步完成，处理总数: {len(items)}")
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler] External Drama 同步崩溃: {e}")

from quark_client.exceptions import ShareLinkError

def check_share_link_status(quark_client, share_url):
    try:
        share_id, password = quark_client.shares.parse_share_url(share_url)
        token = quark_client.shares.get_share_token(share_id, password)
        share_info = quark_client.shares.get_share_info(share_id, token)
        if isinstance(share_info, dict) and 'data' in share_info and share_info['data'].get('list'):
            return "normal"
        return "invalid"
    except Exception:
        return "invalid"

# 监控资源
def check_monitor_links(app_instance):
    from blueprints.admin import _execute_quark_transfer, _execute_baidu_transfer, _smart_fetch_layer
    
    with app_instance.app_context():
        try:
            sys_config = SystemConfig.query.first()
            interval = sys_config.task_interval if sys_config and sys_config.task_interval else 60
            now = datetime.now()
            cutoff = now - timedelta(minutes=interval)
            
            tasks = db.session.query(MonitorTask).filter(
                MonitorTask.is_monitoring == True,
                (MonitorTask.last_check_time == None) | (MonitorTask.last_check_time <= cutoff)
            ).all()

            if not tasks: return

            quark_client = None
            transfer_client = None

            for task in tasks:
                has_transferred = False
                
                # 1. 监控夸克
                if task.quark_source_link:
                    try:
                        if not quark_client:
                            quark_client = QuarkClient(cookies=sys_config.quark_cookie) if sys_config and sys_config.quark_cookie else QuarkClient()
                        
                        share_id, parsed_pwd = quark_client.shares.parse_share_url(task.quark_source_link)
                        token = quark_client.shares.get_share_token(share_id, parsed_pwd)
                        current_count = len(_smart_fetch_layer(quark_client, share_id, token))
                        
                        if current_count == 0:
                            task.quark_status = "invalid"
                        elif task.quark_file_count == 0 or current_count > task.quark_file_count:
                            logging.info(f"[Auto Transfer] 夸克数量增加或为0基准 ({task.quark_file_count} -> {current_count})，执行转存... ID:{task.id}")
                            _execute_quark_transfer(task)
                            has_transferred = True
                        else:
                            task.quark_status = "normal"
                    except Exception as e:
                        task.quark_status = "invalid"
                        logging.info(f"[Probe Error] Quark ID:{task.id} -> {e}")
                
                # 2. 监控百度
                if task.baidu_source_link and sys_config and sys_config.baidu_bduss:
                    try:
                        if not transfer_client:
                            fallback_path = sys_config.baidu_daily_path or sys_config.baidu_save_path or '/资源/'
                            transfer_client = BaiduTransfer(
                                bduss=sys_config.baidu_bduss, bduss_bfess=sys_config.baidu_bduss_bfess,
                                stoken=sys_config.baidu_stoken, save_path=fallback_path
                            )
                        match = re.search(r'/s/([a-zA-Z0-9_-]+)', task.baidu_source_link)
                        pwd_match = re.search(r'[?&]pwd=([a-zA-Z0-9]{4,})', task.baidu_source_link)
                        pwd = pwd_match.group(1) if pwd_match else ""
                        
                        if match:
                            surl = match.group(1)
                            health = transfer_client.check_resource_health(surl)
                            if health.get("status") or health.get("msg") == "提取码错误":
                                current_count = transfer_client.count_share_files(surl, pwd)
                                if current_count == 0:
                                    task.baidu_status = "invalid"
                                elif task.baidu_file_count == 0 or current_count > task.baidu_file_count:
                                    logging.info(f"[Auto Transfer] 百度数量增加或为0基准 ({task.baidu_file_count} -> {current_count})，执行转存... ID:{task.id}")
                                    _execute_baidu_transfer(task)
                                    has_transferred = True
                                else:
                                    task.baidu_status = "normal"
                            else:
                                task.baidu_status = "invalid"
                    except Exception as e:
                        task.baidu_status = "invalid"
                        logging.info(f"[Probe Error] Baidu ID:{task.id} -> {e}")
                
                task.check_count += 1
                if has_transferred: task.transfer_count += 1
                task.last_check_time = now
            
            db.session.commit()
            logging.info(f"[Scheduler] 资源监控与自动转存完毕, 影响记录: {len(tasks)} 条")
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler-Error] Monitor Check Error: {e}")

def process_cleanup_tasks(app_instance):
    """处理云端文件清理与本地过期状态删除任务"""
    with app_instance.app_context():
        try:
            now = datetime.now()
            logging.info(f"[Scheduler-Probe] 轮询触发 | 当前系统时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 1. 提取并执行到期任务
            pending_tasks = AutoCleanupTask.query.filter(
                AutoCleanupTask.status == 0
            ).all()
            
            for task in pending_tasks:
                logging.info(f"[Scheduler-Probe] 发现待执行任务 ID:{task.id} | 设定执行时间:{task.execute_time} | 是否已到期:{task.execute_time <= now}")
                if task.execute_time > now:
                    continue
                
                try:
                    targets = json.loads(task.file_ids)
                    logging.info(f"[Scheduler-Probe] 准备清理 -> 类型: {task.drive_type}, 目标: {targets}")
                    
                    if task.drive_type == 'quark':
                        sys_config = SystemConfig.query.first()
                        if not sys_config or not sys_config.quark_cookie:
                            logging.error(f"[Cleanup] 夸克清理任务取消: 服务端未配置 Quark Cookie")
                            continue
                        quark = QuarkClient(cookies=sys_config.quark_cookie)
                        res = quark.delete_files(file_ids=targets)
                        task.status = 1 if res.get("status") == 200 else -1
                        logging.info(f"[Scheduler-Probe] 夸克清理返回: {res}")
                            
                    elif task.drive_type == 'baidu':
                        sys_config = SystemConfig.query.first()
                        if sys_config and sys_config.baidu_bduss:
                            transfer_client = BaiduTransfer(
                                bduss=sys_config.baidu_bduss,
                                bduss_bfess=sys_config.baidu_bduss_bfess,
                                stoken=sys_config.baidu_stoken,
                                save_path=sys_config.baidu_save_path or '/资源/'
                            )
                            res = transfer_client.delete_file(targets) 
                            task.status = 1 if res.get("status") else -1
                            logging.info(f"[Scheduler-Probe] 百度清理返回: {res}")
                    
                except Exception as e:
                    logging.info(f"[Scheduler-Error] Task {task.id} Execute Error: {e}")
                    task.status = -1

            # 2. 清理过期记录（滞留1小时即物理销毁，同时清理成功(1)与失败(-1)的任务，防脏数据堆积）
            cutoff_time = now - timedelta(hours=1)
            AutoCleanupTask.query.filter(
                AutoCleanupTask.status.in_([1, -1]),
                AutoCleanupTask.execute_time <= cutoff_time
            ).delete(synchronize_session=False)
            
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler-Error] Cleanup tasks collapsed: {e}")

def update_hot_search(app_instance):
    """原子化更新热搜数据，防脏读/断联"""
    with app_instance.app_context():
        try:
            res = requests.get('https://naspt.vip/api/hot-search?limit=12', timeout=10)
            data = res.json()
            if data.get('code') == 200 and isinstance(data.get('data'), list):
                items = data['data']
                # 开启事务隔离：抹除旧版，注入新版
                db.session.query(HotSearch).delete()
                for index, item in enumerate(items):
                    hs = HotSearch(
                        keyword=item.get('keyword', ''),
                        search_count=item.get('count', 0),
                        rank=index + 1
                    )
                    db.session.add(hs)
                db.session.commit()
                logging.info("[Scheduler] Hot search updated successfully.")
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler] Failed to update hot search: {e}")

def update_naspt_ranking(app_instance):
    """原子化抓取并更新 naspt 榜单数据"""
    with app_instance.app_context():
        try:
            res = requests.get('https://naspt.vip/api/ranking/list', timeout=10)
            data = res.json()
            if data.get('code') == 200 and isinstance(data.get('data'), list):
                categories = data['data']
                new_records = []
                
                for cat in categories:
                    cat_id = cat.get('id')
                    cat_name = cat.get('name', '')
                    if not cat_id:
                        continue
                        
                    detail_res = requests.get(f'https://naspt.vip/api/ranking/{cat_id}/items?page=1&size=10', timeout=10)
                    detail_data = detail_res.json()
                    if detail_data.get('code') == 200 and isinstance(detail_data.get('data'), list):
                        items = detail_data['data']
                        for index, item in enumerate(items):
                            new_records.append(NasptRanking(
                                category_id=cat_id,
                                category_name=cat_name,
                                rank=index + 1,
                                title=item.get('title', '')
                            ))
                            
                if new_records:
                    db.session.query(NasptRanking).delete()
                    db.session.bulk_save_objects(new_records)
                    db.session.commit()
                    logging.info("[Scheduler] Naspt Ranking updated successfully.")
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler] Failed to update Naspt Ranking: {e}")

def clean_expired_ips(app_instance):
    """物理抹除过时IP记录，维持单表高性能"""
    with app_instance.app_context():
        try:
            today = datetime.now().date()
            deleted_count = db.session.query(VisitorIPRecord).filter(
                VisitorIPRecord.visit_date < today
            ).delete()
            db.session.commit()
            logging.info(f"[Scheduler] 已清理过期访问IP记录: {deleted_count} 条")
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler-Error] Clean expired IPs error: {e}")

def clean_transfer_records(app_instance):
    """物理抹除历史转存流水记录"""
    with app_instance.app_context():
        try:
            db.session.query(TransferRecord).delete()
            db.session.commit()
            logging.info("[Scheduler] 每日0点清空转存流水记录成功")
        except Exception as e:
            db.session.rollback()
            logging.info(f"[Scheduler-Error] 清理转存流水异常: {e}")

# 初始化数据库
# with app.app_context():
#     db.create_all()
#     init_db_data()

# 挂载后台定时调度器
# 注意：Flask Werkzeug 的 debug 模式会启动两次进程。在生产环境 uWSGI/Gunicorn 下表现正常。
# 1. 声明独立线程池
executors = {
    'default': ThreadPoolExecutor(10),      # 默认池：保留给数据库清理、状态翻转等极速 CPU/DB 任务
    'heavy_io': ThreadPoolExecutor(5)       # 隔离池：专供长时间阻塞的爬虫与网盘接口轮询
}
scheduler = BackgroundScheduler(executors=executors)
# 2. 轻量级任务 -> 走 default 池（无需显式指定）
# scheduler.add_job(func=update_hot_search, trigger="cron", hour=6, minute=0, args=[app])
# scheduler.add_job(func=update_naspt_ranking, trigger="cron", hour=2, minute=0, args=[app])
# scheduler.add_job(func=process_cleanup_tasks, trigger="interval", hours=25, args=[app])
# scheduler.add_job(func=clean_expired_ips, trigger="cron", hour=0, minute=1, args=[app])
# # 3. 重度 I/O 任务 -> 强制指定 executor='heavy_io'
# scheduler.add_job(func=sync_external_drama, trigger="cron", hour=23, minute=0, args=[app], executor='heavy_io')
# scheduler.add_job(func=check_monitor_links, trigger="interval", minutes=1, args=[app], executor='heavy_io', coalesce=True, max_instances=1, misfire_grace_time=120)
# scheduler.start()
# 启动时强制阻断式同步一次数据
update_hot_search(app)
update_naspt_ranking(app)

# def init_drama_async(app_instance):
#     logging.info("[System] 派发异步短剧同步任务，不阻塞主线程...")
#     thread = threading.Thread(target=sync_external_drama, args=(app_instance,))
#     thread.daemon = True
#     thread.start()

# init_drama_async(app)



# --- 核心：全站模板变量注入 ---
@app.context_processor
def inject_global_vars():
    config_data = SiteConfig.query.first()
    sys_data = SystemConfig.query.first()
    if not config_data:
        config_data = {
            'site_name': '基德资源站', 
            'site_slogan': 'Loading...',
            'logo_path': '',
            'seo_title': ''
        }
    return dict(site_config=config_data, system_config=sys_data)

@app.route('/f064d5cfb599369bed4e6ac202dd7afa.txt')
def wechat_verify():
    return "77fc869557bc489557f301383b3cefa0996a9575"

@app.route('/')
def index():
    hot_searches = HotSearch.query.order_by(HotSearch.rank.asc()).all()
    # 新增：拉取后台监控与转存任务
    tasks = MonitorTask.query.order_by(MonitorTask.priority.desc(), MonitorTask.id.desc()).all()
    tv_list = [t for t in tasks if t.type == 'tv']
    movie_list = [t for t in tasks if t.type == 'movie']
    
    return render_template('index.html', hot_searches=hot_searches, tv_list=tv_list, movie_list=movie_list)

@app.route('/daily-update')
def daily_update():
    tasks = MonitorTask.query.order_by(MonitorTask.priority.desc(), MonitorTask.id.desc()).all()
    tv_list = [t for t in tasks if t.type == 'tv']
    movie_list = [t for t in tasks if t.type == 'movie']
    
    rankings = NasptRanking.query.order_by(NasptRanking.category_id.asc(), NasptRanking.rank.asc()).all()
    ranking_dict = {}
    for r in rankings:
        if r.category_name not in ranking_dict:
            ranking_dict[r.category_name] = []
        ranking_dict[r.category_name].append(r)
    
    return render_template('daily-update.html', tv_list=tv_list, movie_list=movie_list, ranking_dict=ranking_dict)

@app.route('/drama')
def drama():
    return render_template('drama.html')

@app.route('/hot')
def hot():
    return render_template('hot.html')

@app.route('/global-search')
def global_search():
    return render_template('global-search.html')

@app.route('/search')
def search():
    rankings = NasptRanking.query.order_by(NasptRanking.category_id.asc(), NasptRanking.rank.asc()).all()
    ranking_dict = {}
    for r in rankings:
        if r.category_name not in ranking_dict:
            ranking_dict[r.category_name] = []
        ranking_dict[r.category_name].append(r)
            
    return render_template('search.html', ranking_dict=ranking_dict)

# api
@app.route('/api/submit-requirement', methods=['POST'])
def api_submit_requirement():
    # 获取前端传来的 JSON 数据
    data = request.json
    if not data:
        return jsonify({'success': False, 'message': '未接收到数据'})
        
    content = data.get('content', '').strip()
    
    # 基础校验
    if not content:
        return jsonify({'success': False, 'message': '需求内容不能为空'})
    if len(content) > 1000: # 可选：防止恶意提交超长文本
        return jsonify({'success': False, 'message': '内容过长，请精简后再提交'})
        
    try:
        # 实例化一个新的 Requirement 记录
        new_requirement = Requirement(content=content)
        
        # 保存到数据库
        db.session.add(new_requirement)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '提交成功'})
        
    except Exception as e:
        db.session.rollback() # 发生异常时回滚
        # 实际部署时建议记录日志，这里为方便调试直接返回错误信息
        return jsonify({'success': False, 'message': f'数据库保存失败: {str(e)}'})

@app.route('/api/do_search', methods=['POST'])
def api_do_search():
    data = request.json or {}
    keyword = data.get('kw', '').strip()
    if not keyword:
        return jsonify({'code': 400, 'message': '搜索关键词不能为空'})
        
    # 提取合法参数透传
    kwargs = {}
    if 'cloud_types' in data: kwargs['cloud_types'] = data['cloud_types']
    if 'filter' in data: kwargs['filter'] = data['filter']
    if 'plugins' in data: kwargs['plugins'] = data['plugins']
    if 'src' in data: kwargs['src'] = data['src']
        
    try:
        print(f"[搜索请求] {keyword} | {kwargs}")
        result = PanSouClient.search(keyword, **kwargs)
        
        # --- 数据深层清洗管道 ---
        if result.get('code') == 200:
            # 探测实际负载结构 (兼容包裹在 data 节点或顶层的情况)
            payload = result.get('data', result)
            merged_data = payload.get('merged_by_type')
            
            if isinstance(merged_data, dict):
                valid_total = 0
                for cloud_type, items in merged_data.items():
                    # 基于列表推导式就地过滤空URL及含'#'名称的节点
                    cleaned_items = [
                        item for item in items 
                        if item.get('url') and '#' not in item.get('note', '')
                    ]
                    merged_data[cloud_type] = cleaned_items
                    valid_total += len(cleaned_items)
                
                # 重新计算并注入真实有效总量，阻断前端脏数据溢出
                payload['total'] = valid_total
                
        return jsonify(result)
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)})
    
@app.route('/api/external_dramas', methods=['POST'])
def api_external_dramas():
    data = request.json or {}
    kw = data.get('kw', '').strip()
    limit = data.get('limit', 100) 
    offset = data.get('offset', 0) # 新增：支持游标偏移量

    query = ExternalDrama.query
    if kw and kw.lower() != 'all':
        query = query.filter(ExternalDrama.title.ilike(f'%{kw}%'))
        
    # 按更新时间倒序并分页切片
    records = query.order_by(ExternalDrama.update_time.desc()).offset(offset).limit(limit).all()
    
    results = []
    for r in records:
        results.append({
            'id': r.id,
            'title': r.title,
            # 调用此前已有的 Fernet 加密套件进行加密
            'baidu_link': PanSouClient.encrypt_data(r.baidu_link) if r.baidu_link else None,
            'quark_link': PanSouClient.encrypt_data(r.quark_link) if r.quark_link else None,
            'update_time': r.update_time.strftime('%Y-%m-%d %H:%M:%S') if r.update_time else ''
        })
        
    return jsonify({'code': 200, 'data': results})

@app.route('/api/decrypt', methods=['POST'])
def api_decrypt():
    data = request.json or {}
    
    # === 新增：单IP限流网关 ===
    client_ip = get_real_ip()
    sys_config = SystemConfig.query.first()
    transfer_limit = sys_config.daily_transfer_limit if sys_config else 20
    
    today = datetime.now().date()
    today_count = TransferRecord.query.filter(
        TransferRecord.ip_address == client_ip,
        db.func.date(TransferRecord.created_at) == today
    ).count()
    
    if today_count >= transfer_limit:
        return jsonify({'code': 429, 'message': f'您今日转存次数已达上限({transfer_limit}次)，请明日再试。'})
    
    data = request.json or {}
    encrypted_url = data.get('url', '')
    encrypted_password = data.get('password', '')
    
    try:
        url = PanSouClient.decrypt_data(encrypted_url)
        password = PanSouClient.decrypt_data(encrypted_password)
        
        # 1. 尝试从 URL 中直接提取提取码 (优先级高于前端传参)
        url_pwd_match = re.search(r'[?&]pwd=([a-zA-Z0-9]{4,})', url)
        if url_pwd_match:
            password = url_pwd_match.group(1)
            
        logging.info(f"[解密成功] URL: {url} | Final Pass: {password}")

        # === 新增：解析 qoark.cn 301 跳转链接 ===
        if 'qoark' in url.lower():
            try:
                # 伪装 User-Agent，防止被拦截
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0'
                }
                # 发送请求，禁止自动跳转，直接抓取 301/302 响应头中的 location
                res = requests.get(url, headers=headers, allow_redirects=False, timeout=10)
                if res.status_code in [301, 302] and 'location' in res.headers:
                    url = res.headers['location']
                    logging.info(f"[qoark解析成功] 获取到真实URL: {url}")
                else:
                    # 兼容可能发生的直接 200 落地情况
                    res_redirect = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
                    url = res_redirect.url
                    logging.info(f"[qoark解析跳转] 获取到真实URL: {url}")
            except Exception as e:
                logging.error(f"[qoark解析异常]: {str(e)}")
                return jsonify({'code': 500, 'message': f'qoark短链解析失败: {str(e)}'})

        # 夸克转存与分享 (已移除 qoark 判断，经过上面处理后 url 已经是纯 quark)
        if 'quark' in url.lower():
            try:
                sys_config = SystemConfig.query.first()
                quark = QuarkClient(cookies=sys_config.quark_cookie) if sys_config and sys_config.quark_cookie else QuarkClient()
                target_dir_id = sys_config.quark_save_dir_id if sys_config and sys_config.quark_save_dir_id else "0"
                save_res = quark.shares.save_share_url(share_url=url, target_folder_id=target_dir_id)
                if save_res.get("status") != 200:
                    return jsonify({'code': 400, 'message': f"夸克转存受阻: {save_res.get('message', '未知错误')}"})
                
                new_file_ids = save_res['task_result']['data']['save_as']['save_as_top_fids']
                share_title = save_res['share_info']['files'][0]['file_name']
                
                # 2. 生成无密永久分享
                share_res = quark.create_share(file_ids=new_file_ids, title=share_title, expire_days=0)
                new_share_url = share_res.get('share_url')
                record_user_transfer(client_ip, share_title)
                if not new_share_url:
                    return jsonify({'code': 500, 'message': '夸克二次分享创建失败'})
                
                cleanup_task = AutoCleanupTask(
                    drive_type='quark',
                    file_ids=json.dumps(new_file_ids),
                    execute_time=datetime.now() + timedelta(minutes=10)
                )
                db.session.add(cleanup_task)
                db.session.commit()
                record_frontend_transfer()
                return jsonify({
                    'code': 200, 
                    'message': 'success', 
                    'data': {
                        'link': new_share_url, 
                        'pwd': '', 
                        'type': '夸克APP'
                    }
                })
                
            except ShareLinkError as e:
                return jsonify({'code': 400, 'message': f'夸克原链接已失效或错误: {str(e)}'})
            except APIError as e:
                return jsonify({'code': 500, 'message': f'夸克网盘接口报错: {str(e)}'})
            except Exception as e:
                logging.info(f"[夸克处理崩溃]: {str(e)}")
                return jsonify({'code': 500, 'message': f'服务端夸克处理异常'})
            
        # 百度转存与分享
        if 'baidu' in url.lower():
            # 2. 提取百度短链 ID (支持带参数或不带参数的链接)
            match = re.search(r'/s/([a-zA-Z0-9_-]+)', url)
            if not match:
                return jsonify({'code': 400, 'message': '无法解析百度短链特征'})
            surl = match.group(1)
            
            sys_config = SystemConfig.query.first()
            if not sys_config or not sys_config.baidu_bduss:
                return jsonify({'code': 500, 'message': '服务端尚未配置网盘凭证'})
            
            transfer_client = BaiduTransfer(
                bduss=sys_config.baidu_bduss,
                bduss_bfess=sys_config.baidu_bduss_bfess,
                stoken=sys_config.baidu_stoken,
                save_path=sys_config.baidu_save_path or '/资源/'
            )
            
            # 3. 检验资源存活
            health = transfer_client.check_resource_health(surl)
            if not health.get("status") and health.get("msg") != "提取码错误":
                print("校验资源存活失败")
                return jsonify({'code': 400, 'message': f"资源已失效: {health.get('msg')}"})
            
            # 4. 验证提取码 (若 URL 提取或参数传入了密码则验证)
            if password:
                verify = transfer_client.verify_pwd(surl, password)
                print(f"[密码验证] {verify}")
                if not verify.get("status"):
                    return jsonify({'code': 400, 'message': '原始提取码错误'})
            
            # 5. 转存入库
            trans = transfer_client.transfer(surl, password or "")
            if not trans.get("status"):
                logging.info(f"[转存失败]: {trans}")
                return jsonify({'code': 400, 'message': f"转存受阻: {trans.get('data', '未知错误')}"})
            
            # 6. 提取挂载点并执行物理上传拦截
            fid = trans['data']['to_fs_id']
            file_path = trans['data']['to']
            
            try:
                from baidupcs_py.baidupcs import BaiduPCSApi
                local_dir = os.path.abspath(os.path.join(app.root_path, 'static', 'drainage'))
                if os.path.exists(local_dir):
                    api = BaiduPCSApi(bduss=sys_config.baidu_bduss, stoken=sys_config.baidu_stoken)
                    for root, dirs, files in os.walk(local_dir):
                        for file in files:
                            local_path = os.path.join(root, file)
                            relative_path = os.path.relpath(local_path, local_dir)
                            remote_path = f"{file_path}/{relative_path}".replace("\\", "/")
                            with open(local_path, "rb") as f:
                                api.upload_file(f, remote_path)
            except Exception as e:
                logging.info(f"[Drainage Upload Error] {e}")
            
            # 7. 创建分享链接（由于打包的是 fid，此时引流文件已被包含在内）
            share_pwd = sys_config.baidu_extract_code or 'yyds'
            baidu_title = file_path.split('/')[-1] if '/' in file_path else file_path
            record_user_transfer(client_ip, baidu_title)
            share = transfer_client.share_file(fid_list=[fid], pwd=share_pwd, period=0)
            if not share.get("status"):
                return jsonify({'code': 400, 'message': '二次分享创建失败'})
            
            file_paths = [trans['data']['to']]
            cleanup_task = AutoCleanupTask(
                drive_type='baidu',
                file_ids=json.dumps(file_paths),
                execute_time=datetime.now() + timedelta(minutes=10)
            )
            db.session.add(cleanup_task)
            db.session.commit()
            record_frontend_transfer()
            return jsonify({
                'code': 200, 
                'message': 'success', 
                'data': {
                    'link': share['data']['link'], 
                    'pwd': share_pwd, 
                    'type': '百度网盘'
                }
            })
            
        return jsonify({'code': 400, 'message': '不支持的链接格式'})
            
    except Exception as e:
        logging.info(f"[解密异常]: {str(e)}")
        return jsonify({'code': 500, 'message': f'服务端解密失败: {str(e)}'})
            
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)})

@app.route('/api/search_local', methods=['POST'])
def api_search_local():
    data = request.json or {}
    kw = data.get('kw', '').strip()
    if not kw:
        return jsonify({'code': 200, 'data': []})
        
    try:
        # 模糊匹配 MonitorTask 中的站长维护资源
        tasks = MonitorTask.query.filter(MonitorTask.name.ilike(f'%{kw}%')).all()
        results = []
        for t in tasks:
            # 必须至少有一个有效结果才下发
            if t.baidu_current_link or t.quark_current_link:
                results.append({
                    'id': t.id,
                    'name': t.name,
                    'baidu_current_link': t.baidu_current_link,
                    'baidu_pwd': t.baidu_pwd,
                    'quark_current_link': t.quark_current_link,
                    'quark_pwd': t.quark_pwd
                })
        return jsonify({'code': 200, 'data': results})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)})


from flask import request, jsonify

@app.route('/api/media', methods=['GET'])
def get_media_list():
    try:
        # 接收过滤参数
        media_type = request.args.get('type')
        category = request.args.get('category')
        region = request.args.get('region')
        year = request.args.get('year', type=int)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)

        # 基础查询对象
        query = Media.query

        # 动态组装查询条件
        if media_type:
            query = query.filter(Media.media_type == media_type)
        if year:
            query = query.filter(Media.release_year == year)
        if category:
            query = query.filter(Media.categories.any(Category.name == category))
        if region:
            query = query.filter(Media.regions.any(Region.name == region))

        # 按评分和最新倒序
        pagination = query.order_by(Media.score.desc(), Media.release_year.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        return jsonify({
            'code': 200,
            'data': [m.to_dict() for m in pagination.items],
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)}), 500


@app.route('/test')
def test():
    base_url = 'https://www.seedhub.cc/categories/1/movies/'
    return jsonify({'code': 200, 'data': 'Hello World!'})

if __name__ == '__main__':
    app.run(host='0.0.0.0',port=5000)
