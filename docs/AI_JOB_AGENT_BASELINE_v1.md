# 《AI Job Agent 总体实施基线 v1.0》

> **控制流权威**：Agent 控制流以 `docs/ARCHITECTURE_CORRECTION_PHASES.md` 为准。本文产品目标 / Tool / Human Gate 仍有效；旧基线中的固定流水线、Program 自动排除/推荐/停止、`decide()` 状态机不再作为实现依据。
>
> **闭环描述**：旧基线 Phase 4–6 的固定闭环（理解目标 → 搜 → 开 → 分析 → 匹配 → 过滤 → 停止 → 推荐）已被 Observe→Remember→Reason→Act 取代。Reasoner payload 是 Memory 视图，不是 Program 处理好的世界真相。

> **执行者**：你 + Cursor（无独立开发团队）  
> **用法**：按 Phase 顺序，把每个 Phase 末尾的 **「Cursor 执行 Prompt」** 原样复制给 Cursor。  
> **架构立场**：沿用已确认的 Agent Loop / State / Tool / LLM 边界；**不推翻、不改成「Tool 拼脚本」**。  
> **版本**：v1.0（2026-09-10）  
> **变更规则**：改 Schema / Phase 顺序 / 红线须升版为 v1.1+，并更新本文件。

---

## 自测与 Phase 依赖确认（执行前必读）

### 结论

| 问题 | 结论 |
|---|---|
| 每个 Phase 是否都有可执行自测？ | **是**。见下方「自测矩阵」；每个 Phase 的「测试要求 / 验收标准 / Cursor Prompt」均含必跑检查。 |
| Phase 间先后关系是否正确处理？ | **是**。严格线性：0→1→2→3→4→5→6，然后 7∥8→9→10→11；每个 Phase 含「前置 Phase / 前置条件 / 下一 Phase 进入条件」；不满足则 **STOP**。 |

### 自测矩阵

| Phase | 自测类型 | 必跑命令 / 检查 | 不依赖真实 BOSS |
|---|---|---|---|
| **0** | 文档/结构检查 | 基线文件存在；README 指向基线；禁止范围未被误改 | 是 |
| **1** | 语义单测 + MockLLM | `pytest tests/test_analyze_candidate.py -q` | 是 |
| **2** | 语义单测 + MockLLM | `pytest tests/test_analyze_job.py tests/test_analyze_candidate.py -q` | 是 |
| **3** | 语义单测 + MockLLM | `pytest tests/test_match_job.py tests/test_analyze_*.py -q` | 是 |
| **4** | Agent Loop + Rules + Mock 源 | `pytest tests/agent tests/test_match_job.py tests/test_analyze_candidate.py tests/test_analyze_job.py -q` | 是（**首次无站闭环**） |
| **5** | Loop 接 BOSS + Human Gate；Fake 必绿 | `pytest tests/agent -q`（及平台单测）；live **optional** | Fake 必过；live 非 CI 门禁 |
| **6** | 继续搜/停止策略 | `pytest tests/agent -q`（含继续/停止用例） | 是（可用 Mock 源） |
| **7** | 猎聘 Adapter 单测 + Registry | `pytest tests/platforms/test_liepin*.py tests/agent -q` | 可用 Fake 结构测 |
| **8** | 51 Adapter 单测 + Registry | `pytest tests/platforms/test_job51*.py tests/agent -q` | 可用 Fake 结构测 |
| **9** | 跨平台去重/排序 | `pytest tests/rules tests/agent -q`（跨平台 fixture） | 是 |
| **10** | 本地历史 IO | `pytest tests/test_history*.py -q` | 是 |
| **11** | 架构预留检查 | 断言自动投递 API 为 NotImplemented；无点击投递代码 | 是 |

### Phase 依赖规则（强制）

1. **不得跨 Phase** 实现后续能力（除非当前 Phase 明确允许的兼容改动）。  
2. 进入 Phase N 前，Phase 0…N-1 必须报告 **PASS**。  
3. 前置不满足 → **STOP**，报告缺失项，**不得**自行改架构绕过。  
4. Runtime State（`INIT`/`SEARCHING`/…）≠ Development Phase（Phase 0/1/…）。

### Phase Dependency Map

```text
Phase 0  基线入库 + 现状冻结标注
    ↓
Phase 1  CandidateProfile + analyze_candidate
    ↓
Phase 2  JobProfile 冻结 + analyze_job 对齐
    ↓
Phase 3  MatchResult + match_job
    ↓
Phase 4  Agent State + Loop + Rules + Mock 数据源   ← 首次无 BOSS 真闭环
    ↓
Phase 5  BOSS 单平台接入 + Human Gate                ← MVP 真站最小闭环
    ↓
Phase 6  自主继续搜索 / 停止决策增强
    ├──→ Phase 7 猎聘 Adapter
    └──→ Phase 8 51Job Adapter
              ↓
         Phase 9 跨平台去重与综合推荐
              ↓
         Phase 10 历史与推荐记录
              ↓
         Phase 11 应用辅助（仅架构预留，不实现投递）
```

**顺序说明**：当前缺口最大的是语义三件套与 Loop；Registry 在 Phase 4 与 Loop 一并落地，避免 Phase 0 无意义大搬迁。BOSS 原子 Tool 已可用，故 **Brain（1–4）不阻塞于 BOSS 可达性**。

---

## 0. 两套概念（禁止混淆）

| 概念 | 是什么 | 例子 |
|---|---|---|
| **Development Phase** | 项目实施阶段（给人 / Cursor 的开发任务） | Phase 0 … Phase 11 |
| **Runtime State** | Agent 一次运行中的状态机 | `INIT` → `SEARCHING` → … → `DONE` |

下文 **Phase** = 开发阶段；**status** = 运行时状态。

---

## 1. 项目最终目标

**产品**：**Resume-driven Job Search Agent**  

**不是**：BOSS 脚本、Tool 集合、JD 分析器、关键词匹配器、爬虫、固定流水线。

**用户输入**：简历 + 求职目标（城市 / 职位偏好 / 薪资 / 排除公司等）  

**Agent 自主闭环**：

```text
理解目标 → CandidateProfile → 搜索计划 → search_* → CommonJob 入库
→ 确定性过滤（去重/黑名单/…）→ open_*（配额内全部合格岗位，无 keyword enrich）
→ interpret_job_actions → analyze_job → JobProfile → match_job → MatchResult
→ 过滤（已投/黑名单/hard 不符）→ 是否继续搜 → 停止 → 排序 → 推荐+理由
```

**人工介入仅限**：登录 / CAPTCHA / 权限 / 浏览器不可用 / 明确访问阻断。  
**禁止**把「请用户手动打开 JD」写成正式产品流程。

---

## 2. 最终 Agent 架构（已冻结）

