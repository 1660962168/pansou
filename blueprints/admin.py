import logging
import os
import time
import shutil
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash, g, current_app,make_response, abort
from werkzeug.utils import secure_filename
from models import Admin, SiteConfig, SystemConfig, Requirement,SiteStat,MonitorTask,TransferRecord, IpLocationCache
from exts import db
from functools import wraps
import re
import sys
import uuid
import threading
import psutil
from datetime import datetime, date, timedelta
from quark_client import QuarkClient
from quark_client.exceptions import APIError, ShareLinkError
from baidupcs_py.baidupcs import BaiduPCSApi
import time
from BaiduTransfer import ProxyManager

def _smart_fetch_layer(quark_client, share_id, token, pdir_fid="0"):
    try:
        res = quark_client.shares.get_share_info(share_id, token, pdir_fid=pdir_fid)
    except TypeError:
        res = quark_client.shares.get_share_info(share_id, token)
    if not isinstance(res, dict) or 'data' not in res: return []
    items = res['data'].get('list', [])
    folders, files = [], []
    for item in items:
        if item.get('is_dir', False) == True or item.get('file_type') in [0, 'dir']: folders.append(item)
        else: files.append(item)
    if len(folders) == 1 and len(files) == 0 and folders[0].get('fid'):
        return _smart_fetch_layer(quark_client, share_id, token, folders[0].get('fid'))
    return files + folders

bp = Blueprint('admin', __name__, url_prefix='/admin')

# --- 夸克异步登录状态机 ---
LOGIN_TASKS = {}

class StreamInterceptor:
    def __init__(self, original_stdout, task_id, event):
        self.original_stdout = original_stdout
        self.task_id = task_id
        self.event = event
        self.buffer = ""

    def write(self, text):
        self.original_stdout.write(text)
        self.original_stdout.flush()
        if not self.event.is_set():
            self.buffer += text
            match = re.search(r'https://su\.quark\.cn/\S+', self.buffer)
            if match:
                LOGIN_TASKS[self.task_id]['url'] = match.group(0)
                self.event.set()

    def flush(self):
        self.original_stdout.flush()

def _quark_login_worker(task_id, event):
    original_stdout = sys.stdout
    interceptor = StreamInterceptor(original_stdout, task_id, event)
    sys.stdout = interceptor
    
    try:
        quark = QuarkClient()
        quark.logout()
        cookie_result = quark.login()
        
        if not event.is_set():
            LOGIN_TASKS[task_id]['url'] = 'ALREADY_LOGGED_IN'
            event.set()
            
        LOGIN_TASKS[task_id]['status'] = 'COMPLETED'
        LOGIN_TASKS[task_id]['result'] = cookie_result
    except Exception as e:
        LOGIN_TASKS[task_id]['status'] = 'ERROR'
        LOGIN_TASKS[task_id]['result'] = str(e)
        if not event.is_set():
            event.set()
    finally:
        sys.stdout = original_stdout
# ------------------------

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

def save_upload_file(file_obj, sub_folder='misc'):
    """
    保存文件并返回相对路径 (用于存入数据库)
    """
    if file_obj and allowed_file(file_obj.filename):
        # 生成安全的文件名 (加时间戳防止重名)
        ext = file_obj.filename.rsplit('.', 1)[1].lower()
        filename = secure_filename(file_obj.filename.split('.')[0])
        new_filename = f"{filename}_{int(time.time())}.{ext}"
        
        # 确保目录存在
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], sub_folder)
        if not os.path.exists(upload_path):
            os.makedirs(upload_path)
            
        # 保存文件
        file_obj.save(os.path.join(upload_path, new_filename))
        
        # 返回给前端访问的相对路径
        return f"/static/uploads/{sub_folder}/{new_filename}"
    return None

def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('admin_id'):
            return redirect(url_for('admin.login'))
        return func(*args, **kwargs)
    return wrapper

@bp.before_request
def load_logged_in_user():
    user_id = session.get('admin_id')
    if user_id is None:
        g.admin = None
    else:
        g.admin = Admin.query.get(user_id)



# 访问路径: /admin/
@bp.route('/')
@login_required
def index():
    today = datetime.now().date()
    
    # 1. 总资源数 (按物理网盘链接算)
    baidu_count = MonitorTask.query.filter(MonitorTask.baidu_source_link != None, MonitorTask.baidu_source_link != '').count()
    quark_count = MonitorTask.query.filter(MonitorTask.quark_source_link != None, MonitorTask.quark_source_link != '').count()
    total_resources = baidu_count + quark_count
    
    # 2. 今日转存
    today_stat = SiteStat.query.filter_by(date=today).first()
    today_transfers = today_stat.frontend_transfers if today_stat else 0
    
    # 3. 待处理需求
    pending_requirements = Requirement.query.filter_by(is_read=False).count()
    
    # 4. 系统负载
    cpu_usage = psutil.cpu_percent(interval=0.1)
    
    # 5. 过去7天浏览量
    seven_days_ago = today - timedelta(days=6)
    weekly_stats = SiteStat.query.filter(SiteStat.date >= seven_days_ago).all()
    stat_dict = {s.date: s.page_views for s in weekly_stats}
    week_labels = [(seven_days_ago + timedelta(days=i)).strftime('%m-%d') for i in range(7)]
    week_data = [stat_dict.get(seven_days_ago + timedelta(days=i), 0) for i in range(7)]
    
    # 6. 每月份浏览量
    current_year_start = date(today.year, 1, 1)
    monthly_stats = SiteStat.query.filter(SiteStat.date >= current_year_start).all()
    monthly_dict = {i: 0 for i in range(1, 13)}
    for s in monthly_stats:
        monthly_dict[s.date.month] += s.page_views
    month_data = [monthly_dict[m] for m in range(1, 13)]
    
    stats = {
        'total_resources': total_resources,
        'today_transfers': today_transfers,
        'pending_requirements': pending_requirements,
        'cpu_usage': cpu_usage,
        'week_labels': week_labels,
        'week_data': week_data,
        'month_data': month_data
    }
    
    return render_template('admin/index.html', stats=stats)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    # 1. 如果 Session 有效，直接进后台 (实现了"30天内有效")
    if session.get('admin_id'):
        return redirect(url_for('admin.index'))

    if request.method == 'GET':
        # 从 Cookie 中尝试获取上次登录的用户名
        last_username = request.cookies.get('remember_user', '')
        return render_template('admin/login.html', username=last_username)

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember') # 这里的 remember 是 checkbox 的值

        admin = Admin.query.filter_by(username=username).first()

        if admin and admin.check_password(password):
            session.clear()
            session['admin_id'] = admin.id
            
            # --- 核心逻辑：30天免登录 ---
            if remember:
                # 勾选了：Session 有效期遵循 config 中的 PERMANENT_SESSION_LIFETIME (30天)
                session.permanent = True
            else:
                # 没勾选：浏览器关闭即失效
                session.permanent = False
            
            # --- 构建响应 ---
            response = make_response(redirect(url_for('admin.index')))
            
            # --- 额外功能：记住用户名 ---
            # 无论是否勾选"记住我"，都把用户名存个 Cookie，方便下次登录自动填账号
            # max_age 设置为 30 天 (单位秒)
            if remember:
                 response.set_cookie('remember_user', username, max_age=30*24*60*60)
            else:
                 # 如果没勾选记住我，可以选择删除这个 cookie，或者仅设为会话级
                 response.delete_cookie('remember_user')

            flash('欢迎回来，管理员！', 'success')
            return response
        
        flash('用户名或密码错误。', 'error')
        return redirect(url_for('admin.login'))

