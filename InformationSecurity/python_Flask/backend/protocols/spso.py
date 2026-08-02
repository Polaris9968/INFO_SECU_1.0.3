# protocols/spso.py — sPSO 协议 wrapper
"""
SpsoPSI / SpsoPSU / SpsoPSICard / SpsoPSIMatch — sPSO 4 协议 wrapper。

SPIKE 2: PSI 切到 sPSO(已上线)
SPIKE 3: PSU / PSI-Card / PSI-Match 切到 sPSO

替代 BaseRunner 的 PSI/PSU/Card/Match 调用,通过 subprocess 启动 spso_runner 二进制。
数据契约保持兼容(读 receiver.txt / sender.txt,写 intersection.txt / union.txt /
cardinality.txt),这样 routes.py 和 app.py 里所有用 read_*_from_file / 
_generic_upload_handler 的逻辑都不需要改。

设计选择(权衡):
  - 继承 BaseRunner 是最干净的方式:所有 spawn/parse 逻辑复用。
  - override `_run_kunlun` → `run` 直接调 sPSO 即可(避开 spawn 两个进程)。
  - spso_runner 是单进程(内部 in-process 跑 sender/receiver 两个线程),
    所以不需要 spawn_order,也不需要 kunlun_build_dir。
  - 4 个类共享同一 helper:_run_spso_mode(group_id, mode, output_filename,
    parse_fn) — 差异只在 mode 和 parse。
  - log_tag 改成 'sPSO-*',其他 metadata 不变。
"""
import os
import time
import subprocess
import sys
import json

# Make spso_python importable (sibling of InformationSecurity/)
_SPSO_PYTHON_DIR = "/root/projects/INFO_SECU_1.0.3/spso_python"
if _SPSO_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SPSO_PYTHON_DIR)

from .base import BaseRunner


SPSO_RUNNER = os.path.join(_SPSO_PYTHON_DIR, "spso_runner")