```text
User
  → Agent Orchestrator
  → Agent State
  → Agent Loop (Observe → Decide → Act → Reduce)
  → Tool Registry
  → Tools（Data Source / Semantic LLM / Rules-Data）
  → External（Browser / 招聘站 / GLM）
  → Tool Result → State Update → Next Action
  → Ranking / Report → 推荐结果
```

| 层 | 职责 |
|---|---|
| Orchestrator | 读 State、决策、调 Tool、写回、停止、Human Gate |
| State | 唯一真相源 |
| Loop | 循环，非固定脚本 |
| Registry | 注册/校验/分发，不编排业务 |
| Data Source | `search_*` / `open_*` → CommonJob（+ raw_actions） |
| Semantic | `parse_user_goal` / `analyze_candidate` / `analyze_job` / `interpret_job_actions` / `match_job` |
| Rules | 去重、黑名单、已投合成结果执行、配额、停止、排序键 |
| Platform Adapter | BOSS / 猎聘 / 51 差异只在此 |

**禁止混放**：DOM ≠ Match；`open_*` ≠ `match_job`；`browse_*` ≠ Orchestrator。

---

## 3. Agent Loop（运行时，已冻结）

```text
while status ∉ {DONE, NEEDS_HUMAN, FAILED}:
  observe(state)
  action = decide(state)          # 代码状态机为主
  result = registry.invoke(action)
  state = reduce(state, result)   # 含确定性 rules
```

LLM **不得**生成 Playwright / 任意代码。  

### enrich_priority 政策（MVP）

MVP **无**关键词 `enrich_priority`。  
对通过确定性过滤的岗位，在 `max_open_jd` 内**按列表顺序**打开。  
若需粗筛，另开后续版本做 **LLM coarse screen**（本基线未排进 MVP）。  
**宁可多开，也不要 keyword 命中才打开。**

---

## 4. Agent State（运行时骨架，已冻结）

```text
AgentState
├── session { session_id, status, created_at }
├── goal: UserGoal
├── candidate { resume_ref, profile, profile_status }
├── search { plans[], active_plan_id, exhausted[], stats }
├── constraints { blacklist[], salary_min, cities[], platforms[],
│                 max_search_results, max_open_jd, max_llm_calls, min_recommend }
├── jobs: Map<JobKey, JobRecord>
├── decisions[]
├── human_gate | null
├── errors[]
└── output | null
```

**Runtime `status`**：  
`INIT | PARSING_GOAL | GATHERING_CANDIDATE | PLANNING | SEARCHING | ENRICHING_JOBS | ANALYZING | MATCHING | FILTERING | RANKING | REPORTING | NEEDS_HUMAN | DONE | FAILED`

**JobRecord.stage**：`listed | opened | analyzed | matched | excluded | recommended`

---

## 5. Tool Architecture（已冻结）

| 组 | Tool | MVP |
|---|---|---|
| Goal | `parse_user_goal` | ★ |
| Candidate | `analyze_candidate` | ★ |
| Job Source | `search_boss_jobs`, `open_boss_job` | ★ |
| Job Source | `search_liepin_*`, `open_liepin_*`, `search_51job_*`, `open_51job_*` | V1 |
| Semantic | `analyze_job`, `interpret_job_actions`, `match_job` | ★ |
| Planning | `plan_search` | MVP 可用代码模板；V1 可 LLM |
| Data | blacklist / tracker（可为 State 配置） | ★ 最小内存/JSON |

**`browse_boss_jobs`**：非正式架构组件 → 废弃入口，**不得**当 Agent。

**`open_*` 契约**：必须能 `goto(job_url)`；返回 CommonJob；可选 `raw_actions`；**不**在 Tool 内 match；**不**要求用户已打开页面。

---

## 6. LLM / Code Boundary（硬红线）

### LLM 负责

目标理解、简历→CandidateProfile、JD→JobProfile、页面动作 intent、匹配与 transferable / knowledge gap、推荐理由（基于 MatchResult）。

### Code 负责

浏览器、抽取、schema/JSON 校验、Tool 调用、State、去重、黑名单、intent→`application_evidence` 合成、配额、重试、错误分类、停止、排序、Human Gate。

### 严禁

keyword / regex 当语义、固定按钮文案/数量/DOM 当意图、heuristic fallback、LLM 不可用时猜测。

失败状态：`llm_unavailable` | `llm_error` | `analysis_failed` → **fail closed**。

Provider：仅现有 `OpenAICompatibleProvider` + env（GLM-4-Flash）。测试用 `MockLLMProvider`（返回 JSON），**不用** keyword Heuristic 冒充生产 Provider。

---

## 7. CandidateProfile Schema（冻结字段）

**对象名**：`CandidateProfile`  
**产生**：`analyze_candidate`（LLM）+ 代码校验  
**状态字段**：`analysis_status`: `ok | insufficient_data | llm_unavailable | llm_error | analysis_failed`

### 7.1 顶层

| 字段 | 类型 | 必填 | 可空 | 含义 | 来源 | 允许 LLM 推断 |
|---|---|---|---|---|---|---|
| `analysis_status` | string enum | 是 | 否 | 分析状态 | 代码 | 否 |
| `error` | string \| null | 否 | 是 | 失败信息 | 代码 | 否 |
| `candidate_id` | string \| null | 否 | 是 | 会话内 id | 代码 | 否 |
| `summary` | string \| null | 否 | 是 | ≤200 字画像摘要 | LLM | 是（须 evidence 支撑） |
| `target_roles` | string[] | 否 | 是 | 简历中体现的目标方向 | LLM | 弱推断须 `explicit=false` |
| `years_experience` | string \| null | 否 | 是 | 总年限表述 | LLM | 仅原文可支撑时 |
| `education` | string \| null | 否 | 是 | 最高学历等 | LLM | 否（须原文） |
| `locations` | string[] | 否 | 是 | 所在/意向城市 | LLM | 弱 |
| `product_capabilities` | CapabilityItem[] | 是* | 可空数组 | 产品能力 | LLM | 是+evidence |
| `business_capabilities` | CapabilityItem[] | 是* | 可空数组 | 业务能力 | LLM | 是+evidence |
| `technical_capabilities` | CapabilityItem[] | 是* | 可空数组 | 技术能力 | LLM | 是+evidence |
| `industry_experience` | ExperienceItem[] | 是* | 可空数组 | 行业经历 | LLM | 是+evidence |
| `management_experience` | CapabilityItem[] | 是* | 可空数组 | 管理经验 | LLM | 是+evidence |
| `project_experience` | ProjectItem[] | 是* | 可空数组 | 项目经验 | LLM | 是+evidence |
| `transferable_capabilities` | TransferableItem[] | 是* | 可空数组 | 可迁移能力 | LLM | **必须**路径+evidence |
| `knowledge_gaps` | GapItem[] | 是* | 可空数组 | 知识缺口 | LLM | 是 |
| `direct_capabilities` | CapabilityItem[] | 是* | 可空数组 | 直接相关能力汇总 | LLM | 是 |
| `raw_evidence_notes` | string \| null | 否 | 是 | 可选总注 | LLM | — |

