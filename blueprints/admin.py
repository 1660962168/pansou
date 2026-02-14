from flask import Blueprint, render_template,request, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash
from exts import db
bp = Blueprint('admin', __name__, url_prefix='/admin')

# 访问路径: /admin/
@bp.route('/')
def index():
    return render_template('admin/index.html')

@bp.route('/login')
def login():
    return render_template('admin/logn.html')

@bp.route('/batch-transfer')
def batch_transfer():
    return render_template('admin/batch_transfer.html')

@bp.route('/transfer-records')
def transfer_records():
    mock_records = [
        {'id': 1024, 'name': '流浪地球2.The.Wandering.Earth.II.2023.4K.mp4', 'source': '百度网盘', 'status': 'success', 'size': '12.5 GB', 'time': '2023-10-27 14:23:45'},
        {'id': 1023, 'name': '奥本海默.Oppenheimer.2023.1080p.mkv', 'source': '百度网盘', 'status': 'processing', 'size': '8.2 GB', 'time': '2023-10-27 14:10:12'},
        {'id': 1022, 'name': '黑暗荣耀.The.Glory.S02.Complete.zip', 'source': '夸克网盘', 'status': 'failed', 'size': '24.1 GB', 'time': '2023-10-26 09:15:33'},
        {'id': 1021, 'name': 'Python深度学习实战.pdf', 'source': '百度网盘', 'status': 'success', 'size': '45.2 MB', 'time': '2023-10-26 08:30:00'},
        {'id': 1020, 'name': '狂飙.The.Knockout.EP01-39.4K.H265', 'source': '百度网盘', 'status': 'success', 'size': '56.8 GB', 'time': '2023-10-25 19:20:11'},
    ]
    return render_template('admin/transfer_records.html', records=mock_records)

@bp.route('/daily-updates')
def daily_updates():
    # --- 模拟数据 ---
    mock_updates = [
        {
            'id': 101,
            'name': '海贼王 (One Piece)',
            'priority': 99,
            'check_count': 152,
            'transfer_count': 45,
            'latest': 'Ep 1080',
            'last_check': '10分钟前',
            'is_monitoring': True,
            'baidu': {
                'status': 'normal', 
                'source_link': 'https://pan.baidu.com/s/src1',
                'code': '8888',
                'current_link': 'https://pan.baidu.com/s/cur1'
            },
            'quark': {
                'status': 'invalid',
                'source_link': 'https://pan.quark.cn/s/src2',
                'code': '',
                'current_link': ''
            }
        },
        {
            'id': 102,
            'name': '咒术回战 第二季',
            'priority': 80,
            'check_count': 89,
            'transfer_count': 89,
            'latest': 'Ep 18',
            'last_check': '1小时前',
            'is_monitoring': True,
            'baidu': {
                'status': 'normal',
                'source_link': 'https://pan.baidu.com/s/src3',
                'code': '1234',
                'current_link': 'https://pan.baidu.com/s/cur3'
            },
            'quark': None 
        }
    ]
    return render_template('admin/daily_updates.html', updates=mock_updates)

@bp.route('/requirements')
def requirements():
    # --- 模拟需求数据 ---
    mock_demands = [
        {
            'id': 1, 
            'content': '求一部老剧《通过屋顶的High Kick》，找了好久都没找到高清的，希望博主能帮忙找一下，感谢！', 
            'time': '2023-10-27 14:30', 
            'is_read': False 
        },
        {
            'id': 2, 
            'content': '网站搜索功能有时候会报错，显示500 Internal Server Error，麻烦看一下服务器日志。还有就是希望能增加一个夜间模式。', 
            'time': '2023-10-26 09:15', 
            'is_read': False 
        },
        {
            'id': 3, 
            'content': '求资源：奥本海默 4K HDR 版本，最好是夸克网盘的，百度网盘下载太慢了。', 
            'time': '2023-10-25 18:20', 
            'is_read': True  # 已读
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        {
            'id': 4, 
            'content': '友情链接申请：这里是电影天堂站长，想跟贵站交换一下友链，流量相当。', 
            'time': '2023-10-24 11:00', 
            'is_read': True 
        },
        
    ]
    return render_template('admin/requirements.html', demands=mock_demands)

@bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    # GET 请求：渲染页面
    if request.method == 'GET':
        return render_template('admin/change_password.html')
    

@bp.route('/system-config', methods=['GET', 'POST'])
def system_config():
    if request.method == 'GET':
        # --- 模拟从数据库读取的配置 ---
        # 真实场景建议存放在 Config 表或 .env 文件中
        config_data = {
            # 1. 网盘配置
            'baidu_cookie': 'BDUSS=xxxxxxxxxxxx...',
            'baidu_ua': 'netdisk;7.0.3.2;PC;PC-Windows;10.0.19043',
            'quark_cookie': 'u_id=yyyyyy...',
            
            # 2. 搜索配置
            'search_api_url': 'http://api.example.com/search',
            'search_api_token': 'sk-Zw8f...',
            'search_timeout': 10, # 请求超时时间(秒)
            
            # 3. 网络与系统
            'proxy_url': '', # 例如 http://127.0.0.1:7890
            'task_interval': 60, # 监控任务间隔(分钟)
            'debug_mode': False,
            
            # 4. 通知配置 (新增建议)
            'notify_webhook': '' # 比如 Bark 或 钉钉机器人
        }
        return render_template('admin/system_config.html', config=config_data)
    


@bp.route('/website-config', methods=['GET', 'POST'])
def website_config():
    if request.method == 'GET':
        # --- 模拟网站配置数据 ---
        site_config = {
            # 1. 基础信息
            'site_name': '基德资源站',
            'site_slogan': '极速、纯净的网盘资源分享平台',
            'site_url': 'https://www.example.com',
            'logo_url': '/static/images/logo.png', 
            'favicon_url': '/static/favicon.ico',
            
            # 2. SEO 设置
            'seo_title': '基德资源站 - 4K电影_电视剧_动漫下载',
            'seo_keywords': '网盘资源,百度网盘,夸克网盘,4K电影,免费下载',
            'seo_description': '每日更新最新热门影视资源，提供百度网盘等高速下载链接...',
        }
        return render_template('admin/website_config.html', config=site_config)
    
    if request.method == 'POST':
        # data = request.json
        # save_to_db(data)
        return jsonify({'success': True, 'message': '网站配置已更新'})