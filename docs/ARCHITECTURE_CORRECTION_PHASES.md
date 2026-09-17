# AI Job Agent 架构纠偏 — 分 Phase 执行书

> **用法**：按 Phase 顺序，把每个 Phase 的「Cursor 执行 Prompt」原样复制给 Cursor。  
> **权威**：控制权原则以用户确认的《现有架构系统性纠偏方案》为准。本文只拆迁移阶段，不改目标。  
> **基线**：`docs/AI_JOB_AGENT_BASELINE_v1.md` 的产品目标 / Tool / Human Gate 仍有效；其中 **固定流水线、decide() 状态机、Program 自动排除/推荐/停止** 已被本纠偏方案取代，不得再按旧基线实现。

---

## 评估结论（执行前必读）

方案原则成立，**按此执行**。不是重写 Agent，而是剥离现有骨架里的 Pipeline 控制权。

现有代码已经具备：

- `run_agent` → `run_loop` → `decide()` → `reason_next_action`
- `tests/agent/test_reasoner_loop.py` 已证明同一 Observation 可走 search / open / ask / finish
- 非法 Action 被拒绝后回到 Reasoner，Program 不另选 Tool

当前仍越权的真实位置：

| 位置 | 越权行为 |
|---|---|
| `agent/validate_action.py` `NEEDS_UNDERSTANDING` | 理解未完成则禁止 search/plan/match/analyze |
| `agent/loop.py` `_reduce_interpret/_reduce_match/_reduce_analyze/_reduce_rank/_reduce_insufficient_match` | Tool Result → Program 自动 exclude / recommended |
| `rules/filters.py` + `rules/application.py` | `application_evidence==applied` / `hard_requirements_met==false` → Program 排除 |
| `tools/decide_next_action.py` + `_reduce_decide_next` | 第二决策器 |
| `agent/reasoner_context.py` `current_job` | Program 用 `in_flight_job` / `next_job_to_open` 指定当前 Job |
| `agent/loop.py` `_apply_ask_user` | ask_user 自动 recommended + 选 Job + GOAL_INCOMPLETE 改成 DONE |
| `agent/loop.py` `_reduce_understanding` | 理解结果自动建任务 / 路由 follow-up / 写入约束 |
| `agent/loop.py` `_apply_goal_constraints` | LLM 抽出的 cities/salary 变成 Program 硬过滤 |
| `understanding/verify_candidate_claims.py` | 同轮语料 LLM 自证 → `supported` |
| `tests/mock_llm.py` `AgentRoutingLLM` | 测试双仍编码固定流水线（可保留为场景 double，不能当生产） |

---

## 必须先钉死的边界（方案未写清、实施时不得走错）

这些不是推翻方案，而是防止「纠偏」时把安全契约和业务决策一起删掉。

### B1. 用户明确约束 ≠ 模型推断

Program **可以**执行用户明确禁止事项，**不可以**把 LLM 推断升级成排除。

| 来源 | 性质 | Program |
|---|---|---|
| `run_agent(constraints=...)` 的 blacklist / already_applied / cities / salary_min | 用户明确约束 | 可在 search ingest 标记 / 拒绝非法 Action |
| tracker 已记录的 applied keys | 独立机械记录 | 可记入 Memory；**不要**再自动 `stage=excluded` 并禁止 Reasoner 再看 |
| `exclude_companies`（用户本轮明确说不要某公司） | 用户明确禁止 | 可并入 blacklist 契约 |
| LLM `application_evidence=applied` | Interpretation | 只记 Memory，Reasoner 决定 skip/inspect/surface |
| `hard_requirements_met=false` / `match.recommendation` | Interpretation | 只记 Memory |
| `analyze_job.analysis_status != ok` | processing status | 记 Observation，不自动 exclude |
| `_apply_goal_constraints` 把 UserGoal.cities/salary 写入 Constraints | 把 Interpretation 变成硬过滤 | **停止拷贝**。Reasoner 自己把 city 放进 search arguments |

### B2. understanding_status 不是总闸门，但是数据完整性约束

- **删除**：`understanding_status != ok` → 禁止整个 search/plan/match/analyze Tool。
- **保留给 Reasoner 看**：`understanding_status` 作为信息状态。
- **Program 仍可拒绝的非法参数**：把未理解的 `pending_goal` 原文直接当作 `keyword` / 匹配事实传入 Tool。这是「参数不合法」，不是「现在不能搜索」。Reasoner 自选 keyword 的 search，即使尚未 understand，也必须放行。

### B3. 执行成功是 Verification；自动排除仍是业务决策

`execute_job_action` 得到页面/系统确认的 applied，可以写入 Verification。  
**不要**因此 `stage=excluded`。下一轮 Reasoner 自己决定是否再看。

### B4. 配额 / 安全 / Human Gate 仍是 Program

- 配额用尽 → **拒绝该 Action**，记 `action_rejected` Observation，回到 Reasoner。禁止 Program 因此 `DONE`。
- `execute_job_action` 无 `user_authorization=apply_job` → 拒绝。
- 登录 / CAPTCHA / 浏览器不可用 → Human Gate 暂停。这是安全暂停，不是业务完成。
- `max_steps` / 未注册 Tool / 自调用 `reason_next_action` → Program 可失败或拒绝。

### B5. 先夺控制权，后改 Memory 形状

不要在同一 Phase 里同时：

- 拆掉 reduce 业务决策，并且
- 重写 Evidence/Claim/Interpretation/Verification 存储，并且
- 统一多平台 Tool 名

控制权不过去，新 Memory 只会变成另一套 Program 真相库。

### B6. 测试必须跟着原则改，不得为了绿而留 Pipeline

旧测试若断言「match 失败 → Program exclude」「interpret applied → Program exclude」「search 后必须 open」，应改成 Agent 原则测试。  
`AgentRoutingLLM` 可以继续扮演「一种合法探索策略」的测试双，但必须在文件头标明：**不是生产调度，不是 Program 应执行的流水线。**

### B7. 本轮不做

