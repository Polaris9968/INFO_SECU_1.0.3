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

            # 协议特定 extras
            if spec.protocol_id == 'psi':
                from app import read_intersection_from_file
                response_data['psi_result'] = read_intersection_from_file(group_id)
            elif spec.protocol_id == 'psi_card':
                from app import read_cardinality_from_file
                response_data['cardinality_result'] = read_cardinality_from_file(group_id)
            elif spec.protocol_id == 'psu':
                from app import read_union_from_file
                response_data['union_result'] = read_union_from_file(group_id)
            elif spec.protocol_id == 'psi_match':
                response_data['subset_result'] = group.get('subset_result')
                response_data['pending_computation'] = group.get('pending_computation')
            elif spec.protocol_id == 'psi_sum':
                response_data.update(_psi_sum_get_extras(group, username))
            elif spec.protocol_id == 'ss_psi':
                response_data['result'] = group.get('result')
                response_data['expected_parties'] = group.get('expected_parties', 4)
                response_data['joined_parties'] = len(group['members'])

            return jsonify(response_data)
        except Exception as e:
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
    kunlun_dir = os.path.join(Config.KUNLUN_PSI_SUM_DATA_DIR, f"group_{group_id}")
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
        os.path.join(kunlun_dir, f"value_{role}.txt"))
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

    app.add_url_rule(f'/api/my-{spec.protocol_id.replace("_", "-")}-groups',
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

        # PSI-Sum: 读 values 字段
        values = None
        if spec.protocol_id == 'psi_sum':
            values_raw = request.form.get('values', '').strip()
            if values_raw:
                try:
                    values = [int(v.strip()) for v in values_raw.split(',') if v.strip()]
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
                    return jsonify({'error': '请等组长(receiver)上传并选择 JSON path'}), 400
        else:
            json_path = None

        try:
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

        # PSI-Sum: 额外写 values 到 {role}_value.txt(Kunlun 二进制读这个)
        if spec.protocol_id == 'psi_sum' and values is not None:
            values_path = _os.path.join(kunlun_dir, f"{role}_value.txt")
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
            return jsonify({
                'success': True, 'message': msg,
                'count': len(items), 'mode': mode,
                'group_id': group_id, 'filename': file.filename
            })
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
                    if spec.protocol_id == 'psi_match':
                        g['subset_result'] = {
                            'cardinality': result.get('cardinality', 0),
                            'is_subset': result.get('is_subset', False),
                            'missing_count': result.get('missing_count', 0),
                            'matched_alice': result.get('matched_alice', []),
                            'matched_count': result.get('matched_count', 0),
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
            if username != group['creator']:
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
            return jsonify({'success': True, 'history': history})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return _auth_required(spec, handler)


def _make_round_download_handler(spec: ProtocolSpec):
    def handler(group_id, round_num):
        try:
            group_id = group_id.upper()
            username = _get_username_or_login(spec)[0]
            file_type = request.args.get('file_type', 'result')
            fpath, err = spec.manager_cls.get_round_data(group_id, round_num, file_type, username)
            if not fpath:
                return jsonify({'error': err}), 404
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
            }
            fname = fname_map.get(spec.protocol_id)
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
                # 找归档 round1
                group = spec.manager_cls.get_group(group_id)
                if not group:
                    return jsonify({'error': '小组不存在'}), 404
                rounds = group.get('rounds', [])
                if not rounds:
                    return jsonify({'error': '尚无归档轮次'}), 404
                last_round = rounds[-1]
                key = 'intersection_with_original' if spec.protocol_id == 'psi' else 'union_with_original'
                fpath = last_round.get('archive_files', {}).get(key)
                if not fpath or not os.path.exists(fpath):
                    return jsonify({'error': '原始版结果不存在(旧归档或尚未生成)'}), 404
                return send_file(fpath, as_attachment=True)
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