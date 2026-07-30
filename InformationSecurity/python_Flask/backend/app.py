#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Flask Web 应用后端
功能:用户注册/登录、JWT认证、文件上传、隐私求交
"""

import os
import json
import re
import uuid
import subprocess
import shutil
import time
import hashlib
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
import bcrypt
import jwt

# 加载 .env (dev 阶段用)。load_dotenv 默认 override=False,
# 已 export 的环境变量优先于 .env,所以 systemd 注入的 env 仍生效。
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))


# ==================== 配置 ====================
class Config:
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY env var is not set. "
            "Copy backend/.env.example to backend/.env and fill in a strong value, "
            "or: export JWT_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')"
        )
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRATION_HOURS = 24

    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'txt', 'csv', 'json'}

    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    USERS_FILE = os.path.join(BASE_DIR, 'data', 'users.json')
    GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'groups.json')
    PSI_GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'psi_groups.json')
    PSI_MATCH_GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'psi_match_groups.json')
    PSI_CARD_GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'psi_card_groups.json')
    PSI_UNION_GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'psi_union_groups.json')
    PSI_SUM_GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'psi_sum_groups.json')
    SS_PSI_GROUPS_FILE = os.path.join(BASE_DIR, 'data', 'ss_psi_groups.json')

    STATIC_FOLDER = os.path.join(os.path.dirname(BASE_DIR), 'frontend')

    # ==================== Kunlun 库路径(统一管理)====================
    # 所有 Kunlun 相关路径都从 KUNLUN_BASE 派生,未来迁移项目只改这里
    KUNLUN_BASE = "/root/projects/INFO_SECU_1.0.3/Kunlun"
    KUNLUN_BUILD_DIR = os.path.join(KUNLUN_BASE, "build")
    KUNLUN_DATA_DIR = os.path.join(KUNLUN_BASE, "PSO_data")
    KUNLUN_PSI_DATA_DIR = os.path.join(KUNLUN_DATA_DIR, "PSI_data")
    KUNLUN_PSI_CARD_DATA_DIR = os.path.join(KUNLUN_DATA_DIR, "PSI_card_data")
    KUNLUN_PSI_UNION_DATA_DIR = os.path.join(KUNLUN_DATA_DIR, "PSI_union_data")
    KUNLUN_PSI_SUM_DATA_DIR = os.path.join(KUNLUN_DATA_DIR, "PSI_sum_data")
    KUNLUN_SS_PSI_DATA_DIR = os.path.join(KUNLUN_DATA_DIR, "SS_PSI_data")

    # ==================== 服务器配置 ====================
    HOST = "0.0.0.0"
    PORT = 5004  # 1.0.3 端口,避开 1.0.2 (5003) 和 1.0.1 (5002)
    DEBUG = True


# ==================== Flask 应用初始化 ====================
STATIC_FOLDER_ABS = os.path.abspath(Config.STATIC_FOLDER)
print(f"[DEBUG] 静态文件夹绝对路径: {STATIC_FOLDER_ABS}")
print(f"[DEBUG] 静态文件夹存在: {os.path.exists(STATIC_FOLDER_ABS)}")

app = Flask(__name__)
CORS(app)
app.config.from_object(Config)

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(Config.USERS_FILE), exist_ok=True)
os.makedirs(Config.STATIC_FOLDER, exist_ok=True)

# ==================== 速率限制 ====================
# 防暴力破解登录 / 防批量注册假账号 / 防 DoS
limiter = Limiter(
    key_func=get_remote_address,  # 按客户端 IP 限速
    app=app,
    default_limits=[],            # 不设全局默认,只对关键端点单独限速
    headers_enabled=True,         # 响应头返回 X-RateLimit-* 给客户端
    storage_uri="memory://",      # 默认内存存储,重启清零(单机够用)
)


# 自定义 429 响应:返回 JSON 而不是默认 HTML
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': f'请求过于频繁,请稍后再试 ({e.description})'}), 429

# ==================== 路径配置 ====================
# Kunlun 路径统一在 Config 类里(KUNLUN_BASE / KUNLUN_BUILD_DIR / KUNLUN_PSI_*_DATA_DIR)

# ==================== 辅助函数 ====================
def load_json_file(filepath, default=None):
    if default is None:
        default = {}
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return default


def save_json_file(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_id(length=6):
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def extract_numbers_from_text(text):
    """从文本中提取所有数字(支持整数、小数、负数)"""
    pattern = r'-?\d+\.?\d*'
    matches = re.findall(pattern, text)
    numbers = []
    for match in matches:
        try:
            num = float(match)
            numbers.append(num)
        except ValueError:
            continue
    return numbers


def _standardize_token(item, mode):
    """单个 token 标准化(供 extract_items_from_file 和 reverse map 复用)。"""
    item = item.strip()
    if not item:
        return None
    if mode == 'auto':
        # 数字直接用,文字/中文/特殊字符走 SHA-256;范围 [-10^15, 10^15] 闭区间
        try:
            n = int(item)
            if -10**15 <= n <= 10**15:
                return str(n)
        except (ValueError, TypeError):
            pass
        h = hashlib.sha256(item.encode('utf-8')).digest()[:16]
        return str(int.from_bytes(h, 'big'))
    elif mode == 'number_only':
        try:
            n = int(item)
            if -10**15 <= n <= 10**15:
                return str(n)
        except (ValueError, TypeError):
            pass
        return None
    elif mode == 'text_all':
        h = hashlib.sha256(item.encode('utf-8')).digest()[:16]
        return str(int.from_bytes(h, 'big'))
    else:
        # fallback 当 auto
        try:
            n = int(item)
            if -10**15 <= n <= 10**15:
                return str(n)
        except (ValueError, TypeError):
            pass
        h = hashlib.sha256(item.encode('utf-8')).digest()[:16]
        return str(int.from_bytes(h, 'big'))


def _build_reverse_map(group, mode='auto'):
    """为 receiver (creator) 构建 std -> original 的 reverse map。
    用于结果列表里把 hash 显示成 receiver 自己上传的原始 token。
    """
    receiver_upload = next(
        (u for u in group.get('uploads', []) if u['username'] == group['creator']),
        None
    )
    if not receiver_upload:
        return {}
    reverse_map = {}
    for orig in receiver_upload.get('original_items', []):
        std = _standardize_token(orig, mode)
        if std and std not in reverse_map:
            reverse_map[std] = orig
    return reverse_map


def _probe_json_paths(content):
    """两阶段 JSON path 上传 - 阶段 1 扫描 JSON 结构。
    返回: [{display: "items.[].email", path: "email", type: "str", count: N, sample: "..."}]
    - display 是 UI 友好展示,如 "items.[].user.email";顶层数组是 "[].name"
    - path 是相对路径(去除 items / 顶层数组包裹),直接传给 _extract_by_path
    - 过滤掉元数据字段(description / meta / note / comment / _id / index)
    - 只支持 list-of-dict 结构;纯数组 / 嵌套 list 不展开
    2026-07-30 放宽:不再只认 items/data/records,识别任意顶层 list 字段 (优先级 items>data>records>其他)
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e.msg}")

    if isinstance(data, list):
        source = data
        wrapper = ''  # 顶层数组:不包裹层
    elif isinstance(data, dict):
        # 2026-07-30: 先试标准 wrapper,再 fallback 到任意顶层 list 字段
        STANDARD_WRAPPERS = ('items', 'data', 'records')
        wrapper = None
        for key in STANDARD_WRAPPERS:
            if key in data and isinstance(data[key], list):
                source = data[key]
                wrapper = key
                break
        if wrapper is None:
            list_keys = [k for k, v in data.items() if isinstance(v, list) and v]
            if not list_keys:
                raise ValueError("JSON 需要是数组,或包含数组字段的对象")
            # 取第一个非空 list 字段 (避免 None/空 list 误中)
            first_key = list_keys[0]
            source = data[first_key]
            wrapper = first_key
    else:
        raise ValueError("JSON 顶层必须是数组或对象")

    paths = []
    _probe_paths_recursive(source, prefix='', wrapper=wrapper, acc=paths, max_depth=3)
    return paths