@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin.login'))

@bp.route('/reset-password', methods=['POST'])
def reset_password():
    """
    通过验证 config.py 中的 SECRET_KEY 来重置密码
    """
    username = request.form.get('username')
    input_key = request.form.get('secret_key')
    new_password = request.form.get('new_password')
    
    if input_key != current_app.config['SECRET_KEY']:
        flash('Secret Key 错误，无法重置密码！', 'error')
        return redirect(url_for('admin.login'))
    
    # 2. 查找并更新用户
    admin = Admin.query.filter_by(username=username).first()
    if not admin:
        flash(f'用户 {username} 不存在！', 'error')
        return redirect(url_for('admin.login'))
        
    # 3. 执行重置
    admin.password = new_password # 触发 model 的 setter 进行加密
    db.session.commit()
    
    flash('密码重置成功，请使用新密码登录。', 'success')
    return redirect(url_for('admin.login'))


@bp.route('/batch-transfer')
@login_required
def batch_transfer():
    return render_template('admin/batch_transfer.html')



from models import MonitorTask
from BaiduTransfer import BaiduTransfer
import json

@bp.route('/daily-updates')
@login_required
def daily_updates():
    return render_template('admin/daily_updates.html')

@bp.route('/api/monitor/list', methods=['GET'])
@login_required
def monitor_list():
    tasks = MonitorTask.query.order_by(MonitorTask.priority.desc(), MonitorTask.id.desc()).all()
    return jsonify({'code': 200, 'data': [t.to_dict() for t in tasks]})

def _parse_local_link(link):
    if not link: return None, None
    pwd = ""
    # 提取密码或提取码
    pwd_match = re.search(r'(?:提取码|密码|pwd)[:：=\s]*([a-zA-Z0-9]{4,})', link)
    if pwd_match: pwd = pwd_match.group(1)
    # 剥离参数保留纯净链接
    clean_link = re.sub(r'[?&](?:pwd|密码|提取码)=.*', '', link).strip().split(' ')[0]
    return clean_link, pwd

@bp.route('/api/monitor/add', methods=['POST'])
@login_required
def monitor_add():
    data = request.json
    is_local = bool(data.get('is_local', False))
    bl_raw = data.get('baidu_link', '').strip()
    ql_raw = data.get('quark_link', '').strip()
    
    bl_clean, bl_pwd = _parse_local_link(bl_raw) if bl_raw else (None, None)
    ql_clean, ql_pwd = _parse_local_link(ql_raw) if ql_raw else (None, None)
    
    new_task = MonitorTask(
        name=data.get('name', '').strip(),
        type=data.get('type', 'tv'),
        priority=int(data.get('priority', 50)),
        is_local=is_local,
        is_monitoring=False # 初始统一为False，本地资源恒为False
    )
    
    if is_local:
        new_task.transfer_count = 0
        if bl_clean:
            new_task.baidu_source_link = bl_clean
            new_task.baidu_current_link = bl_clean
            new_task.baidu_pwd = bl_pwd
            new_task.baidu_status = 'normal'
        if ql_clean:
            new_task.quark_source_link = ql_clean
            new_task.quark_current_link = ql_clean
            new_task.quark_pwd = ql_pwd
            new_task.quark_status = 'normal'
    else:
        new_task.baidu_source_link = bl_raw or None
        new_task.quark_source_link = ql_raw or None

    db.session.add(new_task)
    db.session.commit()
    return jsonify({'code': 200, 'msg': '添加成功'})

@bp.route('/api/monitor/edit', methods=['POST'])
@login_required
def monitor_edit():
    data = request.json
    task = MonitorTask.query.get(data.get('id'))
    if not task: return jsonify({'code': 404, 'msg': '任务不存在'})
    
    task.name = data.get('name', task.name)
    task.type = data.get('type', task.type)
    task.priority = int(data.get('priority', task.priority))
    
    bl_raw = data.get('baidu_link', '').strip()
    ql_raw = data.get('quark_link', '').strip()
    
    if task.is_local:
        if bl_raw:
            bl_clean, bl_pwd = _parse_local_link(bl_raw)
            task.baidu_source_link = bl_clean
            task.baidu_current_link = bl_clean
            task.baidu_pwd = bl_pwd
            task.baidu_status = 'normal'
        if ql_raw:
            ql_clean, ql_pwd = _parse_local_link(ql_raw)
            task.quark_source_link = ql_clean
            task.quark_current_link = ql_clean
            task.quark_pwd = ql_pwd
            task.quark_status = 'normal'
    else:
        if bl_raw: task.baidu_source_link = bl_raw
        if ql_raw: task.quark_source_link = ql_raw
    
    db.session.commit()
    return jsonify({'code': 200, 'msg': '修改成功'})

