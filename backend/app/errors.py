"""统一业务错误码常量与抛出辅助（spec/backend/error-handling.md）。

错误响应结构统一为：
    {"error": {"code": "MACHINE_CODE", "message": "用户可读文案", "detail": {...}}}

实现方式：业务代码调用 raise_api_error() 抛出带结构化 detail 的 HTTPException，
main.py 的全局 handler 再展开成上述外壳。
"""

from typing import NoReturn

from fastapi import HTTPException

# ---- 错误码常量表 ----

AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"  # 登录失败统一文案（防枚举）
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"  # 连续失败触发临时锁定
CHALLENGE_INVALID = "CHALLENGE_INVALID"  # challenge 过期/已用/IP 不符（同一处理路径）
INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"  # refresh 无效/过期/版本不符
PIN_CHANGE_REQUIRED = "PIN_CHANGE_REQUIRED"  # 首登未改 PIN，白名单外一律拒绝
BOOTSTRAP_ALREADY_INITIALIZED = "BOOTSTRAP_ALREADY_INITIALIZED"  # 已有用户，禁止重复初始化
USER_NOT_FOUND = "USER_NOT_FOUND"  # 档案不存在或无权查看（none→404 语义，防枚举）
CUSTODY_HANDOVER_DONE = "CUSTODY_HANDOVER_DONE"  # handover 档案已被本人认领，创建者失权
CONFIRM_NAME_MISMATCH = "CONFIRM_NAME_MISMATCH"  # 删除档案二次确认名字不符
RELATION_INVALID_TRANSITION = "RELATION_INVALID_TRANSITION"  # FSM 非法转换/终态再转换
RELATION_CYCLE_FORBIDDEN = "RELATION_CYCLE_FORBIDDEN"  # elder 边成环
RELATION_DUPLICATE_PAIR = "RELATION_DUPLICATE_PAIR"  # 同对用户已存在非终态边
RELATION_NOT_FOUND = "RELATION_NOT_FOUND"  # 关系边不存在或无权操作
CONNECTION_ALREADY_RESOLVED = "CONNECTION_ALREADY_RESOLVED"  # 合并请求已决议
ATTACHMENT_INVALID = "ATTACHMENT_INVALID"  # 附件校验失败（格式/内容/超限）
ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
ATTACHMENT_NOT_FOUND = "ATTACHMENT_NOT_FOUND"  # 附件不存在或不可见
SPACE_JOIN_NO_TARGET_SPACE = "SPACE_JOIN_NO_TARGET_SPACE"  # 对方尚无家庭空间
SPACE_FORBIDDEN_ACTOR = "SPACE_FORBIDDEN_ACTOR"  # 空间操作权限不足
RELATION_SELF_FORBIDDEN = "RELATION_SELF_FORBIDDEN"  # 自环关系
SPACE_NOT_FOUND = "SPACE_NOT_FOUND"  # 空间/成员记录不存在或无权见
IDENTITY_INVALID_TRANSITION = "IDENTITY_INVALID_TRANSITION"  # v2 身份/确档 FSM 非法转换
OWNER_TRANSFER_REQUIRED = "OWNER_TRANSFER_REQUIRED"  # v2：空间所有者删除前必须移交
OWNER_INVITATION_INVALID = "OWNER_INVITATION_INVALID"  # 邀请无效/过期/已用/撤销（防枚举）
OWNER_INVITATION_ACCOUNT_NOT_CLAIMED = "OWNER_INVITATION_ACCOUNT_NOT_CLAIMED"
OWNER_TRANSFER_INVALID = "OWNER_TRANSFER_INVALID"  # v2：移交 FSM 非法转换/目标不合格
DATA_RIGHT_REQUEST_NOT_FOUND = "DATA_RIGHT_REQUEST_NOT_FOUND"  # v2：请求不存在或非本人（防枚举）
DATA_RIGHT_INVALID_TRANSITION = "DATA_RIGHT_INVALID_TRANSITION"  # v2：数据权利 FSM 非法转换
DATA_RIGHT_EXPORT_NOT_READY = "DATA_RIGHT_EXPORT_NOT_READY"
DATA_RIGHT_REQUEST_EXPIRED = "DATA_RIGHT_REQUEST_EXPIRED"  # 导出过期（有过期下载）
CLAIM_DISPUTE_NOT_FOUND = "CLAIM_DISPUTE_NOT_FOUND"  # v2：争议不存在或无权操作
BREAK_GLASS_NOTE_REQUIRED = "BREAK_GLASS_NOTE_REQUIRED"  # v2：平台人工兜底必须携带理由
VALIDATION_ERROR = "VALIDATION_ERROR"
DISCLOSURE_SCOPE_REQUIRES_SELF = "DISCLOSURE_SCOPE_REQUIRES_SELF"  # v2：逐空间披露仅本人可改
INTERNAL_ERROR = "INTERNAL_ERROR"

# 防枚举统一文案（error-handling.md 红线）
UNIFIED_CREDENTIAL_MESSAGE = "名字或 PIN 码错误"


def raise_api_error(
    status_code: int,
    code: str,
    message: str,
    detail: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> NoReturn:
    """以统一错误结构抛出业务异常（NoReturn：mypy 可知后续不可达）。"""
    payload: dict[str, object] = {"code": code, "message": message}
    if detail is not None:
        payload["detail"] = detail
    raise HTTPException(
        status_code=status_code, detail={"__api_error__": payload}, headers=headers
    ) from None


def extract_api_error(detail: object) -> dict[str, object] | None:
    """识别 raise_api_error 的结构化 detail；普通 HTTPException 返回 None。"""
    if isinstance(detail, dict) and "__api_error__" in detail:
        return detail["__api_error__"]  # type: ignore[no-any-return]
    return None