\* `ok` 时上述数组字段必须存在（可为 `[]`）。

### 7.2 CapabilityItem

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `name` | string | 是 | 能力短名 |
| `kind` | `direct` \| `transferable` \| `inferred` | 是 | |
| `category` | `product` \| `business` \| `technical` \| `industry` \| `management` \| `other` | 是 | |
| `description` | string \| null | 否 | |
| `explicit` | bool | 是 | 是否简历明确写出 |
| `evidence` | Evidence[] | 是 | **至少 1 条**（除非 status≠ok） |
| `confidence` | number 0–1 | 是 | |

### 7.3 TransferableItem

| 字段 | 类型 | 必填 |
|---|---|---|
| `name` | string | 是 |
| `from_domain` | string | 是 |
| `to_domain_hint` | string \| null | 否 |
| `transfer_rationale` | string | 是 |
| `evidence` | Evidence[] | 是 |
| `confidence` | number | 是 |

### 7.4 ExperienceItem / ProjectItem / GapItem / Evidence

**ExperienceItem**：`industry`, `role`, `years_or_duration`, `description`, `evidence[]`, `explicit`, `confidence`  
**ProjectItem**：`name`, `role`, `description`, `outcomes`, `capabilities_demonstrated[]`, `evidence[]`, `explicit`, `confidence`  
**GapItem**：`gap`, `severity` (`high|medium|low`), `evidence[]`, `notes`  

**Evidence**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `quote` | string | 是 | 简历原文摘录 |
| `location_hint` | string \| null | 否 | 段落/经历标题提示 |
| `field` | string \| null | 否 | 支撑的字段名 |

**规则**：无 evidence 的能力项校验失败 → `analysis_failed`。禁止空话能力。

---

## 8. JobProfile Schema（冻结字段）

**对齐并升级**现有 `analyze_job` / `empty_analysis` 输出，正式命名为 **JobProfile**。

| 字段 | 类型 | 必填 | 含义 | LLM 推断 |
|---|---|---|---|---|
| `analysis_status` | enum | 是 | | 否（代码） |
| `error` | string\|null | 否 | | 否 |
| `job_id` / `job_title` / `company_name` | string\|null | 否 | 透传 | 否 |
| `job_summary` | string\|null | 否 | ≤100 字 | 是（仅概括 JD） |
| `hard_requirements` | RequirementItem[] | 是* | **仅硬性** | 见下 |
| `core_requirements` | RequirementItem[] | 是* | | 是，须 JD 依据 |
| `business_requirements` | RequirementItem[] | 是* | | 是 |
| `technical_requirements` | RequirementItem[] | 是* | | 是 |
| `industry_requirements` | RequirementItem[] | 是* | | 是 |
| `bonus_requirements` | RequirementItem[] | 是* | 优先/加分 | 是 |
| `responsibilities` | string[] | 是* | 职责非要求 | 是 |
| `experience_requirements` | `{years, education, seniority, other[]}` | 是* | | 是 |
| `keywords` | string[] | 是* | | 是 |

### RequirementItem

| 字段 | 类型 | 必填 |
|---|---|---|
| `requirement` | string | 是 |
| `category` | string | 是 |
| `importance` | `high` \| `medium` \| `low` | 是 |
| `explicit` | bool | 是 |
| `evidence_quote` | string \| null | 强烈建议 |

### Hard Requirement 严格定义

**只有** JD **明确**表达为必须/硬性的条件可入 `hard_requirements`，例如：「必须…」「须具备…」「要求：本科及以上」「N 年及以上」「持有 XX 证书」等**明示硬约束**。

**不得**因「看起来重要」升为 hard：

- preferred / bonus / 加分 / 有则更好  
- 模型推测的隐含要求  
- 未在 JD 写明的条件  

`bonus` → **只**进 `bonus_requirements`。  
**JobProfile 禁止**候选人/匹配用语。

---

## 9. MatchResult Schema（冻结字段）

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `analysis_status` | enum | 是 | |
| `error` | string\|null | 否 | |
| `job_id` / `candidate_id` | string\|null | 否 | |
| `hard_requirements_met` | bool \| null | 是* | null=无法判断 |
| `hard_requirement_gaps` | HardGap[] | 是* | |
| `capability_assessments` | CapAssessment[] | 是* | 逐项评估 |
| `business_fit` | FitLevel | 是* | |
| `technical_fit` | FitLevel | 是* | |
| `industry_fit` | FitLevel | 是* | |
| `overall_fit` | FitLevel | 是* | |
| `risks` | RiskItem[] | 是* | |
| `knowledge_gaps` | GapItem[] | 是* | 相对本 JD |
| `recommendation` | `yes` \| `weak` \| `no` \| `insufficient_evidence` | 是* | |
| `rationale` | string | 是* | 回答「为什么适合/不适合」 |
| `evidence_summary` | Evidence[] | 否 | |

**FitLevel**：`strong | moderate | weak | none | unknown`  

**CapAssessment**：

| 字段 | 类型 | 含义 |
|---|---|---|
| `dimension` | string | |
| `outcome` | `direct` \| `transferable` \| `missing` \| `insufficient_evidence` | **A/B/C/D 四态** |
| `job_requirement_ref` | string \| null | |
| `candidate_capability_ref` | string \| null | |
| `transfer_rationale` | string \| null | transferable 时必填 |
| `evidence` | Evidence[] | |

**HardGap**：`requirement`, `status` (`fail|unknown`), `notes`, `evidence[]`

**政策（代码执行，Phase 4+）**：  
`hard_requirements_met === false` → MVP **exclude**。  
`recommendation` 不覆盖 blacklist / already_applied。  

**原则**：无行业直接经验 ≠ 无岗位能力；B vs C 只能由 LLM `match_job` 判断。

---

## 10. Current Gap Analysis（基于仓库只读检查）

**仓库实况（基线制定时）**：存在 `tools/`、`llm/`、`browser/`、`tests/`、`browser_poc/`、`.cursor/rules/`；**不存在** `agent/`、`strategies/`、`candidate/`、`matching/`、Orchestrator、State、CandidateProfile 模块、MatchResult 模块。

