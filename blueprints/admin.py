from flask import Blueprint, render_template
from exts import db
bp = Blueprint('admin', __name__, url_prefix='/admin')

# 访问路径: /admin/
@bp.route('/')
def index():
    return "这是管理员后台首页"
