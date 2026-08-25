"""日志脱敏断言测试（implement.md #9）：含 PIN 的请求遍历后，日志无凭据痕迹。

红线（logging-guidelines.md）：PIN（任何形式）、JWT、pin_hash、challenge、
refresh token 永不入日志；姓名/生卒等 PII 只允许进 audit_log 表。
"""

import logging

from conftest import auth_header, create_user_with_pin, login

from app.logctx import JsonFormatter

SECRET_PIN = "741258"


def _formatted_log_lines(caplog) -> list[str]:
    """用生产 JsonFormatter 渲染捕获的记录，检验真实输出形态。"""
    formatter = JsonFormatter()
    return [formatter.format(record) for record in caplog.records]


def test_no_pin_or_token_leaks_in_logs(client, db_session, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        body = client.post("/api/bootstrap/initialize", json={"name": "族长"}).json()
        admin_pin = body["one_time_pin"]

        create_user_with_pin(db_session, "张三", SECRET_PIN)
        tokens = login(client, "张三", SECRET_PIN).json()
        # 失败登录（请求体携带 PIN）
        login(client, "张三", "000000")
        login(client, "不存在的人", SECRET_PIN)
        # challenge 流程（同名同 PIN）：选中后获得全新 token 对
        create_user_with_pin(db_session, "张三", SECRET_PIN)
        conflict = login(client, "张三", SECRET_PIN)
        assert conflict.status_code == 409
        selected = client.post(
            "/api/auth/login/select",
            json={
                "challenge_id": conflict.json()["challenge_id"],
                "user_id": tokens["user"]["id"],
            },
        )
        assert selected.status_code == 200
        selected_tokens = selected.json()
        # 认证后的敏感操作
        refresh_result = client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refresh_result.status_code == 200
        fresh = refresh_result.json()
        client.post(
            "/api/auth/logout",
            headers=auth_header(tokens),
            json={"refresh_token": tokens["refresh_token"]},
        )
        changed = client.put(
            "/api/me/pin",
            headers=auth_header(selected_tokens),
            json={"old_pin": SECRET_PIN, "new_pin": "998877"},
        )
        assert changed.status_code == 200

    log_text = "\n".join(_formatted_log_lines(caplog))

    secrets_never_logged = [
        SECRET_PIN,
        admin_pin,
        "000000",
        "998877",
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["access_token"].split(".")[1],  # JWT payload 段
        fresh["access_token"],
        fresh["refresh_token"],
    ]
    for secret in secrets_never_logged:
        assert secret not in log_text, f"凭据泄露到日志: {secret[:12]}..."

    # JWT 形态的 token 整体不应出现（防未来误加）
    assert "eyJ" not in log_text


def test_log_lines_are_structured_json_with_request_id(client, db_session, caplog) -> None:
    import json as jsonlib

    with caplog.at_level(logging.INFO):
        create_user_with_pin(db_session, "张三", SECRET_PIN)
        response = login(client, "张三", SECRET_PIN)
        request_id = response.headers.get("X-Request-ID")
        assert request_id  # 中间件注入

    lines = [line for line in _formatted_log_lines(caplog) if line.startswith("{")]
    assert lines, "应存在结构化 JSON 日志行"
    parsed = [jsonlib.loads(line) for line in lines]
    for entry in parsed:
        assert {"ts", "level", "logger", "msg", "user_id", "request_id"} <= set(entry)


def test_error_responses_never_contain_stack_traces(client) -> None:
    response = client.post(
        "/api/auth/login",
        content=b"{invalid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in (401, 422)
    text = response.text.lower()
    assert "traceback" not in text
    assert ".py" not in text
