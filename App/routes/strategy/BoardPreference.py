"""
板块个人偏好评分路由 /board_pref

职责：用户对板块的主观喜好（1-10 分，与日期无关），独立于趋势机器打分。
列表以板块主表 eval_board 为基础，LEFT JOIN eval_board_preference 显示/筛选/排序偏好。
"""
from flask import Blueprint, render_template, request, jsonify
import logging

from App.exts import db
from App.models.evaluation.Board import Board
from App.models.evaluation.BoardPreference import BoardPreference
from App.services.board_data_service import (
    latest_trading_date, query_data_status_batch, classify_data_status,
)

logger = logging.getLogger(__name__)

board_pref_bp = Blueprint('board_pref', __name__, url_prefix='/board_pref')


# ============== 板块风格分类（用于"四项分布图"）==============
# 按板块的市场风格把行业板块归为四类。少数判断项的归属说明：
#   - 电力/电源/输配电/风电/光伏/电池 归「成长制造」（电新链），而非传统公用
#   - 电信运营、公用事业、燃气、环保、交运 归「周期资源」（稳定/公用并入）
#   - 汽车整车/服务、医药商业/中药/医疗服务 归「消费」；医药制造/生物/器械归「成长」
# 想调整某板块归属，改下面的 _STYLE_CODES 即可。
STYLE_DEFS = [
    ('growth',   '成长制造'),
    ('consumer', '消费'),
    ('cyclical', '周期资源'),
    ('finance',  '金融地产'),
]
_STYLE_CODES = {
    'growth': [
        'BK1036', 'BK1037', 'BK0448', 'BK0459', 'BK1038', 'BK0447', 'BK0737',
        'BK1046', 'BK1034', 'BK1033', 'BK1032', 'BK1031', 'BK0457', 'BK0428',
        'BK0735', 'BK0480', 'BK1030', 'BK0465', 'BK1044', 'BK1041', 'BK1015',
        'BK0910', 'BK0458',
    ],
    'consumer': [
        'BK0438', 'BK0477', 'BK0456', 'BK1029', 'BK0481', 'BK1016', 'BK0436',
        'BK0482', 'BK0485', 'BK1035', 'BK0734', 'BK0486', 'BK0740', 'BK0433',
        'BK1040', 'BK1042', 'BK0727', 'BK0470', 'BK0733', 'BK0476', 'BK0440',
        'BK0484', 'BK1043',
    ],
    'cyclical': [
        'BK0478', 'BK0732', 'BK1027', 'BK0479', 'BK0437', 'BK1017', 'BK0464',
        'BK0538', 'BK1019', 'BK1039', 'BK0471', 'BK0731', 'BK1018', 'BK0454',
        'BK0739', 'BK0546', 'BK0424', 'BK1020', 'BK0425', 'BK0725', 'BK0726',
        'BK0427', 'BK1028', 'BK0728', 'BK0422', 'BK0429', 'BK0450', 'BK0421',
        'BK0420', 'BK0729', 'BK0730', 'BK0539', 'BK0545', 'BK0736',
    ],
    'finance': [
        'BK0475', 'BK0473', 'BK0474', 'BK0738', 'BK0451', 'BK1045',
    ],
}
STYLE_MAP = {c: k for k, codes in _STYLE_CODES.items() for c in codes}


def _pref_tier(score):
    """偏好分 → 档位 key。"""
    if score is None:
        return 'unscored'
    if score >= 8:
        return 'like'      # 很喜欢
    if score >= 6:
        return 'fav'       # 喜欢
    if score >= 4:
        return 'neutral'   # 中性
    return 'avoid'         # 回避


@board_pref_bp.route('/')
def page():
    return render_template('strategy/board_preference.html')