- 不换 LLM provider，不用 Ollama，不新 SDK
- 不加重启猎聘/51/BOSS 浏览架构
- 不做自动投递
- 不引入关键词 / 启发式语义兜底
- 不把 `search_boss_jobs` 等改成统一 `search_jobs`（那是可选后续，见 Phase 8）
- 不重写 Tool Registry 框架

### B8. 语义红线（全程）

`analyze_job` / `analyze_candidate` / `interpret_job_actions` / `match_job` / `understand_user_input` / `reason_next_action` 必须走真实 LLM provider。不可用则 `llm_unavailable` / `llm_error` / `analysis_failed`。禁止启发式 fallback。

---

## 自测矩阵

| Phase | 必跑 | 不依赖真站 |
|---|---|---|
| 0 | 文档存在；`pytest tests/agent/test_reasoner_loop.py tests/agent/test_registry.py -q` | 是 |
| 1 | `pytest tests/agent tests/rules tests/task -q` | 是 |
| 2 | 同上 + `tests/agent/test_loop_scenarios.py` + `tests/task/test_job_search_task.py` | 是 |
| 3 | `pytest tests/agent tests/test_understand_user_input.py tests/agent/test_candidate_memory_architecture.py -q` | 是 |
| 4 | `pytest tests/agent tests/task -q` | 是 |
| 5 | `pytest tests/agent tests/task tests/test_job_context.py tests/test_conversation_reference.py -q` | 是 |
| 6 | `pytest tests/test_verify_candidate_claims.py tests/test_analyze_candidate.py tests/agent/test_candidate_memory_architecture.py tests/agent -q` | 是 |
| 7 | `pytest tests/agent tests/rules tests/task tests/test_analyze_*.py tests/test_match_job.py -q` | 是 |

平台 live 测试全程 optional，不是门禁。

---

## Phase 依赖

```text
Phase 0  权威冻结 + 越权清单
    ↓
Phase 1  reduce 退回记录器（去掉自动 exclude/recommend）
    ↓
Phase 2  删除第二决策器 decide_next_action
    ↓
Phase 3  去掉理解总闸门 + ask_user 副作用
    ↓
Phase 4  current_job 不再由 Program 指定
    ↓
Phase 5  understanding / task 路由退出 reduce
    ↓
Phase 6  Evidence / Claim / Interpretation / Verification
    ↓
Phase 7  Memory 视图 + 测试原则收口
    ┄
Phase 8  可选：平台 Adapter 合同统一（不阻塞 0–7）
```

进入 Phase N 前，Phase 0…N-1 必须 PASS。不满足则 STOP，不得用旧 Pipeline 绕过。

---

# Phase 0 — 权威冻结 + 越权清单

### 目标

把纠偏方案变成仓库内可执行权威，冻结边界 B1–B8，不改生产行为。

### 前置

无。

### 允许改动

- 新建/更新本文（已存在则只补 Phase 0 验收说明）
- 在 `docs/AI_JOB_AGENT_BASELINE_v1.md` 文首加 **控制流已由纠偏方案取代** 的简短声明（不要重写全书）
- 在越权函数上加简短注释：`# PIPELINE LEAK: ... removed in Phase N`（不改逻辑）
- 删除未使用的 `_candidate_gather_decision`（已确认无调用；若有引用则 STOP 并报告）

### 禁止

改 `reduce` / `validate_action` / Reasoner / Tool / 测试期望。

### 验收

1. 基线文首声明存在。
2. 越权点至少标注：`validate_reasoner_action` 的 understanding 闸门、`_reduce_interpret`、`_reduce_match`、`_reduce_decide_next`、`_apply_ask_user`、`_current_job_snapshot`、`application_exclude_reason`、`verify_candidate_claims`。
3. `pytest tests/agent/test_reasoner_loop.py tests/agent/test_registry.py -q` 全绿。

### 下一 Phase 进入条件

文档与注释就位，现有 Reasoner loop 测试仍绿。

---

## Cursor 执行 Prompt — Phase 0

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 0。只冻结权威，不改生产行为。

权威文件：docs/ARCHITECTURE_CORRECTION_PHASES.md
原则：Reasoner 决策，Memory 记录，Tool 感知/执行，Program 只做 Contract / Safety / Execution。
不要重写 Agent，不要换 LLM，不要改 BOSS/猎聘/51，不要启发式语义兜底。

必须完成：
1. 在 docs/AI_JOB_AGENT_BASELINE_v1.md 文首加简短声明：Agent 控制流以纠偏方案为准；旧基线里的固定流水线、Program 自动排除/推荐/停止、decide() 状态机不再作为实现依据。不要重写全书。
2. 在下列位置加一行 PIPELINE LEAK 注释，写明将在哪个 Phase 移除，不要改逻辑：
   - agent/validate_action.py 的 NEEDS_UNDERSTANDING 闸门
   - agent/loop.py 的 _reduce_interpret / _reduce_match / _reduce_analyze / _reduce_rank / _reduce_insufficient_match / _reduce_decide_next / _apply_ask_user / _apply_goal_constraints / _reduce_understanding
   - agent/reasoner_context.py 的 _current_job_snapshot
   - rules/application.py 的 application_exclude_reason
   - understanding/verify_candidate_claims.py 模块头
3. 若 agent/loop.py 的 _candidate_gather_decision 确认无引用，删除它。有引用则 STOP 并报告。
4. 不要改测试期望，不要改 reduce 行为。

自测：
pytest tests/agent/test_reasoner_loop.py tests/agent/test_registry.py -q

