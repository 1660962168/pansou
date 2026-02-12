import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY = 'jxmvaJRa6EtGAhi5Vbmf-mF2s9OvQa-gVIMOZmWhJP8'
SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'pansuo.db')
SQLALCHEMY_TRACK_MODIFICATIONS = True
