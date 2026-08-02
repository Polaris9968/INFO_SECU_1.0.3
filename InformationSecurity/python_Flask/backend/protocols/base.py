# protocols/base.py — 1.0.3 协议抽象基类
"""
BaseGroupManager / KunlunRunner / ProtocolSpec

设计原则:
1. 每个方法从原 app.py 真实代码 copy 后参数化(不发明新逻辑)
2. 协议特定差异走 hook(file_type_map / archive_filenames / parse_result)
3. 多轮历史是可选能力(PSI-Card / SS-PSI 不支持)
"""
import os
import shutil
import subprocess
import time
from datetime import datetime
from functools import wraps


# ==================== BaseGroupManager ====================
class BaseGroupManager:
    """协议组的通用 CRUD 基类。子类声明 file_path / id_length / max_members / supports_history 等元数据。"""

    # === 子类必须声明的元数据 ===
    file_path: str = ''           # Config 上的文件路径
    id_length: int = 4            # group id 长度
    max_members: int = 2          # 小组人数上限

    # === 多轮历史(默认不启用,PSI/PSU/PSI-Match/PSI-Sum 启用)===
    supports_history: bool = False
    archive_filenames: tuple = ()        # finalize_round 时归档的文件列表
    stale_filenames: tuple = ()          # 归档后顶层要删的 stale 文件
    upload_filenames_to_clear: tuple = ()  # uploaded_<role>.<ext> 模式

    # === 协议特定 result 字段(写入 group dict 的 key)===
    result_field: str = 'result'         # 'psi_result' / 'union_result' / 'cardinality_result' / 'sum_result' / 'result'

    # === file_type → archive_files key 映射 ===
    # 由子类覆盖,base 提供默认
    file_type_map: dict = {}

    # === Hook:协议特定字段(get_group extras)===
    # 默认空,子类覆盖
    @classmethod
    def get_group_extras(cls, group, username):
        return {}

    # === Hook:finalize_round 中是否生成 _with_original 文件 ===
    # PSI/PSU 返回 True(非数字 token 需要 reverse_map),PSI-Match/PSI-Card 返回 False
    generate_with_original: bool = False

    # ---------- 持久化 ----------
    @classmethod
    def load_groups(cls):
        # 2026-07-08:从 app.py PSIGroupManager.load_groups() 参数化(file_path + cls.)
        from app import load_json_file
        data = load_json_file(cls.file_path, {"groups": []})
        if "groups" not in data:
            data["groups"] = []
        return data

    @classmethod
    def save_groups(cls, data):
        from app import save_json_file
        save_json_file(cls.file_path, data)

    @classmethod
    def generate_group_id(cls):
        from app import generate_id
        return generate_id(cls.id_length)

    # ---------- Group lifecycle ----------
    @classmethod
    def create_group(cls, group_name, creator, standardize_mode='auto'):
        """默认实现。子类覆盖 (SS-PSI 加 expected_parties)"""
        data = cls.load_groups()
        group_id = cls.generate_group_id()
        group_data = {
            'id': group_id,
            'name': group_name,
            'creator': creator,
            'members': [creator],
            'uploads': [],
            'rounds': [],
            'standardize_mode': standardize_mode,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        data["groups"].append(group_data)
        cls.save_groups(data)
        return group_data

    @classmethod
    def get_group(cls, group_id):
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                if 'rounds' not in group:
                    group['rounds'] = []
                return group
        return None

    @classmethod
    def add_member(cls, group_id, username):
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                if username in group["members"]:
                    return False, "你已经是该小组成员"
                if len(group["members"]) >= cls.max_members:
                    return False, f"小组已满(最多{cls.max_members}人)"
                group["members"].append(username)
                cls.save_groups(data)
                return True, "加入小组成功"
        return False, "小组不存在"

    @classmethod
    def remove_member(cls, group_id, username):
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                if username in group["members"]:
                    group["members"].remove(username)
                    group["uploads"] = [u for u in group["uploads"] if u["username"] != username]
                    cls.save_groups(data)
                    return True
        return False

    @classmethod
    def delete_group(cls, group_id):
        data = cls.load_groups()
        for i, group in enumerate(data["groups"]):
            if group["id"] == group_id:
                del data["groups"][i]
                cls.save_groups(data)
                return True
        return False

    @classmethod
    def get_user_groups(cls, username):
        data = cls.load_groups()
        result = []
        for group in data["groups"]:
            if username in group["members"]:
                result.append({
                    'id': group['id'],
                    'name': group['name'],
                    'creator': group['creator'],
                    'member_count': len(group['members']),
                    'created_at': group['created_at']
                })
        return result

    # ---------- Upload lifecycle ----------
    @classmethod
    def add_upload(cls, group_id, username, items, **kwargs):
        """默认实现:PSI 风格。子类 override (PSI-Sum 加 values, SS-PSI 加 expected_parties 检查)"""
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                group["uploads"] = [u for u in group["uploads"] if u["username"] != username]
                upload = {
                    'username': username,
                    'numbers': items,
                    'original_items': kwargs.get('original_items') or items,
                    'standardize_mode': kwargs.get('standardize_mode', 'auto'),
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'count': len(items)
                }
                # PSI-Sum 注入 values
                if 'values' in kwargs and kwargs['values'] is not None:
                    upload['values'] = kwargs['values']
                group["uploads"].append(upload)
                cls.save_groups(data)
                return True
        return False

    @classmethod
    def remove_user_upload(cls, group_id, username):
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                original_count = len(group["uploads"])
                group["uploads"] = [u for u in group["uploads"] if u["username"] != username]
                if len(group["uploads"]) < original_count:
                    cls.save_groups(data)
                    return True
        return False

    # ---------- Result 保存 ----------
    @classmethod
    def save_result(cls, group_id, result):
        """统一签名:把 result dict 写入 group[result_field]"""
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                group[cls.result_field] = result
                cls.save_groups(data)
                return True
        return False

    # ---------- 多轮历史:finalize_round ----------
    @classmethod
    def finalize_round(cls, group_id, completed_by):
        """
        通用 finalize_round 骨架。
        PSI/PSU/PSI-Match/PSI-Sum 都能用,差异由:
        - cls.archive_filenames:归档哪些文件
        - cls.stale_filenames:顶层删哪些 stale 文件
        - cls.upload_filenames_to_clear:uploaded_<role>.<ext> 模式
        - cls.generate_with_original:是否生成 _with_original 文件(PSI/PSU True,Match/Card False)
        """
        from app import Config, _build_reverse_map
        if not cls.supports_history:
            return False, "该协议不支持多轮历史"

        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] != group_id:
                continue
            if 'rounds' not in group:
                group['rounds'] = []

            # 协议特定数据目录:用 Config 上的 SPSO_xxx_DATA_DIR 属性
            data_dir_attr = getattr(cls, 'data_dir_attr', None)
            if data_dir_attr is None:
                return False, "协议未声明 data_dir_attr"
            kunlun_dir = os.path.join(getattr(Config, data_dir_attr), f"group_{group_id}")

            round_num = len(group['rounds']) + 1
            archive_dir = os.path.join(kunlun_dir, f"round{round_num}")
            os.makedirs(archive_dir, exist_ok=True)
            archive_files = {}

            # 1. 归档 archive_filenames 列表中的文件
            for fname in cls.archive_filenames:
                src = os.path.join(kunlun_dir, fname)
                if os.path.exists(src):
                    dst = os.path.join(archive_dir, fname)
                    shutil.copy2(src, dst)
                    archive_files[fname.replace('.txt', '')] = dst

            # 2. 读 result 文件
            result_data = cls._read_finalized_result(archive_files, kunlun_dir, group)

            # 3. (可选)生成 _with_original 文件(PSI/PSU 的 reverse map)
            if cls.generate_with_original and result_data.get('intersection_or_values'):
                reverse_map = _build_reverse_map(group, group.get('standardize_mode', 'auto'))
                with_orig_path = os.path.join(archive_dir, cls._with_original_filename())
                with open(with_orig_path, 'w', encoding='utf-8') as f:
                    for v in result_data['intersection_or_values']:
                        f.write((reverse_map.get(v) or v) + '\n')
                archive_files[cls._with_original_key()] = with_orig_path

            # 4. 写 round_record(协议特定字段由 hook 提供)
            uploads_snapshot = cls._extract_uploads_snapshot(group)
            result_summary = result_data['summary']
            # 协议特定:PSI-Sum result 需要从 archive_files 加 cardinality/sum
            if cls.result_summary_extra:
                result_summary = cls.result_summary_extra(archive_files, result_summary)
            comp_seconds, comp_human = cls._extract_computation_timing(group)
            round_record = {
                'round': round_num,
                'completed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'completed_by': completed_by,
                'uploads': uploads_snapshot,
                'archive_dir': archive_dir,
                'archive_files': archive_files,
                'result': result_summary,
                'computation_seconds': comp_seconds,
                'computation_human': comp_human,
            }
            group['rounds'].append(round_record)

            # 5. 清空当前 uploads
            group['uploads'] = []

            # 6. 删顶层 stale 文件
            for stale_fname in cls.stale_filenames:
                stale_path = os.path.join(kunlun_dir, stale_fname)
                if os.path.exists(stale_path):
                    os.remove(stale_path)

            # 7. 删 uploaded_<role>.<ext> 三种后缀
            for role in ('receiver', 'sender'):
                for ext in ('txt', 'csv', 'json'):
                    p = os.path.join(kunlun_dir, f'uploaded_{role}.{ext}')
                    if os.path.exists(p):
                        os.remove(p)

            # 8. 协议特定清理(PSI-Sum 清 sum_result)
            cls._post_finalize_cleanup(group)

            cls.save_groups(data)

            # 9. 清掉 pending_computation + 当前轮结果字段(round_record 已写入,
            #    避免下一轮 UI 残留上一轮数据: 2026-08-02 PSI-Match 下一轮后
            #    交集基数/缺失数/子集判断不消失, SS-PSI 旧 share 残留)
            for _key in ('pending_computation', 'subset_result', 'result'):
                group.pop(_key, None)
            cls.save_groups(data)

            return True, round_record
        return False, "小组不存在"

    @classmethod
    def _extract_uploads_snapshot(cls, group):
        """默认: PSI 风格 {username: numbers list}"""
        return {
            u['username']: u.get('numbers', [])
            for u in group.get('uploads', [])
        }

    @classmethod
    def _extract_computation_timing(cls, group):
        """默认: 从 pending_computation 读"""
        pending = group.get('pending_computation') or {}
        return pending.get('duration_seconds'), pending.get('duration_human')

    @classmethod
    def _post_finalize_cleanup(cls, group):
        """默认空,子类 override(PSI-Sum 清 sum_result)"""
        pass

    # 协议特定 result 补充:PSI-Sum 需要从 archive_files 抽 cardinality/sum
    result_summary_extra = None

    @classmethod
    def _read_finalized_result(cls, archive_files, kunlun_dir, group):
        """
        协议特定:读 finalize_round 时的 result。
        默认:读 intersection.txt。
        子类 override (PSI-Card 读 cardinality.txt, PSI-Sum 读 cardinality+sum, PSU 读 union.txt)。
        """
        result = {'intersection_or_values': [], 'summary': {}}
        if 'intersection' in archive_files:
            try:
                with open(archive_files['intersection'], 'r', encoding='utf-8') as f:
                    intersection = [line.strip() for line in f if line.strip()]
                result['intersection_or_values'] = intersection
                result['summary'] = {'type': 'intersection', 'count': len(intersection)}
            except Exception:
                pass
        return result

    @classmethod
    def _with_original_filename(cls):
        return 'intersection_with_original.txt'

    @classmethod
    def _with_original_key(cls):
        return 'intersection_with_original'

    # ---------- 多轮历史:get_history ----------
    @classmethod
    def get_history(cls, group_id, username):
        if not cls.supports_history:
            return None
        group = cls.get_group(group_id)
        if not group:
            return None
        history = []
        for r in group.get('rounds', []):
            history.append({
                'round': r['round'],
                'completed_at': r['completed_at'],
                'completed_by': r['completed_by'],
                'my_upload_count': len(r['uploads'].get(username, [])),
                'result': r['result'],
                'is_receiver': r['uploads'].get(username, []) != [],
                'computation_seconds': r.get('computation_seconds'),
                'computation_human': r.get('computation_human')
            })
        return history

    # ---------- 多轮历史:get_round_data ----------
    @classmethod
    def get_round_data(cls, group_id, round_num, file_type, username):
        """
        通用:返回归档文件路径。
        file_type 解释依赖 cls.file_type_map(子类声明)。
        """
        if not cls.supports_history:
            return None, "该协议不支持多轮历史"

        group = cls.get_group(group_id)
        if not group:
            return None, "小组不存在"
        rounds = group.get('rounds', [])
        if round_num < 1 or round_num > len(rounds):
            return None, "轮次不存在"
        r = rounds[round_num - 1]
        archive_files = r.get('archive_files', {})

        # file_type 处理
        if file_type in cls.file_type_map:
            # 协议特定映射:大多数需要 role
            role = 'receiver' if username == group['creator'] else 'sender'
            key_fn = cls.file_type_map[file_type]
            if key_fn is None:
                return None, f"该协议不支持 file_type={file_type}"

            # 调用 key_fn(签名可能是 lambda role 或 lambda role, **kw)
            import inspect
            try:
                sig = inspect.signature(key_fn)
                params = list(sig.parameters.keys())
                if 'role' in params:
                    key = key_fn(role=role)
                else:
                    key = key_fn()
            except Exception:
                key = key_fn()

            fpath = archive_files.get(key)
            if not fpath or not os.path.exists(fpath):
                return None, f"文件不存在(key={key})"
            return fpath, None

        return None, f"未知文件类型:{file_type}"