PASS：注释/声明完成，上述测试绿。
FAIL：任何生产行为变化，或测试红。报告后停止。
完成后只报告 Phase 0 PASS/FAIL 和改动文件，不要开始 Phase 1。
```

---

# Phase 1 — reduce 退回 State Reducer

### 目标

Tool Result 只变成 Observation + Memory 记录。Program 不再因为 interpret / match / analyze / rank / insufficient_match 而 exclude 或 recommended。

### 前置

Phase 0 PASS。

### 必须改

1. `_reduce_interpret`：只写入 `interpret_result` + Observation。删除 `post_interpret_exclude_reason` / `application_exclude_reason` 调用。
2. `_reduce_match`：只写入 `match_result` + Observation。删除 `match_exclude_reason` 导致的 `_exclude` / `_task_reject`。不要因为 match 成功就设置「世界当前 Job」并推进任务。
3. `_reduce_analyze`：`analysis_status != ok` 只记 Observation，不 `_exclude`。
4. `_reduce_insufficient_match`：只记录 insufficient match 材料，不 exclude、不 `_task_reject`。
5. `_reduce_rank`：若仍保留 `rank_jobs`，只能计算只读排序视图（例如写进 Observation / output 草稿），**禁止**把 `recommendation in {yes,weak}` 写成 `stage=recommended`，禁止把其余写成 excluded。
6. `_reduce_plan`：plan_search 失败只记 Observation + `stop_reason` 材料；已有 jobs/plans 时不得 `FAILED`。无任何计划且 Reasoner 尚未 finish 时也不得替 Reasoner 结束任务。
7. `_apply_goal_constraints` / `_apply_turn_constraints`：**停止**把 LLM UserGoal 的 `cities` / `salary_min` 写入 `state.constraints`。`exclude_companies` → blacklist 可保留（B1 用户明确禁止）。
8. `list_exclude_reason`：search ingest 仍可标记用户明确 blacklist。`already_applied` 用户/tracker keys 改为记入 Job 记录的 application 材料 / Observation，**不要**再 `stage=excluded`（B1/B3）。cities/salary 仅当来自调用方显式 `constraints`（不是 Goal 拷贝）时可作为契约标记；优先改为记录 `constraint_flags`，让 Reasoner 看见，而不是从 Memory 里抹掉该 Job。若改动面过大：至少停止 Goal→constraints 拷贝，并停止 `application_evidence` 路径。
9. `_reduce_execute`：确认投递成功 → 写 Verification/history Observation；**不要** `_exclude`。失败同样只记录。
10. `rules/stop.py` 的 `should_continue_search` / `should_stop_enriching`：**Loop/reduce 不得再调用它们来决定下一步或 DONE**。配额数字继续进 Reasoner payload。

### 测试要求

新增 `tests/agent/test_program_must_not_decide.py`（可用 `reduce()` 单测 + ScriptedReasonerLLM）：

- interpret 返回 `application_evidence=applied` → Job 仍在 Memory，`stage != excluded`（或至少不被 Program 标 already_applied exclude）
- match `hard_requirements_met=false` → 不 exclude
- analyze `analysis_status=llm_error` → 不 exclude
- search 返回 Job → 不自动 open
- 用户显式 blacklist 仍可在 ingest 被标记，且 Reasoner 能看见

改写（不要用旧期望保绿）：

- `tests/agent/test_rules.py`：`test_hard_requirements_false_excludes` 改为测试「这是可读取的匹配材料，不是 reduce 排除指令」
- 任何断言 `post_interpret_exclude_reason == already_applied` 驱动 Loop 排除的测试

`tests/agent/test_loop_scenarios.py` 里「未打开的 listed job」断言可保留。blacklist 用户约束测试可保留。  
`AgentRoutingLLM` 仍可走 decide_next_action（Phase 2 再删）。

### 禁止

- 不要删除 `decide_next_action`（Phase 2）
- 不要改 understanding 闸门（Phase 3）
- 不要重写 Memory schema（Phase 6）
- 不要把排除逻辑搬进 Tool 或 Reasoner prompt 的硬规则列表

### 验收

`pytest tests/agent tests/rules tests/task -q` 全绿。  
`test_program_must_not_decide.py` 新测试存在且绿。

### 下一 Phase 进入条件

reduce 对 interpret/match/analyze/rank 不再写业务 exclude/recommended。

---

## Cursor 执行 Prompt — Phase 1

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 1。前置 Phase 0 必须已 PASS。

目标：reduce 退回 State Reducer。Tool Result → Observation / Memory 记录。Program 不得因 interpret / match / analyze / rank / insufficient_match / execute 而 exclude 或 recommended。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md 的 B1–B8 与 Phase 1。
原则：Program 可以拒绝非法 Action，不能替 Reasoner 做一个新的正确业务决定。
语义红线：不改 LLM provider，不用启发式/关键词替代语义 Tool。

必须做：
1. agent/loop.py
   - _reduce_interpret：只存 interpret_result + Observation；删除 post_interpret_exclude_reason / application_exclude_reason 导致的 _exclude
   - _reduce_match：只存 match_result + Observation；删除 match_exclude_reason / _exclude / _task_reject；不要因 match 成功自动推进任务
   - _reduce_analyze：analysis_status != ok 只记 Observation，不 _exclude
   - _reduce_insufficient_match：只记 insufficient 材料，不 exclude / _task_reject
   - _reduce_rank：禁止把 recommendation 写成 stage=recommended/excluded
   - _reduce_plan：失败只记录，不在已有 jobs/plans 时 FAILED，不替 Reasoner finish
   - _reduce_execute：成功/失败/already_applied 只写 Observation / history / Verification 材料，不 _exclude
   - _apply_goal_constraints / _apply_turn_constraints：停止把 UserGoal.cities / salary_min 写入 constraints。exclude_companies → blacklist 可保留
2. rules/filters.py 与 rules/application.py：Loop 不再用 application_evidence==applied 或 hard_requirements_met==false 自动排除。函数可改成纯读取/标注，供 payload 展示。
3. rules/stop.py：run_loop / reduce 不得调用 should_continue_search / should_stop_enriching 来结束任务。配额数字可继续进入 Reasoner payload。
4. 新增 tests/agent/test_program_must_not_decide.py，覆盖 Phase 1「测试要求」。
5. 改写因此变红的旧测试：旧测试在保护 Pipeline，应改成保护「Program 不决策」。不要为了绿而把自动 exclude 加回去。
6. tests/agent/test_loop_scenarios.py 里用户 blacklist、search 不自动 open 的断言保留。decide_next_action 本 Phase 先留着。

不要做：删除 decide_next_action；改 NEEDS_UNDERSTANDING；重写 Evidence schema；统一平台 Tool 名；自动投递。

自测：
pytest tests/agent tests/rules tests/task -q

PASS：新原则测试绿，reduce 不再因语义结果自动排除/推荐。
FAIL：用启发式补语义，或把决策搬到 Tool。STOP。
完成后只报告 Phase 1 PASS/FAIL 和改动文件，不要开始 Phase 2。
```