@bp.route('/api/monitor/toggle', methods=['POST'])
@login_required
def monitor_toggle():
    data = request.json
    task = MonitorTask.query.get(data.get('id'))
    if task:
        task.is_monitoring = bool(data.get('is_monitoring'))
        db.session.commit()
    return jsonify({'code': 200, 'msg': '状态已更新'})

def _delete_cloud_file(drive_type, target):
    try:
        if not target: return
        if drive_type == 'quark':
            sys_config = SystemConfig.query.first()
            quark = QuarkClient(cookies=sys_config.quark_cookie) if sys_config and sys_config.quark_cookie else QuarkClient()
            quark.delete_files(file_ids=json.loads(target))
        elif drive_type == 'baidu':
            sys_config = SystemConfig.query.first()
            if sys_config and sys_config.baidu_bduss:
                t = BaiduTransfer(
                    bduss=sys_config.baidu_bduss, 
                    bduss_bfess=sys_config.baidu_bduss_bfess, 
                    stoken=sys_config.baidu_stoken,
                    save_path=sys_config.baidu_save_path or '/资源/'
                )
                t.delete_file(json.loads(target))
    except Exception as e:
        logging.info(f"[Force Delete Error] {drive_type} : {e}")

@bp.route('/api/monitor/delete', methods=['POST'])
@login_required
def monitor_delete():
    data = request.json
    task = MonitorTask.query.get(data.get('id'))
    if not task: return jsonify({'code': 404, 'msg': '记录不存在'})
    
    delete_cloud = bool(data.get('delete_cloud', False))
    
    if delete_cloud:
        # 强行删除网盘关联文件，无视错误阻断
        _delete_cloud_file('baidu', task.baidu_file_path)
        _delete_cloud_file('quark', task.quark_file_id)
    
    db.session.delete(task)
    db.session.commit()
    
    msg = '记录与网盘文件均已删除' if delete_cloud else '仅删除了数据库记录'
    return jsonify({'code': 200, 'msg': msg})

@bp.route('/api/monitor/delete_sub', methods=['POST'])
@login_required
def monitor_delete_sub():
    data = request.json
    task = MonitorTask.query.get(data.get('id'))
    dtype = data.get('type')
    delete_cloud = bool(data.get('delete_cloud', False))
    if not task: return jsonify({'code': 404})
    
    if dtype == 'baidu':
        if delete_cloud:
            _delete_cloud_file('baidu', task.baidu_file_path)
        task.baidu_source_link = None
        task.baidu_current_link = None
        task.baidu_pwd = None
        task.baidu_file_path = None
        task.baidu_status = 'normal'
    elif dtype == 'quark':
        if delete_cloud:
            _delete_cloud_file('quark', task.quark_file_id)
        task.quark_source_link = None
        task.quark_current_link = None
        task.quark_pwd = None
        task.quark_file_id = None
        task.quark_status = 'normal'
        
    db.session.commit()
    msg = '单端记录与网盘文件均已删除' if delete_cloud else '仅删除了单端记录'
    return jsonify({'code': 200, 'msg': msg})

def _execute_quark_transfer(task):
    """原子化封装：执行夸克网盘单端删除、转存、分享及状态更新"""
    if not task.quark_source_link: return "无夸克源链接"
    try:
        sys_config = SystemConfig.query.first()
        quark = QuarkClient(cookies=sys_config.quark_cookie) if sys_config and sys_config.quark_cookie else QuarkClient()
        share_id, parsed_pwd = quark.shares.parse_share_url(task.quark_source_link)
        token = quark.shares.get_share_token(share_id, parsed_pwd)
        task.quark_file_count = len(_smart_fetch_layer(quark, share_id, token))
        url = task.quark_source_link
        
        target_dir_id = sys_config.quark_daily_dir_id or sys_config.quark_save_dir_id or "0"
        save_res = quark.shares.save_share_url(share_url=url, target_folder_id=target_dir_id)
        if save_res.get("status") == 200:
            new_fids = save_res['task_result']['data']['save_as']['save_as_top_fids']
            share_title = save_res['share_info']['files'][0]['file_name']
            share_res = quark.create_share(file_ids=new_fids, title=share_title, expire_days=0)
            
            # 若旧文件存在则清除
            _delete_cloud_file('quark', task.quark_file_id)
            
            task.quark_file_id = json.dumps(new_fids)
            task.quark_current_link = share_res.get('share_url')
            task.quark_pwd = ''
            task.quark_status = 'normal'
            return "夸克执行成功"
        else:
            task.quark_status = 'invalid'
            return f"夸克转存失败: {save_res.get('message')}"
    except Exception as e:
        task.quark_status = 'invalid'
        return f"夸克异常: {e}"

def _upload_drainage_to_baidu(bduss, stoken, remote_base_dir):
    """物理混入引流文件至目标网盘目录"""
    try:
        from baidupcs_py.baidupcs import BaiduPCSApi
        local_dir = os.path.abspath(os.path.join(current_app.root_path, 'static', 'drainage'))
        if not os.path.exists(local_dir):
            return
        api = BaiduPCSApi(bduss=bduss, stoken=stoken)
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, local_dir)
                remote_path = f"{remote_base_dir}/{relative_path}".replace("\\", "/")
                with open(local_path, "rb") as f:
                    api.upload_file(f, remote_path)
    except Exception as e:
        logging.info(f"[Drainage Upload Error] {e}")

