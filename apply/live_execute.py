"""Live page execution: observe → interpret → bind semantic intent → click → verify.

Does not search. Does not guess from button copy. Click is always on a live
element stamped during the current observation, never a historical snapshot.
"""

from __future__ import annotations

from html import escape
from typing import Any

from browser.edge_session import check_human_verification, find_human_verification, visible_text
from job.common_schema import job_key as make_job_key
from tools.interpret_job_actions import ACTION_FIELDS, interpret_job_actions

ACTION_INDEX_ATTR = "data-job-agent-action"
USER_AUTHORIZATION_APPLY = "apply_job"

EXISTING_RELATION_INTENTS = frozenset({"continue_contact", "view_progress"})
EXECUTABLE_INTENTS = ("apply_resume", "start_contact")

EXTRACT_AND_STAMP_JS = r"""() => {
  const attr = "data-job-agent-action";
  document.querySelectorAll("[" + attr + "]").forEach((el) => el.removeAttribute(attr));
  const visible = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2;
  };
  const textOf = (el) => ((el && (el.innerText || el.value || "")) + "").replace(/\s+/g, " ").trim();
  const skipChrome = (el) => !!(el.closest && el.closest(
    'nav, header, footer, [role="navigation"], [class*="similar"], [class*="recommend-job"], [class*="job-list"]'
  ));
  const firstMatch = (selectors, root) => {
    const base = root || document;
    for (const sel of selectors) {
      const el = base.querySelector(sel);
      if (el) return el;
    }
    return null;
  };
  const detailRoot = firstMatch([
    '[class*="job-detail"]',
    '[class*="job-box"]',
    '[class*="detail-content"]',
    "article",
    "main",
  ]) || document.body;
  const opRoot = firstMatch([
    '[class*="job-op"]',
    '[class*="op-area"]',
    '[class*="op-btn"]',
    '[class*="sider-op"]',
    '[class*="btn-container"]',
    '[class*="job-buttons"]',
  ], detailRoot);
  const scope = opRoot || detailRoot;
  const nodes = Array.from(scope.querySelectorAll(
    'button, [role="button"], a[href], input[type="button"], input[type="submit"]'
  ));
  const seen = new Set();
  const actions = [];
  for (const el of nodes) {
    if (!visible(el) || skipChrome(el)) continue;
    const text = textOf(el).slice(0, 80);
    const aria = ((el.getAttribute && el.getAttribute("aria-label")) || "").trim();
    const title = ((el.getAttribute && el.getAttribute("title")) || "").trim();
    const hrefRaw = el.tagName === "A" ? (el.getAttribute("href") || el.href || "") : "";
    const href = (hrefRaw || "").trim();
    const role = ((el.getAttribute && (el.getAttribute("role") || el.getAttribute("type"))) || "").trim();
    const hrefNorm = href.toLowerCase();
    if (!text && !aria && !title && (!href || hrefNorm.startsWith("javascript:") || hrefNorm === "#" || hrefNorm.endsWith("#"))) {
      continue;
    }
    const key = [el.tagName, text, aria, href].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    const parentText = el.parentElement ? textOf(el.parentElement).slice(0, 80) : "";
    const id = el.id ? ("#" + el.id) : "";
    const index = actions.length;
    el.setAttribute(attr, String(index));
    actions.push({
      action_index: index,
      text: text || null,
      aria_label: aria || null,
      title: title || null,
      role: role || el.tagName.toLowerCase(),
      href: href || null,
      tag: el.tagName.toLowerCase(),
      surrounding_text: parentText || null,
      dom_context: (el.tagName.toLowerCase() + id + (role ? ("[role=" + role + "]") : "")).slice(0, 120),
    });
    if (actions.length >= 12) break;
  }
  return actions;
}"""

READ_IDENTITY_JS = r"""() => {
  const textOf = (el) => ((el && el.innerText) || "").replace(/\s+/g, " ").trim();
  const root = document.querySelector("[data-job-id], [class*='job-detail'], main, article, body");
  const companyEl = document.querySelector("[data-company], .company, [class*='company-name']");
  return {
    url: location.href,
    job_id: (root && root.getAttribute && root.getAttribute("data-job-id")) || null,
    title: textOf(document.querySelector("h1")),
    company: (root && root.getAttribute && root.getAttribute("data-company"))
      || (companyEl && (companyEl.getAttribute("data-company") || textOf(companyEl)))
      || "",
    page_kind: (document.body && document.body.getAttribute("data-job-agent-page")) || "open",
  };
}"""


