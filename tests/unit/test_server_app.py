"""Web 服务应用工厂单元测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sagent import __version__
from sagent.config.models import AppConfig
from sagent.server.app import create_app


def _make_config(tmp_path: Path, static_dir: str) -> AppConfig:
    """构造不依赖真实 LLM 与外部资源的测试配置。

    参数:
        tmp_path: 测试临时目录，用于存放会话数据库。
        static_dir: 前端静态资源目录。

    返回:
        测试用 AppConfig。
    """
    return AppConfig(
        llm={"model": "gpt-4o-mini", "api_key": "test"},
        context={"enable_summary": False},
        memory={"enabled": False},
        skills={"enabled": False},
        session={"db_path": str(tmp_path / "sessions.db"), "enable_fts": False},
        server={"static_dir": static_dir},
    )


def test_health_returns_status_and_version(tmp_path):
    config = _make_config(tmp_path, str(tmp_path / "missing-dist"))
    with TestClient(create_app(config)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_server_config_defaults_when_section_missing():
    # 配置文件中缺少 server 段时使用默认值
    config = AppConfig(llm={"model": "gpt-4o-mini", "api_key": "test"}, context={"enable_summary": False})

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8000
    assert config.server.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert config.server.static_dir == "web/dist"
    assert config.server.enable_cors is True


def test_root_without_built_frontend_returns_hint(tmp_path):
    # 静态目录不存在时根路径返回未构建提示，API 仍可用
    config = _make_config(tmp_path, str(tmp_path / "missing-dist"))
    with TestClient(create_app(config)) as client:
        root = client.get("/")
        health = client.get("/api/health")

    assert root.status_code == 200
    assert "前端未构建" in root.text
    assert health.status_code == 200


def test_spa_fallback_serves_index_html(tmp_path):
    # 静态目录存在时，深层前端路由回退到 index.html
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>SAgent 前端</title>", encoding="utf-8")
    config = _make_config(tmp_path, str(dist))

    with TestClient(create_app(config)) as client:
        response = client.get("/some/deep/route")

    assert response.status_code == 200
    assert "SAgent 前端" in response.text


def test_unknown_api_path_returns_not_found(tmp_path):
    # 未知 /api 路径返回统一错误结构，不被 SPA 回退遮蔽
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    config = _make_config(tmp_path, str(dist))

    with TestClient(create_app(config)) as client:
        response = client.get("/api/not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["message"] == "接口不存在"