def _execute_baidu_transfer(task):
    """原子化封装：执行百度网盘单端删除、转存、分享及状态更新"""
    if not task.baidu_source_link: return "无百度源链接"
    try:
        sys_config = SystemConfig.query.first()
        if sys_config and sys_config.baidu_bduss:
            fallback_path = sys_config.baidu_daily_path or sys_config.baidu_save_path or '/资源/'
            transfer_client = BaiduTransfer(
                bduss=sys_config.baidu_bduss, bduss_bfess=sys_config.baidu_bduss_bfess,
                stoken=sys_config.baidu_stoken, save_path=fallback_path
            )
            url = task.baidu_source_link
            match = re.search(r'/s/([a-zA-Z0-9_-]+)', url)
            pwd_match = re.search(r'[?&]pwd=([a-zA-Z0-9]{4,})', url)
            pwd = pwd_match.group(1) if pwd_match else ""
            if match:
                surl = match.group(1)
                task.baidu_file_count = transfer_client.count_share_files(surl, pwd)
            
            if match:
                surl = match.group(1)
                trans = transfer_client.transfer(surl, pwd)
                if trans.get("status"):
                    fid = trans['data']['to_fs_id']
                    file_path = trans['data']['to']
                    
                    # 拦截：转存完毕后，将引流文件混入同一父路径
                    _upload_drainage_to_baidu(sys_config.baidu_bduss, sys_config.baidu_stoken, file_path)
                    
                    share_pwd = sys_config.baidu_extract_code or 'yyds'
                    share = transfer_client.share_file(fid_list=[fid], pwd=share_pwd, period=0)
                    
                    if share.get("status"):
                        _delete_cloud_file('baidu', task.baidu_file_path)
                        task.baidu_file_path = json.dumps([file_path])
                        task.baidu_current_link = share['data']['link']
                        task.baidu_pwd = share_pwd
                        task.baidu_status = 'normal'
                        return "百度执行成功"
                    else:
                        return "百度分享创建失败"
                else:
                    task.baidu_status = 'invalid'
                    return f"百度转存受阻: {trans.get('data')}"
            return "百度源链接格式错误"
        return "百度网盘未配置"
    except Exception as e:
        task.baidu_status = 'invalid'
        return f"百度异常: {e}"

@bp.route('/api/monitor/run', methods=['POST'])
@login_required
def monitor_run():
    """原有一键双端执行"""
    task = MonitorTask.query.get(request.json.get('id'))
    if not task: return jsonify({'code': 404, 'msg': '记录不存在'})
    
    msg_list = []
    if task.quark_source_link: msg_list.append(_execute_quark_transfer(task))
    if task.baidu_source_link: msg_list.append(_execute_baidu_transfer(task))
        
    task.transfer_count += 1
    db.session.commit()
    return jsonify({'code': 200, 'msg': " | ".join(msg_list) or "无有效链接"})

@bp.route('/api/monitor/run_sub', methods=['POST'])
@login_required
def monitor_run_sub():
    """新增单端独立更新执行"""
    data = request.json
    task = MonitorTask.query.get(data.get('id'))
    dtype = data.get('type')
    if not task: return jsonify({'code': 404, 'msg': '记录不存在'})
    
    msg = ""
    if dtype == 'quark': msg = _execute_quark_transfer(task)
    elif dtype == 'baidu': msg = _execute_baidu_transfer(task)
        
    task.transfer_count += 1
    db.session.commit()
    return jsonify({'code': 200, 'msg': msg})