# 2026-07-02 探测时跳过这些“元数据”字段 (description / id / _id 这种各 list 项都会有的辅助字段)
_META_KEY_HINTS = ('description', 'meta', '_meta', 'comment', 'note', 'remark', 'desc', 'tips')


def _probe_paths_recursive(items, prefix, wrapper, acc, max_depth=3):
    """扫描 list of dict 的结构,递归抽取所有叶子 path。
    - prefix: 当前路径前缀,如 "user.name" / "" (顶级)
    - wrapper: 顶层数组名(items/data/records/''),加在 display 前缀
    """
    if not items:
        return
    depth = prefix.count('.') + (0 if prefix == '' else 1)
    if depth >= max_depth:
        return
    if not isinstance(items, list):
        return
    # 只采样 dict 类型的元素
    dict_items = [it for it in items if isinstance(it, dict)]
    if not dict_items:
        # 全是 list/扁平 字符串/数字
        return

    for key in list(dict_items[0].keys()):
        # 收集所有该 key 的非 None 值及其类型
        values = [d.get(key) for d in dict_items]
        non_null = [v for v in values if v is not None]
        if not non_null:
            continue

        type_counts = {}
        for v in non_null:
            t = type(v).__name__
            type_counts[t] = type_counts.get(t, 0) + 1

        # 只考虑 叶子(str / int / float / bool) 类型的字段
        is_leaf = set(type_counts.keys()) <= {'str', 'int', 'float', 'bool'}
        is_pure_dict = set(type_counts.keys()) == {'dict'}

        if is_leaf:
            # 后端抽取要传的是相对 list 元素的 path: 拼 prefix + key
            actual_path = f"{prefix}.{key}" if prefix else key
            # 展示用: 脱首项 wrapper + "[]" 提示
            display_parts = []
            if wrapper:
                display_parts.append(wrapper)
            display_parts.append('[]')
            # 嵌套: prefix 里的每个层级也加 []
            if prefix:
                for part in prefix.split('.'):
                    display_parts.append(part)
                    display_parts.append('[]')
            display_parts.append(key)
            display_path = '.'.join(display_parts)

            # 2026-07-02 过滤“元数据”字段名
            is_meta = any(h == key.lower() for h in _META_KEY_HINTS)
            if is_meta:
                continue

            # 采样示例
            sample = str(non_null[0])[:60]
            acc.append({
                'display': display_path,    # "items.[].email" 或 "email"
                'path': actual_path,         # "email" (顶层数组) 或 "items.email"
                'type': next(iter(type_counts.keys())),
                'count': len(non_null),
                'sample': sample,
                'max_length': max((len(str(v)) for v in non_null[:10]), default=0),
            })
        elif is_pure_dict and depth + 1 < max_depth:
            # 全是 dict 字段 → 递归
            sub_dicts = [v for v in non_null if isinstance(v, dict)]
            if sub_dicts:
                new_prefix = f"{prefix}.{key}" if prefix else key
                _probe_paths_recursive(
                    sub_dicts,
                    prefix=new_prefix,
                    wrapper=wrapper,
                    acc=acc,
                    max_depth=max_depth
                )


