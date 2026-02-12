from flask import Flask, session, g, render_template
import config
from exts import db
from blueprints.auth import bp as auth_bp



app = Flask(__name__)
# 绑定配置文件
app.config.from_object(config)
app.register_blueprint(auth_bp)
db.init_app(app)


@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run()