@bp.route('/api/monitor/batch_transfer_single', methods=['POST'])
@login_required
def monitor_batch_transfer_single():
    data = request.json
    line = data.get('text', '').strip()
    media_type = data.get('type', 'tv')
    is_local = bool(data.get('is_local', False))
    
    if not line:
        return jsonify({'code': 400, 'msg': '空行或无效数据'})
        
    drive_type = None
    source_link = None
    pwd = ""
    custom_name = ""
    
    # 1. 链接与提取码解析
    if 'quark.cn' in line:
        drive_type = 'quark'
        url_match = re.search(r'(https?://[^\s]+quark\.cn[^\s]+)', line)
        if not url_match: return jsonify({'code': 400, 'msg': '未能提取夸克链接'})
        source_link = url_match.group(1)
        pwd_match = re.search(r'(?:提取码|密码|pwd)[:：=\s]*([a-zA-Z0-9]{4,})', line)
        if pwd_match: pwd = pwd_match.group(1)
    elif 'baidu.com' in line:
        drive_type = 'baidu'
        url_match = re.search(r'(https?://[^\s]+baidu\.com[^\s]+)', line)
        if not url_match: return jsonify({'code': 400, 'msg': '未能提取百度链接'})
        source_link = url_match.group(1)
        pwd_match = re.search(r'(?:提取码|密码|pwd)[:：=\s]*([a-zA-Z0-9]{4,})', line)
        if pwd_match: pwd = pwd_match.group(1)
    else:
        return jsonify({'code': 400, 'msg': '不支持的链接格式（仅限百度/夸克）'})

    # 提取链接前缀作为自定义资源名
    url_start_idx = line.find(source_link)
    if url_start_idx > 0:
        raw_prefix = line[:url_start_idx].strip()
        # 过滤掉末尾可能存在的无意义连接符 (如 "名称 - https://...")
        custom_name = re.sub(r'[-:：\s]+$', '', raw_prefix)

    # 统一拼装密码至链接参数，便于底层调用
    if pwd and 'pwd=' not in source_link:
        sep = '&' if '?' in source_link else '?'
        source_link = f"{source_link}{sep}pwd={pwd}"

    # 2. 数据库级查重拦截
    core_id_match = re.search(r'/s/([a-zA-Z0-9_-]+)', source_link)
    if not core_id_match:
        return jsonify({'code': 400, 'msg': '无法解析链接的核心分享ID'})
    core_id = core_id_match.group(1)

    if drive_type == 'quark':
        exist = MonitorTask.query.filter(MonitorTask.quark_source_link.like(f"%{core_id}%")).first()
        if exist: return jsonify({'code': 400, 'msg': f'拦截：已存在于节点【{exist.name}】中'})
    else:
        exist = MonitorTask.query.filter(MonitorTask.baidu_source_link.like(f"%{core_id}%")).first()
        if exist: return jsonify({'code': 400, 'msg': f'拦截：已存在于节点【{exist.name}】中'})

    # 3. 提取文件名与执行转存
    file_name = "未命名资源"
    current_link = ""
    share_pwd = ""
    cloud_file_path = ""
    is_transfer_failed = False
    fail_reason = ""
    file_count_base = 0
    
    if is_local:
        clean_link = re.sub(r'[?&](?:pwd|密码|提取码)=.*', '', source_link).strip()
        source_link = clean_link
        current_link = clean_link
        share_pwd = pwd

    try:
        if not is_local and drive_type == 'quark':
            sys_config = SystemConfig.query.first()
            quark = QuarkClient(cookies=sys_config.quark_cookie) if sys_config and sys_config.quark_cookie else QuarkClient()
            
            # 解析源文件名
            share_id, parsed_pwd = quark.shares.parse_share_url(source_link)
            token = quark.shares.get_share_token(share_id, parsed_pwd)
            share_info = quark.shares.get_share_info(share_id, token)
            if isinstance(share_info, dict) and 'data' in share_info and share_info['data'].get('list'):
                file_name = share_info['data']['list'][0]['file_name']
            
            # 执行转存分享
            target_dir_id = sys_config.quark_daily_dir_id or sys_config.quark_save_dir_id or "0"
            save_res = quark.shares.save_share_url(share_url=source_link, target_folder_id=target_dir_id)
            if save_res.get("status") == 200:
                new_fids = save_res['task_result']['data']['save_as']['save_as_top_fids']
                share_res = quark.create_share(file_ids=new_fids, title=file_name, expire_days=0)
                current_link = share_res.get('share_url')
                cloud_file_path = json.dumps(new_fids)
            else:
                is_transfer_failed = True
                fail_reason = save_res.get('message', '未知错误')
                
        elif not is_local and drive_type == 'baidu':
            sys_config = SystemConfig.query.first()
            if not sys_config or not sys_config.baidu_bduss:
                return jsonify({'code': 500, 'msg': '系统未配置百度网盘凭证'})
            
            fallback_path = sys_config.baidu_daily_path or sys_config.baidu_save_path or '/资源/'
            transfer_client = BaiduTransfer(
                bduss=sys_config.baidu_bduss, bduss_bfess=sys_config.baidu_bduss_bfess,
                stoken=sys_config.baidu_stoken, save_path=fallback_path
            )
            
            trans = transfer_client.transfer(core_id, pwd)
            
            # 容错：转存受阻则等待1秒后强制重试一次
            if not trans.get("status"):
                time.sleep(1)
                trans = transfer_client.transfer(core_id, pwd)
                
            if trans.get("status"):
                fid = trans['data']['to_fs_id']
                file_path = trans['data']['to']
                
                # 拦截：转存完毕后，将引流文件混入同一父路径
                _upload_drainage_to_baidu(sys_config.baidu_bduss, sys_config.baidu_stoken, file_path)
                
                # 解析源文件名 (基于路径末尾)
                file_name = file_path.split('/')[-1] if '/' in file_path else file_path
                share_pwd = sys_config.baidu_extract_code or 'yyds'
                share = transfer_client.share_file(fid_list=[fid], pwd=share_pwd, period=0)
                if share.get("status"):
                    current_link = share['data']['link']
                    cloud_file_path = json.dumps([file_path])
                else:
                    is_transfer_failed = True
                    fail_reason = '百度分享创建失败'
            else:
                is_transfer_failed = True
                fail_reason = trans.get('data', '未知错误')
                
    except Exception as e:
        return jsonify({'code': 500, 'msg': f"执行异常: {str(e)}"})

    # === 初始化数量基准探测 ===
    if not is_local:
        try:
            if drive_type == 'quark' and 'quark' in locals():
                file_count_base = len(_smart_fetch_layer(quark, share_id, token))
            elif drive_type == 'baidu' and 'transfer_client' in locals() and core_id:
                file_count_base = transfer_client.count_share_files(core_id, pwd)
        except Exception as e:
            logging.info(f"[Init Count Error] {e}")

    # 4. 落地入库 (无论成功还是失败，均落地保存)
    final_task_name = custom_name if custom_name else file_name
    
    # 引入查重合并机制：如果同名记录已存在，则将当前网盘链路追加进去
    task_record = MonitorTask.query.filter_by(name=final_task_name).first()
    
    if not task_record:
        # 新建记录
        task_record = MonitorTask(
            name=final_task_name,
            type=media_type,
            priority=100,
            is_monitoring=False,
            transfer_count=0,
            is_local=is_local
        )
        db.session.add(task_record)
        
    # 叠加转存成功次数（本地资源忽略）
    if not is_transfer_failed and not is_local:
        task_record.transfer_count += 1
    
    # 将当前解析与转存的链路数据注入该记录
    if drive_type == 'quark':
        task_record.quark_source_link = source_link
        task_record.quark_current_link = current_link
        task_record.quark_pwd = share_pwd
        task_record.quark_status = 'invalid' if is_transfer_failed else 'normal'
        task_record.quark_file_id = cloud_file_path
        if not is_transfer_failed: task_record.quark_file_count = file_count_base
    else:
        task_record.baidu_source_link = source_link
        task_record.baidu_current_link = current_link
        task_record.baidu_pwd = share_pwd
        task_record.baidu_status = 'invalid' if is_transfer_failed else 'normal'
        task_record.baidu_file_path = cloud_file_path
        if not is_transfer_failed: task_record.baidu_file_count = file_count_base

    db.session.commit()
    
    # 5. 返回结果状态
    if is_transfer_failed:
        # Code 返回非 200 ，触发前端批量列表显示报错，但不阻碍已入库事实
        return jsonify({'code': 500, 'msg': f"转存失败，已将链接保存至每日更新，请手动检查更新。 失败原因：{fail_reason}"})
    
    return jsonify({'code': 200, 'msg': '成功', 'name': final_task_name})

@bp.route('/requirements')
@login_required
def requirements():
    demands_query = Requirement.query.order_by(Requirement.created_at.desc()).all()
    demands_list = [demand.to_dict() for demand in demands_query]
    return render_template('admin/requirements.html', demands=demands_list)