# ==================== BaseRunner (2026-07-31 改名 KunlunRunner → BaseRunner) ====================
class BaseRunner:
    """PSI 协议 runner 基类。所有 Spso* / Mock runner 继承此 (2026-07-31 改名 KunlunRunner)。"""

    # === 子类必须声明 ===
    receiver_exec: str = ''
    sender_exec: str = ''
    data_dir_attr: str = ''
    result_filenames: tuple = ()
    log_tag: str = 'BaseRunner'

    # === 协议特定 ===
    spawn_order: str = 'receiver_first'   # PSI-Sum = 'sender_first'
    kind: str = 'kunlun'                  # SS-PSI = 'mock'

    @classmethod
    def run(cls, group_id):
        if cls.kind == 'mock':
            return cls._run_mock(group_id)
        return cls._run_mock(group_id)  # 2026-07-31: _run_kunlun() 死代码已删, 默认 fallthrough 到 _run_mock

    @classmethod
    def parse_result(cls, *result_texts):
        """协议特定解析。默认:第一文件按行 split 成 list
        多 result_filenames:仅处理第 1 个(子类 override 处理多结果)
        """
        text = result_texts[0]
        items = [line.strip() for line in text.split('\n') if line.strip()]
        if cls.result_filenames == ('cardinality.txt',):
            return {'cardinality': int(text.strip() or 0)}
        if cls.result_filenames == ('union.txt',):
            return {'union': items, 'count': len(items)}
        # PSI 默认
        return {'intersection': items, 'count': len(items)}

    @classmethod
    def _run_mock(cls, group_id):
        """默认 mock 实现(子类覆盖)"""
        return {'success': True, 'mock': True}


