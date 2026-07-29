# protocols/ss_psi.py — SS-PSI (2-party secret-shared PSI) 协议
# SPIKE 5: 从 Kunlun 4-party mock 切换到 sPSO 2-party 真协议
# 协议语义:
#   - alice (creator = party1) 提供 party1.txt (her set)
#   - bob (party2) 提供 party2.txt (his set)
#   - sPSO 输出份额 (shares): alice 持 z, bob 持 r
#   - z ⊕ r = 交集元素 (uint64),反查到原始 token
#   - 双方都拿到交集 (明文),但协议层是份额输出

from app import Config
from .base import BaseGroupManager, KunlunRunner

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
    """sPSO SS-PSI runner (2-party secret-shared PSI)。

    SPIKE 5 实现:
    - 调用 spso_runner --mode ss_psi
    - 解析 === INTERSECTION_START/END === 块 (uint64 列表)
    - 用 blake3 inverse map 还原原始 token
    - 写 intersection.txt (每行一个 token)
    - 返回 {success, cardinality, intersection: [tokens]}

    与 MockSSPSI 兼容的接口:
    - run(group_id) -> {'success': True,
                        'cardinality': N,
                        'intersection': ['token1', 'token2', ...],
                        'computed_at': '...',
                        'computed_by': 'sPSO'}
    """
    kind = 'sPSO'
    log_tag = 'sPSO-SSPSI'
    data_dir_attr = 'KUNLUN_SS_PSI_DATA_DIR'
    result_filenames = ('intersection.txt',)

    @classmethod
    def run(cls, group_id):
        from app import Config
        import subprocess
        import os

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

        # spso_runner 期望 receiver.txt/sender.txt，创建 symlink
        recv_link = os.path.join(data_dir, 'receiver.txt')
        send_link = os.path.join(data_dir, 'sender.txt')
        for lnk, src in [(recv_link, party1_path), (send_link, party2_path)]:
            if os.path.islink(lnk) or os.path.exists(lnk):
                os.remove(lnk)
            os.symlink(src, lnk)

        # 清旧结果
        out_path = os.path.join(data_dir, 'intersection.txt')
        if os.path.exists(out_path):
            os.remove(out_path)

        # spso_runner --mode ss_psi --input-dir <data_dir> --output-file <out_path>
        # 注意：ss_psi 模式不需要 --payload (无 values)
        cmd = [SPSO_RUNNER,
               '--mode', 'ss_psi',
               '--input-dir', data_dir,
               '--output-file', out_path]

        import sys
        print(f"[{cls.log_tag}] SPIKE-5 exec: {' '.join(cmd)}", file=sys.stderr, flush=True)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': 'sPSO 计算超时 (>600s)'}

        if result.returncode != 0:
            err_text = result.stderr[:2000] if result.stderr else 'unknown error'
            return {'success': False, 'error': f'sPSO 失败 (rc={result.returncode}): {err_text}'}

        # 读 intersection.txt (每行一个 token)
        intersection = []
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        intersection.append(line)
        except FileNotFoundError:
            return {'success': False, 'error': f'输出文件不存在：{out_path}'}

        from app import datetime
        return {
            'success': True,
            'cardinality': len(intersection),
            'intersection': intersection,
            'computed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'computed_by': 'sPSO',
        }


from datetime import datetime  # noqa
