# protocols/psi.py — PSI 协议
import os
from app import Config, read_intersection_from_file
from .base import BaseGroupManager, KunlunRunner


class PSIGroupManager(BaseGroupManager):
    file_path = Config.PSI_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = True
    result_field = 'psi_result'
    data_dir_attr = 'SPSO_PSI_DATA_DIR'

    archive_filenames = (
        'receiver.txt', 'sender.txt', 'intersection.txt',
        'receiver_ciphertext.txt', 'sender_ciphertext.txt',
        'sender_result.txt',
        'original_receiver.txt', 'original_sender.txt',
    )
    stale_filenames = (
        'intersection.txt', 'union.txt',
        'receiver_ciphertext.txt', 'sender_ciphertext.txt',
        'sender_result.txt',
        'original_receiver.txt', 'original_sender.txt',
    )

    generate_with_original = True

    file_type_map = {
        'my_plaintext':          lambda role, **kw: f'original_{role}',
        'my_oprf':               lambda role, **kw: f'{role}_ciphertext',
        'my_original':           lambda role, **kw: f'original_{role}',
        'result':                lambda role, **kw: 'intersection',
        'result_with_original':  lambda role, **kw: 'intersection_with_original',
    }


class KunlunPSI(KunlunRunner):
    receiver_exec = 'my_mqrpmt_psi_receiver'
    sender_exec = 'my_mqrpmt_psi_sender'
    data_dir_attr = 'SPSO_PSI_DATA_DIR'
    result_filenames = ('intersection.txt',)
    log_tag = 'Kunlun-PSI'