| 当前模块 | 当前状态 | 是否正确 | 最终归属 | Phase | 处理方式 |
|---|---|---|---|---|---|
| `llm/provider.py` OpenAICompatibleProvider | 已实现 | 正确 | LLM | — | **保留** |
| `get_llm_provider` | 已实现 | 正确 | LLM | — | **保留** |
| `tests/mock_llm.py` MockLLMProvider | 已实现 | 正确 | 测试 | — | **保留并扩展** |
| `.cursor/rules/llm-semantic-redlines.mdc` | 已实现 | 正确 | 原则 | 0 | **保留** |
| `EMPTY_JOB` / `vue_item_to_job` | 已实现 | 部分正确 | CommonJob / platforms/boss | 4–5 | **保留字段**；迁 Adapter |
| `search_boss_jobs` | 已实现 | 正确（数据源） | platforms/boss | 5 | **保留** |
| `open_boss_job` | 已实现 | 基本正确（可 goto URL） | platforms/boss | 5 | **保留**；与 interpret 解耦归 Loop |
| `browse_boss_jobs` | 已实现 | **不正确**（假 Agent） | 废弃 | 0 / 5 | **废弃入口** |
| `TOOL_SPEC`+`dispatch_tool` 在 boss 文件 | 已实现 | 位置不当 | tools/registry | 4 | **迁移** |
| `analyze_job` LLM 主路径 | 已实现 | 正确方向 | JobProfile | 2 | **保留并冻结** |
| `extract_job_requirements` + 词表 | 已实现 | **不符合**生产语义 | 删除或 test-only | 2 | **替换/隔离** |
| `HeuristicJobAnalyzer` | 已实现 | 不当 Provider | 删除或 test-only | 2 | **替换为 MockJSON** |
| `interpret_job_actions` LLM 主路径 | 已实现 | 正确 | Semantic | — | **保留** |
| `infer_application_evidence` | 已实现 | 正确（确定性） | Rules | 4 | **保留** |
| `classify_action_semantically` | 遗留 | 不符合 | 删除 | 2 或 4 | **删除生产可达** |
| `HeuristicActionInterpreter` | 已禁用 | 遗留 | 删除 | 2 或 4 | **删除** |
| `open` 内嵌 analyze_actions | 耦合 | 部分正确 | Loop 编排 | 5 | **重构编排** |
| Agent Orchestrator / Loop / State | **不存在** | — | agent/ | 4 | **新增** |
| `analyze_candidate` / CandidateProfile | **不存在** | — | candidate/ | 1 | **新增** |
| `match_job` / MatchResult | **不存在** | — | matching/ | 3 | **新增** |
| JobProfile 正式契约名 | 隐式 | 部分 | job/ | 2 | **冻结命名+校验** |
| 黑名单/去重/配额/停止 | **不存在** | — | rules/ | 4 | **新增** |
| 猎聘/51 | **不存在** | — | platforms/ | 7–8 | **新增** |
| `browser/edge_*` | 已实现 | 正确（基建） | browser/ | 5 | **保留** |
| `HumanVerificationStopped` | 已实现 | 部分 | human_gate | 5 | **映射进 State** |
| live 测试要求人工 JD | workaround | **非产品** | 测试 only | 5 | **不得产品化** |
| `browser_poc/` | POC | 非正式 | archive | 0 | **标记 POC** |
| 根目录 joblist-*.json 等 | 调试残留 | 非架构 | 删除/archive | 0 | **清理或忽略** |
| README（过时叙述） | 过时 | 不正确 | docs | 0/5 | **按 Phase 更新** |

**原则**：已正确的 search/open/interpret/LLM Provider **禁止无意义大重构**。

---

## 11. 最终目录结构（目标）

```text
docs/
  AI_JOB_AGENT_BASELINE_v1.md          # 本基线

agent/
  state.py
  loop.py
  decide.py
  orchestrator.py
  report.py

candidate/
  schema.py
  analyze_candidate.py

job/
  common_schema.py
  profile_schema.py
  analyze_job.py                       # 从 tools/ 迁入或 re-export

matching/
  schema.py
  match_job.py

platforms/
  boss/
  liepin/                              # Phase 7
  job51/                               # Phase 8

tools/
  registry.py
  interpret_job_actions.py
  parse_user_goal.py

rules/
  dedupe.py
  blacklist.py
  application.py
  quota.py
  stop.py
  rank.py

llm/                                   # 保留
browser/                               # 保留
tests/
  unit/ semantic/ agent/ platforms/ live_optional/
  fixtures/ mock_jobs/ mock_resumes/
.cursor/rules/llm-semantic-redlines.mdc
```

| 现有 | 动作 |
|---|---|
| `tools/boss_job_search.py` | Phase 5 拆到 `platforms/boss/`；Registry 迁出 |
| `tools/analyze_job.py` | Phase 2 对齐 JobProfile + 去 heuristic 生产路径 |
| `tools/interpret_job_actions.py` | 保留主路径；删 heuristic |
| `browse_boss_jobs` | 废弃 |
| `browser_poc/` | 不进产品 |
| `llm/` `browser/` | 保留 |

---

## 12. MVP / V1 / V2

### MVP（Phase 0–5，建议含 6）

简历+目标 → Loop →（Mock 必过，BOSS 可配）→ Profile→Match→过滤→排序→报告。  
**无** keyword enrich；**无**自动投递；**无**人工开 JD 流程。

### MVP 不做

自动投递/沟通/申请、验证码绕过、自动登录破解、多用户、SaaS、UI、复杂 DB、云部署、开放式 Planner 控浏览器。

### V1

Phase 6–9：稳定 BOSS、猎聘、51、跨平台、更好停止策略。

### V2

Phase 10–11+：历史、应用辅助、更强规划与记忆。

---

## 13. Mock 测试路线

```text
MockLLMProvider          → 所有语义 Tool
mock_search_jobs         → CommonJob[]（无浏览器）
mock_open_job            → 补全 JD + raw_actions
fixtures/resumes/*
fixtures/jobs/*
```

**CI 必过**：Phase 4+ agent tests（无 BOSS）。  
**可选**：live BOSS。  
**BOSS 不可达 ≠ 阻塞 Phase 1–4。**

---

## 14. E2E 验收场景

### Mock E2E（必过）

输入：夹具简历 +「帮我找深圳高级产品经理，重点金融科技/支付/复杂业务系统，薪资符合目标」  

期望：parse_goal→CandidateProfile→search→dedupe→blacklist→open→interpret→JobProfile→MatchResult→过滤→必要时继续→Top N+理由。

### 场景矩阵

| # | 场景 | 期望 |
|---|---|---|
| 1 | 高匹配 | recommend yes，理由含直接经验 |
| 2 | 可迁移 | outcome=transferable，不得行业词误杀 |
| 3 | hard 缺 | exclude |
| 4 | 已申请 | 不进新推荐 |
| 5 | 黑名单 | 可不 open，exclude |

### 真实 BOSS（可选）

同 Loop，`data_source=boss`；失败 Human Gate；不改架构。

---

## 15. 禁止事项（全局）

