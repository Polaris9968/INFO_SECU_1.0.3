# protocols/psu.py — PSU (Private Set Union) 协议
from app import Config, read_union_from_file
from .base import BaseGroupManager, BaseRunner

class PSIUnionGroupManager(BaseGroupManager):
    file_path = Config.PSI_UNION_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = True
    result_field = 'union_result'
    data_dir_attr = 'SPSO_PSI_UNION_DATA_DIR'

    archive_filenames = (
        'receiver.txt', 'sender.txt', 'union.txt',
        'original_receiver.txt', 'original_sender.txt',
    )
    stale_filenames = (
        'union.txt',
    )

    generate_with_original = True

    file_type_map = {
        'my_plaintext':         lambda role, **kw: f'original_{role}',
        'my_oprf':              lambda role, **kw: role,  # receiver.txt / sender.txt
        'my_original':          lambda role, **kw: f'original_{role}',
        'result':               lambda role, **kw: 'union',
        'result_with_original': lambda role, **kw: 'union_with_original',
    }

    @classmethod
    def _with_original_filename(cls):
        return 'union_with_original.txt'

    @classmethod
    def _with_original_key(cls):
        return 'union_with_original'

    @classmethod
    def _read_finalized_result(cls, archive_files, kunlun_dir, group):
        """PSU: 读 union.txt"""
        result = {'intersection_or_values': [], 'summary': {}}
        if 'union' in archive_files:
            try:
                with open(archive_files['union'], 'r', encoding='utf-8') as f:
                    items = [line.strip() for line in f if line.strip()]
                result['intersection_or_values'] = items
                result['summary'] = {'type': 'union', 'count': len(items)}
            except Exception:
                pass
        return result

