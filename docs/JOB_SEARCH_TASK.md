# JobSearchTask（D.2）

长期求职任务编排。**不是** AgentState，也**不是** Conversation / JobContext。

## 四个对象

| 对象 | 生命周期 | 职责 |
|---|---|---|
| JobSearchTask | 跨多次 HTTP Run | 搜索策略、探索进度、Human Gate、用户决策、停止原因 |
| AgentState | 单次 Loop | Observe → Decide → Act → Reduce |
| Conversation | 跨轮对话 | 自然语言上下文、JobContext、语义引用 |
| JobContext | 会话内职位工作记忆 | 职位事实、JobProfile、MatchResult、application evidence |

JobSearchTask **只引用** JobContext / JobProfile / MatchResult，不复制这些模型。

不要把 `RUNNING` / `WAITING_USER` / `EXECUTING` / `STOPPED` / `COMPLETED` 写入 ConversationStore。

HTTP Run 结束 ≠ JobSearchTask 结束。

## 任务状态

```text
RUNNING → 自主探索
WAITING_USER → 暂停等用户决定（不是完成）
EXECUTING → 执行用户授权动作
STOPPED → 用户要求停止
COMPLETED → 探索边界 / 配额耗尽，没有必要继续
```

## Agent Decide

`decide_next_action` 用已结构化的 UserGoal / CandidateContext / JobProfile / MatchResult 决定下一步：

- `REJECT_JOB`
- `SURFACE_TO_USER`
- `CONTINUE_EXPLORING`
- `COMPLETE_TASK`

不是 `score >= threshold → Human Gate`。

Search 只提供候选。Analyze / Match 职责不变。
