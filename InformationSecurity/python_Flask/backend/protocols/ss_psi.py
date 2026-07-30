# protocols/ss_psi.py — SS-PSI (Secret-Shared PSI 秘密共享交集) 协议
# SPIKE 5: 从 Kunlun 4-party mock 切换到 sPSO 2-party 真协议
# 协议语义:
#   - alice (creator = party1) 提供 party1.txt (her set)
#   - bob (party2) 提供 party2.txt (his set)
#   - sPSO 输出份额 (shares): alice 持 z, bob 持 r
#   - z ⊕ r = 交集元素 (uint64),反查到原始 token
#   - 双方都拿到交集 (明文),但协议层是份额输出

from app import Config
from .base import BaseGroupManager, KunlunRunner
from datetime import datetime

# Lazy import spso_client at runtime to avoid import cycles
SPSO_RUNNER = '/root/projects/INFO_SECU_1.0.3/spso_python/spso_runner'


class SSPSIGroupManager(BaseGroupManager):
    """SS-PSI 小组管理 (2-party)。

    SPIKE 5 变更:
    - EXPECTED_PARTIES: 4 → 2 (sPSO 是 2-party 协议)
    - max_members: 4 → 2
    - 前端字段: expected_parties 保留但固定为 2
    """
    EXPECTED_PARTIES = 2

    file_path = Config.SS_PSI_GROUPS_FILE
    id_length = 4
    max_members = 2   # ★ 2-party

    supports_history = False
    result_field = 'result'
    data_dir_attr = 'KUNLUN_SS_PSI_DATA_DIR'

    @classmethod
    def create_group(cls, group_name, creator, **kwargs):
        """SS-PSI: 2-party,无 standardize_mode"""
        data = cls.load_groups()
        for g in data["groups"]:
            if g["name"] == group_name and creator in g["members"]:
                return g
        group_id = cls.generate_group_id()
        group_data = {
            'id': group_id,
            'name': group_name,
            'creator': creator,
            'members': [creator],
            'uploads': [],
            'result': None,
            'expected_parties': cls.EXPECTED_PARTIES,  # 固定 2
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        data["groups"].append(group_data)
        cls.save_groups(data)
        return group_data


class SpsoSSPSI(KunlunRunner):
    """sPSO SS-PSI runner (2-party secret-shared PSI / 秘密共享交集)。

    SPIKE 6 实现 (Option A - 真正 secret shares):
    - 调用 spso_runner --mode ss_psi
    - spso_runner 写 <data_dir>/share_sender.txt + share_receiver.txt
      (各 cuckoo_sz 行,每行 32 hex chars = 128-bit block)
    - XOR 命中 bin = 交集元素 (X*[π(i)]);其余 XOR = 0
    - 返回 {share_sender, share_receiver, cardinality_hint, ...}
    - 双方各持一份 share,XOR 才是交集(无任一方单独能读出交集)

    与 MockSSPSI 的接口差异:
    - run(group_id) -> {'success': True,
                        'share_sender': [...],   # 32-hex block, 1 per cuckoo bin
                        'share_receiver': [...], # 同上
                        'cardinality_hint': N,   # XOR != 0 的 bin 数
                        'cuckoo_size': cuckoo_sz,
                        'computed_at': '...',
                        'computed_by': 'sPSO',
                        'duration_seconds': ...,
                        'duration_human': ...}
    - 不再有 plain `intersection` 字段 (隐私设计: 交集不暴露给任一方)
    """
    kind = 'sPSO'
    log_tag = 'sPSO-SSPSI'
    data_dir_attr = 'KUNLUN_SS_PSI_DATA_DIR'
    # 不再有 result_filenames (intersection.txt 已废弃);share 文件固定名
    result_filenames = ('share_sender.txt', 'share_receiver.txt')

    @classmethod
    def run(cls, group_id):
        from app import Config
        import subprocess
        import os
        import time

        try:
            data_dir = os.path.join(getattr(Config, cls.data_dir_attr), f"group_{group_id}")
        except Exception as e:
            return {'success': False, 'error': f'读 Config 失败：{e}'}

        # 确认 party1.txt + party2.txt 存在 (upload handler 写这两个)
        party1_path = os.path.join(data_dir, 'party1.txt')
        party2_path = os.path.join(data_dir, 'party2.txt')
        if not os.path.exists(party1_path):
            return {'success': False, 'error': f'party1.txt 不存在：{party1_path}'}
        if not os.path.exists(party2_path):
            return {'success': False, 'error': f'party2.txt 不存在：{party2_path}'}

        # spso_runner 期望 receiver.txt/sender.txt,创建 symlink
        recv_link = os.path.join(data_dir, 'receiver.txt')
        send_link = os.path.join(data_dir, 'sender.txt')
        for lnk, src in [(recv_link, party1_path), (send_link, party2_path)]:
            if os.path.islink(lnk) or os.path.exists(lnk):
                os.remove(lnk)
            os.symlink(src, lnk)

        # spso_runner --mode ss_psi --input-dir <data_dir>
        # 注意:SS-PSI 模式输出 share 文件到 input_dir,不需要 --output-file
        cmd = [SPSO_RUNNER, '--mode', 'ss_psi', '--input-dir', data_dir]

        import sys as _sys
        print(f"[{cls.log_tag}] SPIKE-6 exec: {' '.join(cmd)}", file=_sys.stderr, flush=True)

        t0 = time.time()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': 'sPSO 计算超时 (>600s)'}
        duration = time.time() - t0

        if result.returncode != 0:
            err_text = result.stderr[:2000] if result.stderr else 'unknown error'
            return {'success': False, 'error': f'sPSO 失败 (rc={result.returncode}): {err_text}'}

        # 读 share_sender.txt + share_receiver.txt
        share_sender_path = os.path.join(data_dir, 'share_sender.txt')
        share_receiver_path = os.path.join(data_dir, 'share_receiver.txt')
        if not os.path.exists(share_sender_path) or not os.path.exists(share_receiver_path):
            return {
                'success': False,
                'error': f'share 文件未生成: {share_sender_path} 或 {share_receiver_path} 不存在'
            }

        try:
            with open(share_sender_path, 'r', encoding='utf-8') as f:
                share_sender = [line.strip() for line in f if line.strip()]
            with open(share_receiver_path, 'r', encoding='utf-8') as f:
                share_receiver = [line.strip() for line in f if line.strip()]
        except Exception as e:
            return {'success': False, 'error': f'读 share 文件失败: {e}'}

        # 计算 cardinality_hint: XOR != 0 的 bin 数 = 真实交集大小
        cardinality_hint = 0
        for s, r in zip(share_sender, share_receiver):
            if len(s) == 32 and len(r) == 32:  # 32 hex chars = 128-bit block
                try:
                    if int(s, 16) ^ int(r, 16) != 0:
                        cardinality_hint += 1
                except ValueError:
                    pass  # 非 hex 跳过

        # duration human
        if duration < 1.0:
            duration_human = f"{int(duration * 1000)} 毫秒"
        elif duration < 60:
            duration_human = f"{duration:.2f} 秒"
        else:
            duration_human = f"{int(duration // 60)} 分 {int(duration % 60)} 秒"

        return {
            'success': True,
            'share_sender': share_sender,
            'share_receiver': share_receiver,
            'cardinality_hint': cardinality_hint,
            'cuckoo_size': len(share_sender),
            'computed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'computed_by': 'sPSO',
            'duration_seconds': round(duration, 4),
            'duration_human': duration_human,
        }


