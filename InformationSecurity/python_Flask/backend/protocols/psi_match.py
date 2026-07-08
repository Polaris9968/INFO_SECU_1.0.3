# protocols/psi_match.py — PSI-Match 协议
from app import Config
from .base import BaseGroupManager, KunlunRunner


class PSIMatchGroupManager(BaseGroupManager):
    file_path = Config.PSI_MATCH_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = True
    result_field = 'subset_result'
    data_dir_attr = 'KUNLUN_PSI_CARD_DATA_DIR'  # PSI-Match 用 PSI-Card 的目录

    archive_filenames = (
        'receiver.txt', 'sender.txt', 'cardinality.txt',
        'original_receiver.txt', 'original_sender.txt',
    )
    stale_filenames = (
        'cardinality.txt',
    )

    generate_with_original = False  # PSI-Match cardinality 是数字,无 list,无 reverse map

    file_type_map = {
        'my_plaintext': lambda role, **kw: f'original_{role}',
        'my_oprf':      lambda role, **kw: role,
        'result':       lambda role, **kw: 'cardinality',
        # PSI-Match 无 result_with_original
    }

    @classmethod
    def _read_finalized_result(cls, archive_files, kunlun_dir, group):
        """PSI-Match: 读 cardinality.txt(数字)"""
        result = {'intersection_or_values': [], 'summary': {}}
        if 'cardinality' in archive_files:
            try:
                with open(archive_files['cardinality'], 'r', encoding='utf-8') as f:
                    cardinality = int(f.read().strip() or 0)
                result['summary'] = {'type': 'cardinality', 'count': cardinality}
            except Exception:
                pass
        return result


# PSI-Match 复用 PSI-Card 的 Kunlun 二进制(同一份 my_mqrpmt_psi_card)
class KunlunPSIMatch(KunlunRunner):
    receiver_exec = 'my_mqrpmt_psi_card_receiver'
    sender_exec = 'my_mqrpmt_psi_card_sender'
    data_dir_attr = 'KUNLUN_PSI_CARD_DATA_DIR'
    result_filenames = ('cardinality.txt',)
    log_tag = 'Kunlun-PSIMatch'

    @classmethod
    def parse_result(cls, cardinality_txt='', **kwargs):
        return {'cardinality': int(cardinality_txt.strip() or 0)}