@board_pref_bp.route('/api/list')
def api_list():
    """板块 + 偏好分分页列表（以主表 eval_board 为基础）。

    query: page, page_size, board_code(模糊), board_name(模糊),
           min_preference(int), only_scored(1), sort_by: preference_desc/preference_asc/board_code
    """
    try:
        Board.ensure_table()
        BoardPreference.ensure_table()

        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 30, type=int)
        board_code = request.args.get('board_code', '').strip()
        board_name = request.args.get('board_name', '').strip()
        min_preference = request.args.get('min_preference', type=int)
        only_scored = request.args.get('only_scored', '').strip() == '1'
        sort_by = request.args.get('sort_by', 'preference_desc')

        q = (db.session.query(Board, BoardPreference.preference_score)
             .outerjoin(BoardPreference, BoardPreference.board_code == Board.board_code))

        if board_code:
            q = q.filter(Board.board_code.like(f'%{board_code}%'))
        if board_name:
            q = q.filter(Board.board_name.like(f'%{board_name}%'))
        if min_preference is not None:
            q = q.filter(BoardPreference.preference_score >= min_preference)
        elif only_scored:
            q = q.filter(BoardPreference.preference_score.isnot(None))

        null_last = (BoardPreference.preference_score.is_(None)).asc()
        if sort_by == 'preference_asc':
            q = q.order_by(null_last, BoardPreference.preference_score.asc(), Board.board_code.asc())
        elif sort_by == 'board_code':
            q = q.order_by(Board.board_code.asc())
        else:
            q = q.order_by(null_last, BoardPreference.preference_score.desc(), Board.board_code.asc())

        pagination = q.paginate(page=page, per_page=page_size, error_out=False)
        items = []
        for board, pref in pagination.items:
            d = board.to_dict()
            d['preference_score'] = pref
            items.append(d)

        # 数据状态（best-effort）
        try:
            codes = [it['board_code'] for it in items if it.get('board_code')]
            status_map = query_data_status_batch(codes)
            ref_date = latest_trading_date()
            for it in items:
                hit = status_map.get(it['board_code'], {})
                it['data_status'] = classify_data_status(
                    hit.get('latest_daily'), hit.get('latest_1m'),
                    hit.get('latest_15m'), ref_date)
        except Exception as ds_err:
            logger.warning(f'附加 data_status 失败: {ds_err}')

        return jsonify({'success': True, 'data': {
            'items': items,
            'pagination': {
                'page': pagination.page, 'pages': pagination.pages,
                'per_page': pagination.per_page, 'total': pagination.total,
                'has_prev': pagination.has_prev, 'has_next': pagination.has_next,
            }
        }})
    except Exception as e:
        logger.exception('板块偏好列表查询失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pref_bp.route('/api/style_distribution')
def api_style_distribution():
    """四类风格的偏好分布：把已打分板块按市场风格归为
    成长制造 / 消费 / 周期资源 / 金融地产 四类，统计每类的
    板块数、平均偏好分、各档位（很喜欢/喜欢/中性/回避）数量。
    供「四项分布图」使用。
    """
    try:
        BoardPreference.ensure_table()
        rows = (db.session.query(
                    BoardPreference.board_code,
                    BoardPreference.preference_score,
                    Board.board_name)
                .outerjoin(Board, Board.board_code == BoardPreference.board_code)
                .filter(BoardPreference.preference_score.isnot(None))
                .all())

        # 初始化四类桶
        buckets = {k: {'key': k, 'label': lab, 'count': 0, 'sum': 0,
                       'tiers': {'like': 0, 'fav': 0, 'neutral': 0, 'avoid': 0},
                       'boards': []}
                   for k, lab in STYLE_DEFS}
        unclassified = []

        for code, score, name in rows:
            style = STYLE_MAP.get((code or '').upper())
            if style is None:
                unclassified.append({'board_code': code, 'preference_score': score})
                continue
            b = buckets[style]
            b['count'] += 1
            b['sum'] += score
            b['tiers'][_pref_tier(score)] += 1
            b['boards'].append({'board_code': code,
                                'board_name': name or code,
                                'preference_score': score})

        styles = []
        for k, _lab in STYLE_DEFS:
            b = buckets[k]
            b['avg'] = round(b['sum'] / b['count'], 2) if b['count'] else None
            b['boards'].sort(key=lambda x: -x['preference_score'])
            b.pop('sum', None)
            styles.append(b)

        all_scores = [r[1] for r in rows]
        overall_avg = round(sum(all_scores) / len(all_scores), 2) if all_scores else None

        return jsonify({'success': True, 'data': {
            'styles': styles,
            'overall_avg': overall_avg,
            'total_scored': len(all_scores),
            'unclassified': unclassified,
        }})
    except Exception as e:
        logger.exception('板块风格分布统计失败')
        return jsonify({'success': False, 'message': str(e)}), 500


@board_pref_bp.route('/api/set', methods=['POST'])
def api_set():
    """设置/更新某板块的个人偏好分（1-10，与日期无关）。

    body: {
        board_code: 'BK0437'   # 必填
        board_name: '半导体'   # 可选，冗余存一份便于查看
        preference_score: 8    # 1-10 整数；传 null / 0 / 空 表示清除打分
        notes: '...'           # 可选
    }
    """
    try:
        BoardPreference.ensure_table()
        data = request.get_json(silent=True) or {}
        board_code = (data.get('board_code') or '').strip()
        if not board_code:
            return jsonify({'success': False, 'message': 'board_code 必填'}), 400

        raw = data.get('preference_score')
        if raw in (None, '', 0, '0'):
            score = None
        else:
            try:
                score = int(raw)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'message': 'preference_score 需为整数'}), 400
            if not (1 <= score <= 10):
                return jsonify({'success': False, 'message': 'preference_score 取值 1-10'}), 400

        row = BoardPreference.upsert(
            board_code=board_code,
            preference_score=score,
            board_name=(data.get('board_name') or None),
            notes=(data.get('notes') if 'notes' in data else None),
        )
        return jsonify({'success': True, 'data': row.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.exception('设置板块偏好分失败')
        return jsonify({'success': False, 'message': str(e)}), 500