def execution_result(
    *,
    ok: bool,
    result: str,
    execution_status: str,
    executed_intent: str | None = "none",
    verification: str = "failed",
    clicked: bool = False,
    identity: dict | None = None,
    application_evidence: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    task_id: str | None = None,
    job_context_id: str | None = None,
    interpret_result: dict | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "ok": ok,
        "result": result,
        "execution_status": execution_status,
        "executed_intent": executed_intent or "none",
        "verification": verification,
        "clicked": bool(clicked),
        "application_evidence": application_evidence,
        "error": error,
        "error_code": error_code,
        "task_id": task_id,
        "job_context_id": job_context_id,
        "user_authorization": USER_AUTHORIZATION_APPLY,
        "source": "user_authorized",
        "interpret_result": interpret_result,
    }
    if identity:
        payload.update(identity)
    if extra:
        payload.update(extra)
    return payload


def job_identity_from_payload(payload: dict) -> dict:
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    return {
        "job_key": payload.get("job_key") or job.get("job_key") or make_job_key(job),
        "job_id": payload.get("job_id") or job.get("job_id"),
        "job_url": payload.get("job_url") or job.get("job_url"),
        "platform": job.get("platform") or payload.get("platform"),
        "job_title": job.get("job_title") or job.get("title") or payload.get("job_title"),
        "company_name": job.get("company_name") or job.get("company") or payload.get("company_name"),
    }


def already_executed_record(history: Any, job_context_id: str | None, job_key: str | None) -> dict | None:
    if not isinstance(history, list):
        return None
    wanted = {(job_context_id or "").strip(), (job_key or "").strip()}
    wanted.discard("")
    if not wanted:
        return None
    for item in history:
        if not isinstance(item, dict):
            continue
        marker = (item.get("job_context_id") or item.get("job_key") or "").strip()
        if marker not in wanted:
            continue
        if item.get("result") in {"applied", "already_applied", "already_executed"}:
            return item
    return None


def select_authorized_semantic_action(
    interpret_result: dict | None,
    *,
    user_authorization: str | None,
) -> dict:
    """Choose a live semantic intent for a user apply_job grant. No button copy."""
    if user_authorization != USER_AUTHORIZATION_APPLY:
        return {"decision": "not_authorized", "intent": None, "index": None}
    if not isinstance(interpret_result, dict) or interpret_result.get("analysis_status") != "ok":
        return {"decision": "interpret_failed", "intent": None, "index": None}
    actions = interpret_result.get("actions")
    if not isinstance(actions, list):
        return {"decision": "no_executable_action", "intent": None, "index": None}
    intents = [item.get("semantic_intent") for item in actions if isinstance(item, dict)]
    if any(intent in EXISTING_RELATION_INTENTS for intent in intents):
        return {"decision": "already_applied", "intent": None, "index": None}
    for wanted in EXECUTABLE_INTENTS:
        for index, item in enumerate(actions):
            if isinstance(item, dict) and item.get("semantic_intent") == wanted:
                return {"decision": "execute", "intent": wanted, "index": index}
    return {"decision": "no_executable_action", "intent": None, "index": None}


def extract_live_actions(page) -> list[dict]:
    raw = page.evaluate(EXTRACT_AND_STAMP_JS)
    if not isinstance(raw, list):
        return []
    actions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = {field: _null_if_blank(item.get(field)) for field in ACTION_FIELDS}
        index = item.get("action_index")
        try:
            action["action_index"] = int(index)
        except (TypeError, ValueError):
            action["action_index"] = len(actions)
        actions.append(action)
    return actions


def observation_actions(live_actions: list[dict]) -> list[dict]:
    return [{field: item.get(field) for field in ACTION_FIELDS} for item in live_actions]


def read_live_identity(page) -> dict:
    try:
        raw = page.evaluate(READ_IDENTITY_JS)
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    from tools.boss_job_search import parse_job_id

    url = _null_if_blank(raw.get("url")) or _null_if_blank(getattr(page, "url", None))
    job_id = _null_if_blank(raw.get("job_id")) or parse_job_id(url)
    return {
        "url": url,
        "job_id": job_id,
        "title": _null_if_blank(raw.get("title")),
        "company": _null_if_blank(raw.get("company")),
        "page_kind": _null_if_blank(raw.get("page_kind")) or "open",
    }