1. 跨 Phase 偷做后续  
2. 擅自改架构（先报告）  
3. 测试 heuristic 进生产  
4. BOSS 异常倒逼改 Agent  
5. 人工开 JD 产品化  
6. 固定按钮文案/DOM 当语义  
7. 关键词代替 LLM；LLM 失败猜测  
8. `browse_boss_jobs` 当 Orchestrator  
9. keyword `enrich_priority`  
10. MVP 自动投递  
11. 验收只写「基本完成」——必须 **PASS/FAIL**  

每个 Phase 结束模板：

```text
当前 Phase：PASS | FAIL
下一 Phase：Phase X
进入条件：
1. …
2. …
若条件不满足：STOP
```

---

## 16. 完整 Phase Roadmap（含 Cursor Prompt）

---

### Phase 0：架构冻结与基线入库

| 项 | 内容 |
|---|---|
| **目标** | 基线写入仓库；标注 POC/废弃；禁止后续漂移 |
| **前置 Phase** | 无 |
| **前置条件** | 仓库可打开；本基线内容已确认 |
| **要解决的问题** | 无总纲、README 过时、browse 被当成 Agent |
| **允许修改** | `docs/`；`README.md` 增加「以基线 v1.0 为准」声明；可选给 `browse_boss_jobs` description 标 DEPRECATED |
| **不允许修改** | BOSS 浏览逻辑、LLM Provider、analyze/interpret 行为、大删 heuristic（留 Phase 2） |
| **新增** | `docs/AI_JOB_AGENT_BASELINE_v1.md` |
| **修改** | `README.md`（短声明） |
| **删除/迁移** | 不强制；可标明忽略抓包 JSON |
| **实现要求** | 文档落地；Gap 与 Phase 图在 docs 中 |
| **测试要求** | （1）`docs/AI_JOB_AGENT_BASELINE_v1.md` 存在；（2）含 Phase 0–11 与三大 Schema 标题；（3）README 含基线路径；（4）`git diff` 无业务逻辑误改 |
| **验收标准** | 上述 4 项全满足 → **PASS** |
| **完成后能力** | 执行顺序冻结 |
| **下一 Phase** | Phase 1 |
| **下一前置** | Phase 0 PASS |

#### Cursor 执行 Prompt — Phase 0

```text
现在执行 Phase 0：架构冻结与基线入库。

严格按照《AI Job Agent 总体实施基线 v1.0》执行。

执行前先只读检查仓库：确认尚无 agent/、candidate/、matching/；确认存在 tools/、llm/、browser/。

本 Phase 目标：
1. 确保 docs/AI_JOB_AGENT_BASELINE_v1.md 存在且为基线全文（若已存在且完整，不要无故删减 Phase/Schema）。
2. 在 README.md 顶部增加简短声明：实施以 docs/AI_JOB_AGENT_BASELINE_v1.md 为准；browse_boss_jobs 不是 Agent Orchestrator。
3. （可选）仅在 TOOL_SPEC 里 browse_boss_jobs 的 description 增加 “DEPRECATED: not the Agent Loop”。

不要修改：
- search_boss_jobs / open_boss_job 业务逻辑
- analyze_job / interpret_job_actions 行为
- llm/provider.py
- browser 启动与 CDP 逻辑
- 不要实现 Candidate/Match/Loop
- 不要为了 BOSS live 改架构

完成后必须自测：
1. Test-Path / 打开 docs/AI_JOB_AGENT_BASELINE_v1.md
2. 确认文中含「Phase 0」…「Phase 11」「CandidateProfile」「JobProfile」「MatchResult」
3. 确认 README 指向该文档
4. 确认未改 boss 搜索/打开核心逻辑

最终报告：
1. 修改/新增文件
2. 是否误改禁止范围（必须否）
3. 验收 PASS/FAIL
4. 是否满足 Phase 1 前置条件
5. 下一 Phase：Phase 1

若无法写入 docs：STOP 并报告，不要改用其它架构方案。
```

---

### Phase 1：CandidateProfile + analyze_candidate

| 项 | 内容 |
|---|---|
| **目标** | 冻结 CandidateProfile；实现 `analyze_candidate` |
| **前置 Phase** | Phase 0 |
| **前置条件** | 基线文档在库；`MockLLMProvider` 可运行；`get_llm_provider` 存在 |
| **允许修改** | 新增 `candidate/`；`tools/__init__` 导出；可把 SPEC 挂到现有 dispatch |
| **不允许修改** | BOSS browser、`analyze_job`、`interpret_job_actions`、Loop、Match |
| **新增** | `candidate/schema.py`, `candidate/analyze_candidate.py`, `tests/test_analyze_candidate.py`, fixtures |
| **实现要求** | LLM JSON→校验→CandidateProfile；fail-closed；evidence 强制；禁止 heuristic |
| **测试要求** | Mock：完整画像、缺简历、非法 JSON、unavailable、无 evidence 失败；命令见下 |
| **验收标准** | `pytest tests/test_analyze_candidate.py -q` 全绿 → **PASS** |
| **完成后能力** | 任意夹具简历 → CandidateProfile |
| **下一 Phase** | Phase 2 |
| **下一前置** | Phase 1 PASS + Schema 与基线 §7 一致（差异须声明） |

#### Cursor 执行 Prompt — Phase 1

```text
现在执行 Phase 1：CandidateProfile。

严格按照 docs/AI_JOB_AGENT_BASELINE_v1.md（总体实施基线 v1.0）§7。

执行前检查：
- Phase 0 文档是否存在
- llm/provider.py 与 tests/mock_llm.py 是否可用
- 确认不要创建 Agent Loop

不要修改：
- tools/boss_job_search.py 的搜索/打开逻辑
- tools/analyze_job.py
- tools/interpret_job_actions.py
- browser/*
- 不要实现 match_job / Agent Loop

请完成：
1. 按基线 §7 实现 candidate/schema.py（字段级校验函数）
2. 实现 analyze_candidate(resume, llm_provider=None) → CandidateProfile
3. 生产路径：get_llm_provider / 注入 Provider → complete_json → schema 校验
4. LLM 不可用/坏 JSON/缺 evidence → llm_unavailable / llm_error / analysis_failed（fail closed）
5. 禁止 keyword/heuristic 提取能力
6. 单测全部使用 MockLLMProvider；扩展 mock 如需要
7. 可把 analyze_candidate 加入 tools 导出；若改 dispatch_tool，仅增加路由，不重构 BOSS

完成后必须运行：
python -m pytest tests/test_analyze_candidate.py -q

最终报告：
1. 修改/新增文件
2. 测试结果
3. CandidateProfile 字段清单（与基线差异必须说明）
4. 验收 PASS/FAIL
5. 是否满足 Phase 2 前置
6. 下一 Phase：Phase 2

若前置不满足：STOP，不要自行改架构。
```

---

### Phase 2：JobProfile 冻结 + analyze_job 对齐

