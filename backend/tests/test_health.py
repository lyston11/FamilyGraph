"""健康检查端点契约测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok_under_api_prefix() -> None:
    client = TestClient(app)
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