def identities_match(expected: dict, observed: dict) -> tuple[bool, str | None]:
    """Strict identity. job_id wins when both sides have it. No fuzzy 'close enough'."""
    if (observed.get("page_kind") or "").lower() == "closed":
        return False, "job_closed"
    exp_id = _null_if_blank(expected.get("job_id"))
    obs_id = _null_if_blank(observed.get("job_id"))
    from tools.boss_job_search import parse_job_id

    if not exp_id:
        exp_id = parse_job_id(expected.get("job_url"))
    if not obs_id:
        obs_id = parse_job_id(observed.get("url"))
    if exp_id and obs_id:
        if exp_id != obs_id:
            return False, "identity_mismatch"
        return True, None
    exp_title = _norm(expected.get("job_title") or expected.get("title"))
    obs_title = _norm(observed.get("title"))
    exp_company = _norm(expected.get("company_name") or expected.get("company"))
    obs_company = _norm(observed.get("company"))
    if exp_title and obs_title and exp_title != obs_title:
        return False, "identity_mismatch"
    if exp_company and obs_company and exp_company != obs_company:
        return False, "identity_mismatch"
    if not obs_id and not obs_title and not obs_company:
        return False, "job_closed"
    return True, None


def click_stamped_action(page, index: int) -> None:
    locator = page.locator(f"[{ACTION_INDEX_ATTR}='{int(index)}']")
    locator.click(timeout=10_000)


def interpret_live_page(page, identity: dict, llm_provider) -> tuple[list[dict], dict]:
    live_actions = extract_live_actions(page)
    page_context = {
        "platform": identity.get("platform"),
        "job_id": identity.get("job_id"),
        "job_url": identity.get("job_url") or identity.get("url"),
        "actions": observation_actions(live_actions),
    }
    analysis = interpret_job_actions(page_context, llm_provider=llm_provider)
    return live_actions, analysis


def execute_authorized_page_action(
    page,
    identity: dict,
    *,
    user_authorization: str | None,
    llm_provider,
    task_id: str | None = None,
    job_context_id: str | None = None,
    click_log: list | None = None,
    skip_identity: bool = False,
) -> dict:
    """Run the live observe → interpret → bind → click → verify loop on an open page."""
    base = {
        "task_id": task_id,
        "job_context_id": job_context_id or identity.get("job_key"),
        "identity": identity,
    }
    if user_authorization != USER_AUTHORIZATION_APPLY:
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error="user authorization is missing or is not apply_job",
            error_code="not_authorized",
            **base,
        )

    gate = _page_human_gate(page)
    if gate:
        return _needs_human_result(identity=identity, task_id=task_id, job_context_id=base["job_context_id"], gate=gate)

    if not skip_identity:
        observed = read_live_identity(page)
        matched, reason = identities_match(identity, observed)
        if not matched:
            return execution_result(
                ok=False,
                result="failed",
                execution_status="failed",
                verification="failed",
                error=reason or "identity_mismatch",
                error_code=reason or "identity_mismatch",
                **base,
            )

    live_actions, analysis = interpret_live_page(page, identity, llm_provider)
    if analysis.get("analysis_status") != "ok":
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error=analysis.get("error") or analysis.get("analysis_status") or "interpret failed",
            error_code="interpret_failed",
            interpret_result=analysis,
            **base,
        )

    choice = select_authorized_semantic_action(analysis, user_authorization=user_authorization)
    if choice["decision"] == "already_applied":
        evidence = ((analysis.get("inferred_context") or {}).get("application_evidence")) or "applied"
        return execution_result(
            ok=False,
            result="already_applied",
            execution_status="already_applied",
            executed_intent="none",
            verification="confirmed",
            application_evidence=evidence,
            interpret_result=analysis,
            **base,
        )
    if choice["decision"] != "execute" or choice["index"] is None:
        error_code = "job_closed" if not live_actions else "no_executable_action"
        if (read_live_identity(page).get("page_kind") or "").lower() == "closed":
            error_code = "job_closed"
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            verification="failed",
            error=choice["decision"],
            error_code=error_code,
            interpret_result=analysis,
            **base,
        )

    intent = choice["intent"]
    index = int(choice["index"])
    if click_log is not None:
        click_log.append(index)
    try:
        click_stamped_action(page, index)
    except Exception as exc:
        return execution_result(
            ok=False,
            result="failed",
            execution_status="failed",
            executed_intent=intent,
            verification="failed",
            clicked=False,
            error=str(exc),
            error_code="click_failed",
            interpret_result=analysis,
            **base,
        )

    try:
        page.wait_for_timeout(600)
    except Exception:
        pass

    gate = _page_human_gate(page)
    if gate:
        return _needs_human_result(
            identity=identity,
            task_id=task_id,
            job_context_id=base["job_context_id"],
            gate=gate,
            clicked=True,
            executed_intent=intent,
        )

    _post_actions, post = interpret_live_page(page, identity, llm_provider)
    verified = verify_execution(post, executed_intent=intent)
    if verified["execution_status"] == "success":
        return execution_result(
            ok=True,
            result="applied",
            execution_status="success",
            executed_intent=intent,
            verification="confirmed",
            clicked=True,
            application_evidence=verified.get("application_evidence") or "applied",
            interpret_result=post,
            extra={"pre_interpret_result": analysis},
            **base,
        )
    return execution_result(
        ok=False,
        result="uncertain",
        execution_status="uncertain",
        executed_intent=intent,
        verification="uncertain",
        clicked=True,
        application_evidence=verified.get("application_evidence") or "uncertain",
        error="click completed but application result is not confirmed",
        error_code="uncertain",
        interpret_result=post,
        extra={"pre_interpret_result": analysis},
        **base,
    )