---

# Phase 2 — 取消第二决策器

### 目标

系统只剩一个业务决策中心：`reason_next_action`。  
Job-level 的 skip / surface / continue / finish 由主 Reasoner 直接输出 `tool` / `ask_user` / `finish`。

### 前置

Phase 1 PASS。

### 必须改

1. 从 Loop 可用 Tool 中移除 `decide_next_action`：
   - 加入 `REASONER_HIDDEN_TOOLS` 或直接从 `build_registry` 注销
   - `available_tool_contracts` 不得再暴露它
2. 删除 `_reduce_decide_next` 的业务副作用；若 Tool 仍被误调用，只记 Observation，不 exclude / 不 COMPLETE_TASK。
3. `AgentRoutingLLM.default_reasoner_decision`：match 之后直接 `ask_user` / `finish` / 再 search / 再 open，**禁止**再调 `decide_next_action`。在 `tests/mock_llm.py` 标明这是测试双的一种探索策略，不是生产流水线。
4. `reason_next_action` 的 prompt 可说明：Job 只是当前考虑对象；不要外包给第二个决策器。不要写死「match 后必须 ask_user」。
5. 改写测试：
   - `tests/agent/test_loop_scenarios.py`：删除「必须调用 decide_next_action」
   - `tests/task/test_job_search_task.py`：用户看到职位改为 Reasoner `ask_user`（可带 job_key）；不要再 stub `decide_next_action`
   - `tests/agent/test_registry.py`：available tools 不再包含 `decide_next_action`

### 允许保留

`tools/decide_next_action.py` 源文件可暂留但必须从生产 Loop 断开。优先删除其 registry 注册。不要留「生产 fallback」。

### 禁止

- 不要把 REJECT_JOB / SURFACE_TO_USER 编码进 `reduce`
- 不要让 Program 在 match 后自动 ask_user
- 不要改 understanding 闸门（Phase 3）

### 验收

- 生产路径：Reasoner → Action，中间无 `decide_next_action`
- `pytest tests/agent tests/rules tests/task -q` 绿
- 高匹配场景仍可由 **测试双 Reasoner** 选择 `ask_user` 进入 WAITING_USER；这是 Reasoner 选择，不是 Program

### 下一 Phase 进入条件

Registry / Mock / 任务测试都不再依赖第二决策器。

---

## Cursor 执行 Prompt — Phase 2

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 2。前置 Phase 0–1 必须已 PASS。

目标：取消第二决策器。只保留 reason_next_action 作为唯一业务决策中心。
Job 的 skip / surface / continue / finish 由主 Reasoner 直接输出 tool / ask_user / finish。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md Phase 2 与 B4/B6。
不要把 REJECT_JOB 逻辑搬进 reduce 或 validate_action。不要启发式。

必须做：
1. tools/registry.py：decide_next_action 对 Loop Reasoner 不可见。加入 REASONER_HIDDEN_TOOLS，或从 build_registry 注销。available_tool_contracts 不得返回它。
2. agent/loop.py：删除或掏空 _reduce_decide_next 的 exclude / COMPLETE_TASK / pending_job 副作用。误调用只记 Observation。
3. tests/mock_llm.py：
   - default_reasoner_decision 在 match 之后不得再调用 decide_next_action
   - 改为 Reasoner 自己 ask_user / finish / 继续 search 或 open
   - 文件/函数注释写明：这是测试双的一种合法探索策略，不是生产 Pipeline，不是 Program 应执行的顺序
4. tools/reason_next_action.py prompt：可写「不要把 Job 决策外包给另一个决策器」。禁止写死 match 后必须 ask_user。
5. 改测试：
   - tests/agent/test_loop_scenarios.py 去掉「必须有 decide_next_action」
   - tests/task/test_job_search_task.py 改为断言 Reasoner ask_user / finish / execute，不再 stub decide_next_action
   - tests/agent/test_registry.py 断言 available tools 不含 decide_next_action
6. 新增测试：match Observation 之后，ScriptedReasoner 走 ask_user、再 search、finish 都合法；Program 不因 match 自动 ask_user。

不要做：改 NEEDS_UNDERSTANDING；改 current_job 选择器（Phase 4）；重写 Memory；自动投递。

自测：
pytest tests/agent tests/rules tests/task -q