# ==================== ProtocolSpec ====================
class ProtocolSpec:
    """协议配置 dict,驱动路由工厂"""

    def __init__(self,
                 protocol_id: str,
                 url_prefix: str,
                 manager_cls,
                 page_filename: str,
                 upload_data_dir_attr: str,
                 id_length: int = 4,
                 max_members: int = 2,
                 runner_cls=None,
                 is_mock: bool = False,
                 # 端点开关
                 has_history: bool = False,
                 has_preview_ciphertext: bool = False,
                 has_download_result: bool = False,
                 has_download_result_with_original: bool = False,
                 has_download_ciphertext_by_role: bool = False,
                 has_download_original_by_role: bool = False,
                 has_download_round: bool = False,
                 has_demo_endpoint: bool = False,
                 finalize_round_endpoint: bool = False,
                 start_computation_endpoint: bool = True,
                 # 端点裁剪
                 supports_leave: bool = True,
                 supports_remove_upload: bool = True,
                 # 认证
                 auth_method: str = 'jwt_required',  # 'jwt_required' / 'login_required_api' / 'none'
                 # Hook
                 get_group_extras=None,
                 pre_upload_hook=None,
                 post_upload_hook=None,
                 start_computation_validator=None,
                 ):
        self.protocol_id = protocol_id
        self.url_prefix = url_prefix
        self.manager_cls = manager_cls
        self.page_filename = page_filename
        self.upload_data_dir_attr = upload_data_dir_attr
        self.id_length = id_length
        self.max_members = max_members
        self.runner_cls = runner_cls
        self.is_mock = is_mock
        self.has_history = has_history
        self.has_preview_ciphertext = has_preview_ciphertext
        self.has_download_result = has_download_result
        self.has_download_result_with_original = has_download_result_with_original
        self.has_download_ciphertext_by_role = has_download_ciphertext_by_role
        self.has_download_original_by_role = has_download_original_by_role
        self.has_download_round = has_download_round
        self.has_demo_endpoint = has_demo_endpoint
        self.finalize_round_endpoint = finalize_round_endpoint
        self.start_computation_endpoint = start_computation_endpoint
        self.supports_leave = supports_leave
        self.supports_remove_upload = supports_remove_upload
        self.auth_method = auth_method
        self.get_group_extras = get_group_extras or (lambda g, u: {})
        self.pre_upload_hook = pre_upload_hook
        self.post_upload_hook = post_upload_hook
        self.start_computation_validator = start_computation_validator

    def __repr__(self):
        return f'<ProtocolSpec {self.protocol_id}>'

# ==================== 兼容旧 _login_required_api ====================
def _login_required_api():
    """
    检查 JWT,返回 (username, error_response)。
    原 app.py line 5040(SS-PSI 段),搬到这里供 factory 复用。
    """
    from flask import request as _req, jsonify as _jf
    auth = _req.headers.get('Authorization', '')
    token = auth.replace('Bearer ', '') if auth.startswith('Bearer ') else auth
    if not token:
        return None, (_jf({'error': '未登录'}), 401)
    # TokenManager 在 app.py 里 — 延迟 import 避免循环
    from app import TokenManager
    payload = TokenManager.verify_token(token)
    if not payload:
        return None, (_jf({'error': 'token 无效或过期'}), 401)
    return payload['username'], None
