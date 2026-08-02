# protocols/psi_match.py — PSI-Match 协议
import os
from app import Config
from .base import BaseGroupManager, BaseRunner

class PSIMatchGroupManager(BaseGroupManager):
    file_path = Config.PSI_MATCH_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = True
    result_field = 'subset_result'
    data_dir_attr = 'SPSO_PSI_CARD_DATA_DIR'  # PSI-Match 用 PSI-Card 的目录

    archive_filenames = (
        'receiver.txt', 'sender.txt', 'cardinality.txt', 'matched.txt',
        'original_receiver.txt', 'original_sender.txt',
    )
    stale_filenames = (
        'cardinality.txt',
        'matched.txt',  # SPIKE 3: matched.txt 也要在归档后顶层删
        # 2026-08-02 fix: 密文/OPRF 中间产物也要归档后删, 否则下一轮
        # 密文预览还显示上一轮数据 (截图: 下一轮后“合计 96 个”残留)
        'receiver_ciphertext.txt',
        'sender_ciphertext.txt',
        'oprf_prf_recver.txt',
        'oprf_prf_sender.txt',
    )

    generate_with_original = False  # SPIKE 3: 现在有 matched_items 可 reverse_map;
                                    # 但 generate_with_original 只决定 PSI/PSU
                                    # 的 intersection_or_values 路径;PSI-Match
                                    # 走 _read_finalized_result 自定义,不影响。

    file_type_map = {
        'my_plaintext': lambda role, **kw: f'original_{role}',
        'my_oprf':      lambda role, **kw: role,
        'result':       lambda role, **kw: 'cardinality',
        # SPIKE 3: result_with_original 现在可以从 matched.txt 推
        'result_with_original': lambda role, **kw: 'matched',
        # PSI-Match 无 result_with_original
    }

    @classmethod
    def _with_original_filename(cls):
        return 'matched_with_original.txt'  # SPIKE 3: 改名(语义更准确)

    @classmethod
    def _with_original_key(cls):
        return 'matched_with_original'

    @classmethod
    def _read_finalized_result(cls, archive_files, kunlun_dir, group):
        """PSI-Match: 读 cardinality.txt(数字)+ matched.txt(SPIKE 3,可选用原始 token 列表)"""
        result = {'intersection_or_values': [], 'summary': {}}
        cardinality = 0
        matched_items = []
        # 1. cardinality.txt(必须)
        if 'cardinality' in archive_files:
            try:
                with open(archive_files['cardinality'], 'r', encoding='utf-8') as f:
                    cardinality = int(f.read().strip() or 0)
            except Exception:
                pass
        # 2. matched.txt(SPIKE 3 新增;reverse_map 后的原始 token 列表)
        matched_path = archive_files.get('matched')
        if matched_path and os.path.exists(matched_path):
            try:
                with open(matched_path, 'r', encoding='utf-8') as f:
                    matched_items = [line.strip() for line in f if line.strip()]
            except Exception:
                pass
        result['summary'] = {
            'type': 'cardinality',
            'count': cardinality,
            'matched_alice': matched_items,   # SPIKE 3: 给前端显示 matched 元素
        }
        return result

# PSI-Match 复用 PSI-Card 的 Kunlun 二进制(同一份 my_mqrpmt_psi_card)