PASS：生产 Loop 不再出现第二决策器；场景测试仍可通过测试双 Reasoner 到达 WAITING_USER。
FAIL：Program 在 match 后自动 ask_user / exclude。STOP。
完成后只报告 Phase 2 PASS/FAIL 和改动文件，不要开始 Phase 3。
```

---

# Phase 3 — 理解不再是闸门；Ask User 无业务副作用

### 目标

1. `understanding_status` 只是信息状态。
2. `ask_user` 只是「需要用户信息 / 用户决定」，不自动 recommended，不擅自选 Job，不把 GOAL_INCOMPLETE 改写成 DONE。

### 前置

Phase 2 PASS。

### 必须改

1. `agent/validate_action.py`：删除 `NEEDS_UNDERSTANDING` 整段闸门。
2. 可选收紧（B2）：若 search arguments 的 keyword 与未理解的 `pending_goal` 原文相同，可拒绝「把原文当搜索词」。Reasoner 自选不同 keyword 必须放行。不要因此禁止整个 search Tool。
3. `_apply_ask_user`：
   - 删除 `error_code == GOAL_INCOMPLETE` → `_finish_goal_incomplete`
   - 删除「有 match_result 就 stage=recommended + surface_job + 选 latest matched」
   - 正确行为：进入 `WAITING_USER`，把 question / 可选 job_key（仅当 Reasoner arguments 显式给出）写入 Observation / output
   - 没有 job_key 就是普通澄清，不要找一个 Job 来推荐
4. `_apply_finish`：Reasoner `finish` 可以结束（这是 Reasoner 决定）。`GOAL_INCOMPLETE` 只有在 Reasoner `action_type=finish` 时才写该 error_code。ask_user 不得变 finish。
5. `reason_next_action` prompt：理解未完成 ≠ 不能行动；不要把原文当结构化事实。
6. 测试：
   - ScriptedReasoner：`understanding_status=not_understood` 仍可 search（自选 keyword）
   - ScriptedReasoner：ask_user + GOAL_INCOMPLETE → 状态 WAITING_USER，不是 DONE
   - ScriptedReasoner：ask_user 且未指定 job_key → 不出现 recommended Job
   - `tests/agent/test_candidate_memory_architecture.py` `test_case3`：若仍期望 GOAL_INCOMPLETE，必须是 **Reasoner finish**，不是 Program 把 ask 改写成 DONE。更优：改为 ask_user 等待目标。两种都合法，由测试双显式选择，并断言 Program 没有改写 action 类型。

### 禁止

- 不要让 `can_search` / `can_match` / `proceed.py` 重新变成闸门
- 不要在 validate 里根据 task_kind 路由

### 验收

`pytest tests/agent tests/test_understand_user_input.py tests/agent/test_candidate_memory_architecture.py -q` 绿。

### 下一 Phase 进入条件

理解闸门与 ask_user 副作用已删除。

---

## Cursor 执行 Prompt — Phase 3

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 3。前置 Phase 0–2 必须已 PASS。

目标：
1. understanding_status 不再是 Program 总闸门。
2. ask_user 不再自动 recommended / 自动选 Job / 把 GOAL_INCOMPLETE 改成 DONE。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md B2 与 Phase 3。
Program 可以拒绝非法参数，不能改写 Reasoner 的 action_type。

必须做：
1. agent/validate_action.py：删除 NEEDS_UNDERSTANDING 闸门。
   可选：若 search keyword 等于未理解的 pending_goal 原文，拒绝该参数（数据完整性）。Reasoner 自选其他 keyword 必须放行。不要禁止整个 search Tool。
2. agent/loop.py _apply_ask_user：
   - 禁止 error_code==GOAL_INCOMPLETE 时调用 _finish_goal_incomplete
   - 禁止因为存在 match_result 就 stage=recommended、surface_job、或 _latest_matched / _pending_record 自动选 Job
   - 进入 WAITING_USER；question 写入 output
   - 只有 Reasoner arguments 显式带 job_key 时，才把该 Job 写入 waiting output。没有 job_key 就是普通澄清
3. _apply_finish：仅当 Reasoner action_type=finish 时结束。GOAL_INCOMPLETE 只能出现在 Reasoner 主动 finish 的 output 里。
4. tools/reason_next_action.py prompt：理解未完成也可以行动；不要把原文当结构化事实。
5. 新增/改测试：
   - not_understood 时 ScriptedReasoner 自选 keyword 搜索必须成功，不得被 validate 拒绝
   - ask_user + GOAL_INCOMPLETE → WAITING_USER，不是 DONE
   - ask_user 未给 job_key → output 没有自动 recommended Job
   - tests/agent/test_candidate_memory_architecture.py test_case3：GOAL_INCOMPLETE 必须来自测试双 Reasoner 的 finish 或改为 ask_user；断言 Program 没有改写 action_type
6. agent/proceed.py 的 can_search / can_match 不得重新接入 validate / loop。无引用可删。

不要做：改 current_job 选择器（Phase 4）；拆 understanding 任务路由（Phase 5）；重写 Memory schema；启发式。

自测：
pytest tests/agent tests/test_understand_user_input.py tests/agent/test_candidate_memory_architecture.py -q

PASS：闸门与 ask_user 副作用消失，上述测试绿。
FAIL：Program 把 ask_user 改写成 finish，或重新引入 understanding 总闸门。STOP。
完成后只报告 Phase 3 PASS/FAIL 和改动文件，不要开始 Phase 4。
```

---

# Phase 4 — Reasoner 自己选择关注哪个 Job

### 目标

「现在研究哪个 Job」不再是 Program 世界状态。  
Program 提供 jobs available；Reasoner 在 Tool arguments 里显式给出 job_id / job_url / job_key。

### 前置

Phase 3 PASS。

### 必须改

1. `build_reasoner_payload`：
   - 保留 `jobs[]` 摘要（stage / 是否已有 interpret/profile/match / constraint_flags）
   - **删除** Program 用 `in_flight_job()` / `next_job_to_open()` 填出来的 `current_job` 作为「指定对象」
   - 可以提供 `waiting_user_job_key`（用户正在被询问的那个，属于 Memory），不要提供「下一个该处理的 Job」
2. `in_flight_job` / `next_job_to_open`：Loop / reduce / payload **不得**再用它们选目标。可删，或降为测试/统计辅助且无调用方。
3. `_find_record`：必须能从 Action arguments 的 job_key / job_id / job_url 解析。解析失败 → Observation 错误，回到 Reasoner；不要 fallback 到「飞行中的 Job」，不要 FAILED 除非是系统级损坏。
4. `pending_job_key`：禁止 match 成功或 Program 扫描 listed 来设置。仅当 Reasoner / 用户授权 apply 的 arguments 显式给出。
5. 测试双与 ScriptedReasoner：open / interpret / analyze / match / execute 必须自带 job 标识，不读 `constraints.current_job`。
6. 新增测试：
   - search 后 payload 无 Program 指定的 current_job
   - Reasoner 打开 listed 中的第二个 Job，而不是必须第一个
   - interpret 不带 job 标识 → 拒绝或 Observation 错误，不默默作用到某个 Job

### 允许

`task.current_job_context_id` 仅表示「上次 ask_user 明确指向的 Job」（WAITING_USER Memory）。不是「Program 正在研究的 Job」。

### 验收

`pytest tests/agent tests/task -q` 绿。  
打开顺序不再被 `next_job_to_open` 锁死。

### 下一 Phase 进入条件

payload 与 reduce 都不再指定当前研究 Job。

