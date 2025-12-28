from flask import Blueprint, render_template, redirect, url_for

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    """网站主页"""
    return render_template('index.html')

@main_bp.route('/home')
def home():
    """主页的别名路由"""
    return redirect(url_for('main_bp.index')) 