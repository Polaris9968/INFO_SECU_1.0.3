# protocols/psi_card.py — PSI-Card (cardinality only) 协议
from app import Config, read_cardinality_from_file
from .base import BaseGroupManager, KunlunRunner


class PSICardGroupManager(BaseGroupManager):
    file_path = Config.PSI_CARD_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = False  # 当前没多轮
    result_field = 'cardinality_result'
    data_dir_attr = 'KUNLUN_PSI_CARD_DATA_DIR'


class KunlunPSICard(KunlunRunner):
    receiver_exec = 'my_mqrpmt_psi_card_receiver'
    sender_exec = 'my_mqrpmt_psi_card_sender'
    data_dir_attr = 'KUNLUN_PSI_CARD_DATA_DIR'
    result_filenames = ('cardinality.txt',)
    log_tag = 'Kunlun-PSICard'

    @classmethod
    def parse_result(cls, cardinality_txt='', **kwargs):
        return {'cardinality': int(cardinality_txt.strip() or 0)}