@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    # GET 请求：渲染页面
    if request.method == 'GET':
        return render_template('admin/change_password.html')
    
    # POST 请求：处理修改密码请求
    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({'message': '未接收到数据'}), 400
            
        old_password = data.get('old_password')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')
        
        if not old_password or not new_password or not confirm_password:
            return jsonify({'message': '请填写完整的密码信息'}), 400
            
        if new_password != confirm_password:
            return jsonify({'message': '两次输入的新密码不一致'}), 400
            
        # 获取当前登录的管理员对象
        admin = Admin.query.get(session.get('admin_id'))
        if not admin:
            return jsonify({'message': '当前用户不存在或登录已失效'}), 401
            
        # 验证旧密码是否正确 (调用 models.py 中 Admin 类的验证方法)
        if not admin.check_password(old_password):
            return jsonify({'message': '当前旧密码输入错误'}), 400
            
        try:
            # 更新为新密码
            admin.password = new_password
            db.session.commit()
            
            # 密码修改成功后，可以主动清除 session 让用户重新登录
            session.clear() 
            
            return jsonify({'message': '密码修改成功，请重新登录'})
            
        except Exception as e:
            db.session.rollback()
            return jsonify({'message': f'修改失败：{str(e)}'}), 500
    



@bp.route('/transfer-records')
@login_required
def transfer_records():
    return render_template('admin/transfer_records.html')

@bp.route('/api/transfer-records/list', methods=['GET'])
@login_required
def api_transfer_records_list():
    try:
        # 左连接缓存表，按时间倒序拉取流水
        records = db.session.query(TransferRecord, IpLocationCache).outerjoin(
            IpLocationCache, TransferRecord.ip_address == IpLocationCache.ip_address
        ).order_by(TransferRecord.created_at.desc()).limit(1000).all()

        data = []
        for tr, ic in records:
            data.append({
                'id': tr.id,
                'ip': tr.ip_address,
                'region': f"{ic.region} {ic.city}".strip() if ic and ic.region else "获取中/未知",
                'resource_name': tr.resource_name,
                'created_at': tr.created_at.strftime('%Y-%m-%d %H:%M:%S')
            })

        sys_config = SystemConfig.query.first()
        limit = sys_config.daily_transfer_limit if sys_config else 20
        return jsonify({'code': 200, 'data': data, 'limit': limit})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})

@bp.route('/api/transfer-limit/update', methods=['POST'])
@login_required
def api_transfer_limit_update():
    try:
        new_limit = int(request.json.get('limit', 20))
        sys_config = SystemConfig.query.first()
        if not sys_config:
            sys_config = SystemConfig()
            db.session.add(sys_config)
        sys_config.daily_transfer_limit = new_limit
        db.session.commit()
        return jsonify({'code': 200, 'msg': '限流阈值更新成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': str(e)})

@bp.route('/system-config', methods=['GET', 'POST'])
@login_required
def system_config():
    # 1. 尝试获取数据库中第一条配置记录
    config_record = SystemConfig.query.first()
    
    # 如果数据库里还没有配置记录，就初始化一条默认空记录
    if not config_record:
        config_record = SystemConfig()
        db.session.add(config_record)
        db.session.commit()

    if request.method == 'GET':
        # GET 请求：将数据库里的配置对象转成字典传给前端页面
        return render_template('admin/system_config.html', config=config_record.to_dict())
    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '未接收到数据'})
            
        try:
            baidu_extract_code = data.get('baidu_extract_code', 'yyds')
            if not re.match(r'^[A-Za-z0-9]{4}$', baidu_extract_code):
                return jsonify({'success': False, 'message': '提取码必须为4位英文或数字'})
            config_record.baidu_bduss = data.get('baidu_bduss', '')
            config_record.baidu_bduss_bfess = data.get('baidu_bduss_bfess', '')
            config_record.baidu_stoken = data.get('baidu_stoken', '')
            config_record.baidu_ua = data.get('baidu_ua', '')
            config_record.baidu_extract_code = baidu_extract_code
            config_record.baidu_save_path = data.get('baidu_save_path', '/资源/')
            config_record.baidu_daily_path = data.get('baidu_daily_path', '')
            config_record.quark_cookie = data.get('quark_cookie', '')
            config_record.quark_save_dir_name = data.get('quark_save_dir_name', '/')
            config_record.quark_save_dir_id = data.get('quark_save_dir_id', '0')
            config_record.quark_daily_dir_name = data.get('quark_daily_dir_name', '/')
            config_record.quark_daily_dir_id = data.get('quark_daily_dir_id', '0')
            config_record.search_api_url = data.get('search_api_url', '')
            config_record.search_api_token = data.get('search_api_token', '')
            config_record.search_timeout = int(data.get('search_timeout', 10))
            config_record.task_interval = int(data.get('task_interval', 60))
            
            config_record.group_qrcode = data.get('group_qrcode', '')
            
            # 3. 提交更改到数据库
            db.session.commit()
            return jsonify({'success': True, 'message': '配置保存成功'})
            
        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': '数据格式有误（如超时时间必须是数字）'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'数据库保存失败: {str(e)}'})
    
@bp.route('/api/baidu/directories', methods=['GET'])
@login_required
def get_baidu_directories():
    sys_config = SystemConfig.query.first()
    if not sys_config or not sys_config.baidu_bduss or not sys_config.baidu_stoken:
        return jsonify({'code': 400, 'msg': '尚未配置百度网盘凭证或凭证不全，请先配置并保存'})
    
    try:
        api = BaiduPCSApi(bduss=sys_config.baidu_bduss, stoken=sys_config.baidu_stoken)
        proxy_manager = ProxyManager()
        
        max_retries = 2
        
        # 引入与 BaiduTransfer 相同的重试与代理自愈机制
        for attempt in range(max_retries + 1):
            try:
                # 显式指定 level，并移除不存在的 force_refresh 参数
                proxies = proxy_manager.get_proxy(level=1)
                
                # 强行注入代理到第三方库的内部 Session 中
                if proxies:
                    if hasattr(api, 'session'):
                        api.session.proxies.update(proxies)
                    elif hasattr(api, '_session'):
                        api._session.proxies.update(proxies)
                
                # 发起请求
                files = api.list('/')
                dir_list = [f.path for f in files if f.is_dir]
                return jsonify({'code': 200, 'data': dir_list})
                
            except Exception as e:
                # 兼容第三方库可能抛出的非 RequestException 异常
                error_str = str(e).lower()
                if attempt < max_retries and any(k in error_str for k in ['timeout', 'proxy', 'connection', 'max retries']):
                    # 触发代理管理器的故障标记，下次循环获取时会自动刷新或进入冷却
                    proxy_manager.mark_failed(level=1)
                    time.sleep(1.5 ** attempt)
                else:
                    raise e
                
                # 强行注入代理到第三方库的内部 Session 中
                if hasattr(api, 'session'):
                    api.session.proxies.update(proxies)
                elif hasattr(api, '_session'):
                    api._session.proxies.update(proxies)
                
                # 发起请求
                files = api.list('/')
                dir_list = [f.path for f in files if f.is_dir]
                return jsonify({'code': 200, 'data': dir_list})
                
            except Exception as e:
                # 兼容第三方库可能抛出的非 RequestException 异常
                error_str = str(e).lower()
                if attempt < max_retries and any(k in error_str for k in ['timeout', 'proxy', 'connection', 'max retries']):
                    force_refresh = True
                    time.sleep(1.5 ** attempt)
                else:
                    raise e
                    
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'获取目录失败: {str(e)}'})
    
