# AI-Job-Agent

**实施总纲**：后续开发以 [docs/AI_JOB_AGENT_BASELINE_v1.md](docs/AI_JOB_AGENT_BASELINE_v1.md)（《AI Job Agent 总体实施基线 v1.0》）为准。按 Phase 0→11 顺序执行。

当前阶段：**Phase 11（基线终点）** — 应用辅助仅架构预留，自动投递未实现。`data_source=mock|boss|liepin|job51`。历史为本地 JSON；可选已投 tracker 供 rules 读。

`browse_boss_jobs` **不是** Agent，也不是 Orchestrator。该旧入口已删除。正式入口是 `run_agent`。用户入口是本地网页：在输入框里用自然语言说明想做什么，可选附上简历或项目资料，后端把同一轮 `message + attachments` 交给现有 Agent。简历不再是必填门槛。

---

## Web 用户入口

```bash
pip install -r requirements.txt
uvicorn api.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000

页面以自然语言输入框为主入口，附件只是补充。不在前端解析职位、城市或简历语义。默认 `data_source=boss`（Agent 自行管理专用招聘浏览器；你只需在弹出的 Agent 窗口登录）。本地无浏览器联调可设置 `AGENT_DATA_SOURCE=mock`。长期画像读写目录为 `data/candidate_profiles`。

---

## Agent 入口

```python
from agent.orchestrator import run_agent

# Mock 闭环（默认，无浏览器、无真实 BOSS）
state = run_agent(
    resume=resume_text,
    goal="帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统",
    data_source="mock",
    llm_provider=llm,  # 生产用 OpenAICompatibleProvider；测试用 MockLLM
)

# 同一 Loop 接真实 BOSS / 猎聘 / 前程无忧（需已登录的 Edge + CDP）
state = run_agent(
    resume=resume_text,
    goal="帮我找深圳高级产品经理",
    data_source="boss",  # 或 "liepin" / "job51"
    llm_provider=llm,
)

# 本地历史 + 已投 tracker（可选；单用户文件，不是云/多用户）
state = run_agent(
    resume=resume_text,
    goal="帮我找深圳高级产品经理",
    data_source="mock",
    llm_provider=llm,
    history_dir="data/history",
    tracker_path="data/already_applied.json",
)

if state.session.status == "NEEDS_HUMAN":
    print(state.human_gate)
elif state.session.status == "DONE":
    print(state.output)
```

Loop：`parse_user_goal` → `analyze_candidate` → `plan_search` / SearchPlan 队列 → `search_*` → 过滤 → `open_*`（配额内按列表顺序）→ `interpret_job_actions` → `analyze_job` → `match_job` → 推荐不足且配额未尽则继续 search/enrich → 停止 → 排序 → 报告。

- `open_*` 只负责导航 + 读取 JD + 可选 `raw_actions`
- interpret / analyze / match 由 Loop 调用，不在 `open_*` 内完成
- 不自动投递、不自动沟通（Phase 11 仅预留接口，调用即 `NotImplementedError`）

## 数据源

| `data_source` | Reasoner Job Actions | Adapter（内部） |
|---|---|---|
| `mock`（默认） | `search_jobs` / `open_job` / `inspect_job` / `inspect_application_state` / `execute_action` | `platforms.mock` |
| `boss` | 同上 | `platforms.boss` |
| `liepin` | 同上 | `platforms.liepin` |
| `job51` | 同上 | `platforms.job51` |

Registry 不注册 `browse_boss_jobs`。该旧入口已从代码中删除，禁止当作 Agent。

## Human Gate

人工介入仅限登录 / CAPTCHA / 权限 / 浏览器不可用 / 明确访问阻断。出现时 `status=NEEDS_HUMAN`，`state.human_gate.reason` 为：

- `login_required`
- `captcha`
- `access_blocked`
- `browser_unavailable`
- `site_blocked_client`

**不是** Human Gate：请用户手动打开 JD。Agent 必须自己 `goto(job_url)`。

## 浏览器连接（`data_source=boss`、`liepin` 或 `job51`）

招聘网站 Tool 执行前由 **Agent 浏览器执行器**自行确保专用 Agent Edge 已启动并可通过 CDP 连接（`ensure_agent_edge` → `connect_existing_edge`）。  
用户不需要区分日常 Edge / Agent Edge，也不需要手动启动、指定端口或勾选 inspect。日常 Edge 保持独立、不受影响。

- 浏览器已就绪：复用
- 未登录 / 验证码 / 访问阻断：`NEEDS_HUMAN`，请在 **Agent 打开的窗口**中处理
- Agent Edge 未就绪：`browser_unavailable`
- CDP 连接故障：`cdp_unavailable`

浏览器启动/连接属于 Tool 执行环境基础设施，**不是** Reasoner 业务 Action（没有 `launch_edge` / `connect_edge` 工作流）。

开发机上若 Cursor Playwright MCP 同时占用同一 Agent CDP，可能互相抢控制权；验收真站时暂时关掉对该 Agent Edge 的 MCP 占用。

## 测试

CI / 本阶段验收（**不依赖真实 BOSS / 猎聘 / 前程无忧**）：

```bash
python -m pytest tests/test_application_assist.py -q
```

可选 live（**不是 CI 门禁**）：

```bash
python tests/test_boss_job_search.py
python tests/test_open_boss_job_live.py
python -m pytest tests/test_glm_connection.py --run-live
```

不要运行 `playwright install chromium`。不要安装 Chrome。

## 尚未实现（基线外 / 升版）

- 自动投递、沟通（接口见 [docs/APPLICATION_ASSIST_DRAFT.md](docs/APPLICATION_ASSIST_DRAFT.md)；调用必 `NotImplementedError`）
- CAPTCHA / 滑块 / 反爬绕过
