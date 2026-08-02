# protocols/routes.py — 路由工厂
"""
自动生成 6 个协议 × 13 种端点 = 81 个路由。

设计原则:
1. 每个 handler body 从 app.py 原代码 copy 后参数化(不发明逻辑)
2. @jwt_required 工厂统一包,SS-PSI 用 _login_required_api,demo 端点无认证
3. create/join/leave/get/delete/my-groups/start-computation/finalize-round/
   history/round-download/preview-ciphertext/download-result* 都是协议特定
"""
import os
from flask import request, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename

from .registry import PROTOCOLS
from .base import ProtocolSpec


# ==================== 工具函数(从 app.py 复用) ====================
def _read_first_n(path, n=20):
    """读文件前 n 行非空内容，返回 (preview, total_count)"""
    if not os.path.exists(path):
        return [], 0
    with open(path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines[:n], len(lines)


# 2026-07-30 Friday 修复:前端 PSI_INT/PSI_MATCH/PSI_UNION/PSI_SUM 的 refresh 函数
# 读 result.data.*_completed / result_preview / my_*_preview / computation_human
# 等 9 个字段,但 1.0.2 重构 (commit 3b27866) 后只返 psi_result/cardinality_result/union_result,
# 导致"明文预览/密文预览/结果预览/下载按钮/下一轮按钮"全不显示。
# 这里统一 helper 生成,4 个 PSI 系协议调用。
_PROTOCOL_EXTRAS_CONFIG = {
    'psi': {
        'result_filename': 'intersection.txt',
        'reader': 'read_intersection_from_file',
        'completed_field': 'psi_completed',
        'count_field': 'intersection_count',
        'preview_field': 'result_preview',
        'full_count_field': 'result_full_count',
        'count_returns_list': True,
    },
    'psi_card': {
        'result_filename': 'cardinality.txt',
        'reader': 'read_cardinality_from_file',
        'completed_field': 'cardinality_completed',
        'count_field': 'cardinality_count',
        'preview_field': 'cardinality_preview',
        'full_count_field': 'cardinality_full_count',
        'count_returns_list': False,
    },
    'psu': {
        'result_filename': 'union.txt',
        'reader': 'read_union_from_file',
        'completed_field': 'union_completed',
        'count_field': 'union_count',
        'preview_field': 'result_preview',  # 2026-07-30 fix: 前端读 result_preview, 后端之前用 union_preview
        'full_count_field': 'union_full_count',
        'count_returns_list': True,
    },
    'psi_match': {
        'result_filename': 'cardinality.txt',
        'reader': 'read_cardinality_from_file',
        'completed_field': 'cardinality_completed',
        'count_field': 'cardinality_count',
        'preview_field': 'cardinality_preview',
        'full_count_field': 'cardinality_full_count',
        'count_returns_list': False,
    },
}


def _make_protocol_extras(group, username, spec):
    """给 4 个 PSI 系协议生成统一 extras dict。"""
    from app import (
        Config,
        read_intersection_from_file,
        read_cardinality_from_file,
        read_union_from_file,
    )
    cfg = _PROTOCOL_EXTRAS_CONFIG[spec.protocol_id]
    reader_map = {
        'read_intersection_from_file': read_intersection_from_file,
        'read_cardinality_from_file': read_cardinality_from_file,
        'read_union_from_file': read_union_from_file,
    }
    group_id = group['id']
    kunlun_dir = os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
    role = 'receiver' if username == group['creator'] else 'sender'

    result = reader_map[cfg['reader']](group_id)
    if cfg['count_returns_list']:
        result_count = len(result) if isinstance(result, list) else 0
        preview_data = result[:20] if isinstance(result, list) else []
    else:
        result_count = int(result) if result is not None else 0
        preview_data = [{'value': str(result_count), 'original': str(result_count)}]

    # 2026-07-30 Friday fix (Bug 3):前端 line 402-403 读 result_preview[i].original/.value,
    # 但 read_intersection_from_file 返回 int list (如 [13810000001, ...]) 不是 dict list。
    # 需要 reverse_map 把 uint64 → 原始 token。
    # 交集里的每个 uint64 都在我自己的 uploads.numbers 里出现过(因为我自己的 numbers 、
    # 对方的 numbers、双方交集合并;交集数字 ＝ 双侧都 hash 后的同值)，
    # 所以自己的 reverse_map 能覆盖交集所有项的“原始 token”。
    if cfg['count_returns_list'] and isinstance(result, list):
        rev_nums = []
        rev_origs = []
        for u in (group.get('uploads') or []):
            if u.get('username') == username:
                rev_nums = [str(n) for n in u.get('numbers', [])]
                rev_origs = u.get('original_items', [])
                break
        # reverse_map: str(numbers[i]) → original_items[i]
        rev_map = dict(zip(rev_nums, rev_origs))
        preview_data = []
        for x in result[:20]:
            try:
                x_int = int(x)
                x_str = str(x_int)
            except (ValueError, TypeError):
                # 2026-07-30 防御: result 里可能有非数字行 (eg. padding sentinel 遗漏)
                # 跳过,不解码
                continue
            preview_data.append({
                'value': x_str,
                'original': rev_map.get(x_str, x_str),  # 本侧映射不到就显示数字本身
            })

    result_path = os.path.join(kunlun_dir, cfg['result_filename'])
    completed = os.path.exists(result_path)

    ciphertext_preview, ciphertext_full = _read_first_n(
        os.path.join(kunlun_dir, f"{role}_ciphertext.txt"))
    original_preview, original_full = _read_first_n(
        os.path.join(kunlun_dir, f"original_{role}.txt"))

    # SPIKE 5 (2026-07-30 Friday demo): sPSO runner 现在 dump OPRF prf_vals 到
    # oprf_prf_sender.txt (PSO sender = OprfRecver) / oprf_prf_recver.txt (PSO recver = OprfSender).
    # 我们按当前用户的 role 选:
    #   - PSO sender 跑 OprfRecver.eval → oprf_prf_sender.txt
    #   - PSO receiver 跑 OprfSender.eval → oprf_prf_recver.txt
    # ALICE = PSO sender (proto 里的 'receiver' 角色), BOB = PSO receiver (proto 里的 'sender' 角色)
    if not ciphertext_preview:
        oprf_filename = 'oprf_prf_sender.txt' if role == 'receiver' else 'oprf_prf_recver.txt'
        oprf_preview, oprf_full = _read_first_n(
            os.path.join(kunlun_dir, oprf_filename))
        if oprf_preview:
            ciphertext_preview = oprf_preview
            ciphertext_full = oprf_full

    # 第二次 fallback: 仍没就退化到 group.uploads 里的 numbers(标准 hash 后的 uint64)
    if not ciphertext_preview:
        for u in (group.get('uploads') or []):
            if u.get('username') == username and u.get('numbers'):
                ciphertext_preview = [str(n) for n in u['numbers'][:20]]
                ciphertext_full = u.get('count', len(u['numbers']))
                break

    pending = group.get('pending_computation') or {}

    return {
        cfg['completed_field']: completed,
        cfg['count_field']: result_count,
        cfg['preview_field']: preview_data,
        cfg['full_count_field']: result_count,
        'my_ciphertext_preview': ciphertext_preview,
        'my_ciphertext_full_count': ciphertext_full,
        'my_original_preview': original_preview,
        'my_original_full_count': original_full,
        'computation_human': pending.get('duration_human'),
        'pending_computation': pending or None,
        # legacy fields 兼容老前端
        'psi_result': result if spec.protocol_id == 'psi' else None,
        'cardinality_result': result if spec.protocol_id in ('psi_card', 'psi_match') else None,
        'union_result': result if spec.protocol_id == 'psu' else None,
    }


def _psi_match_camel_subset(subset_result):
    """PSI-Match 后端存 snake_case subset_result,前端读 camelCase。
    这里转换：is_subset→isSubset, missing_count→missingCount, matched_alice→matchedAlice,
    matched_count→matchedCount, cardinality→intersectionCardinality。
    """
    if not subset_result:
        return None
    return {
        'isSubset': subset_result.get('is_subset', False),
        'intersectionCardinality': subset_result.get('cardinality', 0),
        'missingCount': subset_result.get('missing_count', 0),
        'matchedAlice': subset_result.get('matched_alice', []),
        'matchedCount': subset_result.get('matched_count', 0),
        'cardinality': subset_result.get('cardinality', 0),  # legacy
    }


def _get_username():
    """从 jwt_required 中间件注入的 request.current_user 拿 username"""
    return request.current_user['username']


def _auth_required(spec: ProtocolSpec, fn):
    """认证包装器工厂"""
    from app import jwt_required
    if spec.auth_method == 'jwt_required':
        return jwt_required(fn)
    elif spec.auth_method == 'login_required_api':
        # SS-PSI 用法:函数内先调 _login_required_api() 拿 (username, err)
        # _login_required_api 在 protocols/base.py 里(原 app.py line 5040 段已搬走)
        from protocols.base import _login_required_api
        def wrapped(*args, **kwargs):
            username, err = _login_required_api()
            if err:
                return err
            # 注:不直接给 username,要通过返回附带。SS-PSI 函数自己取
            return fn(*args, **kwargs)
        return wrapped
    elif spec.auth_method == 'none':
        return fn
    raise ValueError(f"unknown auth_method: {spec.auth_method}")


# ==================== 通用 Handler 工厂 ====================

def _make_create_handler(spec: ProtocolSpec):
    """POST /api/<prefix>/create"""
    def handler():
        try:
            data = request.get_json() or {}
            # PSI-Sum / PSU / PSI-Card / PSI: groupName + standardizeMode
            # SS-PSI: name(无 standardizeMode)
            if spec.protocol_id == 'ss_psi':
                username, _ = _get_username_or_login(spec)
                name = data.get('name', '').strip() or f"SS-PSI小组_{username}"
                group = spec.manager_cls.create_group(name, username)
                return jsonify({'success': True, 'group': group})
            else:
                group_name = data.get('groupName', '').strip()
                if not group_name:
                    return jsonify({'error': '小组名称不能为空'}), 400
                if len(group_name) > 50:
                    return jsonify({'error': '小组名称不能超过50个字符'}), 400
                mode = data.get('standardizeMode', 'auto')
                if mode not in ('auto', 'number_only', 'text_all'):
                    mode = 'auto'
                username = _get_username()
                group = spec.manager_cls.create_group(group_name, username, mode)
                return jsonify({
                    'message': f'{spec.protocol_id.upper()} 小组创建成功',
                    'group': {
                        'id': group['id'],
                        'name': group['name'],
                        'creator': group['creator'],
                        'member_count': len(group['members'])
                    }
                }), 201
        except Exception as e:
            return jsonify({'error': f'创建 {spec.protocol_id} 小组失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _get_username_or_login(spec):
    """兼容 jwt_required + login_required_api 两种认证方式"""
    if spec.auth_method == 'jwt_required':
        return _get_username(), None
    elif spec.auth_method == 'login_required_api':
        username, err = _login_required_api_inner()
        return username, err
    return _get_username(), None


def _login_required_api_inner():
    """调 protocols.base._login_required_api(从 request context)"""
    from protocols.base import _login_required_api
    return _login_required_api()


def _make_join_handler(spec: ProtocolSpec):
    """POST /api/<prefix>/join"""
    def handler():
        try:
            data = request.get_json() or {}
            username, _ = _get_username_or_login(spec)

            # SS-PSI: group_id 字段名(其他是 groupId)
            if spec.protocol_id == 'ss_psi':
                group_id = data.get('group_id', '').strip().upper()
                ok, msg = spec.manager_cls.add_member(group_id, username)
                return jsonify({'success': ok, 'message': msg, 'group_id': group_id})

            group_id = data.get('groupId', '').strip().upper()
            if not group_id or len(group_id) != spec.id_length:
                return jsonify({'error': f'请输入{spec.id_length}位小组ID'}), 400
            success, message = spec.manager_cls.add_member(group_id, username)
            if success:
                return jsonify({'message': message})
            return jsonify({'error': message}), 400
        except Exception as e:
            return jsonify({'error': f'加入 {spec.protocol_id} 小组失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_leave_handler(spec: ProtocolSpec):
    """POST /api/<prefix>/leave"""
    def handler():
        try:
            data = request.get_json() or {}
            username = _get_username()
            group_id = data.get('groupId', '').strip().upper()
            if not group_id:
                return jsonify({'error': '小组ID不能为空'}), 400
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            if group['creator'] == username:
                return jsonify({'error': '组长不能退出小组,请先解散小组'}), 400
            if spec.manager_cls.remove_member(group_id, username):
                return jsonify({'message': f'已退出 {spec.protocol_id.upper()} 小组'})
            return jsonify({'error': '你不是该小组成员'}), 400
        except Exception as e:
            return jsonify({'error': f'退出 {spec.protocol_id} 小组失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_get_group_handler(spec: ProtocolSpec):
    """GET /api/<prefix>/<id>"""
    def handler(group_id):
        try:
            group_id = group_id.upper()
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            username = _get_username_or_login(spec)[0]
            if username not in group['members']:
                return jsonify({'error': '你不是该小组成员'}), 403

            response_data = {
                'success': True,
                'group': group,
            }

            # 2026-07-30 Friday 修复:前端 PSI/PSI-Match/PSI-Sum refresh 函数读 result.data.my_upload/other_upload,
            # 但之前只返回 group,前端 uploCount=0 + 没按钮。补预解析字段(2-party 协议适用)
            uploads = group.get('uploads', [])
            response_data['my_upload'] = next((u for u in uploads if u.get('username') == username), None)
            response_data['other_upload'] = next((u for u in uploads if u.get('username') != username), None)

            # 协议特定 extras
            if spec.protocol_id in ('psi', 'psi_card', 'psu', 'psi_match'):
                response_data.update(_make_protocol_extras(group, username, spec))
                if spec.protocol_id == 'psi_match':
                    # PSI-Match 额外透传 subset_result(前端读 camelCase 字段)
                    response_data['subset_result'] = _psi_match_camel_subset(group.get('subset_result'))
            elif spec.protocol_id == 'psi_sum':
                response_data.update(_psi_sum_get_extras(group, username))
            elif spec.protocol_id == 'ss_psi':
                response_data['result'] = group.get('result')
                response_data['expected_parties'] = group.get('expected_parties', 4)
                response_data['joined_parties'] = len(group['members'])

            return jsonify(response_data)
        except Exception as e:
            import traceback
            with open('/tmp/flask_get_err.log', 'a') as _f:
                _f.write(f"\n=== {spec.protocol_id} GET {group_id} ===\n")
                _f.write(traceback.format_exc())
            return jsonify({'error': f'获取小组失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_delete_group_handler(spec: ProtocolSpec):
    """DELETE /api/<prefix>/<id>"""
    def handler(group_id):
        try:
            group_id = group_id.upper()
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            username = _get_username_or_login(spec)[0]
            if group['creator'] != username:
                return jsonify({'error': '只有组长可以解散小组'}), 403
            if spec.manager_cls.delete_group(group_id):
                return jsonify({'message': '小组已解散'})
            return jsonify({'error': '解散失败'}), 400
        except Exception as e:
            return jsonify({'error': f'解散 {spec.protocol_id} 小组失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_my_groups_handler(spec: ProtocolSpec):
    """GET /api/my-<protocol>-groups"""
    def handler():
        try:
            username = _get_username_or_login(spec)[0]
            groups = spec.manager_cls.get_user_groups(username)
            return jsonify({'success': True, 'groups': groups})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


# ==================== 协议特定辅助 ====================

def _psi_sum_get_extras(group, username):
    """PSI-Sum GET 端点需要返回的额外字段(从原 line 5107-5189 抄)"""
    from app import (
        Config,
        read_psi_sum_cardinality_from_file,
        read_sum_from_file,
    )
    group_id = group['id']
    kunlun_dir = os.path.join(Config.SPSO_PSI_SUM_DATA_DIR, f"group_{group_id}")
    role = 'receiver' if username == group['creator'] else 'sender'

    def _read_first_n(path, n=20):
        if not os.path.exists(path):
            return [], 0
        with open(path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
        return lines[:n], len(lines)

    my_original_preview, my_original_full_count = _read_first_n(
        os.path.join(kunlun_dir, f"original_{role}.txt"))
    my_values_preview, my_values_full_count = _read_first_n(
        os.path.join(kunlun_dir, f"value_{role}.txt"))  # v1.1.4 Bug#1 / 2026-08-02 统一命名
    my_ciphertext_preview, my_ciphertext_full_count = _read_first_n(
        os.path.join(kunlun_dir, f"{role}_ciphertext.txt"))

    sum_str = read_sum_from_file(group_id)

    return {
        'role': role,
        'cardinality_result': read_psi_sum_cardinality_from_file(group_id),
        'sum_result': sum_str,
        'sum_result_int': int(sum_str) if (sum_str and sum_str.lstrip('-').isdigit()) else None,
        'sum_persisted': group.get('sum_result'),
        'my_original_preview': my_original_preview,
        'my_original_full_count': my_original_full_count,
        'my_values_preview': my_values_preview,
        'my_values_full_count': my_values_full_count,
        'my_ciphertext_preview': my_ciphertext_preview,
        'my_ciphertext_full_count': my_ciphertext_full_count,
    }


# ==================== register_routes 主入口 ====================

def register_routes(app):
    """在 app.py 末尾调用: from protocols.routes import register_routes; register_routes(app)"""
    for spec in PROTOCOLS:
        _register_protocol(app, spec)


def _register_protocol(app, spec: ProtocolSpec):
    """为一个协议生成所有端点"""
    prefix = spec.url_prefix

    # === 通用 7 个端点 ===
    app.add_url_rule(f'{prefix}/create', f'{spec.protocol_id}_create',
                     _make_create_handler(spec), methods=['POST'])

    app.add_url_rule(f'{prefix}/join', f'{spec.protocol_id}_join',
                     _make_join_handler(spec), methods=['POST'])

    if spec.supports_leave:
        app.add_url_rule(f'{prefix}/leave', f'{spec.protocol_id}_leave',
                         _make_leave_handler(spec), methods=['POST'])

    app.add_url_rule(f'{prefix}/<group_id>', f'{spec.protocol_id}_get',
                     _make_get_group_handler(spec), methods=['GET'])

    app.add_url_rule(f'{prefix}/<group_id>', f'{spec.protocol_id}_delete',
                     _make_delete_group_handler(spec), methods=['DELETE'])

    app.add_url_rule(f'{prefix}/upload', f'{spec.protocol_id}_upload',
                     _make_upload_handler(spec), methods=['POST'])

    app.add_url_rule(f'/api/my-{spec.url_prefix.rsplit("/",1)[-1].replace("-groups","").replace("-group","")}-groups',
                     f'my_{spec.protocol_id}_groups',
                     _make_my_groups_handler(spec), methods=['GET'])

    if spec.supports_remove_upload:
        if spec.protocol_id == 'psi_sum':
            # PSI-Sum 特殊: POST /api/psi-sum-group/<id>/delete-upload
            app.add_url_rule(f'{prefix}/<group_id>/delete-upload',
                             f'{spec.protocol_id}_delete_upload',
                             _make_delete_upload_handler(spec), methods=['POST'])
        else:
            app.add_url_rule(f'{prefix}/<group_id>/upload',
                             f'{spec.protocol_id}_delete_upload',
                             _make_delete_upload_handler(spec), methods=['DELETE'])

    # === start-computation ===
    if spec.start_computation_endpoint:
        app.add_url_rule(f'{prefix}/<group_id>/start-computation',
                         f'{spec.protocol_id}_start',
                         _make_start_computation_handler(spec), methods=['POST'])

    # === finalize-round ===
    if spec.finalize_round_endpoint:
        app.add_url_rule(f'{prefix}/<group_id>/finalize-round',
                         f'{spec.protocol_id}_finalize_round',
                         _make_finalize_round_handler(spec), methods=['POST'])

    # === history ===
    if spec.has_history:
        app.add_url_rule(f'{prefix}/<group_id>/history',
                         f'{spec.protocol_id}_history',
                         _make_history_handler(spec), methods=['GET'])

    # === round-download ===
    if spec.has_download_round:
        app.add_url_rule(f'{prefix}/<group_id>/round/<int:round_num>/download',
                         f'{spec.protocol_id}_round_download',
                         _make_round_download_handler(spec), methods=['GET'])

    # === preview-ciphertext ===
    if spec.has_preview_ciphertext:
        app.add_url_rule(f'{prefix}/<group_id>/preview-ciphertext',
                         f'{spec.protocol_id}_preview_ciphertext',
                         _make_preview_ciphertext_handler(spec), methods=['GET'])

    # === download-result ===
    if spec.has_download_result:
        app.add_url_rule(f'{prefix}/<group_id>/download-result',
                         f'{spec.protocol_id}_download_result',
                         _make_download_result_handler(spec), methods=['GET'])

    # === download-result-with-original ===
    if spec.has_download_result_with_original:
        app.add_url_rule(f'{prefix}/<group_id>/download-result-with-original',
                         f'{spec.protocol_id}_download_result_with_original',
                         _make_download_result_with_original_handler(spec), methods=['GET'])

    # === download-ciphertext/<role> ===
    if spec.has_download_ciphertext_by_role:
        app.add_url_rule(f'{prefix}/<group_id>/download-ciphertext/<role>',
                         f'{spec.protocol_id}_download_ciphertext',
                         _make_download_by_role_handler(spec, 'ciphertext'), methods=['GET'])

    # === download-original/<role> ===
    if spec.has_download_original_by_role:
        app.add_url_rule(f'{prefix}/<group_id>/download-original/<role>',
                         f'{spec.protocol_id}_download_original',
                         _make_download_by_role_handler(spec, 'original'), methods=['GET'])

    # === demo endpoint ===
    if spec.has_demo_endpoint:
        if spec.protocol_id == 'psi_sum':
            app.add_url_rule('/api/psi-sum-demo', 'psi_sum_demo',
                             _psi_sum_demo_handler, methods=['GET'])
        elif spec.protocol_id == 'ss_psi':
            app.add_url_rule('/api/ss-psi-demo', 'ss_psi_demo',
                             _ss_psi_demo_handler, methods=['GET'])


# ==================== Upload / delete-upload / start-computation ====================

def _make_upload_handler(spec: ProtocolSpec):
    """POST /api/<prefix>/upload"""
    def handler():
        return _generic_upload_handler(spec)
    return _auth_required(spec, handler)


def _generic_upload_handler(spec: ProtocolSpec):
    """
    通用 upload:从原 psi upload 函数(line 2993)参数化
    差异:PSI-Sum 多 values 字段;SS-PSI 是 mock
    关键:还必须把 items / original_items / 原字节写到 kunlun_dir/{role}.txt 等 3 个文件
    """
    from app import (
        allowed_file,
        _probe_json_paths,
        _standardize_token,
        _parse_json_items,
        extract_items_from_file,
        Config,
    )
    import os as _os
    try:
        group_id = request.form.get('groupId', '').upper()
        if not group_id:
            return jsonify({'error': '小组ID不能为空'}), 400
        group = spec.manager_cls.get_group(group_id)
        if not group:
            return jsonify({'error': f'{spec.protocol_id} 小组不存在'}), 404
        username, _ = _get_username_or_login(spec)
        if username not in group['members']:
            return jsonify({'error': '你不是该小组成员'}), 403
        if 'file' not in request.files:
            return jsonify({'error': '未上传文件'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '文件名为空'}), 400
        if not allowed_file(file.filename):
            return jsonify({'error': '只支持.txt/csv/json 文件'}), 400

        # 探测模式(probe=true)
        if request.args.get('probe', '').lower() == 'true':
            if not file.filename.lower().endswith('.json'):
                return jsonify({
                    'success': True, 'is_probe': True, 'paths': [],
                    'filename': file.filename,
                    'message': '仅 .json 文件支持探测结构'
                })
            probe_content = file.read().decode('utf-8')
            try:
                probe_paths = _probe_json_paths(probe_content)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({
                'success': True, 'is_probe': True, 'paths': probe_paths,
                'filename': file.filename,
                'message': f'探测完成,发现 {len(probe_paths)} 个潜在字段路径'
            })

        # PSI-Sum: 解析 token + value
        # 2026-07-31 Friday: 统一两种上传格式
        #   模式 A (新, 优先): 单个文件, 每行 `token,value` (value 可选, 没逗号则 value=0)
        #     - receiver (creator) 端的 value 会被 add_upload 忽略 (协议设计: 只有 sender 提供 value)
        #   模式 B (兼容旧 UI): 两个文件 (主 file + valueFile)
        #   模式 C (兼容老 form): form 'values' 字段 (逗号分隔整数)
        values = None
        values_from_main = False  # 标记: 主 file 解析后负责拆 values
        if spec.protocol_id == 'psi_sum':
            values_from_main = True  # 默认主 file 含 value
            if 'valueFile' in request.files:
                vf = request.files['valueFile']
                if vf.filename != '':
                    vf_content = vf.read().decode('utf-8')
                    values = [v.strip() for v in vf_content.replace(',', '\n').split('\n') if v.strip()]
                    try:
                        values = [int(v) for v in values]
                    except ValueError:
                        return jsonify({'error': 'value 文件必须全是整数(逗号分隔)'}), 400
                    values_from_main = False
            elif request.form.get('values', '').strip():
                values_raw = request.form.get('values', '').strip()
                try:
                    values = [int(v.strip()) for v in values_raw.split(',') if v.strip()]
                    values_from_main = False
                except ValueError as e:
                    return jsonify({'error': f'values 字段解析失败(需逗号分隔整数):{str(e)}'}), 400

        content = file.read().decode('utf-8')
        mode = group.get('standardize_mode', 'auto')
        is_json = file.filename.lower().endswith('.json')

        # 统一解析入口(与老 app.py psi upload 一致:走 extract_items_from_file,内部会调 _standardize_token)
        # JSON path:receiver 可选 path,sender 强制沿用 group.json_path
        if is_json:
            if username == group['creator']:
                form_path = request.form.get('path', '').strip() or None
                if not form_path:
                    try:
                        peek = __import__('json').loads(content)
                        if isinstance(peek, dict):
                            peek_path = peek.get('path')
                            if isinstance(peek_path, str):
                                form_path = peek_path
                    except Exception:
                        pass
                json_path = form_path
            else:
                json_path = group.get('json_path')
                if not json_path:
                    # 2026-07-30 修复: 兼容 receiver 探测 0 paths 的场景
                    # (eg. 纯 token 数组 ["a","b"],后端探测返回 paths=[] → 触发 alert "由后端默认提取"
                    # → 实际上 _parse_json_items 走"档 1a" 自动模式能成功,但 group.json_path 未被写入)
                    # sender 不再 400,改为走自动模式 (None → _parse_json_items 自动 fallback)
                    json_path = None
        else:
            json_path = None

        try:
            # 2026-07-31 Friday: PSI-Sum 走专用 parser (CSV `token,value`), 其他协议走 extract_items_from_file
            if spec.protocol_id == 'psi_sum' and values_from_main:
                from .psi_sum import PSISumGroupManager
                if is_json:
                    # 2026-08-02: JSON 走专用 parser (v1.1.3 的版本丢失后重建)
                    # 自动探测 token/value 字段, 显式 override 用 form 的 tokenPath / valuePath
                    token_path = request.form.get('tokenPath', '').strip() or None
                    value_path = request.form.get('valuePath', '').strip() or None
                    items, original_items, parsed_values = PSISumGroupManager.parse_json_with_values(
                        content, mode, token_path=token_path, value_path=value_path)
                else:
                    # PSI-Sum 单文件 CSV mode: parse_csv_with_values
                    items, original_items, parsed_values = PSISumGroupManager.parse_csv_with_values(content, mode)
                if not values:
                    values = parsed_values  # 仅当用户没传独立 valueFile 时, 用主 file 解析的 values
            else:
                items, original_items = extract_items_from_file(content, file.filename, mode, path=json_path)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        if not items:
            return jsonify({'error': '文件中未找到有效数据'}), 400

        # 持久化 group.json_path(receiver 上传 JSON 且选了 path 时写入)
        if is_json and username == group['creator'] and json_path:
            data = spec.manager_cls.load_groups()
            g = next((x for x in data.get('groups', []) if x['id'] == group_id), None)
            if g is not None:
                g['json_path'] = json_path
                spec.manager_cls.save_groups(data)

        # 写文件到 kunlun_dir(3 个)
        kunlun_dir = _os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
        _os.makedirs(kunlun_dir, exist_ok=True)
        # SS-PSI: party1/party2; 其他协议：receiver/sender
        if spec.protocol_id == 'ss_psi':
            role = 'party1' if username == group['creator'] else 'party2'
        else:
            role = 'receiver' if username == group['creator'] else 'sender'

        std_path = _os.path.join(kunlun_dir, f"{role}.txt")
        # SS-PSI: 写原始 token (不 standardize)，其他协议写 standardized tokens
        write_items = original_items if spec.protocol_id == 'ss_psi' else items
        with open(std_path, 'w', encoding='utf-8') as f:
            for item in write_items:
                f.write(f"{item}\n")

        original_path = _os.path.join(kunlun_dir, f"original_{role}.txt")
        with open(original_path, 'w', encoding='utf-8') as f:
            for orig in original_items:
                f.write(f"{orig}\n")

        filename_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'txt'
        if filename_ext not in ('txt', 'csv', 'json'):
            filename_ext = 'txt'
        uploaded_path = _os.path.join(kunlun_dir, f"uploaded_{role}.{filename_ext}")
        with open(uploaded_path, 'wb') as f:
            f.write(content.encode('utf-8'))

        # PSI-Sum: 额外写 values 到 value_{role}.txt(spso_runner 读这个)
        # 2026-08-02 E2E fix: 原来写 {role}_value.txt 与 archive_filenames / file_type_map
        # 的 value_{role} 命名不一致 → 历史下载 my_value 404
        # 2026-08-02 哥反馈: receiver(组长) 的 value 也被传上去了 → receiver 端不写 value 文件
        if spec.protocol_id == 'psi_sum' and values is not None and role == 'sender':
            values_path = _os.path.join(kunlun_dir, f"value_{role}.txt")
            with open(values_path, 'w', encoding='utf-8') as f:
                for v in values:
                    f.write(f'{v}\n')

        # 调用 add_upload(写 JSON)
        if spec.protocol_id == 'psi_sum':
            ok, msg = spec.manager_cls.add_upload(group_id, username, items,
                                                   original_items=original_items,
                                                   values=values)
        else:
            ok = spec.manager_cls.add_upload(group_id, username, items,
                                             original_items=original_items,
                                             standardize_mode=mode)
            msg = '上传成功' if ok else '上传失败'
        if ok:
            resp = {
                'success': True, 'message': msg,
                'count': len(items), 'mode': mode,
                'group_id': group_id, 'filename': file.filename
            }
            # 2026-08-02 fix: 上传响应补 value_count (前端提示 +N 个 value 依赖它,
            # 之前没返回 → 前端读 undefined 永远显示“(未传 value)”)
            # receiver 端 value 强制忽略 → value_count 恒 0
            if spec.protocol_id == 'psi_sum':
                is_recv = (username == group['creator'])
                resp['value_count'] = 0 if is_recv else sum(1 for v in (values or []) if v != 0)
            return jsonify(resp)
        return jsonify({'error': msg if isinstance(msg, str) else '上传失败'}), 400
    except Exception as e:
        return jsonify({'error': f'上传文件失败:{str(e)}'}), 500


def _make_delete_upload_handler(spec: ProtocolSpec):
    def handler(group_id):
        try:
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            if spec.manager_cls.remove_user_upload(group_id, username):
                return jsonify({'success': True, 'message': '已删除上传'})
            return jsonify({'error': '没有上传记录'}), 400
        except Exception as e:
            return jsonify({'error': f'删除上传失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_start_computation_handler(spec: ProtocolSpec):
    """POST /api/<prefix>/<id>/start-computation"""
    def handler(group_id):
        try:
            group_id = group_id.upper()
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            username = _get_username_or_login(spec)[0]
            if username != group['creator']:
                return jsonify({'error': '只有组长(receiver)可以触发开始运算'}), 403
            uploaded_users = list(set([u['username'] for u in group.get('uploads', [])]))
            if len(uploaded_users) < spec.max_members:
                return jsonify({'error': f'需要 {spec.max_members} 方都上传(当前 {len(uploaded_users)} 方)'}), 400

            # 检查当前轮是否已运算
            from app import Config
            data_dir = os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
            # PSI 默认 intersection.txt / PSU union.txt / Card+Match cardinality.txt / Sum cardinality.txt
            result_check_files = {
                'psi': 'intersection.txt',
                'psi_card': 'cardinality.txt',
                'psu': 'union.txt',
                'psi_match': 'cardinality.txt',
                'psi_sum': 'cardinality.txt',
                'ss_psi': None,  # mock
            }
            check_fname = result_check_files.get(spec.protocol_id)
            if check_fname:
                check_path = os.path.join(data_dir, check_fname)
                if os.path.exists(check_path):
                    return jsonify({'error': '当前轮已运算完成,请先归档当前轮(finalize-round)'}), 409

            print(f"[{spec.protocol_id.upper()}] {username} 按下 [开始运算] 按钮(group={group_id})")

            # 调用 runner
            from app import _compute_with_timing
            runner = spec.runner_cls
            result = _compute_with_timing(runner.run, group_id)
            if not result.get('success'):
                return jsonify({'error': result.get('error', '计算失败')}), 500

            # 协议特定:存 result + pending_computation
            data = spec.manager_cls.load_groups()
            for g in data['groups']:
                if g['id'] == group_id:
                    # PSIMatch: 直接存 subset_result(SPIKE 3.5 fix: 含 is_subset / missing_count / matched_alice)
                    # 2026-08-02 E2E fix: 存双方元素数快照 — 前端结果卡读它,
                    # 否则 finalize 后 uploads 清空 → 结果卡“我的/对方元素数”变 0
                    if spec.protocol_id == 'psi_match':
                        uploads_by_user = {u['username']: u for u in g.get('uploads', [])}
                        g['subset_result'] = {
                            'cardinality': result.get('cardinality', 0),
                            'is_subset': result.get('is_subset', False),
                            'missing_count': result.get('missing_count', 0),
                            'matched_alice': result.get('matched_alice', []),
                            'matched_count': result.get('matched_count', 0),
                            'my_count': uploads_by_user.get(username, {}).get('count', 0),
                            'other_count': next((u.get('count', 0) for u in g.get('uploads', []) if u['username'] != username), 0),
                        }
                    g['pending_computation'] = {
                        'duration_seconds': result.get('duration_seconds'),
                        'duration_human': result.get('duration_human'),
                    }
                    break
            spec.manager_cls.save_groups(data)

            # 协议特定:返回字段
            response = {
                'success': True,
                'duration_seconds': result.get('duration_seconds'),
                'duration_human': result.get('duration_human'),
            }
            if spec.protocol_id == 'psi':
                response['intersection'] = result.get('intersection', [])
                response['intersection_count'] = len(result.get('intersection', []))
            elif spec.protocol_id == 'psi_card':
                response['cardinality'] = result.get('cardinality', 0)
            elif spec.protocol_id == 'psu':
                response['union'] = result.get('union', [])
                response['union_count'] = len(result.get('union', []))
            elif spec.protocol_id == 'psi_match':
                response['cardinality'] = result.get('cardinality', 0)
                # SPIKE 3.5 fix: routes.py 之前只返回 cardinality, 不透传 is_subset / matched_alice
                # 前端 PSI-Match 页面 .psi-match-page 看不到 subsetMatchResult, missingMatchElements 永远是 0
                response['is_subset'] = result.get('is_subset', False)
                response['missing_count'] = result.get('missing_count', 0)
                response['matched_alice'] = result.get('matched_alice', [])
                response['matched_count'] = result.get('matched_count', 0)
            elif spec.protocol_id == 'psi_sum':
                # PSI-Sum: 把 result 存到 sum_result 字段
                data = spec.manager_cls.load_groups()
                for g in data['groups']:
                    if g['id'] == group_id:
                        g['sum_result'] = {
                            'cardinality': result.get('cardinality', 0),
                            'sum': result.get('sum', 0),
                            'sum_str': result.get('sum_str', '0'),
                            'computed_at': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'computed_by': username,
                            'duration_seconds': result.get('duration_seconds'),
                            'duration_human': result.get('duration_human'),
                        }
                        break
                spec.manager_cls.save_groups(data)
                response['cardinality'] = result.get('cardinality', 0)
                response['sum'] = result.get('sum', 0)
                response['sum_str'] = result.get('sum_str', '0')
            elif spec.protocol_id == 'ss_psi':
                # SPIKE 6 Option A: SS-PSI 存 share_sender/share_receiver 到 group['result']
                # 之前只 update response 不存 group,导致 GET group 后前端 downloadResult 拿不到
                # share 文件。注意:share 是双方各持一份,XOR 才是交集(隐私设计,无明文交集暴露)
                data_for_save = spec.manager_cls.load_groups()
                for _g in data_for_save['groups']:
                    if _g['id'] == group_id:
                        _g['result'] = {
                            'share_sender': result.get('share_sender', []),
                            'share_receiver': result.get('share_receiver', []),
                            'cardinality_hint': result.get('cardinality_hint', 0),
                            'cuckoo_size': result.get('cuckoo_size', 0),
                            'computed_at': result.get('computed_at'),
                            'computed_by': result.get('computed_by'),
                        }
                        break
                spec.manager_cls.save_groups(data_for_save)
                response.update(result)
            return jsonify(response), 200
        except Exception as e:
            return jsonify({'error': f'运算失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_finalize_round_handler(spec: ProtocolSpec):
    def handler(group_id):
        try:
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            if username not in group['members']:
                return jsonify({'error': '你不是该小组成员'}), 403
            if spec.protocol_id != 'ss_psi' and username != group['creator']:  # v1.1.4 Bug#7
                return jsonify({'error': '只有组长可以归档当前轮'}), 403
            ok, result = spec.manager_cls.finalize_round(group_id, username)
            if ok:
                return jsonify({'success': True, 'round_record': result})
            return jsonify({'error': result}), 400
        except Exception as e:
            return jsonify({'error': f'归档失败:{str(e)}'}), 500
    return _auth_required(spec, handler)


def _make_history_handler(spec: ProtocolSpec):
    def handler(group_id):
        try:
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            if username not in group['members']:
                return jsonify({'error': '你不是该小组成员'}), 403
            history = spec.manager_cls.get_history(group_id, username)
            if history is None:
                return jsonify({'error': '该协议不支持多轮历史'}), 400
            # 2026-07-30 fix: 前端 4 处 loadHistory 都读 result.data.rounds
            # 之前返 'history' 字段,前端拿不到,一直显示"暂无历史记录"
            return jsonify({'success': True, 'rounds': history, 'history': history})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


def _make_round_download_handler(spec: ProtocolSpec):
    def handler(group_id, round_num):
        try:
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            # 2026-08-02 E2E fix: 前端历史下载按钮传 ?type=(PSI/PSI-Match/PSI-Sum),
            # 后端原来只读 ?file_type= → 非 result 类型全部 fallback 成 result 或 404
            file_type = request.args.get('file_type') or request.args.get('type') or 'result'
            fpath, err = spec.manager_cls.get_round_data(group_id, round_num, file_type, username)
            if not fpath:
                return jsonify({'error': err}), 404
            # 2026-08-02: get_round_data 可能返回 BytesIO (PSI-Sum result_both 合并文件)
            if hasattr(fpath, 'read'):
                return send_file(fpath, as_attachment=True,
                                 download_name=f'{spec.protocol_id}_round{round_num}_{file_type}.txt')
            return send_file(fpath, as_attachment=True)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


def _make_preview_ciphertext_handler(spec: ProtocolSpec):
    def handler(group_id):
        try:
            from app import Config
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            if username not in group['members']:
                return jsonify({'error': '你不是该小组成员'}), 403
            data_dir = os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
            role = 'receiver' if username == group['creator'] else 'sender'
            ct_path = os.path.join(data_dir, f"{role}_ciphertext.txt")
            if not os.path.exists(ct_path):
                return jsonify({'preview': [], 'count': 0})
            with open(ct_path, 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f if l.strip()]
            return jsonify({'preview': lines[:20], 'count': len(lines)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


def _make_download_result_handler(spec: ProtocolSpec):
    def handler(group_id):
        try:
            from app import Config
            group_id = group_id.upper()
            data_dir = os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
            # 协议特定结果文件名
            fname_map = {
                'psi': 'intersection.txt',
                'psi_card': 'cardinality.txt',
                'psu': 'union.txt',
                # 2026-08-02 E2E fix: PSI-Match registry 已开 has_download_result,
                # 但 fname_map 漏了它 → 400 "该协议不支持 download-result"
                'psi_match': 'cardinality.txt',
            }
            fname = fname_map.get(spec.protocol_id)
            # v1.1.4 Bug#2: PSI-Sum 按角色返回不同文件
            # 2026-08-02 E2E fix: 补 username(原代码 psi_sum 分支引用未定义变量 → 500)
            username = _get_username_or_login(spec)[0]
            if spec.protocol_id == 'psi_sum':
                # 2026-08-02 哥反馈: 下载当前结果双方都要有基数和求和 → 合并一个文件
                cardinality_path = os.path.join(data_dir, 'cardinality.txt')
                sum_path = os.path.join(data_dir, 'sum.txt')
                if not os.path.exists(cardinality_path) or not os.path.exists(sum_path):
                    return jsonify({'error': '结果文件不存在(请先完成运算)'}), 404
                with open(cardinality_path, 'r', encoding='utf-8') as _f:
                    _card = _f.read().strip()
                with open(sum_path, 'r', encoding='utf-8') as _f:
                    _sum = _f.read().strip()
                _content = f"交集基数: {_card}\n求和: {_sum}\n"
                import io as _io
                return send_file(_io.BytesIO(_content.encode('utf-8')),
                                 as_attachment=True,
                                 download_name='psi_sum_result.txt')
            if not fname:
                return jsonify({'error': '该协议不支持 download-result'}), 400
            fpath = os.path.join(data_dir, fname)
            if not os.path.exists(fpath):
                return jsonify({'error': '结果文件不存在'}), 404
            return send_file(fpath, as_attachment=True)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


def _make_download_result_with_original_handler(spec: ProtocolSpec):
    def handler(group_id):
        try:
            from app import Config
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            data_dir = os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
            # PSI: intersection_with_original.txt(从归档读)
            # PSU: union_with_original.txt(从归档读)
            # PSI-Match / PSI-Card: 当前 round 的 reverse map 文件(未归档版本)
            if spec.protocol_id in ('psi', 'psu'):
                group = spec.manager_cls.get_group(group_id)
                if not group:
                    return jsonify({'error': '小组不存在'}), 404
                rounds = group.get('rounds', [])
                if rounds:
                    last_round = rounds[-1]
                    key = 'intersection_with_original' if spec.protocol_id == 'psi' else 'union_with_original'
                    fpath = last_round.get('archive_files', {}).get(key)
                    if fpath and os.path.exists(fpath):
                        return send_file(fpath, as_attachment=True)
                # 2026-08-02 fix: 无归档(运算后未保存轮次)也支持下载 — 实时 reverse_map
                # (之前直接 404 “尚无归档轮次” → 前端“下载失败”; 且 union.txt 含
                # __spike2_pad_* 假 token, 这里一并过滤)
                from app import _build_reverse_map
                reverse_map = _build_reverse_map(group, group.get('standardize_mode', 'auto'))
                fname = 'intersection.txt' if spec.protocol_id == 'psi' else 'union.txt'
                fpath = os.path.join(data_dir, fname)
                if not os.path.exists(fpath):
                    return jsonify({'error': '结果文件不存在(请先完成运算)'}), 404
                with open(fpath, 'r', encoding='utf-8') as f:
                    lines = [l.strip() for l in f if l.strip() and not l.startswith('__spike')]
                with_orig = '\n'.join(reverse_map.get(v, v) for v in lines) + '\n'
                import io as _io
                return send_file(_io.BytesIO(with_orig.encode('utf-8')),
                                 as_attachment=True,
                                 download_name=fname.replace('.txt', '_with_original.txt'))
            else:
                # PSI-Match / PSI-Card: 用 reverse map 实时生成
                from app import _build_reverse_map
                group = spec.manager_cls.get_group(group_id)
                if not group:
                    return jsonify({'error': '小组不存在'}), 404
                role = 'receiver' if username == group['creator'] else 'sender'
                reverse_map = _build_reverse_map(group, group.get('standardize_mode', 'auto'))
                # 读结果文件
                fname = 'cardinality.txt'
                fpath = os.path.join(data_dir, fname)
                if not os.path.exists(fpath):
                    return jsonify({'error': '结果文件不存在'}), 404
                with open(fpath, 'r', encoding='utf-8') as f:
                    text = f.read()
                # 尝试 reverse
                with_orig = reverse_map.get(text.strip(), text.strip())
                import io
                return send_file(io.BytesIO((with_orig + '\n').encode('utf-8')),
                                 as_attachment=True,
                                 download_name='cardinality_with_original.txt')
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


def _make_download_by_role_handler(spec: ProtocolSpec, kind: str):
    """kind = 'ciphertext' or 'original'"""
    def handler(group_id, role):
        try:
            from app import Config
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            group = spec.manager_cls.get_group(group_id)
            if not group:
                return jsonify({'error': '小组不存在'}), 404
            if username not in group['members']:
                return jsonify({'error': '你不是该小组成员'}), 403
            # 权限:只能下载自己的 role
            my_role = 'receiver' if username == group['creator'] else 'sender'
            if role != my_role:
                return jsonify({'error': f'权限不足:只能下载自己角色 ({my_role}) 的文件'}), 403
            data_dir = os.path.join(getattr(Config, spec.upload_data_dir_attr), f"group_{group_id}")
            if kind == 'ciphertext':
                fname = f"{role}_ciphertext.txt"
            else:  # original
                fname = f"original_{role}.txt"
            fpath = os.path.join(data_dir, fname)
            if not os.path.exists(fpath):
                # 兜底:从归档读
                rounds = group.get('rounds', [])
                for r in reversed(rounds):
                    af = r.get('archive_files', {})
                    key = fname.replace('.txt', '')
                    af_path = af.get(key)
                    if af_path and os.path.exists(af_path):
                        fpath = af_path
                        break
                else:
                    return jsonify({'error': '文件不存在'}), 404
            return send_file(fpath, as_attachment=True)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


# ==================== Demo Handlers ====================

def _psi_sum_demo_handler():
    """GET /api/psi-sum-demo(无认证)"""
    return jsonify({
        'success': True,
        'plaintext': [f'用户A{str(i).zfill(3)}' for i in range(1, 21)],
        'ciphertext': [f'{hashlib.md5(str(i).encode()).hexdigest()[:16]}' for i in range(1, 21)],
        'result': {'cardinality': 8, 'sum': 12345},
        'note': 'PSI-Sum 演示模式 (mock 数据, 不真运算)'
    })


import hashlib  # noqa


def _ss_psi_demo_handler():
    """GET /api/ss-psi-demo(无认证)"""
    return jsonify({
        'success': True,
        'plaintext': [f'user_blacklisted_{str(i).zfill(3)}' for i in range(1, 21)],
        'ciphertext': [f'{hashlib.md5(("ss_"+str(i)).encode()).hexdigest()[:16]}' for i in range(1, 21)],
        'result': {'cardinality': 3, 'intersection': ['user_blacklisted_023', 'user_blacklisted_089', 'user_blacklisted_142']},
        'note': 'SS-PSI 演示模式 (mock 数据, 不真运算)'
    })