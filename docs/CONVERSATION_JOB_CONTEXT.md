# Conversation JobContext（D.1）

跨轮职位工作记忆。**不是** CandidateProfile，也**不是** JobSearchTask 生命周期。

## 边界

| 对象 | 职责 |
|---|---|
| Conversation / JobContext | 「我刚才讨论的是哪个职位？」 |
| JobSearchTask（D.2） | 任务已处理/跳过哪些职位、正在等谁决策、下一步做什么 |

JobSearchTask（D.2）见 `docs/JOB_SEARCH_TASK.md`。Conversation 不是 JobSearchTask 的替代品。

Job-specific 判断（例如某个职位的医疗支付匹配）不得写入长期 CandidateProfile。

## JobContext 字段

复用已有 `JobRecord` / `JobProfile` / `MatchResult` / `application_evidence`：

- `context_id` / `job_key` / `platform` / `job_id` / `job_url` / `title` / `company`
- `job_profile`（完整 JobProfile）
- `job_listing`（CommonJob，含 JD 原文若系统已保存）
- `match_result`（完整 MatchResult）
- `application_evidence` / `interpret_result`
- `source_run_id` / `source_round` / `listed_order` / `round_ordinal`
- `recommendation_context`（推荐卡片字段，附在完整 MatchResult 之外）
- `stage`

## ConversationStore 接口（D.2 可直接复用）

```text
ensure(conversation_id) -> Conversation
session_context(conversation_id) -> dict | None
remember(conversation_id, state, run_id=None)
get_job_contexts(conversation_id) -> list[dict]
get_job_context(conversation_id, context_id) -> dict | None
save_job_context(conversation_id, context) -> dict | None
```

`session_context` 含完整 `job_contexts`（给 Resolver）以及无 `job_id` 的 `job_context_catalog`（给 Understanding）。

## ConversationReference

Understanding **只**输出语义引用，不输出 `job_id`：

```text
conversation_reference:
  type: conversation_reference
  target_kind: job
  reference_text: ...
  resolution_hint:
    recency / ordinal / round_offset / recommended_only / highest_match / semantic_filters
task_kind: new_job_search | follow_up_job | update_goal | job_reference
```

## Reference Resolver

`resolve_conversation_reference(reference, job_contexts) -> ReferenceResolution`

- 只用结构化 hint 过滤 Conversation 中的 JobContext
- 唯一命中 → `resolved`
- 0 个或多个命中 → `reference_unresolved`（不猜、不默认最后一个）

## Follow-up 路径

Observe → Decide → Act → Reduce 不变。

Follow-up：Understanding → Resolver → hydrate JobContext → `ENRICHING_JOBS` 上的 `match_job`（原 JobProfile + 当前 CandidateContext）。不 Search / Open / Analyze Job。

新的 `task_kind=new_job_search` 仍走 Search。
