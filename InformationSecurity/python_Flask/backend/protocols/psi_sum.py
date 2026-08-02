# protocols/psi_sum.py — PSI-Sum 协议
import os
from datetime import datetime
from app import Config, _standardize_token
from .base import BaseGroupManager, BaseRunner

class PSISumGroupManager(BaseGroupManager):
    file_path = Config.PSI_SUM_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = True
    result_field = 'sum_result'
    data_dir_attr = 'SPSO_PSI_SUM_DATA_DIR'

    archive_filenames = (
        'receiver.txt', 'sender.txt',
        'original_receiver.txt', 'original_sender.txt',
        'value_receiver.txt', 'value_sender.txt',
        'cardinality.txt', 'sum.txt',
        # 2026-08-02 哥要求: 历史加“我的密文”下载
        'receiver_ciphertext.txt', 'sender_ciphertext.txt',
    )
    stale_filenames = (
        'receiver.txt', 'sender.txt',
        'original_receiver.txt', 'original_sender.txt',
        'value_receiver.txt', 'value_sender.txt',
        'cardinality.txt', 'sum.txt',
        # 2026-08-02: ciphertext/OPRF 归档后也删 (下一轮密文预览不残留)
        'receiver_ciphertext.txt', 'sender_ciphertext.txt',
        'oprf_prf_recver.txt', 'oprf_prf_sender.txt',
    )

    generate_with_original = False  # PSI-Sum 不生成 _with_original

    file_type_map = {
        'my_plaintext':       lambda role, **kw: f'original_{role}',
        'my_value':           lambda role, **kw: f'value_{role}',
        # 2026-08-02 哥要求: 历史加“我的密文”下载
        'my_ciphertext':      lambda role, **kw: f'{role}_ciphertext',
        'result_cardinality': lambda role, **kw: 'cardinality',
        'result_sum':         lambda role, **kw: 'sum',
        # 2026-08-02 哥要求: 历史结果下载统一“基数+求和” (tuple → get_round_data 合并)
        'result_both':        lambda role, **kw: ('cardinality', 'sum'),
        # PSI-Sum 不支持 my_ciphertext
    }

    # === PSI-Sum 特有的 4 个 hook ===

    @classmethod

    # === PSI-Sum 专用 parser (2026-07-31) ===
    # 区别于 extract_items_from_file 的 re.findall(r'[^\s,;\n]+', content)
    # (会把 `alice,100` 拆成两个 token ['alice', '100'])
    # 这里手动按行解析,每行可选 `token,value` (value 为整数, 可选)
    @staticmethod
    def parse_csv_with_values(content, mode='auto'):
        """PSI-Sum CSV 解析:返回 (items, original_items, values)
        - items: standardized token list (走 _standardize_token)
        - original_items: 原始 token list (跟 items 一一对应)
        - values: int list (跟 items 一一对应, value 缺失 → 0)
        """
        items = []
        original_items = []
        values = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(',', 1)
            token_raw = parts[0].strip()
            value_raw = parts[1].strip() if len(parts) > 1 else ''
            if not token_raw:
                continue
            std = _standardize_token(token_raw, mode)
            if std is None:
                continue
            try:
                v = int(value_raw) if value_raw else 0
            except ValueError:
                try:
                    v = int(float(value_raw))
                except ValueError:
                    raise ValueError(f"value 必须为整数: '{value_raw}' (token='{token_raw}')")
            items.append(std)
            original_items.append(token_raw)
            values.append(v)
        return items, original_items, values

    # === PSI-Sum JSON parser (2026-08-02 重建: v1.1.3 的版本在 8-1 目录清理时丢失) ===
    # 自动探测 token/value 字段, 支持 3 种结构:
    #   1. list-of-dict: [{"token": "a", "value": 10}, ...]
    #   2. wrapper: {"items"|"data"|"records"|...: [...]} 或任意顶层 list
    #   3. 纯 token list: ["a", "b"] (value 全 0)
    # 显式 override: token_path / value_path (dotted path, 如 "user.id")
    @staticmethod
    def parse_json_with_values(content, mode='auto', token_path=None, value_path=None):
        import json as _json
        data = _json.loads(content)
        # 解 wrapper: dict 里找 list
        if isinstance(data, dict):
            for key in ('items', 'data', 'records', 'values', 'list', 'rows'):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                for _v in data.values():
                    if isinstance(_v, list):
                        data = _v
                        break
        if not isinstance(data, list):
            raise ValueError('JSON 顶层必须是数组或包含数组字段(items/data/records 等)')
        if not data:
            return [], [], []

        def _get_path(obj, path):
            cur = obj
            for _p in path.split('.'):
                if isinstance(cur, dict) and _p in cur:
                    cur = cur[_p]
                else:
                    return None
            return cur

        # 纯 token list: ["a", "b", 3]
        if all(not isinstance(x, (dict, list)) for x in data):
            items, original_items, values = [], [], []
            for tok in data:
                tok = str(tok).strip()
                std = _standardize_token(tok, mode)
                if std is None:
                    continue
                items.append(std)
                original_items.append(tok)
                values.append(0)
            return items, original_items, values

        token_keys = ['token', 'id', '_id', 'name', 'user_id', 'email', 'user', 'key']
        value_keys = ['value', 'amount', 'sum', 'weight', 'score', 'val', 'count', 'price', 'total']
        items, original_items, values = [], [], []
        for row in data:
            if not isinstance(row, dict):
                continue
            # ---- token ----
            tok = None
            if token_path:
                tok = _get_path(row, token_path)
            if tok is None:
                for _k in token_keys:
                    if _k in row and row[_k] is not None:
                        tok = row[_k]
                        break
            if tok is None:
                # 第一个非 value 字段的标量
                for _k, _v in row.items():
                    if _k in value_keys or _k == token_path:
                        continue
                    if isinstance(_v, (str, int, float)) and not isinstance(_v, bool):
                        tok = _v
                        break
            if tok is None:
                continue
            tok = str(tok).strip()
            std = _standardize_token(tok, mode)
            if std is None:
                continue
            # ---- value ----
            val = 0
            v_raw = None
            if value_path:
                v_raw = _get_path(row, value_path)
            if v_raw is None:
                for _k in value_keys:
                    if _k in row and row[_k] is not None:
                        v_raw = row[_k]
                        break
            if v_raw is None:
                # 第一个非 token 字段的数字
                for _k, _v in row.items():
                    if _k == token_path or (_k in token_keys and _k != token_path):
                        continue
                    if isinstance(_v, (int, float)) and not isinstance(_v, bool):
                        v_raw = _v
                        break
            if v_raw is not None:
                try:
                    val = int(v_raw)
                except (ValueError, TypeError):
                    try:
                        val = int(float(v_raw))
                    except (ValueError, TypeError):
                        raise ValueError(f"value 必须为整数: '{v_raw}' (token='{tok}')")
            items.append(std)
            original_items.append(tok)
            values.append(val)
        return items, original_items, values

    @classmethod
    def add_upload(cls, group_id, username, items, **kwargs):
        """PSI-Sum: items + values,必须等长
        2026-07-31: receiver (creator) 端的 value 被强制忽略 (协议设计: 只有 sender 提供 value)
        """
        values = kwargs.get('values')
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                if username not in group["members"]:
                    return False, "你不是该小组成员"
                is_receiver = (username == group['creator'])
                if is_receiver:
                    # receiver 端 value 强制忽略 (Kunlun PSI-Sum 协议只读 sender value)
                    values = None
                else:
                    # sender 端必须有 value
                    if values is not None and len(values) != len(items):
                        return False, f"value 数量 ({len(values)}) 必须与 set 元素数量 ({len(items)}) 一致"
                group["uploads"] = [u for u in group["uploads"] if u["username"] != username]
                group["uploads"].append({
                    'username': username,
                    'items': items,
                    'original_items': kwargs.get('original_items') or items,
                    'values': values,
                    'has_values': values is not None,
                    # 2026-08-02: value_count 改为非 0 value 数 (纯 token 文件不再提示
                    # “未传 value”, 前端只在有真实 value 时显示 +N 个 value)
                    'value_count': sum(1 for v in values if v != 0) if values is not None else 0,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'count': len(items),
                })
                cls.save_groups(data)
                return True, "上传成功"
        return False, "小组不存在"

    @classmethod
    def _extract_uploads_snapshot(cls, group):
        """PSI-Sum: snapshot 包含 items + values"""
        snapshot = {}
        for u in group.get('uploads', []):
            snapshot[u['username']] = {
                'items': u.get('items', []),
                'values': u.get('values'),
            }
        return snapshot

    @classmethod
    def _extract_computation_timing(cls, group):
        """PSI-Sum: 从 sum_result 读"""
        sr = group.get('sum_result') or {}
        return sr.get('duration_seconds'), sr.get('duration_human')

    @classmethod
    def _post_finalize_cleanup(cls, group):
        """PSI-Sum: 清空 sum_result"""
        group['sum_result'] = None

    @classmethod
    def save_result(cls, group_id, result):
        """PSI-Sum: 完整签名保存 sum_result 字段"""
        data = cls.load_groups()
        for group in data["groups"]:
            if group["id"] == group_id:
                group["sum_result"] = result
                cls.save_groups(data)
                return True
        return False