---

## Cursor 执行 Prompt — Phase 4

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 4。前置 Phase 0–3 必须已 PASS。

目标：Reasoner 自己决定下一步关注哪个 Job。Program 只提供 jobs available in memory，不得设置 current_job 并让后续逻辑围着它转。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md Phase 4。
不要启发式。不要改平台 Adapter。

必须做：
1. agent/reasoner_context.py：
   - 保留 jobs[] 摘要
   - 删除用 in_flight_job() / next_job_to_open() 组装的 current_job「指定对象」
   - 可以保留 waiting_user_job_key（用户正在被问的 Job，属于 Memory）
2. agent/state.py 的 in_flight_job / next_job_to_open：loop / reduce / payload 不得再用来选目标。无调用则删除。
3. agent/loop.py _find_record：只从 Action arguments / 明确 job_key / job_id / job_url 解析。失败记 Observation 并回到 Reasoner，不 fallback 到「飞行中 Job」。
4. pending_job_key：禁止因 match 成功或扫描 listed 而设置。只有 Reasoner / 用户授权 apply 的显式 arguments 才能设置。
5. tests/mock_llm.py 与 tests/agent/test_reasoner_loop.py：open / interpret / analyze / match / execute 必须自己带 job 标识，不依赖 constraints.current_job。
6. 新增测试：
   - search 后 Reasoner payload 没有 Program 指定的 current_job
   - ScriptedReasoner 可以打开 listed 里的非第一个 Job
   - interpret 缺少 job 标识不会被默默套到某个 Job

不要做：拆 _reduce_understanding 的任务路由（Phase 5）；重写 Evidence schema；恢复 decide_next_action。

自测：
pytest tests/agent tests/task -q

PASS：Job 目标由 Reasoner arguments 指定；上述测试绿。
FAIL：Program 再次用 in_flight / next_to_open 指定当前 Job。STOP。
完成后只报告 Phase 4 PASS/FAIL 和改动文件，不要开始 Phase 5。
```

---

# Phase 5 — 理解与任务路由退出 reduce

### 目标

`understand_user_input` 的结果是 Interpretation + Observation。  
Program 不再根据 `task_kind` / `resolve_task_intent` 自动建任务、follow-up、skip、apply、stop。

### 前置

Phase 4 PASS。

### 必须改

1. `_reduce_understanding`：
   - 校验 schema / analysis_status
   - 把 understanding 写入 Memory（goal 字段、supplement claims、preferences 作为 Claim/Interpretation，不要当成已验证事实）
   - 写 Observation：`user_turn_understood`（含 task_kind 原文、是否有 goal、是否有附件）
   - **删除** `INTENT_CLARIFY / RESUME / FOLLOW_UP` 分支里的 Program 业务推进
   - **删除** `has_user_goal → create_bound_task` 自动建任务
   - **删除** `_reduce_follow_up` / `_reduce_task_control` 从 reduce 触发
2. 任务动作改由 Reasoner 显式 Action 执行，例如：
   - 绑定/恢复任务、skip_job、apply（仍需 user_authorization）、stop_task
   - 或先用 Observation 把「理解层认为这是 continue_task」交给 Reasoner，下一轮 Reasoner 再选 Action
   - 不要在 reduce 里执行这些业务
3. `_reduce_follow_up` 的「引用无法解析 → 直接 DONE」必须已经不存在；只能是 Observation `reference_unresolved`，由 Reasoner ask_user。
4. `task/identity.py` 的 `resolve_task_intent` 可改为纯函数，结果进入 payload / Observation，不进 reduce switch。
5. 改测试：`tests/task/test_job_search_task.py`、`tests/test_conversation_reference.py`、`tests/agent/test_reasoner_loop.py` 的 clarify 用例。断言：理解之后 Program 不 DONE、不自动 skip；Reasoner 看到 Observation 后才 ask / skip / search。

### 禁止

- 不要为了「对话还能用」把路由留在 reduce
- 不要用 keyword 匹配「继续/跳过/投递」替代 LLM understanding（understanding Tool 本身仍是 LLM）

### 验收

`pytest tests/agent tests/task tests/test_job_context.py tests/test_conversation_reference.py -q` 绿。

### 下一 Phase 进入条件

理解结果不再驱动任务状态机。

---

## Cursor 执行 Prompt — Phase 5

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 5。前置 Phase 0–4 必须已 PASS。

目标：understand_user_input 只产生 Interpretation + Observation。Program / reduce 不再根据 task_kind 自动建任务、follow-up、skip、apply、stop。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md Phase 5。
任务不是固定状态机。下一步由 Reasoner 选。
语义红线：understand_user_input 必须继续走 LLM；禁止用「继续/跳过」关键词在 Program 里猜意图。

必须做：
1. agent/loop.py _reduce_understanding：
   - 只做 schema 校验、写入 understanding / goal 字段 / supplement claims / preferences、刷新只读 context、写 Observation
   - 删除 INTENT_CLARIFY / RESUME / FOLLOW_UP 引发的 Program 推进
   - 删除 has_user_goal 时 create_bound_task
   - 删除从此函数调用 _reduce_follow_up / _reduce_task_control
2. 引用未解析、任务需澄清：只写 Observation（已有 user_turn_understood / reference_unresolved / task_clarification 就保持），由 Reasoner ask_user。禁止 _finish_unresolved_reference / _finish_task_clarification 在 reduce 里 DONE。
3. resolve_task_intent 变成可放入 payload 的材料，不是 reduce switch。
4. 若仍需要 skip/apply/stop 的持久化，做成 Reasoner 可调用的显式 Action / Program 执行契约（apply 仍要求 user_authorization）。不要从 understanding reduce 触发。
5. 改写 tests/task/test_job_search_task.py、tests/test_conversation_reference.py、tests/agent/test_reasoner_loop.py：
   - 理解之后 Program 不自动 DONE / skip / apply
   - ScriptedReasoner 看到 Observation 再 ask_user 或显式任务 Action
6. 新增测试：同一条 understanding Observation，Reasoner 选 ask / search / finish 都合法。

不要做：重写 Evidence/Claim 存储形状（Phase 6）；恢复 understanding 总闸门；恢复 decide_next_action；启发式意图。

自测：
pytest tests/agent tests/task tests/test_job_context.py tests/test_conversation_reference.py -q

PASS：理解不再驱动任务状态机；上述测试绿。
FAIL：reduce 仍按 task_kind 推进业务。STOP。
完成后只报告 Phase 5 PASS/FAIL 和改动文件，不要开始 Phase 6。
```

