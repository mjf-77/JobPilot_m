"""FastAPI 入口：挂载 SSE 端点与前端页面。

lifespan 里做预热：建 checkpoint 表 + 编译图，
这样首个请求不会因为初始化而明显变慢。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.applications import router as applications_router
from app.api.auth import router as auth_router
from app.api.chat import get_graph
from app.api.chat import router as chat_router
from app.api.resume import router as resume_router
from app.api.threads import router as threads_router
from app.config import settings
from app.db.base import init_db
from app.graph.checkpointer import get_checkpointer

WEB_DIR = settings.base_dir / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):#`lifespan` 是 FastAPI 的**生命周期钩子**
    init_db()  # 业务表：SQLite 还是 MySQL 由 DATABASE_URL 决定
    get_checkpointer()  # checkpoint 表（LangGraph 用 SqliteSaver）
    get_graph()  # 编译图（进程内单例）
    yield
#- **应用启动的时候**：执行 `yield` 之前的代码
# 执行到 `yield`：FastAPI 正式开始接收 HTTP 请求，服务对外提供服务


app = FastAPI(title="JobPilot", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(resume_router)
app.include_router(threads_router)
app.include_router(applications_router)
app.include_router(chat_router)  #注册路由，把我们写的`/api/chat`接口挂载进 app
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
