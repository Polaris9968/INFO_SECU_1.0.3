# protocols/psi_card.py — PSI-Card (cardinality only) 协议
from app import Config, read_cardinality_from_file
from .base import BaseGroupManager, BaseRunner

class PSICardGroupManager(BaseGroupManager):
    file_path = Config.PSI_CARD_GROUPS_FILE
    id_length = 4
    max_members = 2

    supports_history = False  # 当前没多轮
    result_field = 'cardinality_result'
    data_dir_attr = 'SPSO_PSI_CARD_DATA_DIR'

