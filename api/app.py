"""Thin FastAPI entry. Collects user message + attachments, then calls run_agent()."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.orchestrator import run_agent
from agent.state import SUPPORTED_DATA_SOURCES
from api.profile_view import load_profile_view
from api.resume_extract import ResumeExtractError, extract_attachment_text, normalize_attachment_kind
from candidate.errors import ATTACHMENT_PROCESSING_FAILED
from api.runs import ConversationStore, RunStore, start_agent_run
from storage.candidate_profile import DEFAULT_CANDIDATE_ID
from task.store import JobSearchTaskStore

LOGGER = logging.getLogger("api")
ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
DEFAULT_DATA_SOURCE = "boss"
DEFAULT_PROFILE_DIR = ROOT_DIR / "data" / "candidate_profiles"
DEFAULT_TASK_DIR = ROOT_DIR / "data" / "job_search_tasks"

app = FastAPI(title="AI Job Agent", docs_url=None, redoc_url=None)
run_store = RunStore()
conversation_store = ConversationStore()
task_store = JobSearchTaskStore(DEFAULT_TASK_DIR)
run_agent_fn = run_agent
llm_provider = None
profile_dir = str(DEFAULT_PROFILE_DIR)
candidate_id = DEFAULT_CANDIDATE_ID


def configured_data_source(requested: str | None = None) -> str:
    raw = (requested or os.environ.get("AGENT_DATA_SOURCE") or DEFAULT_DATA_SOURCE).strip()
    source = raw.lower()
    if source not in SUPPORTED_DATA_SOURCES:
        raise ResumeExtractError("unsupported_data_source", f"不支持的数据源：{raw}")
    return source


def configured_profile_dir() -> str:
    return os.environ.get("CANDIDATE_PROFILE_DIR") or profile_dir


@app.post("/api/run-agent")
async def post_run_agent(
    message: str = Form(default=""),
    goal: str = Form(default=""),
    conversation_id: str | None = Form(default=None),
    data_source: str | None = Form(default=None),
    attachments: list[UploadFile] | None = File(default=None),
    attachment_kinds: list[str] | None = Form(default=None),
):
    text = (message or goal or "").strip()
    try:
        records = await _load_attachments(attachments, attachment_kinds)
        source = configured_data_source(data_source)
    except ResumeExtractError as exc:
        code = ATTACHMENT_PROCESSING_FAILED if exc.error_code == "resume_unreadable" else exc.error_code
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error_code": code, "message": exc.message},
        )

    if not text and not records:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error_code": "empty_input",
                "message": "请告诉我你想让我帮你做什么，也可以补充你的经历或上传材料。",
            },
        )

    resume_text = None
    conversation = conversation_store.ensure(conversation_id)
    session_context = conversation_store.session_context(conversation.conversation_id)
    LOGGER.info(
        "run-agent accepted message_chars=%s attachments=%s source=%s conversation=%s",
        len(text),
        len(records),
        source,
        conversation.conversation_id,
    )
    run = start_agent_run(
        run_store,
        message=text,
        attachments=records,
        resume_text=resume_text,
        data_source=source,
        run_agent_fn=run_agent_fn,
        llm_provider=llm_provider,
        profile_dir=configured_profile_dir(),
        candidate_id=candidate_id,
        session_context=session_context,
        conversation_id=conversation.conversation_id,
        conversation_store=conversation_store,
        task_store=task_store,
    )
    return {
        "ok": True,
        "run_id": run.run_id,
        "conversation_id": conversation.conversation_id,
        "run_phase": "running",
        "status": None,
        "message": "正在理解你的需求……",
    }


@app.get("/api/run-agent/{run_id}")
def get_run_agent(run_id: str):
    payload = run_store.snapshot(run_id)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error_code": "run_not_found", "message": "找不到这次运行。"},
        )
    return payload


@app.get("/api/profile")
def get_profile():
    return load_profile_view(configured_profile_dir(), candidate_id)


async def _load_attachments(
    uploads: list[UploadFile] | None,
    kinds: list[str] | None,
) -> list[dict]:
    files = [item for item in (uploads or []) if item is not None]
    kind_values = [str(item) for item in (kinds or [])]
    records: list[dict] = []
    for index, upload in enumerate(files):
        # Client label only — never World business identity.
        hint = normalize_attachment_kind(kind_values[index] if index < len(kind_values) else None)
        filename = upload.filename
        try:
            data = await upload.read()
        except Exception:
            raise ResumeExtractError("empty_file", "附件是空的。")
        finally:
            await upload.close()
        text = extract_attachment_text(filename=filename, data=data)
        record = {
            "filename": Path(filename or "").name or None,
            "text": text,
        }
        if hint:
            record["client_hint"] = hint
        records.append(record)
    return records


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=False)