@bp.route('/api/quark/directories', methods=['GET'])
@login_required
def get_quark_directories():
    sys_config = SystemConfig.query.first()
    if not sys_config or not sys_config.quark_cookie:
        return jsonify({'code': 400, 'msg': '尚未配置夸克网盘凭证或凭证不全，请先配置并保存'})
    
    try:
        from quark_client import QuarkClient
        with QuarkClient(cookies=sys_config.quark_cookie) as client:
            response = client.list_files(folder_id="0", page=1, size=50)
            if response.get('status') == 200 and 'data' in response:
                all_items = response['data'].get('list', [])
                folders = [{'name': item.get('file_name'), 'id': item.get('fid')} for item in all_items if item.get('dir') is True]
                return jsonify({'code': 200, 'data': folders})
            else:
                return jsonify({'code': 500, 'msg': '获取数据失败，请检查网络或凭证。'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'获取目录失败: {str(e)}'})

@bp.route('/website-config', methods=['GET', 'POST'])
@login_required # 记得加权限控制
def website_config():
    # 获取唯一的配置记录 (id=1)
    config_record = SiteConfig.query.first()
    
    if request.method == 'GET':
        return render_template('admin/website_config.html', config=config_record)
    
    if request.method == 'POST':
        try:
            # 1. 更新文本字段
            config_record.site_name = request.form.get('site_name')
            config_record.site_slogan = request.form.get('site_slogan')
            config_record.seo_title = request.form.get('seo_title')
            config_record.seo_keywords = request.form.get('seo_keywords')
            config_record.seo_description = request.form.get('seo_description')

            # 2. 处理 Logo 上传
            logo_file = request.files.get('logo_file')
            if logo_file and logo_file.filename != '':
                path = save_upload_file(logo_file, 'logo')
                if path:
                    config_record.logo_path = path

            # 3. 处理 Favicon 上传
            favicon_file = request.files.get('favicon_file')
            if favicon_file and favicon_file.filename != '':
                path = save_upload_file(favicon_file, 'icon')
                if path:
                    config_record.favicon_path = path

            db.session.commit()
            flash('网站配置已更新成功！', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'更新失败: {str(e)}', 'error')

        return redirect(url_for('admin.website_config'))

@bp.route('/start_quark_login', methods=['POST'])
@login_required
def start_quark_login():
    task_id = str(uuid.uuid4())
    event = threading.Event()
    
    LOGIN_TASKS[task_id] = { 'status': 'PENDING', 'url': None, 'result': None }
    
    thread = threading.Thread(target=_quark_login_worker, args=(task_id, event))
    thread.daemon = True
    thread.start()
    
    if event.wait(timeout=10.0):
        url = LOGIN_TASKS[task_id]['url']
        if url == 'ALREADY_LOGGED_IN':
            LOGIN_TASKS[task_id]['status'] = 'COMPLETED'
            return jsonify({'code': 2, 'msg': '本地缓存有效，已自动登录', 'task_id': task_id})
        elif url:
            LOGIN_TASKS[task_id]['status'] = 'WAITING_SCAN'
            return jsonify({'code': 0, 'task_id': task_id, 'url': url})
        else:
            return jsonify({'code': -1, 'msg': '提取登录 URL 失败'})
    else:
        return jsonify({'code': -1, 'msg': '提取登录 URL 超时'})

@bp.route('/verify_quark_login', methods=['POST'])
@login_required
def verify_quark_login():
    task_id = request.json.get('task_id')
    if not task_id or task_id not in LOGIN_TASKS:
        return jsonify({'code': -1, 'msg': '无效或已过期的 task_id'})
    
    task = LOGIN_TASKS[task_id]
    
    if task['status'] == 'COMPLETED':
        cookie_val = task['result']
        if not isinstance(cookie_val, str):
            import json
            cookie_val = json.dumps(cookie_val)
            
        # 存入数据库
        config_record = SystemConfig.query.first()
        if config_record:
            config_record.quark_cookie = cookie_val
            db.session.commit()
            
        return jsonify({'code': 0, 'msg': '登录成功，凭证已入库'})
    elif task['status'] == 'ERROR':
        return jsonify({'code': -1, 'msg': f"登录异常: {task['result']}"})
    else:
        return jsonify({'code': 1, 'msg': '登录进行中，等待用户扫码'})

# ================= 引流配置 (文件管理器) =================
def get_safe_drainage_path(sub_path):
    """防穿透路径安全校验，锁定沙箱边界"""
    drainage_root = os.path.abspath(os.path.join(current_app.root_path, 'static', 'drainage'))
    if not os.path.exists(drainage_root):
        os.makedirs(drainage_root, exist_ok=True)
        
    if not sub_path:
        sub_path = ''
    sub_path = sub_path.lstrip('/').lstrip('\\')
    target_path = os.path.abspath(os.path.join(drainage_root, sub_path))
    
    if not target_path.startswith(drainage_root):
        raise ValueError("非法的路径访问试图跨越安全沙箱")
    return target_path

