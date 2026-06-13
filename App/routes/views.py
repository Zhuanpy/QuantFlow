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
    # rnn.html 模板早已不存在（旧入口），重定向到 RNN 处理管线总览入口页，
    # 避免旧书签/外链触发 TemplateNotFound 500。
    return redirect(url_for('RnnData.rnn_data_page'))

# 注：原 issue_bp 已迁移到 App.routes.issues_route（合并三处重复路由）