| 项 | 内容 |
|---|---|
| **目标** | 正式 JobProfile；强化 hard；生产去 heuristic |
| **前置 Phase** | Phase 1 |
| **前置条件** | Phase 1 PASS（`analyze_candidate` 与测试存在） |
| **允许修改** | `tools/analyze_job.py`（或迁 `job/`）、`job/profile_schema.py`、`tests/test_analyze_job.py` |
| **不允许修改** | BOSS 导航、interpret 主路径、candidate、Loop、match |
| **删除/隔离** | 生产不可达 HeuristicJobAnalyzer；测试改 MockLLM |
| **实现要求** | Prompt+校验落实 hard/bonus；输出=JobProfile |
| **测试要求** | bonus 不进 hard；明示学历进 hard；unavailable；回归 candidate 测 |
| **验收标准** | `pytest tests/test_analyze_job.py tests/test_analyze_candidate.py -q` 全绿 → **PASS** |
| **完成后能力** | 夹具 JD → JobProfile |
| **下一 Phase** | Phase 3 |
| **下一前置** | Phase 2 PASS |

#### Cursor 执行 Prompt — Phase 2

```text
现在执行 Phase 2：JobProfile。

严格按照 docs/AI_JOB_AGENT_BASELINE_v1.md §8。

执行前检查：Phase 1 PASS（analyze_candidate 与测试存在）。

不要修改：
- boss search/open/browse 行为（除必要的 import 路径）
- interpret_job_actions 语义主路径
- analyze_candidate
- 不要实现 match_job / Agent Loop

请完成：
1. 新增 job/profile_schema.py（或等价），字段对齐基线 JobProfile
2. 升级 analyze_job：输出校验为 JobProfile；强化 hard_requirements 严格定义
3. preferred/bonus/加分不得进入 hard
4. 生产路径禁止走 extract_job_requirements / HeuristicJobAnalyzer
5. 重写 tests/test_analyze_job.py：用 MockLLMProvider，覆盖 hard/bonus/forbidden/unavailable
6. 可保留 extract_job_requirements 仅当明确不在生产路径；优先删除生产可达引用

完成后必须运行：
python -m pytest tests/test_analyze_job.py tests/test_analyze_candidate.py -q

最终报告：修改文件、测试、Hard 规则说明、PASS/FAIL、Phase 3 前置、下一 Phase=Phase 3。
前置不足则 STOP。
```

---

### Phase 3：MatchResult + match_job

| 项 | 内容 |
|---|---|
| **目标** | `match_job`；四态 A/B/C/D |
| **前置 Phase** | Phase 2 |
| **前置条件** | Phase 1–2 PASS |
| **允许修改** | 新增 `matching/`；导出；Mock 测 |
| **不允许修改** | Browser、BOSS、Loop、放宽 hard、keyword match |
| **测试要求** | 直匹配 / transferable / hard 缺 / insufficient_evidence；回归 analyze 测 |
| **验收标准** | `pytest tests/test_match_job.py tests/test_analyze_*.py -q` 全绿 → **PASS** |
| **完成后能力** | 双 Profile → MatchResult |
| **下一 Phase** | Phase 4 |
| **下一前置** | Phase 3 PASS |

#### Cursor 执行 Prompt — Phase 3

```text
现在执行 Phase 3：MatchResult。

严格按基线 §9。前置：Phase 1–2 已 PASS。

不要修改：BOSS、browser、Agent Loop（不要创建完整 Loop）、不要加 enrich_priority 关键词逻辑。

请完成：
1. matching/schema.py — MatchResult 字段级校验
2. matching/match_job.py — LLM only；输入双 Profile；输出 MatchResult
3. 必须支持 capability outcome: direct | transferable | missing | insufficient_evidence
4. hard_requirements_met / gaps / recommendation / rationale / fits
5. fail closed；禁止关键词匹配代替 LLM
6. tests/test_match_job.py + fixtures

完成后必须运行：
python -m pytest tests/test_match_job.py tests/test_analyze_job.py tests/test_analyze_candidate.py -q

报告：文件、测试、四态示例、PASS/FAIL、下一 Phase=Phase 4。
```

---

### Phase 4：最小 Agent Loop + Mock 数据源（首次真闭环）

| 项 | 内容 |
|---|---|
| **目标** | State + Loop + Rules + Registry + Fake search/open；**无真实 BOSS** |
| **前置 Phase** | Phase 3 |
| **前置条件** | Phase 1–3 PASS |
| **允许修改** | `agent/`、`rules/`、`tools/registry.py`、`parse_user_goal`、mock platforms、相关测试 |
| **不允许修改** | 接真实 BOSS；自动投递；keyword enrich；heuristic 语义 |
| **enrich 政策** | 确定性过滤后按序 open 直至 `max_open_jd` |
| **测试要求** | 场景 1–5 全 Mock；无网络 |
| **验收标准** | `pytest tests/agent tests/test_match_job.py tests/test_analyze_candidate.py tests/test_analyze_job.py -q` 全绿 → **PASS** |
| **完成后能力** | 简历+目标 → 推荐报告（Mock） |
| **下一 Phase** | Phase 5 |
| **下一前置** | Phase 4 PASS |

#### Cursor 执行 Prompt — Phase 4

```text
现在执行 Phase 4：最小 Agent Loop / Orchestrator（Mock 数据源）。

严格按基线 Agent Loop / State / enrich 政策（禁止关键词 enrich_priority；配额内顺序打开）。

前置：Phase 1–3 PASS。

不要：
- 连接真实 BOSS / 修改 edge 登录绕过
- 实现自动投递
- 用 browse_boss_jobs 作为 Orchestrator
- heuristic 语义 fallback

请完成：
1. agent/state.py — AgentState / JobRecord / runtime status
2. agent/decide.py + loop.py + orchestrator.py — Observe→Decide→Act→Reduce
3. tools/registry.py — 统一 dispatch；注册语义 Tool + mock_search_jobs + mock_open_job
4. rules：dedupe, blacklist, application, quota, stop, rank
5. parse_user_goal（LLM+校验）或结构化 goal（若跳过 LLM 须在报告声明）
6. Fake 平台返回 CommonJob 与 JD/actions
7. Loop：goal→candidate→search→filter→open→interpret→analyze_job→match→filter→rank→report
8. tests/agent/* 覆盖：高匹配、transferable、hard 缺、已申请、黑名单
9. browse_boss_jobs 不得被 Loop 调用

完成后必须运行：
python -m pytest tests/agent tests/test_match_job.py tests/test_analyze_candidate.py tests/test_analyze_job.py -q

报告：架构草图、文件、测试、是否无 BOSS 闭环 PASS/FAIL、下一 Phase=Phase 5。
```

---

### Phase 5：BOSS 单平台完整 Agent 闭环

