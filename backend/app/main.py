"""应用入口。生产模式下同端口托管 frontend/dist。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import REPO_ROOT, get_settings
from app.routers import (
    artifacts,
    chat,
    evaluations,
    generated,
    interviews,
    jobs,
    projects,
    resumes,
    runs,
    settings as settings_router,
    system,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_dirs()
    yield


app = FastAPI(title="求职工作台", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.dev_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(runs.router)
app.include_router(jobs.router)
app.include_router(resumes.router)
app.include_router(generated.router)
app.include_router(evaluations.router)
app.include_router(projects.router)
app.include_router(interviews.router)
app.include_router(chat.router)
app.include_router(artifacts.router)
app.include_router(settings_router.router)

_dist = REPO_ROOT / "frontend" / "dist"
if _dist.exists():

    class SPAStaticFiles(StaticFiles):
        """未命中的 GET 回退到 index.html，支持前端 history 路由。"""

        async def get_response(self, path: str, scope):  # type: ignore[override]
            try:
                return await super().get_response(path, scope)
            except Exception:
                if scope["method"] in ("GET", "HEAD"):
                    return await super().get_response("index.html", scope)
                raise

    app.mount("/", SPAStaticFiles(directory=_dist, html=True), name="frontend")


def dev() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":  # pragma: no cover
    dev()
