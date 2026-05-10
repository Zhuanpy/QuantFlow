from flask import render_template, request, redirect, url_for, flash, Blueprint

# 主页蓝图
main_bp = Blueprint('main_bp', __name__)

@main_bp.route('/index')
@main_bp.route('/')
@main_bp.route('/public/')
def index():
    return render_template('index.html')

# 深度学习蓝图
dl_bp = Blueprint('dl_bp', __name__)

@dl_bp.route('/dl')
def dl():
    return render_template('data/download_page.html')

# RNN蓝图
rnn_bp = Blueprint('rnn_bp', __name__)

@rnn_bp.route('/rnn')
def rnn():
    return render_template('rnn.html')

# 注：原 issue_bp 已迁移到 App.routes.issues_route（合并三处重复路由）