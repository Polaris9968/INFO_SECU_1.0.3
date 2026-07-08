# protocols/ss_psi.py — SS-PSI (多方,4方) 协议(mock 实现)
from app import Config
from .base import BaseGroupManager, KunlunRunner


class SSPSIGroupManager(BaseGroupManager):
    EXPECTED_PARTIES = 4

    file_path = Config.SS_PSI_GROUPS_FILE
    id_length = 4
    max_members = 4   # ★ EXPECTED_PARTIES

    supports_history = False
    result_field = 'result'
    data_dir_attr = 'KUNLUN_SS_PSI_DATA_DIR'

    @classmethod
    def create_group(cls, group_name, creator, **kwargs):
        """SS-PSI: 加 expected_parties 字段,无 standardize_mode"""
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
            'expected_parties': cls.EXPECTED_PARTIES,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        data["groups"].append(group_data)
        cls.save_groups(data)
        return group_data


class MockSSPSI(KunlunRunner):
    """SS-PSI: 不真跑,返回 mock 结果"""
    kind = 'mock'
    log_tag = 'SS-PSI-Mock'

    @classmethod
    def _run_mock(cls, group_id):
        from app import datetime as dt_mod
        from app import SSPSIGroupManager
        group = SSPSIGroupManager.get_group(group_id)
        if not group:
            return {'success': False, 'error': '小组不存在'}
        return {
            'success': True,
            'cardinality': 3,
            'intersection': ['user_blacklisted_023', 'user_blacklisted_089', 'user_blacklisted_142'],
            'parties': [
                {'name': m, 'count': len([u for u in group['uploads'] if u['username'] == m][0]['items'])}
                for m in group['members']
            ],
            'computed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'computed_by': '',
            'note': 'mock 运算结果(多方协议未真实现,仅验证 4 方成员管理流程)'
        }


from datetime import datetime  # noqa