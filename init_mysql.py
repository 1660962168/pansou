import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    stream=sys.stdout
)

from app import app
from exts import db
from models import Admin, SiteConfig, SystemConfig

def init_mysql_data():
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
    logging.info("MySQL Database initialization completed.")

if __name__ == '__main__':
    logging.info("Starting MySQL table creation...")
    with app.app_context():
        # 建立空表
        db.create_all()
        # 注入基础配置数据
        init_mysql_data()