def verify_execution(post_interpret: dict | None, *, executed_intent: str | None) -> dict:
    """Success only when live semantics show an existing relationship after the click."""
    del executed_intent
    if not isinstance(post_interpret, dict) or post_interpret.get("analysis_status") != "ok":
        return {
            "execution_status": "uncertain",
            "verification": "uncertain",
            "application_evidence": "uncertain",
        }
    inferred = post_interpret.get("inferred_context") if isinstance(post_interpret.get("inferred_context"), dict) else {}
    intents = [
        item.get("semantic_intent")
        for item in (post_interpret.get("actions") or [])
        if isinstance(item, dict)
    ]
    if inferred.get("application_evidence") == "applied" or any(intent in EXISTING_RELATION_INTENTS for intent in intents):
        return {
            "execution_status": "success",
            "verification": "confirmed",
            "application_evidence": "applied",
        }
    return {
        "execution_status": "uncertain",
        "verification": "uncertain",
        "application_evidence": inferred.get("application_evidence") or "uncertain",
    }


class FixtureJobPage:
    """Deterministic in-process job page for mock/local execution tests.

    Supports the live execute contract: evaluate, locator().click(), inner_text, wait.
    Not a success stub: click mutates live actions, and verification still runs.
    """

    def __init__(
        self,
        identity: dict,
        *,
        buttons: list[dict] | None = None,
        page_kind: str = "open",
        banner: str = "",
        swap_on_click: bool = True,
        url: str | None = None,
    ):
        self.identity = dict(identity or {})
        self.buttons = [dict(item) for item in (buttons or [])]
        self.page_kind = page_kind
        self.banner = banner or ""
        self.swap_on_click = swap_on_click
        self.url = url or self.identity.get("job_url") or "https://mock.local/job"
        self.clicks: list[int] = []

    def evaluate(self, expression, *args, **kwargs):
        source = expression if isinstance(expression, str) else str(expression)
        if "data-job-agent-page" in source and "data-job-id" in source:
            return {
                "url": self.url,
                "job_id": self.identity.get("job_id"),
                "title": self.identity.get("job_title") or self.identity.get("title") or "",
                "company": self.identity.get("company_name") or self.identity.get("company") or "",
                "page_kind": self.page_kind,
            }
        if ACTION_INDEX_ATTR in source:
            return self._stamped_actions()
        if "BOSS_LOGGED_IN" in source or "web/geek/chat" in source or "geek/recommend" in source:
            # Fixture pages are not a live BOSS session unless a test stubs otherwise.
            if "loginCta" in source or "geekNav" in source or "geekJobs" in source or "geek/recommend" in source:
                return False
        return {}

    def locator(self, selector: str):
        return _FixtureLocator(self, selector)

    def inner_text(self, selector: str = "body") -> str:
        del selector
        parts = [self.banner, str(self.identity.get("job_title") or ""), str(self.identity.get("company_name") or "")]
        for item in self.buttons:
            parts.append(str(item.get("text") or item.get("aria_label") or ""))
        return "\n".join(part for part in parts if part)

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    def _click_selector(self, selector: str) -> None:
        index = _index_from_selector(selector)
        if index is None or index < 0 or index >= len(self.buttons):
            raise RuntimeError(f"no live element for {selector}")
        self.clicks.append(index)
        if self.swap_on_click:
            self.buttons = [
                {"text": "Continue existing conversation", "aria_label": "continue_contact", "tag": "button"},
                {"text": "View application progress", "aria_label": "view_progress", "tag": "button"},
            ]

    def _stamped_actions(self) -> list[dict]:
        actions = []
        for index, item in enumerate(self.buttons):
            actions.append(
                {
                    "action_index": index,
                    "text": item.get("text"),
                    "aria_label": item.get("aria_label"),
                    "title": item.get("title"),
                    "role": item.get("role") or "button",
                    "href": item.get("href"),
                    "tag": item.get("tag") or "button",
                    "surrounding_text": item.get("surrounding_text"),
                    "dom_context": item.get("dom_context") or "button",
                }
            )
        return actions


