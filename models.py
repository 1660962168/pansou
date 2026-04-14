from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from exts import db
from datetime import datetime

class Admin(db.Model):
    __tablename__ = 'admin'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    password_hash = db.Column(db.String(200), nullable=False)
    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')
    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
class SiteConfig(db.Model):
    __tablename__ = 'site_config'
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(50), default='XXX资源站')
    site_slogan = db.Column(db.String(100), default='极速、纯净的网盘资源分享平台')
    logo_path = db.Column(db.String(200), default='/static/images/logo.png') # 存储相对路径
    favicon_path = db.Column(db.String(200), default='/static/favicon.ico')
    seo_title = db.Column(db.String(100), default='XXX资源站 - 4K电影_电视剧_动漫下载')
    seo_keywords = db.Column(db.String(200), default='网盘资源,百度网盘,夸克网盘,4K电影,免费下载')
    seo_description = db.Column(db.Text, default='每日更新最新热门影视资源，提供百度网盘等高速下载链接...')

class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    id = db.Column(db.Integer, primary_key=True)
    
    # 网盘配置
    baidu_bduss = db.Column(db.String(255), default='')
    baidu_bduss_bfess = db.Column(db.String(255), default='')
    baidu_stoken = db.Column(db.String(255), default='')
    baidu_ua = db.Column(db.String(255), default='netdisk;7.0.3.2;PC;PC-Windows;10.0.19043')
    baidu_extract_code = db.Column(db.String(4), default='yyds', nullable=False)
    baidu_save_path = db.Column(db.String(255), default='/资源/')
    baidu_daily_path = db.Column(db.String(255), default='')
    quark_cookie = db.Column(db.Text, default='')
    quark_save_dir_name = db.Column(db.String(255), default='/')
    quark_save_dir_id = db.Column(db.String(100), default='0')
    quark_daily_dir_name = db.Column(db.String(255), default='/')
    quark_daily_dir_id = db.Column(db.String(100), default='0')
    
    # 搜索配置
    search_api_url = db.Column(db.String(255), default='')
    search_api_token = db.Column(db.String(255), default='')
    search_timeout = db.Column(db.Integer, default=10)
    
    # 任务设置与扩展
    task_interval = db.Column(db.Integer, default=60)
    group_qrcode = db.Column(db.String(255), default='') # 加群活码
    daily_transfer_limit = db.Column(db.Integer, default=20) # 每日单IP转存限制

    # 在 class SystemConfig(db.Model): 中找到 “# 任务设置与扩展” 附近，添加以下字段：
    maintenance_mode = db.Column(db.Boolean, default=False) # 系统维护开关
    maintenance_whitelist = db.Column(db.Text, default='')  # IP白名单 (多IP支持换行或逗号分隔)

    cos_region = db.Column(db.String(50), nullable=True, comment='COS 区域')
    cos_secret_id = db.Column(db.String(100), nullable=True, comment='COS SecretId')
    cos_secret_key = db.Column(db.String(100), nullable=True, comment='COS SecretKey')
    cos_bucket = db.Column(db.String(100), nullable=True, comment='COS 存储桶名称')

    # 请搜索 def to_dict(self): 并将其返回值更新为包含此字段：
    def to_dict(self):
        return {
            'baidu_bduss': self.baidu_bduss,
            'baidu_bduss_bfess': self.baidu_bduss_bfess,
            'baidu_stoken': self.baidu_stoken,
            'baidu_ua': self.baidu_ua,
            'baidu_extract_code': self.baidu_extract_code,
            'baidu_save_path': self.baidu_save_path,
            'baidu_daily_path': self.baidu_daily_path,
            'quark_cookie': self.quark_cookie,
            'quark_save_dir_name': self.quark_save_dir_name,
            'quark_save_dir_id': self.quark_save_dir_id,
            'quark_daily_dir_name': self.quark_daily_dir_name,
            'quark_daily_dir_id': self.quark_daily_dir_id,
            'search_api_url': self.search_api_url,
            'search_api_token': self.search_api_token,
            'search_timeout': self.search_timeout,
            'task_interval': self.task_interval,
            'group_qrcode': self.group_qrcode,
            'daily_transfer_limit': self.daily_transfer_limit,
            'maintenance_mode': self.maintenance_mode,
            'maintenance_whitelist': self.maintenance_whitelist
        }
    