| 项 | 内容 |
|---|---|
| **目标** | 真实 BOSS Tool 接入同一 Loop；Human Gate |
| **前置 Phase** | Phase 4 |
| **前置条件** | Phase 4 Mock Loop PASS |
| **允许修改** | `platforms/boss/`；Loop 编排 interpret；human_gate；README |
| **不允许修改** | 人工开 JD 产品化；改 Match 语义；因 BOSS 不稳改 Loop；自动投递；猎聘/51 |
| **测试要求** | Fake/agent 必绿；live optional 且非 CI 门禁 |
| **验收标准** | `pytest tests/agent -q` 全绿；boss 失败映射 Human Gate 有单测或文档化用例 → **PASS** |
| **完成后能力** | 同 Loop 可切换 mock\|boss |
| **下一 Phase** | Phase 6 |
| **下一前置** | Phase 5 PASS |

#### Cursor 执行 Prompt — Phase 5

```text
现在执行 Phase 5：BOSS 单平台 Agent 闭环。

前置：Phase 4 Mock Loop PASS。

不要：
- 把“用户手动打开 JD”写成正式流程或 Human Gate 原因
- 为 live 失败引入 heuristic
- 自动投递/沟通
- 实现猎聘/51

请完成：
1. search_boss_jobs / open_boss_job 适配为平台 Tool，供 Registry 调用
2. Agent 可配置 data_source=boss|mock
3. open 负责导航+JD+可选 raw_actions；interpret/analyze/match 由 Loop 调用
4. HumanVerificationStopped / EdgeNotConnected / 访问阻断 → human_gate + NEEDS_HUMAN
5. 更新 README：基线、Agent 入口、废弃 browse 作为 Agent
6. live 测试若保留，必须标记 optional，不得作为 CI 必过

完成后必须运行：
python -m pytest tests/agent -q
（及新增平台单测；不要强依赖 live BOSS 才算 PASS）

报告：PASS/FAIL、Human Gate 列表、是否仍可用 Mock、下一 Phase=Phase 6。
```

---

### Phase 6：自主继续搜索与停止决策

| 项 | 内容 |
|---|---|
| **目标** | 推荐不足时追加 SearchPlan；明确停止条件 |
| **前置 Phase** | Phase 5 |
| **前置条件** | Phase 5 PASS |
| **允许修改** | `agent/decide.py`、search plans、可选 `plan_search`、agent 测试 |
| **不允许修改** | 开放式 ReAct 写浏览器；keyword 换词表当语义；自动投递；跨做 7/8 |
| **测试要求** | 首轮不足→第二 plan；触顶停止；Human Gate 不丢 State |
| **验收标准** | `pytest tests/agent -q`（含继续/停止用例）全绿 → **PASS** |
| **完成后能力** | 自主继续搜 / 停止 |
| **下一 Phase** | Phase 7（或声明后并行 8） |
| **下一前置** | Phase 6 PASS |

#### Cursor 执行 Prompt — Phase 6

```text
现在执行 Phase 6：Agent 自主搜索 / 继续 / 停止。

前置 Phase 5 PASS。严格基线停止条件与配额。

不要：自动投递；heuristic 语义；跨做猎聘/51。

请完成：
1. SearchPlan 队列与 exhausted 标记
2. decide：recommended < min_recommend 且配额未尽 → 继续 search 或继续 enrich
3. 可选 plan_search（LLM）生成下一关键词；必须 schema 校验
4. 测试：不足则继续；触顶停止；Human Gate 不丢 State

完成后必须运行：
python -m pytest tests/agent -q

报告 PASS/FAIL，下一 Phase=Phase 7。
```

---

### Phase 7：猎聘 Adapter

| 项 | 内容 |
|---|---|
| **目标** | `search_liepin_jobs` / `open_liepin_job` → CommonJob |
| **前置 Phase** | Phase 6（Loop 平台无关；最低 Phase 4+ 已存在） |
| **前置条件** | Phase 6 PASS（若跳过 6 须书面声明且 Phase 5 PASS） |
| **允许修改** | `platforms/liepin/`、registry 注册、`tests/platforms/` |
| **不允许修改** | Match/Candidate 语义；analyze_job 内写猎聘分支；自动投递 |
| **测试要求** | Adapter 单测（Fake page 或结构）；Registry 可调用；agent 回归不红 |
| **验收标准** | `pytest tests/platforms/test_liepin*.py tests/agent -q` 全绿 → **PASS** |
| **完成后能力** | 第二数据源可插拔 |
| **下一 Phase** | Phase 8 |
| **下一前置** | Phase 7 PASS |

#### Cursor 执行 Prompt — Phase 7

```text
现在执行 Phase 7：猎聘 Adapter。

前置：Agent Loop 平台无关已存在；Phase 6 PASS（或已声明的例外）。

只新增 platforms/liepin/* 与测试；禁止改 Match/Candidate 语义。
输出 CommonJob；注册 search_liepin_jobs / open_liepin_job。
无稳定环境可用 Fake+结构测 PASS。
不要自动投递。

完成后必须运行：
python -m pytest tests/platforms/test_liepin*.py tests/agent -q

报告后下一 Phase=Phase 8。
```

---

### Phase 8：51Job Adapter

| 项 | 内容 |
|---|---|
| **目标** | `search_51job_jobs` / `open_51job_job` → CommonJob |
| **前置 Phase** | Phase 6；建议 Phase 7 PASS（可与 7 并行，但合并前两者皆 PASS） |
| **前置条件** | 同 Phase 7 对 Loop 的要求 |
| **允许修改** | `platforms/job51/`、registry、测试 |
| **不允许修改** | 通用 Matching；自动投递 |
| **测试要求** | 同 Phase 7 模式 |
| **验收标准** | `pytest tests/platforms/test_job51*.py tests/agent -q` 全绿 → **PASS** |
| **完成后能力** | 第三数据源 |
| **下一 Phase** | Phase 9 |
| **下一前置** | Phase 7 与 Phase 8 均 PASS |

#### Cursor 执行 Prompt — Phase 8

```text
现在执行 Phase 8：51Job Adapter。
同 Phase 7 约束，目录 platforms/job51/。
注册 search_51job_jobs / open_51job_job。

完成后必须运行：
python -m pytest tests/platforms/test_job51*.py tests/agent -q

完成后下一 Phase=Phase 9（进入条件：Phase 7+8 均 PASS）。
```

---

### Phase 9：跨平台去重、统一排序与综合推荐

| 项 | 内容 |
|---|---|
| **目标** | 跨平台 dedupe；统一 rank；综合报告 |
| **前置 Phase** | Phase 7 + Phase 8 |
| **前置条件** | 至少两平台可产出带 `platform` 的 CommonJob（含 fake） |
| **允许修改** | `rules/dedupe.py`、`rules/rank.py`、report、测试 |
| **不允许修改** | 用关键词语义去重代替 Match；自动投递 |
| **测试要求** | 跨平台重复岗位 fixture；排序稳定 |
| **验收标准** | `pytest tests/rules tests/agent -q` 全绿 → **PASS** |
| **完成后能力** | 综合推荐 |
| **下一 Phase** | Phase 10 |
| **下一前置** | Phase 9 PASS |

