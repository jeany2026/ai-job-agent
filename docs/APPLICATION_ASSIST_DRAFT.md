# 应用辅助接口草案（Phase 11）

> 基线 v1.0 终点：预留「人工确认后投递辅助」，**不实现**自动点击 / 自动沟通 / 验证码绕过。  
> 对应：[AI_JOB_AGENT_BASELINE_v1.md](AI_JOB_AGENT_BASELINE_v1.md) Phase 11。

## 产品边界

当前 Agent 停在推荐报告。本草案只增加可扩展接口，不改变 Loop。

```text
简历 + 目标
  → parse / analyze_candidate / search / open / interpret / analyze_job / match
  → 过滤 / 停止 / 排序 / 报告
  → [预留] prepare_apply_assist（草案，需人工确认）
  → [未实现] execute_apply_assist / auto_apply / send_communication
```

- **允许（草案）**：把 Loop 已产出的结构化字段（`job_key`、`semantic_intent`、`recommendation`、`application_evidence`）组成确认单。
- **禁止（本 Phase 与 MVP）**：点击申请、发送沟通、自动登录破解、验证码/滑块绕过。
- **人工确认**：任何未来投递辅助的前置条件。确认后本版本仍 `NotImplementedError`。
- **不进 Agent Loop**：`build_registry` 不注册下列 Tool；`decide` / `run_loop` 不得调用。

语义仍只来自现有 LLM Tool（`interpret_job_actions` / `match_job`）。本模块不猜按钮文案、JD、简历含义。

## 预留点清单

| 预留点 | 位置 | 本 Phase 行为 |
|---|---|---|
| `prepare_apply_assist` | `apply/assist.py` | 返回确认草案；不执行 |
| `human_confirmation` | `apply/assist.py` | 结构化确认载荷 |
| `execute_apply_assist` | `apply/assist.py` | `NotImplementedError`（统一自动投递入口） |
| `auto_apply` | `apply/assist.py` | `NotImplementedError` |
| `click_apply` | `apply/assist.py` | `NotImplementedError` |
| `auto_contact` | `apply/assist.py` | `NotImplementedError` |
| `send_communication` | `apply/assist.py` | `NotImplementedError` |
| `apply_on_platform` | `apply/platforms.py` | `NotImplementedError`（mock/boss/liepin/job51） |
| `contact_on_platform` | `apply/platforms.py` | `NotImplementedError` |
| `auto_apply` Tool spec | `AUTO_APPLY_SPEC` | 草案；**不**注册到 Registry |
| `send_communication` Tool spec | `SEND_COMMUNICATION_SPEC` | 草案；**不**注册到 Registry |

平台 `search_*` / `open_*` 只导航与读 JD / `raw_actions`，不申请、不沟通。

## 确认草案字段

`prepare_apply_assist` 输出（`schema_version=1`）：

| 字段 | 含义 |
|---|---|
| `assist_status` | `awaiting_human_confirmation` |
| `confirmation_required` | 恒为 `true` |
| `auto_execution` | 恒为 `not_implemented` |
| `job_key` / `platform` / `job_url` | 透传已有标识 |
| `proposed_actions` | 仅复制 `interpret_result.actions[].semantic_intent` 中的 `apply_resume` / `start_contact` / `continue_contact` |
| `application_evidence` | 透传 `inferred_context.application_evidence` |
| `match_recommendation` | 透传 `match_result.recommendation` |

非法或缺失 intent **不**用关键词/按钮文案补全。

## 明确不做

1. Playwright `page.click` / 定位「立即沟通」「投递简历」并点击  
2. 自动发送打招呼 / 聊天消息  
3. CAPTCHA / 滑块 / 反爬绕过（Human Gate 仍只暂停）  
4. 把投递编进 `run_agent` / `decide`  
5. 用确认标志跳过 `NotImplementedError`

后续若升版实现辅助，须另开版本，并仍以人工确认为硬前置。
