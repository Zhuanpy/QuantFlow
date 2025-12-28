from flask import render_template, request, redirect, url_for, flash, Blueprint
from ..models import Issue
from ..exts import db
from sqlalchemy.exc import SQLAlchemyError

# 创建蓝图
issue_bp = Blueprint('stock_issue_bp', __name__)


# 问题页面：展示所有问题
@issue_bp.route('/issues_index')
def issues_index():
    # 使用 .all() 获取所有数据，必要时可以使用分页
    issues = Issue.query.order_by(Issue.id.desc()).all()
    return render_template('issue/issue.html', issues=issues)


# 添加问题
@issue_bp.route('/add_issue', methods=['GET', 'POST'])
def add_issue():
    if request.method == 'POST':
        question = request.form.get('question')
        solution = request.form.get('solution')
        status = request.form.get('status', '未解决')  # 默认状态为 "未解决"

        if not question or not solution:
            flash('问题和解决方案不能为空！', 'warning')
            return redirect(url_for('stock_issue_bp.add_issue'))

        new_issue = Issue(question=question, solution=solution, status=status)

        try:
            db.session.add(new_issue)
            db.session.commit()
            flash('问题添加成功！', 'success')
        except SQLAlchemyError as e:
            db.session.rollback()
            flash(f'添加问题时发生错误: {str(e)}', 'danger')

        return redirect(url_for('stock_issue_bp.issues_index'))

    return render_template('issue/add_issue.html')


# 更新问题状态
@issue_bp.route('/update_status/<int:id>', methods=['GET', 'POST'])
def update_status(id):
    issue = Issue.query.get_or_404(id)  # 使用 get_or_404 自动处理找不到记录的情况
    if request.method == 'POST':
        status = request.form.get('status')
        if status not in ['未解决', '已解决']:  # 确保状态是有效的
            flash('无效的状态值', 'warning')
            return redirect(url_for('stock_issue_bp.update_status', id=id))

        issue.status = status
        try:
            db.session.commit()
            flash('问题状态更新成功！', 'success')
        except SQLAlchemyError as e:
            db.session.rollback()
            flash(f'更新问题状态时发生错误: {str(e)}', 'danger')

        return redirect(url_for('stock_issue_bp.issues_index'))

    return render_template('issue/update_status.html', issue=issue)


# 删除问题
@issue_bp.route('/delete_issue/<int:id>', methods=['POST'])
def delete_issue(id):
    issue = Issue.query.get(id)
    if issue:
        db.session.delete(issue)
        db.session.commit()
        flash('问题已删除', 'success')
    else:
        flash('未找到该问题', 'error')
    return redirect(url_for('stock_issue_bp.issues_index'))
