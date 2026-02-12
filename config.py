SECRET_KEY = 'your_secret_key'
#数据库配置信息
HOSTNAME = '127.0.0.1'
PORT = '3306'
DATABASE = 'work_situation'
USERNAME = 'root'
PASSWORD = '000000'
DB_URI = 'mysql+pymysql://{}:{}@{}:{}/{}?charset=utf8'.format(USERNAME, PASSWORD, HOSTNAME, PORT, DATABASE)
SQLALCHEMY_DATABASE_URI = DB_URI
SQLALCHEMY_TRACK_MODIFICATIONS = True


