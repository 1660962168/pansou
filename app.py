from flask import Flask, session, g, render_template
import config
from exts import db
from blueprints.admin import bp as admin_bp
from livereload import Server # 测试用
import models



app = Flask(__name__)
app.config.from_object(config)
app.register_blueprint(admin_bp)
db.init_app(app)


with app.app_context():
    db.create_all()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/daily-update')
def daily_update():
    return render_template('daily-update.html')

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
    return render_template('search.html')


if __name__ == '__main__':
    app.debug = True
    server = Server(app.wsgi_app)
    server.watch('templates/*.html')
    server.watch('static/*.*')
    # 开放0.0.0.0
    server.serve(port=5000, host='0.0.0.0')
