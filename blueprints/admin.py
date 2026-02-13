from flask import Blueprint, render_template
from exts import db
bp = Blueprint('admin', __name__, url_prefix='/admin')

# 访问路径: /admin/
@bp.route('/')
def admin_index():
    return render_template('admin/index.html')

@bp.route('/login')
def login():
    return render_template('admin/logn.html')