---

# Phase 6 — 语义层级 + Resume 只是 Evidence

### 目标

区分 Evidence / Claim / Interpretation / Verification。  
Resume / 用户输入 / 附件进入同一信息体系。  
删除「LLM 抽取 + LLM 自证 = 事实」。

### 前置

Phase 5 PASS。控制权已经不在 reduce。

### 必须改

1. 在 AgentState / candidate memory 中增加（可与旧 `facts` 并存一个 Phase）：
   - `evidence[]`：source / provenance / timestamp / content_type / origin / content / source_ref
   - `claims[]`：derived_from evidence ids
   - `interpretations[]`：derived_from evidence/claim ids
   - `verifications[]`：必须指向独立依据（用户确认，或 Tool 机械执行结果）
2. `run_agent(resume=...)` 降为兼容入口：把简历变成 `content_type=resume` 的 Evidence / attachment。禁止再把它当成 Candidate Agent 特殊语义入口。
3. 删除承担业务路由的 `if kind == "resume"`（`_normalize_attachments` 只允许打 content_type 标签，不得走另一套管线）。
4. `analyze_candidate` / `understand_user_input` 的输出进入 Claim + Interpretation，`evidence_status` 默认 `unverified`。
5. `verify_candidate_claims`：
   - 不得再把同轮语料的 LLM 判断写成 Verification / `supported` 事实
   - 可改名为 support_check，结果是 Interpretation（consistency_hint），或直接默认全部 `unverified`
   - 没有独立证据就保持 `unverified`
6. `interpret_job_actions` 的 `application_evidence` 定位为 Interpretation，写入 Job 的 interpretations，不是 Verification（除非后续有独立机械证据）。
7. `project_profile_view` 必须只读投影，并保留 derived_from。禁止投影过程把 unverified 升级成 fact。
8. `uninterpreted_evidence` 若要实现：Evidence 已被 Claim.derived_from 或 Interpretation 引用即算进入处理链。不要要求每条 Evidence 都有直接 Interpretation。
9. 冲突保留：同一 skill 的用户 Claim 与简历 Evidence 不一致时不得 merge 成单一真相。
10. 测试：
    - 同轮 verify 不得产生 Verification.supported
    - resume 与 user_message 都出现在 evidence 列表，路由相同
    - ScriptedReasoner 可在只有 user_message、没有 resume 时 search（已有场景则加固断言：不是因为缺 resume 被 Program 拦住）

### 禁止

- 不要把 `candidate.memory.facts` 继续当成「模型认为为真」
- 不要在本 Phase 改平台 Tool
- 不要让 Program 根据 verification 缺失自动拒绝 search

### 验收

`pytest tests/test_verify_candidate_claims.py tests/test_analyze_candidate.py tests/agent/test_candidate_memory_architecture.py tests/agent -q` 绿。

### 下一 Phase 进入条件

语义层级可表达，且 LLM 自证不再等于事实。

---

## Cursor 执行 Prompt — Phase 6

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 6。前置 Phase 0–5 必须已 PASS。

目标：建立 Evidence / Claim / Interpretation / Verification。Resume 只是 Evidence 的一种 content_type。删除 LLM 自证当事实。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md B5 与 Phase 6。
控制权已经在 Reasoner。本 Phase 只改信息层级，不把新字段变成 Program 闸门。
语义红线：analyze_candidate / understand_user_input 继续走真实 LLM；失败则 llm_unavailable / llm_error / analysis_failed。禁止关键词抽取。

必须做：
1. 在 candidate memory / AgentState 增加 evidence / claims / interpretations / verifications（本 Phase 允许与旧 facts 并存，但新写入不得把 LLM 输出当成 verified fact）。
2. 每条重要字段能回答 derived_from。project_profile_view 只读，且不得把 unverified 升级为 fact。
3. run_agent(resume=...) 变为兼容包装：把简历变成 content_type=resume 的 Evidence/attachment。删除 if kind==resume 的业务路由。_normalize_attachments 只能打标签。
4. understand / analyze_candidate 写入 Claim + Interpretation，默认 unverified。
5. understanding/verify_candidate_claims.py：同轮语料 LLM 判断不是 Verification。改为 support_check / Interpretation，或全部保持 unverified。禁止再写 evidence_status=supported 当事实成立。
6. interpret_job_actions.application_evidence 作为 Interpretation 存入 Memory，不是 Verification。
7. 若计算 uninterpreted_evidence_ids：Evidence 被 Claim.derived_from 或 Interpretation 引用即算已进入处理链。
8. 允许冲突：用户 Claim「做过支付」与简历未提支付必须同时保留，不得 Program merge 成单一真相。
9. 测试：
   - tests/test_verify_candidate_claims.py：自证不得变成 Verification.supported
   - resume 与 user_message 都进入同一 evidence 体系
   - 无 resume 也能由 Reasoner 选择 search（Program 不拦）
   - 投影不含「无来源技能」

不要做：用新语义字段恢复 understanding 闸门；自动 exclude；换 LLM；统一平台 Tool 名；自动投递。

自测：
pytest tests/test_verify_candidate_claims.py tests/test_analyze_candidate.py tests/agent/test_candidate_memory_architecture.py tests/agent -q

