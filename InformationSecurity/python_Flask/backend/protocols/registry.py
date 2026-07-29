# protocols/registry.py — 6 个 ProtocolSpec 实例
"""
基于对 app.py 实际 @app.route 的精确统计(81 协议路由 + 2 demo = 83)。

SPIKE 2 集成: PROTOCOL_PSI 的 runner_cls 从 KunlunPSI 切到 SpsoPSI。
SPIKE 3 集成: PROTOCOL_PSU / PROTOCOL_PSI_CARD / PROTOCOL_PSI_MATCH 切到 sPSO。
Kunlun 类保留作 fallback(注释标 'SPIKE 3: fallback')。
"""
from .base import ProtocolSpec
from .psi import PSIGroupManager, KunlunPSI
from .spso import SpsoPSI, SpsoPSU, SpsoPSICard, SpsoPSIMatch, SpsoPSISum
from .psi_card import PSICardGroupManager, KunlunPSICard
from .psu import PSIUnionGroupManager, KunlunPSU
from .psi_match import PSIMatchGroupManager, KunlunPSIMatch
from .psi_sum import PSISumGroupManager, KunlunPSISum
from .ss_psi import SSPSIGroupManager, SpsoSSPSI


# ==================== 6 个 ProtocolSpec ====================

PROTOCOL_PSI = ProtocolSpec(
    protocol_id='psi',
    url_prefix='/api/psi-group',
    manager_cls=PSIGroupManager,
    page_filename='privacy_intersection.html',
    upload_data_dir_attr='KUNLUN_PSI_DATA_DIR',
    id_length=4,
    runner_cls=SpsoPSI,        # SPIKE 2: 切到 sPSO,KunlunPSI 保留作 fallback
    # 端点开关
    has_history=True,
    has_preview_ciphertext=True,
    has_download_result=True,
    has_download_result_with_original=True,
    has_download_ciphertext_by_role=True,
    has_download_original_by_role=True,
    has_download_round=True,
    finalize_round_endpoint=True,
    start_computation_endpoint=True,
    # 认证
    auth_method='jwt_required',
)

PROTOCOL_PSI_CARD = ProtocolSpec(
    protocol_id='psi_card',
    url_prefix='/api/psi-card-group',
    manager_cls=PSICardGroupManager,
    page_filename='psi_match.html',
    upload_data_dir_attr='KUNLUN_PSI_CARD_DATA_DIR',
    id_length=4,
    runner_cls=SpsoPSICard,           # SPIKE 3: 切到 sPSO,KunlunPSICard 保留作 fallback
    has_history=False,                       # PSI-Card 当前无多轮
    has_preview_ciphertext=False,
    has_download_result=True,
    has_download_result_with_original=True,
    has_download_ciphertext_by_role=False,
    has_download_original_by_role=False,
    has_download_round=False,
    finalize_round_endpoint=False,
    start_computation_endpoint=True,
    auth_method='jwt_required',
)

PROTOCOL_PSU = ProtocolSpec(
    protocol_id='psu',
    url_prefix='/api/psi-union-group',
    manager_cls=PSIUnionGroupManager,
    page_filename='privacy_union.html',
    upload_data_dir_attr='KUNLUN_PSI_UNION_DATA_DIR',
    id_length=4,
    runner_cls=SpsoPSU,               # SPIKE 3: 切到 sPSO,KunlunPSU 保留作 fallback
    has_history=True,
    has_preview_ciphertext=True,
    has_download_result=True,
    has_download_result_with_original=True,
    has_download_ciphertext_by_role=False,
    has_download_original_by_role=False,
    has_download_round=True,
    finalize_round_endpoint=True,
    start_computation_endpoint=True,
    auth_method='jwt_required',
)

PROTOCOL_PSI_MATCH = ProtocolSpec(
    protocol_id='psi_match',
    url_prefix='/api/psi-match-group',
    manager_cls=PSIMatchGroupManager,
    page_filename='psi_match.html',
    upload_data_dir_attr='KUNLUN_PSI_CARD_DATA_DIR',   # PSI-Match 复用 PSI-Card 目录
    id_length=4,
    runner_cls=SpsoPSIMatch,          # SPIKE 3: 切到 sPSO(用 psi 模拟),KunlunPSIMatch 保留作 fallback
    has_history=True,
    has_preview_ciphertext=True,
    has_download_result=False,                          # PSI-Match 无 download-result
    has_download_result_with_original=True,
    has_download_ciphertext_by_role=False,
    has_download_original_by_role=False,
    has_download_round=True,
    finalize_round_endpoint=True,
    start_computation_endpoint=True,
    auth_method='jwt_required',
)

PROTOCOL_PSI_SUM = ProtocolSpec(
    protocol_id='psi_sum',
    url_prefix='/api/psi-sum-group',
    manager_cls=PSISumGroupManager,
    page_filename='psi_match.html',
    upload_data_dir_attr='KUNLUN_PSI_SUM_DATA_DIR',
    id_length=4,
    runner_cls=SpsoPSISum,             # SPIKE 4: 切到 sPSO, KunlunPSISum 保留作 fallback
    has_history=True,
    has_preview_ciphertext=False,
    has_download_result=False,
    has_download_result_with_original=False,
    has_download_ciphertext_by_role=False,
    has_download_original_by_role=False,
    has_download_round=True,
    has_demo_endpoint=True,
    finalize_round_endpoint=True,
    start_computation_endpoint=True,
    auth_method='jwt_required',
    # PSI-Sum 特殊: delete-upload 是 POST /api/psi-sum-group/<id>/delete-upload
    supports_leave=True,
    supports_remove_upload=True,
)

PROTOCOL_SS_PSI = ProtocolSpec(
    protocol_id='ss_psi',
    url_prefix='/api/ss-psi-groups',                   # ★ 复数
    manager_cls=SSPSIGroupManager,
    # SPIKE 5 (2026-07-30):page_filename='ss_psi.html' 已删除 — 该文件不存在,
    # SS-PSI UI 全在 home.html 的嵌入容器 + home-psi-pages.js 的 window.SS_PSI IIFE
    upload_data_dir_attr='KUNLUN_SS_PSI_DATA_DIR',
    id_length=4,
    max_members=2,  # SPIKE 5: 2-party
    runner_cls=SpsoSSPSI,
    is_mock=False,
    has_history=False,
    has_preview_ciphertext=False,
    has_download_result=False,
    has_download_result_with_original=False,
    has_download_ciphertext_by_role=False,
    has_download_original_by_role=False,
    has_download_round=False,
    has_demo_endpoint=True,
    finalize_round_endpoint=False,
    start_computation_endpoint=True,
    # SS-PSI 特殊
    supports_leave=False,
    supports_remove_upload=False,
    auth_method='login_required_api',                   # ★ 不用 @jwt_required
)


PROTOCOLS = [
    PROTOCOL_PSI,
    PROTOCOL_PSI_CARD,
    PROTOCOL_PSU,
    PROTOCOL_PSI_MATCH,
    PROTOCOL_PSI_SUM,
    PROTOCOL_SS_PSI,
]