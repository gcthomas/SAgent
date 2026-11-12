"""Web API 路由统一注册入口。

会话、轨迹路由在此集中 include；后续对话、运行与审批路由按同一结构追加，
保证 create_app 只依赖本模块完成路由装配。
"""

from __future__ import annotations

from fastapi import FastAPI

from .approvals import router as approvals_router
from .chat import router as chat_router
from .runs import router as runs_router
from .sessions import router as sessions_router
from .trace import router as trace_router


def register_routers(app: FastAPI) -> None:
    """注册全部业务路由。

    参数:
        app: 已完成共享依赖装配的 FastAPI 应用。
    """
    app.include_router(sessions_router)
    app.include_router(trace_router)
    app.include_router(chat_router)
    app.include_router(runs_router)
    app.include_router(approvals_router)
