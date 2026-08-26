"""Steward/ActionCard 领域事件 taxonomy 常量（V2.4 Block S1）。

domain_events.type 命名空间约定 `<domain>.<event>`；card.* 与 steward.* 为
V2.4 新增命名空间。事件只追加（append-only，services/domain_events.emit 唯一
写入口），消费方按 type 前缀订阅。卡片生命周期每次转换都必须落对应事件；
steward 作业完成/失败与冲突/缺失发现同样落事件（ST-3：目的明确的 DomainEvent，
不采集泛行为）。
"""

# ---- ActionCard 生命周期（转换 → 事件一一对应）----
EVENT_CARD_VIEWED = "card.viewed"
EVENT_CARD_DISMISSED = "card.dismissed"
EVENT_CARD_ACCEPTED = "card.accepted"
EVENT_CARD_EXECUTED = "card.executed"
EVENT_CARD_EXPIRED = "card.expired"
EVENT_CARD_SUPERSEDED = "card.superseded"

CARD_EVENT_BY_ACTION: dict[str, str] = {
    "view": EVENT_CARD_VIEWED,
    "dismiss": EVENT_CARD_DISMISSED,
    "accept": EVENT_CARD_ACCEPTED,
    "execute": EVENT_CARD_EXECUTED,
    "expire": EVENT_CARD_EXPIRED,
    "supersede": EVENT_CARD_SUPERSEDED,
}

# ---- Steward 作业与发现 ----
EVENT_STEWARD_JOB_COMPLETED = "steward.job_completed"
EVENT_STEWARD_JOB_FAILED = "steward.job_failed"
EVENT_STEWARD_CONFLICT_DETECTED = "steward.conflict_detected"  # confirmed 事实互相矛盾
EVENT_STEWARD_GAP_DETECTED = "steward.gap_detected"  # 缺失（如 sibling 无共同父母），只报告

# ---- 聚合类型 ----
AGGREGATE_ACTION_CARD = "action_card"
AGGREGATE_STEWARD_JOB = "steward_job"
