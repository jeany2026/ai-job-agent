"""Web entry tests. Mock run_agent; never open BOSS."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.state import AgentState, JobRecord, new_agent_state
from api.resume_extract import DOC_UNSUPPORTED_MESSAGE, ResumeExtractError, extract_attachment_text, extract_resume_text
from api.runs import ConversationStore, RunStore
from storage.candidate_profile import CandidateProfileStore
from tests.mock_llm import AgentRoutingLLM, candidate_profile_response, understanding_response

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RESUME = (ROOT / "tests" / "fixtures" / "resumes" / "sample_pm.txt").read_text(encoding="utf-8")
GOAL = "帮我找深圳高级产品经理，金融科技/支付方向"


def make_pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET\n"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj",
        f"4 0 obj << /Length {len(stream.encode('latin-1'))} >> stream\n{stream}endstream endobj",
        "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj.encode("latin-1") + b"\n"
    xref_pos = len(out)
    xref = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets)
    trailer = f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    return out + "".join(xref).encode("latin-1") + trailer.encode("latin-1")


def make_docx_bytes(text: str) -> bytes:
    from docx import Document

    document = Document()
    for line in text.splitlines() or [text]:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def done_state() -> AgentState:
    state = new_agent_state(resume=SAMPLE_RESUME, goal_input=GOAL, data_source="mock")
    state.status = "DONE"
    match = {
        "recommendation": "yes",
        "overall_fit": "strong",
        "hard_requirements_met": True,
        "rationale": "产品规划经验可以直接覆盖支付业务系统，而不是只因为关键词相同。",
        "capability_assessments": [
            {"dimension": "产品规划", "outcome": "direct"},
            {
                "dimension": "复杂业务系统设计",
                "outcome": "transferable",
                "transfer_rationale": "支付中台经验可迁移到医疗支付对账场景",
            },
        ],
        "risks": [{"risk": "行业切换", "severity": "medium", "notes": "医疗合规节奏不同"}],
        "knowledge_gaps": [{"gap": "保险牌照细节", "severity": "low"}],
    }
    listed = {
        "platform": "mock",
        "job_id": "mock-direct",
        "job_title": "高级产品经理",
        "company_name": "支付科技有限公司",
        "job_url": "https://mock.local/job/mock-direct",
    }
    state.jobs["mock:mock-direct"] = JobRecord(
        job_key="mock:mock-direct",
        stage="recommended",
        listed_order=0,
        listed=listed,
        opened=listed,
        match_result=match,
    )
    state.goal = {
        "analysis_status": "ok",
        "target_roles": ["高级产品经理"],
        "cities": ["深圳"],
        "salary_min": None,
        "focus_areas": ["金融科技"],
        "exclude_companies": [],
        "platforms": [],
    }
    state.output = {
        "recommended": [
            {
                "job_key": "mock:mock-direct",
                "platform": "mock",
                "job_id": "mock-direct",
                "job_title": "高级产品经理",
                "company_name": "支付科技有限公司",
                "recommendation": "yes",
                "overall_fit": "strong",
                "hard_requirements_met": True,
                "rationale": match["rationale"],
                "capability_assessments": match["capability_assessments"],
            }
        ],
        "excluded": [],
    }
    return state


def failed_state(*, message: str, kind: str = "candidate", profile_status: str = "llm_unavailable") -> AgentState:
    state = new_agent_state(resume=SAMPLE_RESUME, goal_input=GOAL, data_source="mock")
    state.status = "FAILED"
    state.errors.append({"kind": kind, "message": message})
    state.candidate.profile = {"analysis_status": profile_status, "error": message}
    state.candidate.profile_status = "failed"
    return state


def _ok_profile(**overrides) -> dict:
    profile = candidate_profile_response()
    profile["analysis_status"] = "ok"
    profile["error"] = None
    profile["candidate_id"] = "default"
    profile.update(overrides)
    return profile


class _Routing(AgentRoutingLLM):
    def __init__(self, understanding: dict | None = None):
        super().__init__()
        self.understanding = understanding

    def complete_json(self, *, system: str, user: str) -> dict:
        if self.understanding is not None and "UserInputUnderstanding" in system:
            self.calls.append({"system": system, "user": user})
            return self.understanding
        return super().complete_json(system=system, user=user)


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    from agent.orchestrator import run_agent
    from api import app as module

    monkeypatch.setattr(module, "run_store", RunStore())
    monkeypatch.setattr(module, "conversation_store", ConversationStore())
    monkeypatch.setattr(module, "llm_provider", None)
    monkeypatch.setattr(module, "run_agent_fn", run_agent)
    monkeypatch.setattr(module, "profile_dir", str(tmp_path))
    monkeypatch.setattr(module, "candidate_id", "default")
    return module


@pytest.fixture
def client(app_module):
    return TestClient(app_module.app)


def wait_run(client: TestClient, run_id: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/run-agent/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if last.get("run_phase") == "finished":
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish: {last}")


def start_run(
    client: TestClient,
    *,
    message: str = GOAL,
    filename: str | None = None,
    content: bytes | None = None,
    kind: str = "resume",
    files: list | None = None,
    kinds: list[str] | None = None,
    data_source: str | None = "mock",
    conversation_id: str | None = None,
    extra: dict | None = None,
):
    data = {"message": message}
    if data_source:
        data["data_source"] = data_source
    if conversation_id:
        data["conversation_id"] = conversation_id
    if extra:
        data.update(extra)
    upload = files
    if upload is None and filename is not None:
        data["attachment_kinds"] = kinds or [kind]
        upload = [("attachments", (filename, content, "application/octet-stream"))]
    elif kinds:
        data["attachment_kinds"] = kinds
    kwargs = {"data": data}
    if upload:
        kwargs["files"] = upload
    return client.post("/api/run-agent", **kwargs)


def test_extract_pdf_success():
    text = extract_resume_text(filename="鲍玲俐_简历.pdf", data=make_pdf_bytes("Senior Product Manager resume"))
    assert "Senior Product Manager resume" in text


def test_extract_docx_success():
    text = extract_resume_text(filename="resume.docx", data=make_docx_bytes(SAMPLE_RESUME))
    assert "高级产品经理" in text
    assert "支付" in text


def test_extract_rejects_unsupported_extension():
    with pytest.raises(ResumeExtractError) as exc:
        extract_resume_text(filename="resume.txt", data=b"hello")
    assert exc.value.error_code == "unsupported_extension"


def test_extract_attachment_txt():
    text = extract_attachment_text(filename="project.txt", data="微信小程序结算项目说明".encode("utf-8"))
    assert "微信小程序" in text


def test_extract_rejects_empty_file():
    with pytest.raises(ResumeExtractError) as exc:
        extract_resume_text(filename="resume.pdf", data=b"")
    assert exc.value.error_code == "empty_file"


def test_extract_rejects_oversize(monkeypatch):
    from api import resume_extract

    monkeypatch.setattr(resume_extract, "MAX_RESUME_BYTES", 16)
    with pytest.raises(ResumeExtractError) as exc:
        extract_resume_text(filename="resume.pdf", data=b"x" * 17)
    assert exc.value.error_code == "file_too_large"


def test_extract_doc_is_explicitly_unsupported():
    with pytest.raises(ResumeExtractError) as exc:
        extract_resume_text(filename="resume.doc", data=b"\xd0\xcf\x11\xe0" + b"not-a-real-doc")
    assert exc.value.error_code == "doc_unsupported"
    assert exc.value.message == DOC_UNSUPPORTED_MESSAGE


def test_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "你想让我帮你做什么" in html
    assert "告诉我你想找什么工作" in html
    assert "我的候选人画像" in html
    assert "整理成功后会出现在这里" in html
    assert "记住即可" not in html
    assert "添加简历" in html
    assert "添加项目资料" in html
    assert "发送" in html
    assert "chat-panel" in html
    assert "请上传简历" not in html
    assert "result-panel" in html


def test_d1_message_without_resume_starts_agent(app_module, client):
    calls = []

    def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return done_state()

    app_module.run_agent_fn = fake_run_agent
    response = start_run(client, message=GOAL)
    assert response.status_code == 200
    payload = wait_run(client, response.json()["run_id"])
    assert calls
    assert calls[0]["message"] == GOAL or calls[0]["goal"] == GOAL
    assert not calls[0].get("resume")
    assert payload["status"] == "DONE"
    assert payload["conversation_id"]


def test_d2_existing_profile_message_only_runs(app_module, client, tmp_path):
    CandidateProfileStore(tmp_path).create(_ok_profile(), candidate_id="default", source_kinds=["resume"])
    app_module.llm_provider = AgentRoutingLLM()
    response = start_run(client, message="帮我找深圳高级产品经理。")
    payload = wait_run(client, response.json()["run_id"], timeout=20)
    assert payload["status"] == "WAITING_USER"
    assert payload["ok"] is True
    assert payload["recommended"]


def test_d3_message_plus_resume_runs(app_module, client):
    calls = []

    def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return done_state()

    app_module.run_agent_fn = fake_run_agent
    response = start_run(
        client,
        filename="resume.docx",
        content=make_docx_bytes(SAMPLE_RESUME),
        kind="resume",
    )
    payload = wait_run(client, response.json()["run_id"])
    assert payload["status"] == "DONE"
    # Upload is raw attachment intake — not a pre-labeled resume= business identity.
    assert not calls[0].get("resume")
    attachments = calls[0].get("attachments") or []
    assert attachments
    assert "高级产品经理" in (attachments[0].get("text") or "")
    hints = [item.get("client_hint") for item in attachments]
    assert "resume" in hints


def test_d4_message_plus_project_attachment_runs(app_module, client):
    calls = []

    def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return done_state()

    app_module.run_agent_fn = fake_run_agent
    response = start_run(
        client,
        message="帮我找高级产品经理，这个项目也请考虑进去。",
        filename="project.txt",
        content="微信小程序结算与对账项目说明".encode("utf-8"),
        kind="project_document",
    )
    payload = wait_run(client, response.json()["run_id"])
    assert payload["ok"] is True
    attachments = calls[0]["attachments"]
    assert any(item.get("client_hint") == "project_document" for item in attachments)
    assert "微信小程序" in attachments[0]["text"]


def test_d5_message_resume_and_project_are_one_input(app_module, client):
    calls = []

    def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return done_state()

    app_module.run_agent_fn = fake_run_agent
    files = [
        ("attachments", ("resume.docx", make_docx_bytes(SAMPLE_RESUME), "application/octet-stream")),
        ("attachments", ("project.txt", "运动小程序项目说明".encode("utf-8"), "text/plain")),
    ]
    response = start_run(
        client,
        message="帮我找深圳高级产品经理，附件里的项目也一并考虑。",
        files=files,
        kinds=["resume", "project_document"],
    )
    payload = wait_run(client, response.json()["run_id"])
    assert payload["status"] == "DONE"
    attachments = calls[0]["attachments"]
    hints = {item.get("client_hint") for item in attachments}
    assert "resume" in hints
    assert "project_document" in hints
    assert calls[0]["message"] == "帮我找深圳高级产品经理，附件里的项目也一并考虑。"


def test_d6_goal_only_can_search_not_missing_resume(app_module, client):
    app_module.llm_provider = AgentRoutingLLM()
    response = start_run(client, message="帮我找深圳高级产品经理。")
    assert response.status_code == 200
    assert response.json().get("error_code") != "missing_resume"
    payload = wait_run(client, response.json()["run_id"], timeout=20)
    assert payload["error_code"] != "missing_resume"
    assert payload["error_code"] != "insufficient_candidate"
    assert payload["status"] in {"WAITING_USER", "DONE"}
    assert "请补充经历、简历或项目资料" not in (payload.get("message") or "")
    assert "missing_resume" not in (payload.get("message") or "")


def test_d7_analyze_bumps_profile_without_remember_phrase(app_module, client, tmp_path):
    store = CandidateProfileStore(tmp_path)
    store.create(_ok_profile(), candidate_id="default", source_kinds=["resume"])
    app_module.llm_provider = _Routing(
        understanding_response(
            user_goal={"target_roles": ["高级产品经理"]},
            candidate_supplement={
                "claimed_projects": [
                    {"name": "运动小程序", "quote": "运动小程序", "source_kind": "user_statement"}
                ]
            },
            persist_requested=False,
        )
    )
    response = start_run(client, message="这次也考虑我的运动小程序。帮我找高级产品经理。")
    payload = wait_run(client, response.json()["run_id"], timeout=20)
    assert payload["status"] == "WAITING_USER"
    assert payload["profile_updated"] is True
    assert store.load("default")["profile_version"] == 2
    assert "persist_requested" not in payload


def test_d8_goal_only_does_not_fabricate_profile_update(app_module, client, tmp_path):
    store = CandidateProfileStore(tmp_path)
    store.create(_ok_profile(), candidate_id="default", source_kinds=["resume"])
    app_module.llm_provider = _Routing(
        understanding_response(
            user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
            persist_requested=False,
        )
    )
    response = start_run(client, message="帮我找深圳高级产品经理。")
    payload = wait_run(client, response.json()["run_id"], timeout=20)
    assert payload["status"] == "WAITING_USER"
    assert payload["profile_updated"] is False
    assert store.load("default")["profile_version"] == 1


def test_d9_get_profile_reads_current_record(client, tmp_path):
    CandidateProfileStore(tmp_path).create(_ok_profile(), candidate_id="default", source_kinds=["resume"])
    response = client.get("/api/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["profile_version"] == 1
    assert body["core_capabilities"]
    assert "姓名" not in str(body)


def test_followup_reuses_conversation_context(app_module, client):
    calls = []

    def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return done_state()

    app_module.run_agent_fn = fake_run_agent
    first = start_run(client, message="帮我找深圳高级产品经理。")
    first_payload = wait_run(client, first.json()["run_id"])
    second = start_run(
        client,
        message="再重点看看医疗支付。",
        conversation_id=first_payload["conversation_id"],
    )
    wait_run(client, second.json()["run_id"])
    context = calls[1]["session_context"]
    assert context["previous_goal"]["target_roles"] == ["高级产品经理"]
    assert calls[1].get("resume") in {None, ""}


def test_api_rejects_unsupported_extension(client):
    response = start_run(client, filename="resume.exe", content=b"hello")
    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_extension"


def test_api_rejects_empty_file(client):
    response = start_run(client, filename="resume.pdf", content=b"")
    assert response.status_code == 400
    assert response.json()["error_code"] == "empty_file"


def test_api_unreadable_resume_is_attachment_failure_not_missing_candidate(client):
    response = start_run(client, filename="resume.pdf", content=b"%PDF-1.4 not-a-real-pdf")
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "ATTACHMENT_PROCESSING_FAILED"
    assert "请补充经历、简历或项目资料" not in (body.get("message") or "")


def test_api_rejects_oversize(client, monkeypatch):
    from api import resume_extract

    monkeypatch.setattr(resume_extract, "MAX_RESUME_BYTES", 16)
    response = start_run(client, filename="resume.pdf", content=b"x" * 17)
    assert response.status_code == 400
    assert response.json()["error_code"] == "file_too_large"


def test_api_rejects_empty_input(client):
    response = client.post("/api/run-agent", data={"message": "   ", "data_source": "mock"})
    assert response.status_code == 400
    assert response.json()["error_code"] == "empty_input"


def test_no_missing_resume_product_gate():
    app_source = (ROOT / "api" / "app.py").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "missing_resume" not in app_source
    assert "请上传简历" not in html
    assert "请输入职位关键词" not in html
    assert "请输入城市" not in html
    assert "missing_resume" not in js


def test_api_rejects_doc_with_clear_message(client):
    response = start_run(client, filename="resume.doc", content=b"\xd0\xcf\x11\xe0legacy")
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "doc_unsupported"
    assert "DOC" in body["message"]


def test_llm_timeout_is_shown_as_llm_error():
    from api.serialize import serialize_agent_state

    state = failed_state(message="The read operation timed out", profile_status="llm_error")
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "llm_error"
    assert payload["message"] == "大模型调用超时，请稍后点「重试」。这不是登录或人工验证问题。"
    assert "llm_error" not in payload["message"]


def test_llm_http_error_message_is_explicit():
    from api.serialize import LLM_ERROR_MESSAGE, serialize_agent_state

    state = failed_state(
        message='LLM HTTP 500: {"error":{"code":"1234","message":"网络错误"}}',
        profile_status="llm_error",
        kind="reasoner",
    )
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "llm_error"
    assert payload["message"] == LLM_ERROR_MESSAGE
    assert "登录" not in payload["message"] or "不是登录" in payload["message"]


def test_invalid_json_error_is_not_shown_as_service_outage():
    from api.serialize import LLM_INVALID_JSON_MESSAGE, serialize_agent_state

    state = failed_state(
        message="LLM response is not valid JSON: Expecting ',' delimiter: line 12 column 5 (char 301)",
        profile_status="llm_error",
        kind="reasoner",
    )
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "llm_invalid_json"
    assert payload["message"] == LLM_INVALID_JSON_MESSAGE
    assert "服务暂时出错" not in payload["message"]
    assert "格式无效" in payload["message"]


def test_llm_invalid_json_error_code_is_honest():
    from api.serialize import LLM_INVALID_JSON_MESSAGE, serialize_agent_state

    state = failed_state(
        message="rewrite still broken",
        profile_status="ok",
        kind="reasoner",
    )
    state.output = {"error_code": "llm_invalid_json", "message": "rewrite still broken"}
    payload = serialize_agent_state(state)
    assert payload["error_code"] == "llm_invalid_json"
    assert payload["message"] == LLM_INVALID_JSON_MESSAGE


def test_frontend_exposes_llm_retry_control():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "data-llm-retry" in js
    assert "renderLlmRetry" in js
    assert "模型服务暂时" in js
    assert "模型输出格式无效" in js
    assert "llm_invalid_json" in js
    assert "重试" in js
    assert 'href="/style.css?v=' in html
    assert 'src="/app.js?v=' in html
    assert "发送" in html
    assert "run-actions" in html
    assert 'type="submit">发送</button>' in html
    assert "outcome_kind" in js or "no_search" in js
    assert "paintStages(data.status, data.stats)" in js



def test_run_agent_failed_is_returned(app_module, client):
    app_module.run_agent_fn = lambda **_kwargs: failed_state(
        message="LLM provider is not configured",
        profile_status="llm_unavailable",
    )
    response = start_run(client, filename="resume.docx", content=make_docx_bytes(SAMPLE_RESUME))
    payload = wait_run(client, response.json()["run_id"])
    assert payload["status"] == "FAILED"
    assert payload["ok"] is False
    assert payload["error_code"] == "llm_unavailable"
    assert "llm_unavailable" not in payload["message"]


def test_needs_human_is_terminal_for_this_run(app_module, client):
    def fake_run_agent(**_kwargs):
        state = new_agent_state(resume=SAMPLE_RESUME, goal_input=GOAL, data_source="boss")
        state.status = "NEEDS_HUMAN"
        state.human_gate = {"reason": "login_required", "message": "请先在 Edge 中手动登录 BOSS。"}
        return state

    app_module.run_agent_fn = fake_run_agent
    response = start_run(client, filename="resume.docx", content=make_docx_bytes(SAMPLE_RESUME))
    payload = wait_run(client, response.json()["run_id"])
    assert payload["status"] == "NEEDS_HUMAN"
    assert payload["ok"] is False
    assert payload["error_code"] == "login_required"
    assert "Agent" in payload["message"] and "登录" in payload["message"]


def test_frontend_can_render_agent_fields():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for token in (
        "job_title",
        "company_name",
        "recommendation",
        "overall_fit",
        "rationale",
        "matched_capabilities",
        "transferable_capabilities",
        "knowledge_gaps",
        "job_url",
        "NEEDS_HUMAN",
        "transfer_rationale",
        "profile_updated",
        "conversation_id",
    ):
        assert token in js
    assert "你想让我帮你做什么" in html
    assert "理解你的需求" in html
    assert "整理结果" in html
    assert "parse_user_goal" not in html
    assert "analyze_candidate" not in html


def test_upload_docx_real_run_agent_mock_loop(app_module, client):
    app_module.llm_provider = AgentRoutingLLM()
    response = start_run(
        client,
        filename="resume.docx",
        content=make_docx_bytes(SAMPLE_RESUME),
        data_source="mock",
    )
    assert response.status_code == 200
    payload = wait_run(client, response.json()["run_id"], timeout=20)
    assert payload["status"] == "WAITING_USER"
    assert payload["ok"] is True
    titles = {item["job_title"] for item in payload["recommended"]}
    assert "高级产品经理" in titles
    hit = next(item for item in payload["recommended"] if item["job_id"] == "mock-direct")
    assert hit["recommendation"] == "yes"
    assert hit["capability_assessments"]
    assert payload["candidate_id"]