class _FixtureLocator:
    def __init__(self, page: FixtureJobPage, selector: str):
        self._page = page
        self._selector = selector

    def click(self, timeout: int | None = None) -> None:
        del timeout
        self._page._click_selector(self._selector)


def _index_from_selector(selector: str) -> int | None:
    text = selector or ""
    marker = f"{ACTION_INDEX_ATTR}="
    if marker not in text:
        return None
    raw = text.split(marker, 1)[1]
    raw = raw.strip("[]'\" ")
    try:
        return int(raw)
    except ValueError:
        return None


def build_local_job_html(
    identity: dict,
    *,
    page_kind: str = "open",
    buttons: list[dict] | None = None,
    banner: str = "",
    swap_on_click: bool = True,
) -> str:
    """Deterministic HTML for mock/local execution. Not a BOSS copy catalog."""
    job_id = escape(str(identity.get("job_id") or ""))
    title = escape(str(identity.get("job_title") or identity.get("title") or "job"))
    company = escape(str(identity.get("company_name") or identity.get("company") or ""))
    ops = []
    for item in buttons or []:
        text = escape(str(item.get("text") or item.get("aria_label") or "action"))
        aria = escape(str(item.get("aria_label") or ""))
        ops.append(f'<button type="button" aria-label="{aria}">{text}</button>')
    ops_html = "\n".join(ops)
    after = (
        '<button type="button" aria-label="continue_contact">Continue existing conversation</button>'
        '<button type="button" aria-label="view_progress">View application progress</button>'
    )
    script = ""
    if swap_on_click:
        script = f"""<script>
  const ops = document.getElementById("ops");
  if (ops) {{
    ops.addEventListener("click", (event) => {{
      const target = event.target && event.target.closest ? event.target.closest("button, a") : null;
      if (!target) return;
      ops.innerHTML = {after!r};
    }});
  }}
</script>"""
    banner_html = f"<p>{escape(banner)}</p>" if banner else ""
    return f"""<!doctype html>
<html><body data-job-agent-page="{escape(page_kind)}">
{banner_html}
<main class="job-detail" data-job-id="{job_id}" data-company="{company}">
  <h1>{title}</h1>
  <p class="company">{company}</p>
  <div class="job-op" id="ops">{ops_html}</div>
</main>
{script}
</body></html>"""


def _page_human_gate(page) -> dict | None:
    try:
        check_human_verification(page, "execute_job_action")
    except Exception as exc:
        from agent.human_gate import classify_exception

        gate = classify_exception(exc, source="execute_job_action")
        if gate:
            return gate
        raise
    url = getattr(page, "url", "") or ""
    text = visible_text(page)
    hit = find_human_verification(text, url)
    if hit:
        from agent.human_gate import human_gate_payload

        return human_gate_payload("captcha", f"site verification required: {hit}", source="execute_job_action")
    login_signals = ("未检测到 BOSS 登录态", "请先在 Edge 中手动登录", "login_required")
    haystack = f"{url}\n{text}"
    if any(signal in haystack for signal in login_signals):
        from agent.human_gate import human_gate_payload

        return human_gate_payload("login_required", "未检测到 BOSS 登录态", source="execute_job_action")
    return None


def _needs_human_result(
    *,
    identity: dict,
    task_id: str | None,
    job_context_id: str | None,
    gate: dict,
    clicked: bool = False,
    executed_intent: str | None = "none",
) -> dict:
    result = execution_result(
        ok=False,
        result="needs_human",
        execution_status="needs_human",
        executed_intent=executed_intent,
        verification="failed",
        clicked=clicked,
        error=gate.get("message") or gate.get("reason"),
        error_code=gate.get("reason") or "needs_human",
        task_id=task_id,
        job_context_id=job_context_id,
        identity=identity,
        extra={"human_gate": gate},
    )
    return result


def _null_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text


def _norm(value: Any) -> str:
    text = _null_if_blank(value)
    return text.casefold() if text else ""