@bp.route('/api/drainage/list', methods=['GET'])
@login_required
def drainage_list():
    sub_path = request.args.get('path', '')
    try:
        target_path = get_safe_drainage_path(sub_path)
        if not os.path.exists(target_path):
            os.makedirs(target_path, exist_ok=True)
        
        items = []
        for name in os.listdir(target_path):
            full_path = os.path.join(target_path, name)
            stat = os.stat(full_path)
            is_dir = os.path.isdir(full_path)
            items.append({
                'name': name,
                'is_dir': is_dir,
                'size': stat.st_size if not is_dir else 0,
                'mtime': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            })
        items.sort(key=lambda x: (not x['is_dir'], x['name']))
        return jsonify({'code': 200, 'data': items})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})

@bp.route('/api/drainage/mkdir', methods=['POST'])
@login_required
def drainage_mkdir():
    data = request.json or {}
    sub_path = data.get('path', '')
    folder_name = data.get('name', '').strip()
    if not folder_name or re.search(r'[\\/:*?"<>|]', folder_name):
        return jsonify({'code': 400, 'msg': '文件夹名称不合法'})
    try:
        target_path = get_safe_drainage_path(os.path.join(sub_path, folder_name))
        os.makedirs(target_path, exist_ok=True)
        return jsonify({'code': 200, 'msg': '创建成功'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})

@bp.route('/api/drainage/create_text', methods=['POST'])
@login_required
def drainage_create_text():
    data = request.json or {}
    sub_path = data.get('path', '')
    file_name = data.get('name', '').strip()
    content = data.get('content', '')
    
    if not file_name or re.search(r'[\\/:*?"<>|]', file_name):
        return jsonify({'code': 400, 'msg': '文件名称不合法'})
    if not file_name.lower().endswith('.txt'):
        file_name += '.txt'
        
    try:
        target_path = get_safe_drainage_path(os.path.join(sub_path, file_name))
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'code': 200, 'msg': '文件创建成功'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})

@bp.route('/api/drainage/upload', methods=['POST'])
@login_required
def drainage_upload():
    sub_path = request.form.get('path', '')
    file_obj = request.files.get('file')
    if not file_obj:
        return jsonify({'code': 400, 'msg': '未接收到文件'})
        
    filename = file_obj.filename
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    allowed_exts = {'png', 'jpg', 'jpeg', 'gif', 'txt', 'md', 'json'}
    if ext not in allowed_exts:
        return jsonify({'code': 400, 'msg': '拦截: 不支持的文件类型'})
        
    try:
        # 弃用 secure_filename，改用正则清洗以保留中文字符
        safe_name = filename.replace('\\', '/').split('/')[-1] # 提取基础文件名防穿越
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', safe_name)
        if not safe_name or safe_name == f".{ext}": 
            safe_name = f"upload_{int(time.time())}.{ext}"
            
        target_path = get_safe_drainage_path(os.path.join(sub_path, safe_name))
        
        # 分块流保存防 OOM
        file_obj.save(target_path)
        # 1GB 大小校验
        if os.path.getsize(target_path) > 1024 * 1024 * 1024:
            os.remove(target_path)
            return jsonify({'code': 400, 'msg': '文件超过1GB大小限制'})
        return jsonify({'code': 200, 'msg': '上传成功'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})

@bp.route('/api/drainage/delete', methods=['POST'])
@login_required
def drainage_delete():
    data = request.json or {}
    sub_path = data.get('path', '')
    names = data.get('names', [])
    if not names:
        return jsonify({'code': 400, 'msg': '未选择要删除的文件'})
        
    try:
        for name in names:
            target = get_safe_drainage_path(os.path.join(sub_path, name))
            if os.path.isdir(target):
                shutil.rmtree(target)
            elif os.path.isfile(target):
                os.remove(target)
        return jsonify({'code': 200, 'msg': '删除成功'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})

# api
@bp.route('/requirements/action', methods=['POST'])
@login_required
def requirement_action():
    # 获取前端传来的 JSON 数据
    data = request.json
    if not data:
        return jsonify({'success': False, 'message': '未提供操作数据'})

    action = data.get('action') # 对应前端传来的 'delete', 'read', 'read_all'
    req_id = data.get('id')     # 需求 ID（全部已读时不需要传这个）

    try:
        # === 1. 删除单个需求 ===
        if action == 'delete':
            if not req_id:
                return jsonify({'success': False, 'message': '缺少需求ID'})
                
            req = Requirement.query.get(req_id)
            if not req:
                return jsonify({'success': False, 'message': '该需求记录不存在或已被删除'})
                
            db.session.delete(req)
            db.session.commit()
            return jsonify({'success': True, 'message': '删除成功'})

        # === 2. 标记单个需求为已读 ===
        elif action == 'read':
            if not req_id:
                return jsonify({'success': False, 'message': '缺少需求ID'})
                
            req = Requirement.query.get(req_id)
            if req and not req.is_read:
                req.is_read = True
                db.session.commit()
            return jsonify({'success': True, 'message': '已标记为已读'})

        # === 3. 全部标记为已读 ===
        elif action == 'read_all':
            # 批量更新：把所有 is_read 为 False 的记录都更新为 True
            Requirement.query.filter_by(is_read=False).update({'is_read': True})
            db.session.commit()
            return jsonify({'success': True, 'message': '全部标记为已读'})

        # === 4. 未知操作拦截 ===
        else:
            return jsonify({'success': False, 'message': '未知的操作类型'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'数据库操作失败: {str(e)}'})

@bp.route('/system-maintenance', methods=['GET', 'POST'])
@login_required
def system_maintenance():
    config_record = SystemConfig.query.first()
    if not config_record:
        config_record = SystemConfig()
        db.session.add(config_record)
        db.session.commit()

    if request.method == 'GET':
        return render_template('admin/system_maintenance.html', config=config_record.to_dict())
    
    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '未接收到数据'})
            
        try:
            config_record.maintenance_mode = bool(data.get('maintenance_mode'))
            config_record.maintenance_whitelist = data.get('maintenance_whitelist', '')
            db.session.commit()
            return jsonify({'success': True, 'message': '维护配置保存成功'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'保存失败: {str(e)}'})