def _run_spso_mode(group_id, data_dir_attr, spso_mode,
                   output_filename, parse_fn,
                   input_basename_a='receiver.txt',
                   input_basename_b='sender.txt'):
    """共享 helper:跑 spso_runner --mode <spso_mode>。

    Args:
      group_id: 小组 ID
      data_dir_attr: Config 上的数据目录属性名(如 'SPSO_PSI_DATA_DIR')
      spso_mode: 'psi' / 'psu' / 'card' / 'ss_psi'
      output_filename: spso_runner --output-file 参数值(协议特定文件名)
      parse_fn(content: str) -> dict:从输出文件内容生成结果 dict
      input_basename_a / input_basename_b: 输入文件 basename(默认 receiver/sender)

    Returns:
      dict: {'success': True, ...} 或 {'success': False, 'error': '...'}
    """
    # Lazy import to avoid loading spso_client unless needed
    try:
        from spso_client import SpsoClient
    except ImportError as e:
        return {'success': False, 'error': f'Cannot import spso_client: {e}'}

    from app import Config

    data_dir = os.path.join(getattr(Config, data_dir_attr), f"group_{group_id}")
    os.makedirs(data_dir, exist_ok=True)

    # 删旧结果文件
    out_path = os.path.join(data_dir, output_filename)
    if os.path.exists(out_path):
        os.remove(out_path)

    if not os.path.exists(SPSO_RUNNER):
        return {'success': False, 'error': f'spso_runner 二进制不存在: {SPSO_RUNNER}'}

    receiver_path = os.path.join(data_dir, input_basename_a)
    sender_path = os.path.join(data_dir, input_basename_b)
    if not os.path.exists(receiver_path):
        return {'success': False, 'error': f'{input_basename_a} 不存在: {receiver_path}'}
    if not os.path.exists(sender_path):
        return {'success': False, 'error': f'{input_basename_b} 不存在: {sender_path}'}

    try:
        client = SpsoClient(runner_path=SPSO_RUNNER, timeout=600)
        # 调 run_psi_on_input(其内部根据 mode 参数路由)
        # SPIKE 5 (2026-07-30 Friday demo): dump OPRF 中间产物到 data_dir
        # 给前端 ciphertext 预览看出“已加密”语义。安全是 demo 妥协。
        result_lines = client.run_psi_on_input(
            input_dir=data_dir,
            output_file=out_path,
            mode=spso_mode,
            dump_dir=data_dir,
        )
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'sPSO 计算超时(>600s)'}
    except FileNotFoundError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        err_text = str(e)
        if len(err_text) > 2000:
            err_text = err_text[:2000] + "...(truncated)"
        return {'success': False, 'error': f'sPSO 启动失败: {err_text}'}

    # parse_fn(读 output file,返回 dict)
    try:
        if os.path.exists(out_path):
            with open(out_path, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            content = ''
        parsed = parse_fn(content, result_lines)
    except Exception as e:
        return {'success': False, 'error': f'解析结果失败: {e}'}

    # 2026-08-02 E2E fix: sPSO dump 的是 oprf_prf_{recver,sender}.txt (spike5 demo),
    # 但前端 preview-ciphertext / 历史下载 my_oprf 读 {role}_ciphertext.txt (Kunlun 习惯)。
    # 这里复制一份保持兼容,否则“我的密文”下载永远 404。
    try:
        for src_name, dst_name in (('oprf_prf_recver.txt', 'receiver_ciphertext.txt'),
                                   ('oprf_prf_sender.txt', 'sender_ciphertext.txt')):
            src_path = os.path.join(data_dir, src_name)
            dst_path = os.path.join(data_dir, dst_name)
            if os.path.exists(src_path):
                with open(src_path, 'rb') as sf, open(dst_path, 'wb') as df:
                    df.write(sf.read())
    except Exception as e:
        # 非致命: ciphertext 兼容文件同步失败不阻断主流程
        print(f"[spso] ciphertext 兼容文件同步跳过: {e}")

    parsed['success'] = True
    return parsed


# ============================================================
# SpsoPSI  — SPIKE 2
# ============================================================
class SpsoPSI(BaseRunner):
    """sPSO PSI runner. Replaces KunlunPSI for SPIKE 2 integration.

    与 BaseRunner 兼容的接口:
      - run(group_id): 主入口
      - parse_result(text): 默认按行 split

    关键差异:
      - 不需要 spawn 两个进程(spso_runner 内部 in-process)
      - 不需要 cwd=kunlun_build_dir
      - input_dir / output_file 用 group_id 下的子目录
    """
    receiver_exec = 'spso_runner'   # 兼容 BaseRunner 字段(实际不用)
    sender_exec = 'spso_runner'     # 兼容 BaseRunner 字段(实际不用)
    data_dir_attr = 'SPSO_PSI_DATA_DIR'  # 复用 sPSO 路径(SPEC 要求)
    result_filenames = ('intersection.txt',)
    log_tag = 'sPSO-PSI'

    @classmethod
    def run(cls, group_id):
        def _parse(content, lines):
            items = [l for l in lines]
            return {'intersection': items, 'count': len(items)}
        return _run_spso_mode(
            group_id,
            data_dir_attr=cls.data_dir_attr,
            spso_mode='psi',
            output_filename='intersection.txt',
            parse_fn=_parse,
        )


# ============================================================
# SpsoPSU  — SPIKE 3 (Private Set Union)
# ============================================================
class SpsoPSU(BaseRunner):
    """sPSO PSU runner. Replaces KunlunPSU for SPIKE 3.

    与 KunlunPSU 相同接口:
      - run(group_id) -> {'success': True, 'union': [...], 'count': N}
                       或 {'success': False, 'error': '...'}

    ⚠️ SPIKE 3 fix (Friday 补, 21:50):
    spso_runner 在 PSU mode 时把 padding sentinel 也写进 union.txt,导致
    union 含 `__spike2_pad_recver_N` / `__spike2_pad_sender_N` 等伪 token。
    修复: parse 时 filter 掉 `__spike` 前缀的行。
    (PSI / Match / SS-PSI 路径已天然不污染 — spso_runner 自己 filter)
    """
    receiver_exec = 'spso_runner'
    sender_exec = 'spso_runner'
    data_dir_attr = 'SPSO_PSI_UNION_DATA_DIR'
    result_filenames = ('union.txt',)
    log_tag = 'sPSO-PSU'

    @classmethod
    def run(cls, group_id):
        def _parse(content, lines):
            items = [l for l in lines if not l.startswith('__spike')]
            return {'union': items, 'count': len(items)}
        return _run_spso_mode(
            group_id,
            data_dir_attr=cls.data_dir_attr,
            spso_mode='psu',
            output_filename='union.txt',
            parse_fn=_parse,
        )


# ============================================================
# SpsoPSICard  — SPIKE 3 (Cardinality)
# ============================================================
class SpsoPSICard(BaseRunner):
    """sPSO PSI-Card runner. Replaces KunlunPSICard for SPIKE 3.

    与 KunlunPSICard 相同接口:
      - run(group_id) -> {'success': True, 'cardinality': N}
                       或 {'success': False, 'error': '...'}
    """
    receiver_exec = 'spso_runner'
    sender_exec = 'spso_runner'
    data_dir_attr = 'SPSO_PSI_CARD_DATA_DIR'
    result_filenames = ('cardinality.txt',)
    log_tag = 'sPSO-PSICard'

    @classmethod
    def run(cls, group_id):
        def _parse(content, lines):
            # spso_runner 写一个整数到 cardinality.txt
            if not lines:
                return {'cardinality': 0}
            try:
                return {'cardinality': int(lines[0].strip() or '0')}
            except ValueError:
                return {'cardinality': 0}
        return _run_spso_mode(
            group_id,
            data_dir_attr=cls.data_dir_attr,
            spso_mode='card',
            output_filename='cardinality.txt',
            parse_fn=_parse,
        )


# ============================================================
# SpsoPSIMatch  — SPIKE 3 (PSI-Match 用 PSI 模拟)
# ============================================================
class SpsoPSIMatch(BaseRunner):
    """sPSO PSI-Match runner. Replaces KunlunPSIMatch for SPIKE 3.

    SPIKE 3 决策(已和 Polaris 对齐):
      sPSO 没有专门 match mode。我们用 psi 模拟:
      1. 跑 --mode psi 拿 intersection(uint64)
      2. spso_runner 内部已 reverse_map 到原始 token
      3. 返回 Alice(receiver/creator)的 matched token list + boolean

    与 KunlunPSIMatch 兼容的接口:
      - run(group_id) -> {'success': True,
                           'matched_alice': [...],   # receiver(Alice)视角的匹配元素
                           'matched_count': N,
                           'match': bool(N>0),
                           'cardinality': N}          # 兼容 routes.py 读

    文件写入:
      - cardinality.txt: 写入整数(count of matches)— _read_finalized_result 要读 int
      - matched.txt:     写入 Alice 匹配的元素 list(每行一个 token)
                         _read_finalized_result 也会读这个(可选)
    """
    receiver_exec = 'spso_runner'
    sender_exec = 'spso_runner'
    data_dir_attr = 'SPSO_PSI_CARD_DATA_DIR'   # PSI-Match 复用 PSI-Card 目录
    result_filenames = ('cardinality.txt', 'matched.txt')  # ★ SPIKE 3: 增加 matched.txt
    log_tag = 'sPSO-PSIMatch'

    @classmethod
    def run(cls, group_id):
        # Friday 22:30 fix: 真正算 is_subset + missing_count
        # is_subset = (alice 全部元素都在 bob 里) = matched_alice.count == alice.numbers.count
        # missing_count = alice 有但 bob 没有的元素数 = alice.count - matched.count
        # 读 group dict 拿 alice(receiver / creator) 的 upload numbers count
        from app import Config
        try:
            data_dir_attr = cls.data_dir_attr
            data_dir = os.path.join(getattr(Config, data_dir_attr), f"group_{group_id}")
        except Exception as e:
            return {'success': False, 'error': f'读 Config 失败: {e}'}

        # alice = group creator = receiver (BaseRunner 历史约定)
        # 暂不依赖 group(为了避免又走 BaseGroupManager load_groups),参数走 group.json
        try:
            group_json_path = os.path.join(data_dir, 'group.json')
            if os.path.exists(group_json_path):
                with open(group_json_path, 'r', encoding='utf-8') as f:
                    ginfo = json.loads(f.read())
            else:
                # 找 group 数据从 manager load
                from .base import BaseGroupManager
                # 找 manager_by_data_dir: PSIMatchGroupManager
                mgr_paths = {
                    'SPSO_PSI_CARD_DATA_DIR': 'psi_card_groups.json',
                }
                groups_file = mgr_paths.get(data_dir_attr)
                if groups_file is None:
                    return {'success': False, 'error': f'未知的 data_dir_attr: {data_dir_attr}'}
                from app import load_json_file
                # Friday 22:24 fix: 用 PSIMatchGroupManager 代替 PSICardGroupManager
                # 原 bug: PSI-Match group 存于 psi_match_groups.json, PSICardGroupManager 读 psi_card_groups.json
                # 导致 "小组 XXX 不存在" 错误
                from .psi_match import PSIMatchGroupManager
                _data = PSIMatchGroupManager.load_groups()
                g = next((x for x in _data.get('groups', []) if x['id'] == group_id), None)
                if not g:
                    return {'success': False, 'error': f'小组 {group_id} 不存在'}
                ginfo = g
            # 找 alice (creator) 的 upload count
            alice_upload = next((u for u in ginfo.get('uploads', []) if u.get('username') == ginfo.get('creator')), None)
            alice_count = (alice_upload or {}).get('count', 0)
        except Exception as e:
            return {'success': False, 'error': f'读小组信息失败: {e}'}

        def _parse(content, lines):
            matched = [l for l in lines]
            matched_set = set(matched)
            matched_count = len(matched)
            is_subset = matched_count == alice_count
            missing_count = max(0, alice_count - matched_count)
            return {
                'matched_alice': matched,
                'matched_count': matched_count,
                'is_subset': is_subset,             # ★ SPIKE 3.5 fix
                'missing_count': missing_count,     # ★ SPIKE 3.5 fix (alice 中不在 bob 里的元素数)
                'cardinality': matched_count,       # 兼容 routes.py / _read_finalized_result
                'count': matched_count,
                'alice_count': alice_count,         # 给 routes 写 subset_result 用
            }

        # 调 _run_spso_mode: output 写 matched.txt(因为 cardinality.txt 要写 int,不能 list)
        # 之后我们额外把 cardinality 写到 cardinality.txt
        result = _run_spso_mode(
            group_id,
            data_dir_attr=cls.data_dir_attr,
            spso_mode='psi',                # 内部跑 PSI 拿交集
            output_filename='matched.txt',  # SPIKE 3 新文件,装 matched list
            parse_fn=_parse,
        )
        if result.get('success'):
            # 写 cardinality.txt(integer)— 兼容 _read_finalized_result
            cardinality_path = os.path.join(data_dir, 'cardinality.txt')
            try:
                with open(cardinality_path, 'w', encoding='utf-8') as f:
                    f.write(str(result['cardinality']))
            except Exception as e:
                return {'success': False, 'error': f'写 cardinality.txt 失败: {e}'}
        return result


# ============================================================
# SpsoPSISum  — SPIKE 4 (PSI-Sum: Σ values[i] for i ∈ X∩Y, mod q)
# ============================================================
class SpsoPSISum(BaseRunner):
    """sPSO PSI-Sum runner. Replaces KunlunPSISum for SPIKE 4.

    协议:
      - alice (creator = receiver) 提供 receiver.txt (her set)
      - bob (sender) 提供 sender.txt (his set) + value_sender.txt (per-item 加权) → 2026-08-02 实际文件 value_sender.txt
      - sum = Σ sender_value[i] for i where sender_set[i] ∈ recver_set, mod q
      - receiver (alice) 学会这个 sum; sender (bob) 不知道 sum

    与 KunlunPSISum 兼容的接口:
      - run(group_id) -> {'success': True,
                          'cardinality': N,
                          'sum': S,
                          'sum_str': 'S'}

    文件写入:
      - cardinality.txt: 写交集大小(int)
      - sum.txt:         写 sum 值(int, mod q)
    """
    receiver_exec = 'spso_runner'
    sender_exec = 'spso_runner'
    data_dir_attr = 'SPSO_PSI_SUM_DATA_DIR'
    result_filenames = ('cardinality.txt', 'sum.txt')
    log_tag = 'sPSO-PSISum'

    @classmethod
    def run(cls, group_id):
        from app import Config
        try:
            data_dir = os.path.join(getattr(Config, cls.data_dir_attr), f"group_{group_id}")
        except Exception as e:
            return {'success': False, 'error': f'读 Config 失败: {e}'}

        # 读 sender_values (bob 的 values) — 必须等于 sender.txt 的行数
        # 2026-08-02 E2E fix: 上传/归档统一用 value_{role}.txt (原来 sender_value.txt)
        sender_values_path = os.path.join(data_dir, 'value_sender.txt')
        sender_values = []
        try:
            with open(sender_values_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sender_values.append(int(line))
        except FileNotFoundError:
            return {'success': False, 'error': f'sender value 文件不存在: {sender_values_path}'}
        except ValueError as e:
            return {'success': False, 'error': f'value 文件含非整数: {e}'}

        if not sender_values:
            return {'success': False, 'error': 'sender values 为空,PSI-Sum 无法计算'}

        # 用 spso_client.run_sum_on_input 拿 sum (mod q)
        out_path = os.path.join(data_dir, 'sum.txt')
        # 清旧文件,避免读脏数据
        for f in ('cardinality.txt', 'sum.txt'):
            p = os.path.join(data_dir, f)
            if os.path.exists(p):
                os.remove(p)

        try:
            from spso_client import SpsoClient
            client = SpsoClient(runner_path=SPSO_RUNNER, timeout=600)
            sum_value = client.run_sum_on_input(
                input_dir=data_dir,
                output_file=out_path,
                sender_values=sender_values,
            )
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': 'sPSO 计算超时(>600s)'}
        except FileNotFoundError as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            err_text = str(e)
            if len(err_text) > 2000:
                err_text = err_text[:2000] + "...(truncated)"
            return {'success': False, 'error': f'sPSO 启动失败: {err_text}'}

        # 写 sum.txt first (兼容 _read_finalized_result 如 routes psi_sum case reader)
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(str(sum_value))
        except Exception as e:
            return {'success': False, 'error': f'写 sum.txt 失败: {e}'}

        # SPIKE 4: cardinality 靠 PSI-CARD mode 单独跑一次拿(1~2 协议交替跑)
        # 原指望 OPRF eq_share 一次拿,但 OPRF eq_share 是 secret-shared(本地看不到 truth),
        # 只能在交换前算(true_eq = sender_share XOR recver_share,pre-PermCG);
        # 但 PermCG 之后位置被打乱,无法 catch。因此 2 protocol runs:
        # - 一次 --mode psi_sum 拿 sum
        # - 一次 --mode card 拿 cardinality
        # 每次 ~0.3s,total ~0.6s,用户可接受
        def _parse_card(content, lines):
            if not lines:
                return {'cardinality': 0}
            try:
                return {'cardinality': int(lines[0].strip() or '0')}
            except ValueError:
                return {'cardinality': 0}
        card_result = _run_spso_mode(
            group_id,
            data_dir_attr=cls.data_dir_attr,
            spso_mode='card',
            output_filename='cardinality.txt',
            parse_fn=_parse_card,
        )
        if not card_result.get('success'):
            return card_result

        cardinality = card_result.get('cardinality', 0)
        return {
            'success': True,
            'cardinality': cardinality,
            'sum': sum_value,
            'sum_str': str(sum_value),
        }