def _extract_by_path(data_list, path):
    """按点分隔 path 提取每个 item 的字段值(不依赖第三方 JSONPath 库)。
    例: path="user.email" → 走 data[i]["user"]["email"]
    支持 dict 取 key 和 list 取 int index。
    """
    parts = path.split('.')
    result = []
    for idx, item in enumerate(data_list):
        cur = item
        try:
            for p in parts:
                if isinstance(cur, dict):
                    cur = cur[p]
                elif isinstance(cur, list):
                    cur = cur[int(p)]
                else:
                    raise KeyError(p)
            if cur is None:
                raise ValueError(f"item[{idx}] 路径 '{path}' 值为 null")
            result.append(str(cur))
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise ValueError(f"item[{idx}] 路径 '{path}' 走不通: {e}")
    return result


def _pick_top_list_field(data, prefer=('items', 'data', 'records')):
    """2026-07-30: 从 dict 中挑选一个 list 字段。
    优先顺序:items > data > records > 第一个非空 list 字段。
    返回 (source, key) 或 (None, None)。
    """
    for k in prefer:
        if k in data and isinstance(data[k], list):
            return data[k], k
    for k, v in data.items():
        if isinstance(v, list) and v:
            return v, k
    return None, None


def _parse_json_items(content, path=None):
    """解析 JSON 内容,返回原始 token 列表(不标准化,交给上层统一处理)。
    档 1:
        ["a", "b"]                     → ["a", "b"]
        {"items": ["a", "b"]}          → ["a", "b"]
        {"users": ["a", "b"]}          → ["a", "b"]  (2026-07-30 放宽,识别任意顶层 list 字段)
    档 2:
        {"path": "user.email",
         "data": [{"user": {"email": "a"}}, ...]}  → 走 path 提取
        {"path": "user.email",
         "items": [{"user": {"email": "a"}}, ...]}  → items 优先于 data
        {"path": "user.email",
         "users": [{"user": {"email": "a"}}, ...]}  → 2026-07-30 也识别 users
    2026-07-02:path 参数(可选)由调用方传入 ① 优先于 ② JSON 顶层字段
    2026-07-30:放宽 wrapper 识别 (items/data/records 或任意顶层 list 字段)
    失败:raise ValueError,给中文友好错误
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e.msg} (line {e.lineno} col {e.colno})")

    # 档 1a: 纯数组
    if isinstance(data, list):
        return [str(x) if x is not None else '' for x in data]

    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是数组或对象")

    # 档 2: path 模式 (2026-07-02: 调用方传入 path 优先于 JSON 顶层字段)
    effective_path = path if path is not None else data.get('path')
    if effective_path is not None:
        if not isinstance(effective_path, str) or not effective_path.strip():
            raise ValueError("path 字段必须是非空字符串")
        source, key = _pick_top_list_field(data)
        if source is None:
            raise ValueError("path 模式下需要 items/data/records 或其他顶层 list 字段")
        if not isinstance(source, list):
            raise ValueError(f"{key} 字段必须是数组")
        return _extract_by_path(source, effective_path)

    # 档 1b: 任意顶层 list 字段 (2026-07-30 放宽)
    source, key = _pick_top_list_field(data)
    if source is not None:
        return [str(x) if x is not None else '' for x in source]

    raise ValueError("对象需要顶层数组字段 (items/data/records 或其他任意 list 字段)")


def extract_items_from_file(content, filename, mode='auto', path=None):
    """统一文件解析入口:按后缀分发(.json / 其他)。
    2026-07-02 加 path 参数:透传给 _parse_json_items,用于接收方自定义 JSON path。
    返回: (standardized_items, original_items)
    """
    if filename and filename.lower().endswith('.json'):
        try:
            raw_items = _parse_json_items(content, path=path)
        except ValueError as e:
            raise ValueError(str(e))
    else:
        raw_items = re.findall(r'[^\s,;\n]+', content)

    if not raw_items:
        return [], []

    std_items = []
    for item in raw_items:
        s = _standardize_token(item, mode)
        if s is not None:
            std_items.append(s)
    return std_items, raw_items


def calculate_statistics(numbers):
    """计算统计信息"""
    if not numbers:
        return {}
    return {
        'count': len(numbers),
        'min': min(numbers),
        'max': max(numbers),
        'average': sum(numbers) / len(numbers) if numbers else 0,
        'sum': sum(numbers)
    }


def read_intersection_from_file(group_id):
    """从 intersection.txt 读取交集结果"""
    psi_data_dir = Config.KUNLUN_PSI_DATA_DIR
    group_dir = os.path.join(psi_data_dir, f"group_{group_id}")
    result_file = os.path.join(group_dir, "intersection.txt")

    if not os.path.exists(result_file):
        return []

    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()

    intersection = []
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        # 2026-07-30 防御:跳过 SPIKE 2 sPSO padding sentinel
        if line.startswith('__spike2_pad_'):
            continue
        try:
            if '.' in line:
                num = float(line)
                if num.is_integer():
                    intersection.append(int(num))
                else:
                    intersection.append(num)
            else:
                intersection.append(int(line))
        except ValueError:
            intersection.append(line)

    return intersection

def read_cardinality_from_file(group_id):
    """从 cardinality.txt 读取交集基数"""
    result_file = os.path.join(Config.KUNLUN_PSI_CARD_DATA_DIR, f"group_{group_id}", "cardinality.txt")

    if not os.path.exists(result_file):
        return None

    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()

    try:
        return int(content)
    except ValueError:
        return None


def read_sum_from_file(group_id):
    """从 PSI-Sum group_dir / sum.txt 读取关联求和值(以字符串返回，避免 BigInt 转 Number 丢精度)"""
    result_file = os.path.join(Config.KUNLUN_PSI_SUM_DATA_DIR, f"group_{group_id}", "sum.txt")
    if not os.path.exists(result_file):
        return None
    with open(result_file, 'r', encoding='latin-1') as f:
        return f.read().strip() or None


def read_psi_sum_cardinality_from_file(group_id):
    """从 PSI-Sum group_dir / cardinality.txt 读取交集基数(由 receiver 写)"""
    result_file = os.path.join(Config.KUNLUN_PSI_SUM_DATA_DIR, f"group_{group_id}", "cardinality.txt")
    if not os.path.exists(result_file):
        return None
    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()
    try:
        return int(content)
    except ValueError:
        return None


def read_union_from_file(group_id):
    """从 union.txt 读取并集结果"""
    result_file = os.path.join(Config.KUNLUN_PSI_UNION_DATA_DIR, f"group_{group_id}", "union.txt")

    if not os.path.exists(result_file):
        return []

    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()

    union_result = []
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        # 2026-07-30 防御:跳过 SPIKE 2 sPSO padding sentinel (eg. __spike2_pad_sender_24)
        # Kunlun 不知道 padding 概念,会把 sentinel 当 token 写入 result 文件。
        # 根治要等 sPSO padding 不污染 input。
        if line.startswith('__spike2_pad_'):
            continue
        try:
            if '.' in line:
                num = float(line)
                if num.is_integer():
                    union_result.append(int(num))
                else:
                    union_result.append(num)
            else:
                union_result.append(int(line))
        except ValueError:
            union_result.append(line)

    return union_result

# ==================== Kunlun PSI 调用 ====================
def run_kunlun_psi(group_id):
    """调用 Kunlun 可执行文件执行 PSI 计算"""
    kunlun_build_dir = Config.KUNLUN_BUILD_DIR
    receiver_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psi_receiver")
    sender_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psi_sender")

    psi_data_dir = Config.KUNLUN_PSI_DATA_DIR
    group_dir = os.path.join(psi_data_dir, f"group_{group_id}")
    os.makedirs(group_dir, exist_ok=True)

    result_file = os.path.join(group_dir, "intersection.txt")

    if not os.path.exists(receiver_exec):
        return {'success': False, 'error': f'接收方可执行文件不存在: {receiver_exec}'}
    if not os.path.exists(sender_exec):
        return {'success': False, 'error': f'发送方可执行文件不存在: {sender_exec}'}

    if os.path.exists(result_file):
        os.remove(result_file)

    print(f"[Kunlun] 启动接收方进程... (group: {group_id})")
    receiver_proc = subprocess.Popen(
        [receiver_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    time.sleep(1.5)

    print(f"[Kunlun] 启动发送方进程... (group: {group_id})")
    sender_proc = subprocess.Popen(
        [sender_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    try:
        sender_stdout, sender_stderr = sender_proc.communicate(timeout=300)
        receiver_stdout, receiver_stderr = receiver_proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        sender_proc.kill()
        receiver_proc.kill()
        return {'success': False, 'error': 'PSI 计算超时(超过300秒)'}

    if sender_proc.returncode != 0:
        print(f"[Kunlun] 发送方错误: {sender_stderr}")
        return {'success': False, 'error': f'发送方执行失败: {sender_stderr}'}

    if receiver_proc.returncode != 0:
        print(f"[Kunlun] 接收方错误: {receiver_stderr}")
        return {'success': False, 'error': f'接收方执行失败: {receiver_stderr}'}

    if not os.path.exists(result_file):
        return {'success': False, 'error': '结果文件未生成'}

    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()

    intersection = []
    for line in content.split('\n'):
        line = line.strip()
        if line:
            try:
                if '.' in line:
                    num = float(line)
                    if num.is_integer():
                        intersection.append(int(num))
                    else:
                        intersection.append(num)
                else:
                    intersection.append(int(line))
            except ValueError:
                intersection.append(line)

    print(f"[Kunlun] PSI 计算完成,交集大小: {len(intersection)}")

    return {
        'success': True,
        'intersection': intersection,
        'count': len(intersection)
    }

# ==================== Kunlun PSI_card 调用 ====================
def run_kunlun_psi_card(group_id):
    """调用 Kunlun 可执行文件执行 PSI-Card 计算(交集基数)"""
    kunlun_build_dir = Config.KUNLUN_BUILD_DIR
    receiver_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psi_card_receiver")
    sender_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psi_card_sender")

    psi_card_data_dir = Config.KUNLUN_PSI_CARD_DATA_DIR
    group_dir = os.path.join(psi_card_data_dir, f"group_{group_id}")
    os.makedirs(group_dir, exist_ok=True)

    result_file = os.path.join(group_dir, "cardinality.txt")

    if not os.path.exists(receiver_exec):
        return {'success': False, 'error': f'接收方可执行文件不存在: {receiver_exec}'}
    if not os.path.exists(sender_exec):
        return {'success': False, 'error': f'发送方可执行文件不存在: {sender_exec}'}

    if os.path.exists(result_file):
        os.remove(result_file)

    print(f"[Kunlun-Card] 启动接收方进程... (group: {group_id})")
    receiver_proc = subprocess.Popen(
        [receiver_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    time.sleep(1.5)

    print(f"[Kunlun-Card] 启动发送方进程... (group: {group_id})")
    sender_proc = subprocess.Popen(
        [sender_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    try:
        sender_stdout, sender_stderr = sender_proc.communicate(timeout=300)
        receiver_stdout, receiver_stderr = receiver_proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        sender_proc.kill()
        receiver_proc.kill()
        return {'success': False, 'error': 'PSI-Card 计算超时(超过300秒)'}

    if sender_proc.returncode != 0:
        print(f"[Kunlun-Card] 发送方错误: {sender_stderr}")
        return {'success': False, 'error': f'发送方执行失败: {sender_stderr}'}

    if receiver_proc.returncode != 0:
        print(f"[Kunlun-Card] 接收方错误: {receiver_stderr}")
        return {'success': False, 'error': f'接收方执行失败: {receiver_stderr}'}

    if not os.path.exists(result_file):
        return {'success': False, 'error': '结果文件未生成'}

    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()

    try:
        cardinality = int(content)
    except ValueError:
        return {'success': False, 'error': f'无法解析基数结果: {content}'}

    print(f"[Kunlun-Card] PSI-Card 计算完成,交集基数: {cardinality}")

    return {
        'success': True,
        'cardinality': cardinality
    }

# ==================== Kunlun PSU 调用 ====================
def run_kunlun_psu(group_id):
    """调用 Kunlun 可执行文件执行 PSU 计算(并集)"""
    kunlun_build_dir = Config.KUNLUN_BUILD_DIR
    receiver_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psu_receiver")
    sender_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psu_sender")

    psi_union_data_dir = Config.KUNLUN_PSI_UNION_DATA_DIR
    group_dir = os.path.join(psi_union_data_dir, f"group_{group_id}")
    os.makedirs(group_dir, exist_ok=True)

    result_file = os.path.join(group_dir, "union.txt")

    if not os.path.exists(receiver_exec):
        return {'success': False, 'error': f'接收方可执行文件不存在: {receiver_exec}'}
    if not os.path.exists(sender_exec):
        return {'success': False, 'error': f'发送方可执行文件不存在: {sender_exec}'}

    if os.path.exists(result_file):
        os.remove(result_file)

    print(f"[Kunlun-PSU] 启动接收方进程... (group: {group_id})")
    receiver_proc = subprocess.Popen(
        [receiver_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    time.sleep(1.5)

    print(f"[Kunlun-PSU] 启动发送方进程... (group: {group_id})")
    sender_proc = subprocess.Popen(
        [sender_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    try:
        sender_stdout, sender_stderr = sender_proc.communicate(timeout=300)
        receiver_stdout, receiver_stderr = receiver_proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        sender_proc.kill()
        receiver_proc.kill()
        return {'success': False, 'error': 'PSU 计算超时(超过300秒)'}

    if sender_proc.returncode != 0:
        print(f"[Kunlun-PSU] 发送方错误: {sender_stderr}")
        return {'success': False, 'error': f'发送方执行失败: {sender_stderr}'}

    if receiver_proc.returncode != 0:
        print(f"[Kunlun-PSU] 接收方错误: {receiver_stderr}")
        return {'success': False, 'error': f'接收方执行失败: {receiver_stderr}'}

    if not os.path.exists(result_file):
        return {'success': False, 'error': '结果文件未生成'}

    with open(result_file, 'r', encoding='latin-1') as f:
        content = f.read().strip()

    union_result = []
    for line in content.split('\n'):
        line = line.strip()
        if line:
            try:
                if '.' in line:
                    num = float(line)
                    if num.is_integer():
                        union_result.append(int(num))
                    else:
                        union_result.append(num)
                else:
                    union_result.append(int(line))
            except ValueError:
                union_result.append(line)

    print(f"[Kunlun-PSU] PSU 计算完成,并集大小: {len(union_result)}")

    return {
        'success': True,
        'union': union_result,
        'count': len(union_result)
    }


def _format_duration(seconds):
    """自适应时间显示。<60s "X.XX 秒";<3600s "X 分 Y 秒";>=3600s "X 小时 Y 分 Y 秒" """
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.2f} 秒"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m} 分 {s} 秒"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h} 小时 {m} 分 {s} 秒"


# ==================== Kunlun PSI-Sum 调用 ====================
def run_kunlun_psi_sum(group_id):
    """调用 Kunlun 可执行文件执行 PSI-Sum 计算(交集基数 + 关联求和)

    注意:跟 PSI-Card / PSU 不同,PSI-Sum 的 sender 是 server 监听端口,
    receiver 是 client 连过去 -- 调度顺序必须 **先 sender 后 receiver**。
    """
    kunlun_build_dir = Config.KUNLUN_BUILD_DIR
    sender_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psi_sum_sender")
    receiver_exec = os.path.join(kunlun_build_dir, "my_mqrpmt_psi_sum_receiver")

    psi_sum_data_dir = Config.KUNLUN_PSI_SUM_DATA_DIR
    group_dir = os.path.join(psi_sum_data_dir, f"group_{group_id}")
    os.makedirs(group_dir, exist_ok=True)

    cardinality_file = os.path.join(group_dir, "cardinality.txt")
    sum_file = os.path.join(group_dir, "sum.txt")

    if not os.path.exists(receiver_exec):
        return {'success': False, 'error': f'接收方可执行文件不存在: {receiver_exec}'}
    if not os.path.exists(sender_exec):
        return {'success': False, 'error': f'发送方可执行文件不存在: {sender_exec}'}

    for f in (cardinality_file, sum_file):
        if os.path.exists(f):
            os.remove(f)

    # sender 先启动 (监听端口)
    print(f"[Kunlun-Sum] 启动发送方进程 (sender 监听) ... (group: {group_id})")
    sender_proc = subprocess.Popen(
        [sender_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    time.sleep(1.5)

    # receiver 后启动 (连过去)
    print(f"[Kunlun-Sum] 启动接收方进程 (receiver connect) ... (group: {group_id})")
    receiver_proc = subprocess.Popen(
        [receiver_exec, group_id],
        cwd=kunlun_build_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='latin-1'
    )

    try:
        sender_stdout, sender_stderr = sender_proc.communicate(timeout=300)
        receiver_stdout, receiver_stderr = receiver_proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        sender_proc.kill()
        receiver_proc.kill()
        return {'success': False, 'error': 'PSI-Sum 计算超时(超过300秒)'}

    if sender_proc.returncode != 0:
        print(f"[Kunlun-Sum] 发送方错误: {sender_stderr}")
        return {'success': False, 'error': f'发送方执行失败: {sender_stderr}'}

    if receiver_proc.returncode != 0:
        print(f"[Kunlun-Sum] 接收方错误: {receiver_stderr}")
        return {'success': False, 'error': f'接收方执行失败: {receiver_stderr}'}

    if not os.path.exists(cardinality_file):
        return {'success': False, 'error': 'cardinality 结果文件未生成'}
    if not os.path.exists(sum_file):
        return {'success': False, 'error': 'sum 结果文件未生成'}

    with open(cardinality_file, 'r', encoding='latin-1') as f:
        cardinality = int(f.read().strip())
    with open(sum_file, 'r', encoding='latin-1') as f:
        sum_str = f.read().strip()
    # sum 是 BigInt,可能超过 JS Number 范围 -- 保持为字符串返回
    try:
        sum_val = int(sum_str)
    except ValueError:
        sum_val = sum_str

    print(f"[Kunlun-Sum] PSI-Sum 计算完成, 交集基数: {cardinality}, SUM: {sum_str}")

    return {
        'success': True,
        'cardinality': cardinality,
        'sum': sum_val,
        'sum_str': sum_str,
    }


def _compute_with_timing(run_func, group_id):
    """包一层计时;调 run_func(group_id),success 时给返回字典塞 duration_seconds/duration_human"""
    t0 = time.time()
    result = run_func(group_id)
    elapsed = time.time() - t0
    if result.get('success'):
        result['duration_seconds'] = round(elapsed, 3)
        result['duration_human'] = _format_duration(elapsed)
        print(f"[Kunlun] {run_func.__name__} 耗时 {elapsed:.3f}s")
    return result

# ==================== 用户数据管理 ====================
class UserManager:
    @staticmethod
    def load_users():
        return load_json_file(Config.USERS_FILE, {})

    @staticmethod
    def save_users(users):
        save_json_file(Config.USERS_FILE, users)

    @staticmethod
    def get_user(username):
        users = UserManager.load_users()
        return users.get(username)

    @staticmethod
    def create_user(username, password, email=None):
        users = UserManager.load_users()
        if username in users:
            return None, "用户名已存在"
        if email:
            for user in users.values():
                if user.get('email') == email:
                    return None, "该邮箱已被注册"

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        users[username] = {
            'username': username,
            'password': password_hash,
            'email': email,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'is_admin': False
        }
        UserManager.save_users(users)
        return users[username], "注册成功"

    @staticmethod
    def verify_password(username, password):
        user = UserManager.get_user(username)
        if not user:
            return None
        if bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            return user
        return None

    @staticmethod
    def get_all_users():
        users = UserManager.load_users()
        return [
            {
                'username': u['username'],
                'email': u.get('email', ''),
                'created_at': u.get('created_at', '')
            }
            for u in users.values()
        ]

    @staticmethod
    def delete_user(username):
        users = UserManager.load_users()
        if username in users:
            del users[username]
            UserManager.save_users(users)
            return True
        return False


# ==================== JWT Token 管理 ====================
class TokenManager:
    @staticmethod
    def generate_token(username, is_admin=False):
        payload = {
            'username': username,
            'is_admin': is_admin,
            'exp': datetime.utcnow() + timedelta(hours=Config.JWT_EXPIRATION_HOURS),
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)

    @staticmethod
    def verify_token(token):
        try:
            return jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None


def jwt_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header[7:]
        if not token:
            return jsonify({'error': '未提供认证Token'}), 401
        payload = TokenManager.verify_token(token)
        if not payload:
            return jsonify({'error': 'Token无效或已过期'}), 401
        request.current_user = payload
        return f(*args, **kwargs)
    return decorated_function



# ==================== 用户认证路由 ====================
# 从原 app.py 2537-2620 搬过来(原文件被协议段打散)
@app.route('/api/register', methods=['POST'])
@limiter.limit("5 per hour")  # 注册:每小时 5 次(防批量注册假账号)
def api_register():
    try:
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        password_confirm = (data.get('passwordConfirm') or '').strip()
        email = (data.get('email') or '').strip()

        if not username or len(username) < 3:
            return jsonify({'error': '用户名长度应在3-20个字符之间'}), 400
        if not password or len(password) < 6:
            return jsonify({'error': '密码长度至少6位'}), 400

        # 后端兏底校验两次密码一致(防止恶意客户端绕过前端)
        if password != password_confirm:
            return jsonify({'error': '两次输入的密码不一致'}), 400

        user_data, message = UserManager.create_user(username, password, email if email else None)
        if user_data is None:
            return jsonify({'error': message}), 400
        return jsonify({'message': message, 'username': username}), 201
    except Exception as e:
        print(f"[/api/register] 服务器错误: {e}", flush=True)
        return jsonify({'error': '服务器内部错误,请稍后重试'}), 500


@app.route('/api/login', methods=['POST'])
@limiter.limit("100 per minute")  # 测试期间临时放宽到 100/min (生产应改回 5/min)
def api_login():
    try:
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()

        if not username or not password:
            return jsonify({'error': '用户名或密码错误'}), 401

        user = UserManager.verify_password(username, password)
        if not user:
            return jsonify({'error': '用户名或密码错误'}), 401

        token = TokenManager.generate_token(username, user.get('is_admin', False))
        return jsonify({'token': token, 'username': username, 'is_admin': user.get('is_admin', False)}), 200
    except Exception as e:
        print(f"[/api/login] 服务器错误: {e}", flush=True)
        return jsonify({'error': '服务器内部错误,请稍后重试'}), 500


@app.route('/api/me', methods=['GET'])
@jwt_required
def api_me():
    user = UserManager.get_user(request.current_user['username'])
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    return jsonify({
        'username': user['username'],
        'email': user.get('email', ''),
        'is_admin': user.get('is_admin', False)
    })


# ---------- 管理员 ----------
@app.route('/api/users', methods=['GET'])
@jwt_required
def api_get_users():
    if not request.current_user.get('is_admin'):
        return jsonify({'error': '权限不足'}), 403
    users = UserManager.get_all_users()
    return jsonify({'users': users})


@app.route('/api/users/<username>', methods=['DELETE'])
@jwt_required
def api_delete_user(username):
    if not request.current_user.get('is_admin'):
        return jsonify({'error': '权限不足'}), 403
    if username == request.current_user['username']:
        return jsonify({'error': '不能删除自己的账号'}), 400
    if UserManager.delete_user(username):
        return jsonify({'message': f'用户 {username} 已删除'})
    return jsonify({'error': '用户不存在'}), 404
# ==================== 隐私求交 API ====================


# ==================== 静态文件服务 ====================
# ==================== 路由:静态文件服务 ====================
@app.route('/')
@app.route('/login.html')
def index():
    return send_from_directory(STATIC_FOLDER_ABS, 'login_register.html')


@app.route('/home.html')
def home():
    return send_from_directory(STATIC_FOLDER_ABS, 'home.html')


@app.route('/collaborate.html')
def collaborate():
    return send_from_directory(STATIC_FOLDER_ABS, 'collaborate.html')


@app.route('/privacy_intersection.html')
def privacy_intersection():
    return send_from_directory(STATIC_FOLDER_ABS, 'privacy_intersection.html')

@app.route('/psi_match.html')  # 新增
def psi_match():
    return send_from_directory(STATIC_FOLDER_ABS, 'psi_match.html')

@app.route('/privacy_union.html')
def privacy_union():
    return send_from_directory(STATIC_FOLDER_ABS, 'privacy_union.html')


@app.route('/<path:filename>')
def serve_static(filename):
    # 2026-07-30 Friday fix: 开发模式禁止缓存静态文件(避免 Chrome 缓存旧 JS 导致修了不生效)
    response = send_from_directory(STATIC_FOLDER_ABS, filename)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# ==================== API 路由 ====================


# ==================== 协议路由注册(2026-07-08 重构) ====================
# 81 个协议路由由 protocols.routes 工厂生成
# Friday 22:15: 改成函数内 import,避开循环(protocols/psi.py 顶部 from app import Config vs app.py 末尾 register_routes 互锁)
def _register_protocol_routes_lazy():
    from protocols.routes import register_routes
    register_routes(app)

# Friday 22:23: 加 error handler 把 500 traceback 打到 stderr (DEBUG=False 抓不到)
@app.errorhandler(Exception)
def _friday_error_log(e):
    import traceback
    import sys as _sys  # 2026-07-30 Friday fix: 之前漏 import sys 导致 handler 自己 NameError
    traceback.print_exc()
    _sys.stdout.flush()
    return jsonify({'error': f'internal: {type(e).__name__}: {str(e)[:300]}'}), 500

# ==================== 启动程序 ====================
if __name__ == '__main__':
    # 在 main 块里才走完整 import 链,避免循环加载时 protocols/* 的 `from app import` 撞 partial module
    _register_protocol_routes_lazy()

    print("=" * 50)
    print("🚀 Flask 服务器启动中...")
    print("=" * 50)
    print(f"📁 静态文件目录: {STATIC_FOLDER_ABS}")
    print(f"📁 上传文件目录: {Config.UPLOAD_FOLDER}")
    print(f"📄 用户数据文件: {Config.USERS_FILE}")
    print("=" * 50)
    print(f"🌐 访问地址: http://localhost:{Config.PORT}/login.html")
    print(f"🌐 访问地址: http://127.0.0.1:{Config.PORT}/login.html")
    print("=" * 50)
    print("按 Ctrl+C 停止服务器")
    print("=" * 50)

    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)