PASS：语义层级可追溯；LLM 自证不再是事实；测试绿。
FAIL：supported 仍由同轮 LLM 自证产生 Verification。STOP。
完成后只报告 Phase 6 PASS/FAIL 和改动文件，不要开始 Phase 7。
```

---

# Phase 7 — Memory 一等公民 + 测试原则收口

### 目标

Reasoner payload 变成视图，而不是「Program 处理好的世界真相」。  
跨轮持久化遵守语义层级。  
测试从固定流程测试转为 Agent 原则测试。

### 前置

Phase 6 PASS。

### 必须改

1. `build_reasoner_payload` 收敛为：
   - Goal / Context
   - Evidence / Observations / Claims / Interpretations / Verifications
   - Available Tools / Constraints（配额、用户明确禁止、安全）
   - History / Uncertainty
   - jobs available（含原始 JD evidence 引用）
   - **不要**再给「这个 Job 已排除，所以下一步应该分析」这类 Program 结论
2. `CandidateProfile` / `JobProfile` 在 payload 中标明 `kind=interpretation_projection`，并带 derived_from。Reasoner 必须仍能看到原始 JD / 简历 Evidence。
3. `CandidateProfileStore` 持久化：存 evidence/claims/interpretations/verifications（或可还原它们的结构），不要只存「profile = facts」。
4. `rank_jobs` / `build_report`：只读投影 / 输出组装。不改变业务状态。无调用方可从 Loop 工具表拿掉 `rank_jobs`。
5. 删除或隔离 `should_continue_search` 等 Program 停搜决策；payload 只给 remaining quota。
6. 测试收口：
   - 同一 Observation：search / open / ask / finish 都合法（已有则保持）
   - Program 不得自动 exclude / recommend / apply / finish
   - search 后不得自动 open
   - 仓库内不得再存在生产路径：`reason_next_action` + `decide_next_action` + reduce routing + stop rules 共同决定下一步
   - `AgentRoutingLLM` 继续只作为场景 double
7. 给 `docs/AI_JOB_AGENT_BASELINE_v1.md` 补一句：Phase 4–6 旧闭环描述已被 Observe→Remember→Reason→Act 取代。

### 验收

`pytest tests/agent tests/rules tests/task tests/test_analyze_candidate.py tests/test_analyze_job.py tests/test_match_job.py -q` 绿。

### 下一 Phase

纠偏主线完成。Phase 8 可选。

---

## Cursor 执行 Prompt — Phase 7

```text
你在 AI Job Agent 仓库做架构纠偏 Phase 7。前置 Phase 0–6 必须已 PASS。

目标：Memory 成为一等公民。Reasoner payload 是视图，不是 Program 处理好的世界真相。测试改为 Agent 原则测试。

权威：docs/ARCHITECTURE_CORRECTION_PHASES.md Phase 7 与第 49/50/55 节原则。
不要重写 Tool Registry。不要换 LLM。不要自动投递。

必须做：
1. agent/reasoner_context.py：payload 提供 Goal/Context、Evidence、Observations、Claims、Interpretations、Verifications、Available Tools、用户明确 Constraints、History、Uncertainty、jobs available。删除「当前 Job 已排除 / 下一步应该分析」这类 Program 结论字段。
2. CandidateProfile / JobProfile 在 payload 里标明 interpretation_projection，带 derived_from。必须同时能看到原始 JD / 简历 Evidence。
3. storage/candidate_profile.py：跨 run 持久化遵守 Evidence/Claim/Interpretation/Verification。禁止只把 profile 当 facts 写回后再当唯一真相读出。
4. rank_jobs / build_report 只读。不改 stage。可从 Loop 可用工具移除 rank_jobs。
5. rules/stop.py / agent/proceed.py：不得再参与下一步决策。payload 只保留 remaining quota 数字。
6. 测试收口（新增或加固，不要再写固定流水线测试）：
   - 同一 Observation 可 search / open / ask / finish
   - Program 不自动 exclude / recommend / apply / finish
   - search 后不自动 open
   - 生产路径不存在第二决策器 + reduce 业务路由 + stop rules 共治
7. 在 docs/AI_JOB_AGENT_BASELINE_v1.md 注明旧 Phase 4–6 闭环描述已被 Observe→Remember→Reason→Act 取代。

不要做：统一 search_boss_jobs 为 search_jobs（那是可选 Phase 8）；引入启发式；把 Verification 缺失当成 search 闸门。

自测：
pytest tests/agent tests/rules tests/task tests/test_analyze_candidate.py tests/test_analyze_job.py tests/test_match_job.py -q

PASS：payload 是视图；持久化不把 Interpretation 当唯一事实；原则测试绿。
FAIL：为了旧测试把 Program 决策加回去。STOP。
完成后只报告 Phase 7 PASS/FAIL。不要自行开始 Phase 8。
```

---

# Phase 8 — 可选：平台 Adapter 合同（不阻塞主线）

### 目标

Reasoner 只看见 `search_jobs` / `open_job` / `inspect_job` / `inspect_application_state` / `execute_action`。  
BOSS / 猎聘 / 51 的 selector / DOM / URL 留在 Adapter。

### 前置

Phase 7 PASS。仅当明确要求做多平台合同统一时才执行。

### 不要提前做的原因

现有 Reasoner 已通过 `available_tools` 选 `search_boss_jobs` 等。过早改名会把 0–7 的测试与 Mock 一起炸开，且不增加控制权清晰度。

---

## Cursor 执行 Prompt — Phase 8（可选，默认不要跑）

```text
只有用户明确要求「统一多平台 Tool 合同」时才做。前置 Phase 0–7 必须 PASS。

目标：Reasoner 使用平台无关 Job Actions；BOSS/猎聘/51 差异留在 Adapter。不要把 CSS/DOM 渗入 Reasoner / Memory / Match。

不要改语义红线，不要自动投递，不要把 Adapter 做成小 Agent。

自测：pytest tests/platforms tests/agent -q
完成后报告 PASS/FAIL。
```

---

## 每个 Phase 自检（执行者填）

执行完一个 Phase，用这五问验收，任一为「后者」则 FAIL：

1. 这段逻辑是在记录世界，还是在决定下一步？
2. 是在执行 Reasoner 已有决定，还是在替它做决定？
3. 字段是 Evidence / Claim / Interpretation / Verification 中的哪一种？
4. 状态是世界事实、模型理解，还是 Reasoner 当前决定？
5. 规则是在防止非法行为，还是在指导正确业务行为？
