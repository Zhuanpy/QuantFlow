# -*- coding: utf-8 -*-
"""
待实现/想法/问题清单 路由

页面：
    GET  /issues                            列表 + 新建（同一页 AJAX）

API：
    GET  /issues/api/list?status=&q=        过滤列出
    POST /issues/api/create                 {question, solution?, status?}
    POST /issues/api/<id>/update            {question?, solution?, status?}
    POST /issues/api/<id>/delete

设计：把"突然冒出的想法 / 待修 bug / 待做功能"快速记下来，
等有空再回头实现，实现完把状态切到"已实现"。
"""
from datetime import datetime
import logging
import re

from flask import Blueprint, render_template, request, jsonify, abort

from App.exts import db
from App.models.strategy.StockIssueModels import Issue

logger = logging.getLogger(__name__)

# 蓝图名称保留 issue_bp，端点保留 issues —— base.html / index.html 已链到这里
issue_bp = Blueprint('issue_bp', __name__)


# 列表预览里把 base64 内嵌图替换成占位符
# Why: solution 走富文本后可能塞进 data:image/...（动辄几十 KB），
#      列表里一次返回几十条会把页面拖慢；编辑页拉单条时仍是完整 HTML。
_IMG_DATA_RE = re.compile(
    r'<img\b[^>]*\bsrc=["\']data:[^"\']*["\'][^>]*>', re.IGNORECASE)
def _strip_inline_images(html):
    if not html:
        return html
    return _IMG_DATA_RE.sub('<span class="img-ph">🖼️ 图片</span>', html)


# --------------- 页面 ---------------
@issue_bp.route('/issues')
def issues():
    return render_template('issues.html',
                           statuses=list(Issue.ALL_STATUSES),
                           types=list(Issue.ALL_TYPES),
                           TYPE_EMOJI=Issue.TYPE_EMOJI)


@issue_bp.route('/issues/<int:item_id>/edit')
def issue_edit_page(item_id):
    row = Issue.query.get(item_id)
    if not row:
        abort(404)
    return render_template('issue_edit.html',
                           issue=row,
                           statuses=list(Issue.ALL_STATUSES),
                           types=list(Issue.ALL_TYPES),
                           TYPE_EMOJI=Issue.TYPE_EMOJI)


# --------------- API ---------------
def _parse_payload(data):
    """从 JSON 请求体抽取允许字段，做基础校验。"""
    out = {}
    if 'question' in data:
        q = (data.get('question') or '').strip()
        if not q:
            raise ValueError('标题不能为空')
        if len(q) > 200:
            raise ValueError('标题最长 200 字')
        out['question'] = q
    if 'solution' in data:
        s = data.get('solution')
        out['solution'] = (s.strip() if isinstance(s, str) else s) or None
    if 'status' in data:
        st = (data.get('status') or '').strip()
        if st not in Issue.ALL_STATUSES:
            raise ValueError(f'非法 status: {st}')
        out['status'] = st
    if 'type' in data:
        tp = (data.get('type') or '').strip()
        if tp and tp not in Issue.ALL_TYPES:
            raise ValueError(f'非法 type: {tp}')
        out['type'] = tp or Issue.TYPE_IDEA
    return out


@issue_bp.route('/issues/api/list', methods=['GET'])
def api_list():
    try:
        status = (request.args.get('status') or '').strip()
        type_ = (request.args.get('type') or '').strip()
        q_text = (request.args.get('q') or '').strip()

        query = Issue.query
        if status and status in Issue.ALL_STATUSES:
            query = query.filter(Issue.status == status)
        if type_ and type_ in Issue.ALL_TYPES:
            query = query.filter(Issue.type == type_)
        if q_text:
            like = f'%{q_text}%'
            query = query.filter(db.or_(Issue.question.like(like),
                                        Issue.solution.like(like)))

        # 排序：未实现/进行中 在前，按创建时间倒序
        items = query.order_by(
            db.case(
                (Issue.status == Issue.STATUS_PENDING, 0),
                (Issue.status == Issue.STATUS_IN_PROGRESS, 1),
                (Issue.status == Issue.STATUS_DONE, 2),
                (Issue.status == Issue.STATUS_CLOSED, 3),
                else_=9,
            ),
            Issue.created_at.desc(),
            Issue.id.desc(),
        ).all()

        # 状态/类型计数：只跟随关键词过滤，不跟随状态/类型过滤
        # 这样切 tab 时数字不会"跳没"——展示的是"如果我只点这个 tab 会看到多少"
        count_query = Issue.query
        if q_text:
            like = f'%{q_text}%'
            count_query = count_query.filter(db.or_(Issue.question.like(like),
                                                    Issue.solution.like(like)))
        rows = (count_query
                .with_entities(Issue.status, db.func.count(Issue.id))
                .group_by(Issue.status).all())
        counts = {s: 0 for s in Issue.ALL_STATUSES}
        total = 0
        for st, c in rows:
            if st in counts:
                counts[st] = int(c)
            total += int(c)
        # 类型计数
        type_rows = (count_query
                     .with_entities(Issue.type, db.func.count(Issue.id))
                     .group_by(Issue.type).all())
        type_counts = {t: 0 for t in Issue.ALL_TYPES}
        for tp, c in type_rows:
            # 旧数据 type=None 归到默认类型
            key = tp if tp in type_counts else Issue.TYPE_IDEA
            type_counts[key] = type_counts.get(key, 0) + int(c)

        items_payload = []
        for r in items:
            d = r.to_dict()
            d['solution_preview'] = _strip_inline_images(d.get('solution'))
            items_payload.append(d)
        return jsonify({
            'success': True,
            'data': {
                'items': items_payload,
                'counts': counts,
                'type_counts': type_counts,
                'total': total,
            },
        })
    except Exception as e:
        logger.exception('issues 列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@issue_bp.route('/issues/api/create', methods=['POST'])
def api_create():
    try:
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)
        if 'question' not in fields:
            return jsonify({'success': False, 'message': '标题必填'}), 400
        row = Issue(
            question=fields['question'],
            solution=fields.get('solution'),
            status=fields.get('status') or Issue.STATUS_PENDING,
            type=fields.get('type') or Issue.TYPE_IDEA,
        )
        if row.status == Issue.STATUS_DONE:
            row.resolved_at = datetime.utcnow()
        db.session.add(row)
        db.session.commit()
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception('issues 创建失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@issue_bp.route('/issues/api/<int:item_id>/update', methods=['POST'])
def api_update(item_id):
    try:
        row = Issue.query.get_or_404(item_id)
        data = request.get_json(silent=True) or {}
        fields = _parse_payload(data)

        for k, v in fields.items():
            setattr(row, k, v)

        # 状态切换时维护 resolved_at
        if 'status' in fields:
            if fields['status'] == Issue.STATUS_DONE and not row.resolved_at:
                row.resolved_at = datetime.utcnow()
            elif fields['status'] != Issue.STATUS_DONE:
                row.resolved_at = None

        row.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'data': row.to_dict()})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception(f'issues 更新失败 id={item_id}')
        return jsonify({'success': False, 'message': str(e)}), 500


@issue_bp.route('/issues/api/<int:item_id>/delete', methods=['POST'])
def api_delete(item_id):
    try:
        row = Issue.query.get(item_id)
        if not row:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        db.session.delete(row)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.exception(f'issues 删除失败 id={item_id}')
        return jsonify({'success': False, 'message': str(e)}), 500