class Requirement(db.Model):
    __tablename__ = 'requirement'
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False) # 需求内容
    created_at = db.Column(db.DateTime, default=datetime.now) # 提交时间
    is_read = db.Column(db.Boolean, default=False) # 是否已读状态
    
    # 辅助方法：转为字典供前端使用
    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'time': self.created_at.strftime('%Y-%m-%d %H:%M'), # 格式化时间输出
            'is_read': self.is_read
        }

class HotSearch(db.Model):
    __tablename__ = 'hot_search'
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(100), nullable=False)
    search_count = db.Column(db.Integer, default=0)
    rank = db.Column(db.Integer, default=0) # 严格控制前端输出顺序



class ExternalDrama(db.Model):
    __tablename__ = 'external_drama'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), unique=True, nullable=False, index=True)
    baidu_link = db.Column(db.Text, nullable=True)
    quark_link = db.Column(db.Text, nullable=True)
    update_time = db.Column(db.DateTime, default=datetime.now)

class AutoCleanupTask(db.Model):
    __tablename__ = 'auto_cleanup_task'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    drive_type = db.Column(db.String(20), nullable=False) # 'baidu' 或 'quark'
    file_ids = db.Column(db.Text, nullable=False) # JSON格式，存储文件ID或路径
    execute_time = db.Column(db.DateTime, nullable=False, index=True) # 计划执行时间
    status = db.Column(db.Integer, default=0, index=True) # 0:待执行 1:已完成 -1:失败

class MonitorTask(db.Model):
    __tablename__ = 'monitor_task'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    type = db.Column(db.String(20), default='tv')
    priority = db.Column(db.Integer, default=50)
    is_monitoring = db.Column(db.Boolean, default=False) # 是否正在监控中
    check_count = db.Column(db.Integer, default=0)
    transfer_count = db.Column(db.Integer, default=0)
    last_check_time = db.Column(db.DateTime, nullable=True)

    baidu_source_link = db.Column(db.Text, nullable=True)
    baidu_current_link = db.Column(db.Text, nullable=True)
    baidu_pwd = db.Column(db.String(20), nullable=True)
    baidu_status = db.Column(db.String(20), default='normal')
    baidu_file_path = db.Column(db.Text, nullable=True)

    quark_source_link = db.Column(db.Text, nullable=True)
    quark_current_link = db.Column(db.Text, nullable=True)
    quark_pwd = db.Column(db.String(20), nullable=True)
    quark_status = db.Column(db.String(20), default='normal')
    quark_file_id = db.Column(db.Text, nullable=True)
    
    baidu_file_count = db.Column(db.Integer, default=0)
    quark_file_count = db.Column(db.Integer, default=0)
    is_local = db.Column(db.Boolean, default=False) # 标识是否为本地直接分享资源

    def to_dict(self):
        last_check = self.last_check_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_check_time else '尚未检查'
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'priority': self.priority,
            'is_monitoring': self.is_monitoring,
            'check_count': self.check_count,
            'transfer_count': self.transfer_count,
            'last_check': last_check,
            'baidu': {
                'status': self.baidu_status,
                'source_link': self.baidu_source_link,
                'current_link': self.baidu_current_link,
                'code': self.baidu_pwd
            } if self.baidu_source_link else None,
            'quark': {
                'status': self.quark_status,
                'source_link': self.quark_source_link,
                'current_link': self.quark_current_link,
                'code': self.quark_pwd
            } if self.quark_source_link else None,
            'baidu_file_count': self.baidu_file_count,
            'quark_file_count': self.quark_file_count,
            'is_local': self.is_local
        }

class SiteStat(db.Model):
    __tablename__ = 'site_stat'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, unique=True, nullable=False, index=True) # 统计日期
    page_views = db.Column(db.Integer, default=0) # 浏览量
    frontend_transfers = db.Column(db.Integer, default=0) # 前台触发转存量

