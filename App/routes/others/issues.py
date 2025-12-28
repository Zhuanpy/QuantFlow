from flask import Blueprint, render_template, request, flash, redirect, url_for
import logging

logger = logging.getLogger(__name__)

issue_bp = Blueprint('others_issue_bp', __name__)

@issue_bp.route('/issues')
def issues_index():
    """问题列表页面"""
    return render_template('issues/index.html')

@issue_bp.route('/issues/create', methods=['GET', 'POST'])
def create_issue():
    """创建新问题"""
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        # TODO: 保存问题到数据库
        flash('问题已创建')
        return redirect(url_for('others_issue_bp.issues_index'))
    return render_template('issues/create.html')

@issue_bp.route('/issues/<int:issue_id>')
def show_issue(issue_id):
    """显示问题详情"""
    # TODO: 从数据库获取问题
    return render_template('issues/show.html', issue_id=issue_id) 