from contextlib import asynccontextmanager
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import ensure_db_ready
from routers import convert


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_db_ready()
    yield


app = FastAPI(title="Image to Excel Studio API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"服务器处理异常：{exc}",
            "message": str(exc),
            "path": request.url.path,
        },
    )


app.include_router(convert.router)