#### Cursor 执行 Prompt — Phase 9

```text
现在执行 Phase 9：跨平台去重与综合推荐。
前置：Phase 7+8 PASS。
确定性 dedupe + rank；报告含 platform。
禁止改 LLM 匹配原则。

完成后必须运行：
python -m pytest tests/rules tests/agent -q

下一 Phase=Phase 10。
```

---

### Phase 10：历史状态与推荐记录

| 项 | 内容 |
|---|---|
| **目标** | 本地文件保存 run/推荐；可选已投 tracker |
| **前置 Phase** | Phase 9 |
| **前置条件** | Phase 9 PASS |
| **允许修改** | 本地 storage 模块、tracker 读入 rules、测试 |
| **不允许修改** | 云部署、多用户、复杂 DB（除非另行批准） |
| **测试要求** | 读写 round-trip；损坏文件 fail 可控 |
| **验收标准** | `pytest tests/test_history*.py -q` 全绿 → **PASS** |
| **完成后能力** | 可追溯历史 |
| **下一 Phase** | Phase 11 |
| **下一前置** | Phase 10 PASS |

#### Cursor 执行 Prompt — Phase 10

```text
现在执行 Phase 10：历史与推荐记录（本地文件即可）。
不要云部署/多用户。接入 tracker 供 rules 读。

完成后必须运行：
python -m pytest tests/test_history*.py -q

下一 Phase=Phase 11。
```

---

### Phase 11：应用辅助（仅架构预留）

| 项 | 内容 |
|---|---|
| **目标** | 预留「人工确认后投递辅助」接口；**不实现**自动点击 |
| **前置 Phase** | Phase 10 |
| **前置条件** | Phase 10 PASS |
| **允许修改** | 草案模块/文档、NotImplemented 接口 |
| **不允许修改** | 实现自动申请/沟通/验证码绕过 |
| **测试要求** | 调用自动投递 API 必须 raise NotImplementedError（或等价）；静态检查无 click apply |
| **验收标准** | 预留存在 + 自动化投递未实现 → **PASS** |
| **完成后能力** | 架构可扩展，MVP/V1 仍安全 |
| **下一 Phase** | 无（基线终点；新需求升版） |
| **下一前置** | — |

#### Cursor 执行 Prompt — Phase 11

```text
现在执行 Phase 11：应用辅助架构预留 only。
新增接口草案与文档，所有自动投递方法 NotImplemented。
禁止实现自动申请/沟通。

完成后必须自测：
1. 调用预留自动投递入口 → NotImplementedError（或测试断言）
2. grep/搜索确认无正式「点击申请/发送沟通」生产实现

报告：预留点清单、确认未实现自动化投递、基线 Phase 全部终点。
```

---

## 17. 实施总表（速查）

| Phase | 目标 | 核心模块 | 依赖 | 完成后能力 | 验收方式 |
|---|---|---|---|---|---|
| 0 | 基线入库 | docs | — | 顺序冻结 | 文档检查 |
| 1 | Candidate | candidate/ | 0 | 简历→Profile | pytest candidate |
| 2 | JobProfile | job/+analyze_job | 1 | JD→Profile | pytest analyze_job + candidate |
| 3 | Match | matching/ | 2 | 双 Profile→Match | pytest match + analyze_* |
| 4 | Loop+Mock | agent/+rules/ | 3 | **无 BOSS 真闭环** | pytest agent + 语义回归 |
| 5 | BOSS | platforms/boss | 4 | MVP 真源可配 | pytest agent；live optional |
| 6 | 自主搜停 | decide/plans | 5 | 继续/停止 | pytest agent 策略 |
| 7 | 猎聘 | platforms/liepin | 6 | 第二源 | pytest platforms + agent |
| 8 | 51 | platforms/job51 | 6 | 第三源 | pytest platforms + agent |
| 9 | 跨平台 | rules/rank | 7+8 | 综合推荐 | pytest rules + agent |
| 10 | 历史 | 本地存储 | 9 | 可追溯 | pytest history |
| 11 | 投递预留 | 接口文档 | 10 | 不实现自动投 | NotImplemented 检查 |

**第一次真正 Agent 闭环：Phase 4（Fake）。**  
**第一次真实招聘站闭环：Phase 5（BOSS）。**

---

## 18. 最终自检

| 能力 | 负责 Phase | 完成后是否具备 |
|---|---|---|
| 理解目标 | 4（parse_user_goal） | 是 |
| 分析候选人 | 1 | 是 |
| 搜索岗位 | 4 mock / 5 boss | 是 |
| 获取 JD | 4/5 open | 是 |
| 分析 JD | 2 | 是 |
| 申请关系 | interpret + rules（4/5） | 是 |
| 匹配+可迁移 | 3 | 是 |
| 过滤黑名单/已投/hard | 4 | 是 |
| 继续搜/停止 | 4 最小停止；6 增强 | 是 |
| 排序+推荐 | 4 | 是 |
| 多平台 | 7–9 | V1 |
| 无人工开 JD | 全程 | 是 |
| 无自动投递 MVP | 11 仅预留 | 是 |
| 每 Phase 可自测 | 自测矩阵 | 是 |
| Phase 依赖可强制 | Dependency Map + STOP | 是 |

**结论**：按 Phase 0→5（建议+6）执行后，用户仅凭「简历+求职目标」可在 **Mock 必过、BOSS 可配** 下获得真正的 Resume-driven Agent。  

- 若停在 Phase 3：有语义无自主循环 → **缺 Phase 4**。  
- 若只有 BOSS Tool 无 1–4 → **不是 Agent**。

---

## 19. Human Gate（产品）

必须暂停：`login_required` / `captcha` / `access_blocked` / `browser_unavailable` / `site_blocked_client`。  

**排除**：「请用户手动打开 JD」。Live 测试人工开 JD = **测试 workaround only**。

---

## 20. 设计冲突优先级

1. 最终 Agent 产品目标  
2. Agent Loop / State 完整性  
3. LLM ↔ Deterministic 边界  
4. CandidateProfile / JobProfile / MatchResult 稳定  
5. Tool 可复用  
6. 平台解耦  
7. 可测试性（无 BOSS 可测 Brain）  
8. 当前 POC 兼容  
9. 开发便利  

**POC 让路于架构。**

---

**下一步**：从 **Phase 0** 开始，将对应「Cursor 执行 Prompt」复制给 Cursor。若本文件已由本次写入完成 Phase 0 的文档部分，执行 Phase 0 时只需补 README 声明并做文档自测，然后报告 PASS 再进入 Phase 1。
