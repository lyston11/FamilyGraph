"""保底名额：四类辅助在预算内都不得被结构性饿死。

## 实测背景（生产，2026-10-08）

预算是 **per job** 的（默认 6），而 explanation **按卡逐个**预留（每张卡一个
attempt，上限 `MAX_CARDS_PER_JOB`）。因此 `explanation 5 + ranking 1 = 6` 之后，
排在后面的 terminology 与 candidate **结构上**拿不到名额——不是被抢占，而是轮到
它们时预算已为 0。实测 22 次 terminology 中 16 次 `budget_exhausted`。

`_ordered_kinds` 的按空间轮转只决定**顺序**，不保证**名额**。修复是两遍预留：
保底遍（每类先拿 1 个）+ 填充遍（剩余按轮转顺序填满）。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/services/steward_assist.py"


def _source() -> str:
    return SRC.read_text()


def _function_source(name: str) -> str:
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(_source(), node) or ""
    raise AssertionError(f"{name} 不存在")


def test_reserve_uses_two_passes_with_a_floor() -> None:
    """`_reserve_plan_attempts` 必须有保底遍 + 填充遍。"""
    body = _function_source("_reserve_plan_attempts")
    assert "reserve_pass(_MIN_ATTEMPTS_PER_KIND)" in body, "缺少保底遍"
    assert "reserve_pass(None)" in body, "缺少填充遍"
    # 保底遍必须先于填充遍，否则保底没有意义。
    assert body.index("reserve_pass(_MIN_ATTEMPTS_PER_KIND)") < body.index(
        "reserve_pass(None)"
    ), "保底遍必须在填充遍之前"


def test_floor_is_one_attempt_per_kind() -> None:
    """保底取 1：目标是「每类都能轮到」，不是均分预算。

    取 1 时四类都有活则各得 1 个、剩 2 个按轮转填充；只有一类有活时该类仍可用满
    全部预算（保底遍是填充遍的前缀，不浪费）。
    """
    tree = ast.parse(_source())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_MIN_ATTEMPTS_PER_KIND":
                    assert isinstance(node.value, ast.Constant)
                    assert node.value.value == 1, "_MIN_ATTEMPTS_PER_KIND 必须为 1"
                    return
    raise AssertionError("_MIN_ATTEMPTS_PER_KIND 不存在")


def test_subject_is_attempted_once_across_both_passes() -> None:
    """同一 subject 在两遍之间不得重复预留（否则保底遍会重复扣预算）。"""
    body = _function_source("_reserve_plan_attempts")
    assert "attempted_subjects" in body, "缺少去重集合"
    assert "if subject_key in attempted_subjects" in body, "缺少 subject 去重判断"


def test_explanation_cannot_monopolise_the_budget() -> None:
    """`MAX_CARDS_PER_JOB` 必须小于每 job 预算，否则单类可独占全部名额。

    这是 5→3 的根据：取 5 时 `explanation 5 + ranking 1 = 6` 恰好吃满预算。
    """
    from app import config

    assert (
        config.STEWARD_ASSIST_MAX_CARDS_PER_JOB < config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB
    ), "MAX_CARDS_PER_JOB 不得 >= 每 job 预算——explanation 会独占全部名额，" "使其它三类结构性饿死"
    # 四类各至少 1 个名额后必须仍有剩余，否则保底遍本身就填满预算。
    kinds = 4
    assert config.STEWARD_ASSIST_MAX_CARDS_PER_JOB + kinds <= (
        config.STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB + 1
    ), "explanation 上限加四类保底已超过预算，保底无法全部落实"


# ------------------------------------------------- B: context_hash 回显


def test_validator_does_not_require_hash_echo() -> None:
    """terminology 校验器不得要求模型逐字回显 `context_hash`。

    ## 实测背景

    旧校验要求 `set(payload) == {"version","context_hash","items"}` 且哈希与服务端
    重算值逐字相等。生产 attempt 82 落 `degraded/invalid_output`，诊断显示
    `text_chars=104, looks_like_json=true`——紧凑 JSON
    `{"version":1,"context_hash":"<64 hex>","items":[]}` 恰好约 105 字符，即模型
    **格式正确、items 为空（合法的「无改善」结果）**，只是哈希没逐字对上。

    ## 为什么去掉不损失安全性

    `context_hash` 从来不是信任边界：每个 item 的 `target_ref` 必须在服务端栅栏
    给出的代号集内且不得重复；`semantic_hash`/`concept_code` 会用
    `current_target_context` 在**当前**数据库状态上重算比对；`term` 必须落在服务端
    `allowed_terms` 内。回显的哈希无法授权任何一个 item。
    """
    src = Path(__file__).resolve().parents[1] / "app/services/steward_terminology.py"
    body = src.read_text()
    start = body.index("def validate_model_output")
    end = body.index("def ", start + 10)
    validator = body[start:end]
    assert '"context_hash" !=' not in validator, "校验器仍在逐字比对 context_hash"
    assert (
        '{"version", "context_hash", "items"}' not in validator
    ), "校验器仍要求精确键集包含 context_hash"
    # 仍必须拒绝 items 之外的多余字段（防协议漂移）。
    assert '{"version", "context_hash"}' in validator, "未对额外字段设限"


def test_prompt_no_longer_asks_for_hash_echo() -> None:
    """提示词不得再要求模型回显 `context_hash`（要求了就该校验，不校验就别要求）。"""
    body = _source()
    start = body.index('"terminology": (')
    end = body.index('"explanation": (')
    prompt = body[start:end]
    assert "context_hash" not in prompt, (
        "terminology 提示词仍在要求回显 context_hash——要求了却不校验会造成提示词与" "校验器不一致"
    )


# ------------------------------------------------- C: 提取方式必须一致


def test_terminology_uses_the_same_lenient_extractor() -> None:
    """terminology 必须与其它 kind 用**同一个** JSON 提取器。

    candidate/ranking/explanation 都经 `steward_guard._extract_json`（容忍 json 围栏、
    允许 JSON 前后有解释文字），而 terminology 曾用裸 `json.loads`。于是模型给出带围栏或
    带说明的输出时，**只有 terminology 失败**。生产上三次 `degraded/invalid_output`
    的诊断均为 `looks_like_json=false`、`text_chars` 546/1456/687，而同一提示词下
    candidate 与 ranking 正常。

    宽容提取**不降低安全性**：真正的授权是服务端重算栅栏（target_ref 集合、
    semantic_hash/concept_code 重算、allowed_terms 限定），提取方式不影响它们。

    注意：必须检查**代码**而不是文本。注释里会提到旧写法，用字符串包含判断会误报
    （这一版就是被注释绊倒后改成 AST 的）。
    """
    src = Path(__file__).resolve().parents[1] / "app/services/steward_terminology.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "validate_model_output"):
            continue
        calls = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                calls.add(getattr(fn, "attr", None) or getattr(fn, "id", None))
        assert (
            "_extract_json" in calls
        ), "terminology 未调用与其它 kind 相同的宽容提取器 `_extract_json`"
        assert (
            "loads" not in calls
        ), "terminology 仍直接调用 `json.loads`——带围栏/带说明的输出会独自失败"
        return
    raise AssertionError("validate_model_output 不存在")