class NasptRanking(db.Model):
    __tablename__ = 'naspt_ranking'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category_id = db.Column(db.Integer, index=True)
    category_name = db.Column(db.String(50), nullable=False)
    rank = db.Column(db.Integer, default=0)
    title = db.Column(db.String(255), nullable=False)

class VisitorIPRecord(db.Model):
    __tablename__ = 'visitor_ip_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ip_address = db.Column(db.String(45), nullable=False) # 兼容IPv4/IPv6
    visit_date = db.Column(db.Date, nullable=False)
    
    # 建立联合索引以加速高并发下的去重查询
    __table_args__ = (
        db.Index('idx_ip_date', 'ip_address', 'visit_date'),
    )

class CustomDrama(db.Model):
    __tablename__ = 'custom_drama'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False)
    cover = db.Column(db.String(255), nullable=True)
    baidu_link = db.Column(db.Text, nullable=True)
    quark_link = db.Column(db.Text, nullable=True)

class IpLocationCache(db.Model):
    __tablename__ = 'ip_location_cache'
    ip_address = db.Column(db.String(45), primary_key=True)
    region = db.Column(db.String(50), default='')
    city = db.Column(db.String(50), default='')
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class TransferRecord(db.Model):
    __tablename__ = 'transfer_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ip_address = db.Column(db.String(45), nullable=False, index=True)
    resource_name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

class ProxyNode(db.Model):
    __tablename__ = 'proxy_node'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    level = db.Column(db.Integer, nullable=False, unique=True)
    ip_port = db.Column(db.String(50), nullable=False)
    is_failed = db.Column(db.Boolean, default=False)
    last_refresh_time = db.Column(db.DateTime, nullable=False)


# ================= 影视资源重构模型 =================

# 1. 多对多关联表 (支持级联删除)
media_category = db.Table('media_category',
    db.Column('media_id', db.Integer, db.ForeignKey('media.id', ondelete='CASCADE'), primary_key=True),
    db.Column('category_id', db.Integer, db.ForeignKey('category.id', ondelete='CASCADE'), primary_key=True)
)

media_region = db.Table('media_region',
    db.Column('media_id', db.Integer, db.ForeignKey('media.id', ondelete='CASCADE'), primary_key=True),
    db.Column('region_id', db.Integer, db.ForeignKey('region.id', ondelete='CASCADE'), primary_key=True)
)

media_language = db.Table('media_language',
    db.Column('media_id', db.Integer, db.ForeignKey('media.id', ondelete='CASCADE'), primary_key=True),
    db.Column('language_id', db.Integer, db.ForeignKey('language.id', ondelete='CASCADE'), primary_key=True)
)

# 2. 维度字典表
class Category(db.Model):
    __tablename__ = 'category'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)

class Region(db.Model):
    __tablename__ = 'region'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)

class Language(db.Model):
    __tablename__ = 'language'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)

# 3. 影视主表 (Entity)
class Media(db.Model):
    __tablename__ = 'media'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    media_type = db.Column(db.String(20), nullable=False, index=True) # 枚举: movie, tv, anime
    score = db.Column(db.Float, default=0.0, index=True) # 0.0 代表暂无评分
    link = db.Column(db.Text, nullable=True)
    cover_url = db.Column(db.Text, nullable=True)
    release_date = db.Column(db.Date, nullable=True) # 完整日期
    release_year = db.Column(db.Integer, nullable=True, index=True) # 年份冗余字段，极速筛选
    intro = db.Column(db.Text, nullable=True)
    
    # ORM 关联关系设置
    categories = db.relationship('Category', secondary=media_category, backref=db.backref('medias', lazy='dynamic'))
    regions = db.relationship('Region', secondary=media_region, backref=db.backref('medias', lazy='dynamic'))
    languages = db.relationship('Language', secondary=media_language, backref=db.backref('medias', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'media_type': self.media_type,
            'score': self.score,
            'link': self.link,
            'cover_url': self.cover_url,
            'release_date': self.release_date.strftime('%Y-%m-%d') if self.release_date else None,
            'release_year': self.release_year,
            'intro': self.intro,
            'categories': [c.name for c in self.categories],
            'regions': [r.name for r in self.regions],
            'languages': [l.name for l in self.languages]
        }