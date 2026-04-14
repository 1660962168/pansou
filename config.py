import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = 'jxmvaJRa6EtGAhi5Vbmf-mF2s9OvQa-gVIMOZmWhJP8'
SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://pansou:bLS6faErf7m5Lmfs@127.0.0.1:3306/pansou?charset=utf8mb4'
SQLALCHEMY_TRACK_MODIFICATIONS = True

PERMANENT_SESSION_LIFETIME = timedelta(days=30)
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'ico', 'svg'}