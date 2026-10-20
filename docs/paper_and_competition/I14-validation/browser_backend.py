"""Loopback-only synthetic UI acceptance server; never opens the production knowledge store."""
# ruff: noqa: E402
import os
from pathlib import Path
import secrets
import sys
import tempfile

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "engineering/python"))
temporary = tempfile.TemporaryDirectory(prefix="lingjing-review-browser-")
os.environ["LINGJING_PROCESS_KNOWLEDGE_DIR"] = temporary.name
os.environ["LNN_ENV"] = "test"
os.environ["LNN_JWT_SECRET"] = secrets.token_urlsafe(48)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.auth.dependencies import get_current_user
from app.rag.review_routes import router
from app.rag.process_quadruple import ProcessQuadruple, get_process_quadruple_index
from app.rag.service import add_process_quadruple

app = FastAPI(title="Synthetic process review acceptance only")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:1421"], allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
app.dependency_overrides[get_current_user] = lambda: {"username": "synthetic_review_engineer", "role": "engineer"}


@app.middleware("http")
async def fixture_auth(request: Request, call_next):
    if request.method != "OPTIONS" and request.headers.get("authorization") != "Bearer synthetic-browser-fixture-only":
        from starlette.responses import JSONResponse
        return JSONResponse({"detail": "Synthetic fixture authentication required"}, status_code=401)
    return await call_next(request)


app.include_router(router)


@app.post("/api/rag/process/add")
async def add(request: dict):
    return await add_process_quadruple(request)


index = get_process_quadruple_index()
index.add(ProcessQuadruple("hole", "drill", "fixture_drill_d8", {"spindle_rpm": 1200}, material="fixture_steel",
                          source="synthetic_test", candidate_context={"source_url": "https://example.org/synthetic-test.pdf",
                          "page": 2, "parameter_units": {"spindle_rpm": "rpm"}, "unresolved": ["仅为隔离验收的合成资料"],
                          "note": "合成样本，仅验证界面与审核流程，不用于实际加工"}))

if __name__ == "__main__":
    try:
        uvicorn.run(app, host="127.0.0.1", port=18765)
    finally:
        temporary.cleanup()
