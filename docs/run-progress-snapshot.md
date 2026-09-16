# Run Progress Snapshot 开发文档

状态：第一阶段已实现（最小闭环）；WebUI 展示仍待后续阶段

## 1. 背景与目标

RuiClaw 当前已经支持 Session Summary、Context Compaction、Checkpoint 和 Run
Ledger。上下文压缩能够减少发送给模型的历史消息，但如果早期工具调用被裁掉，Agent
可能无法判断哪些动作已经完成，从而重复读取、重复修改文件，或者在恢复时偏离原任务。

本功能增加一个面向单次 Run 的临时执行状态：`Run Progress Snapshot`。它在上下文
即将超出预算或 Run 被中断前生成，在压缩后重新注入模型上下文，帮助 Agent 从最近的
可靠执行边界继续工作。

核心目标：

1. 在压缩前保留当前 Run 的客观执行进度；
2. 防止工具调用和文件修改因历史裁剪而重复执行；
3. 支持 Run 中断后的恢复，而不依赖 `/goal` 或长期 Memory；
4. 不改变现有 Session 原始历史、Run Ledger 和 Checkpoint 的审计语义。

## 2. 与现有机制的边界

| 机制 | 生命周期 | 主要职责 |
|---|---|---|
| Session Summary | 当前会话 | 压缩旧对话，减少历史 Token |
| Run Progress Snapshot | 单次 Run | 记录当前 Run 做到哪一步 |
| Checkpoint | 单次 Run | 保存可恢复的执行边界和工具状态 |
| Run Ledger | 长期审计 | 记录发生过的模型、工具和阶段事件 |
| `/goal` | 跨 Run | 用户主动声明的长期目标 |
| Memory / Dream | 跨会话 | 沉淀可复用的长期事实和经验 |

Snapshot 不能写入长期 Memory，也不能替代 Ledger。Ledger 是事实来源，Snapshot 是
面向恢复的结构化投影。

## 3. 设计原则

- 第一版只记录客观状态，不强行从 Ledger 推断完整任务计划；
- 优先使用确定性规则生成，LLM 总结只作为可选增强；
- Snapshot 必须绑定 `run_id`，不能跨 Run 复用；
- 已完成的工具调用、工具参数和副作用状态不能因为压缩而丢失；
- 持久化快照只保存必要摘要，不保存完整 Prompt、密钥或敏感工具参数；
- Snapshot 的大小必须有上限，不能因为保留进度又造成新的上下文膨胀；
- 模型看到的是 Snapshot 副本，原始消息和 Ledger 保持不可变审计记录。

## 4. 数据模型

第一版建议使用版本化 JSON：

```json
{
  "schema_version": 1,
  "run_id": "run_...",
  "session_id": "webui:project",
  "status": "running",
  "completed_actions": [
    {
      "tool": "list_dir",
      "target": "/project",
      "result": "success"
    },
    {
      "tool": "read_file",
      "target": "pyproject.toml",
      "result": "success"
    }
  ],
  "failed_actions": [
    {
      "tool": "pytest",
      "error_kind": "import_error",
      "error_summary": "ModuleNotFoundError",
      "retryable": true
    }
  ],
  "pending_tool_calls": [],
  "last_checkpoint_id": "checkpoint_...",
  "artifacts": ["src/app.py", "tests/test_app.py"],
  "resume_hint": "修复导入错误后重新运行 pytest",
  "updated_at": "2026-09-16T00:00:00Z"
}
```

字段约束：

- `completed_actions`、`failed_actions` 只允许来自已落盘的工具事件或阶段事件；
- `pending_tool_calls` 优先来自 Runtime Checkpoint，不由模型猜测；
- `artifacts` 只记录已观测到的工作区路径，并做路径归一化和数量上限；
- `resume_hint` 第一版由规则生成，例如“重试失败的 pytest”，不得成为唯一恢复依据；
- `status` 至少包含 `running`、`interrupted`、`completed`、`failed`；
- 所有快照必须记录 `schema_version`，以便后续迁移。

## 5. 生命周期与触发点

```text
tool_started / tool_completed / tool_failed
                    ↓
          更新内存中的 Progress State
                    ↓
checkpoint_created / run_interrupted
                    ↓
             持久化 Snapshot
                    ↓
Context 即将超出预算
                    ↓
       生成并冻结当前 Snapshot
                    ↓
             压缩旧消息
                    ↓
   注入 Snapshot + 摘要 + 最近消息
```

建议接入点：

1. `AgentRunner` 工具执行完成或失败后更新内存状态；
2. `Run Ledger` 写入 `tool_completed`、`tool_failed`、`checkpoint_created`、
   `run_interrupted` 后，由同一 `run_id` 生成对应事件；
3. `ContextGovernor` 判断输入预算不足时，先冻结 Snapshot，再执行历史裁剪或摘要；
4. `AgentLoop` 创建下一次模型请求时，把 Snapshot 作为 Runtime Context 注入；
5. Run 正常结束、失败或恢复完成后，写入最终状态并清理运行时缓存，但保留审计文件。

## 6. 与上下文压缩的组合方式

压缩后的模型上下文建议按以下顺序组织：

```text
System Prompt
Runtime Context
  - Run Progress Snapshot
  - Session Summary
  - Workspace / Memory / Skills metadata
最近未压缩消息
当前用户消息
```

Snapshot 应放在 Session Summary 之后、最近消息之前，并使用明确的不可混淆标记：

```text
## Current Run Progress

This is a runtime recovery snapshot for the current run.
Do not repeat completed actions unless new evidence invalidates them.
Use the latest checkpoint and tool result as the source of truth.
```

注入内容必须携带 Runtime Context metadata，例如：

```json
{
  "ruiclaw_context_source": "run_progress",
  "run_id": "run_...",
  "checkpoint_id": "checkpoint_..."
}
```

这样 Context Ledger 可以单独统计 Snapshot 的 Token 占用，不把它误计为普通历史消息。

## 7. 恢复和幂等语义

恢复时按以下优先级判断状态：

1. 未完成的 Provider/Tool Checkpoint；
2. Run Ledger 中最后一个有效工具结果；
3. Run Progress Snapshot；
4. Session Summary 和最近消息。

Snapshot 不能直接让 Agent 重新执行一个已经成功的副作用工具。对于写文件、发送消息、
支付或其他副作用操作，恢复前必须检查原始 tool result、幂等键或工具自己的状态查询。

## 8. 持久化位置和隔离

运行中的 Snapshot 放在 Run Runtime State 中，推荐路径：

```text
.ruiclaw/runs/<run_id>/progress.json
```

最终 Snapshot 的摘要和哈希可以写入 Run Ledger manifest，但不应把完整快照复制到长期
Memory。临时 Run、Dream、Evolution Review 和 Benchmark Run 必须按现有 `run_kind`
隔离，避免进度快照被自进化候选提取器误认为用户任务经验。

## 9. 第一阶段实现范围

第一阶段只实现最小闭环：

1. 增加 `RunProgressSnapshot` 和 `RunProgressTracker` 数据结构；
2. 从工具开始、完成、失败和 Checkpoint 事件生成客观状态；
3. 在 ContextGovernor 触发压缩前生成 Snapshot；
4. 将 Snapshot 注入压缩后的 Runtime Context；
5. 在 Run Ledger 中记录 Snapshot 的创建、更新时间、哈希和 Token 大小；
6. 支持 Run 中断后从 Snapshot + Checkpoint 恢复；
7. 增加大小上限、敏感字段过滤和旧 schema 兼容；
8. 暂不实现 LLM 自动规划、跨 Run 合并和长期 Memory 写入。

后续阶段可以增加：

- 从用户目标或 `/goal` 读取显式计划；
- 基于模型生成 `resume_hint`，但必须保留确定性事实字段；
- 任务步骤依赖图和完成条件；
- WebUI Run Inspector 中展示 Progress Timeline；
- Snapshot 与 Recovery Bench、上下文漂移评测联动。

## 10. 测试与验收标准

### 单元测试

- 工具成功、失败、超时和取消事件能正确更新状态；
- 同一个 `tool_call_id` 重复事件不会产生重复 action；
- Snapshot 超过大小上限时按确定性规则裁剪；
- 敏感参数不会进入快照；
- 不同 `run_id` 的状态完全隔离；
- 旧版本或损坏 Snapshot 能安全忽略并回退到 Checkpoint/Session。

### 集成测试

- 上下文超预算前一定先创建 Snapshot；
- 压缩后模型能看到已完成、失败和待处理动作；
- 恢复流程不会重复成功的副作用工具；
- Snapshot 不改变原始 Session History 和 Ledger 完整性；
- 取消、重启、Provider 错误和工具超时后可以继续恢复。

### 评测指标

- 重复工具调用率；
- 上下文压缩后的任务继续完成率；
- 恢复后路径偏离率；
- Snapshot Token 占比；
- 恢复成功率和恢复耗时；
- 副作用工具重复执行次数；
- Run Ledger 与 Snapshot 的关联完整率。

## 11. 预期收益

该设计不改变现有 Context Compaction 的核心实现，而是在压缩前增加一层结构化执行状态：

```text
Session Summary 解决“历史太长”
Run Progress Snapshot 解决“当前做到哪一步”
Checkpoint 解决“从哪里继续执行”
Run Ledger 解决“事后如何证明发生过什么”
```

最终目标是让 RuiClaw 在长程任务中即使发生上下文压缩、进程重启或 Run 中断，也能基于
客观执行证据继续任务，而不是依赖模型重新猜测历史。

## 12. 第一阶段实现记录

当前实现位于：

- `ruiclaw/agent/run_progress.py`：`RunProgressSnapshot` 和
  `RunProgressTracker`；
- `ruiclaw/agent/runner.py`：工具结果、Checkpoint 和 Run 结果接入；
- `ruiclaw/agent/context_governance.py`：历史压缩后注入 `run_progress` Runtime Context；
- `ruiclaw/agent/loop.py`：将当前 `run_id` 传入 Runner。

第一阶段已经支持：

1. 工具成功、失败和待执行调用的确定性记录；
2. 完成动作、失败动作、恢复提示和最近 Checkpoint 的快照渲染；
3. Checkpoint payload 携带 `progress_snapshot`；
4. 历史上下文压缩后重新注入当前 Run 快照；
5. 快照字段、动作和 artifact 数量上限；
6. 运行结果返回最终 `progress_snapshot`。

已验证：Run Progress 单元测试、原子持久化/重载、工具执行回归、Runner 集成、Compact
命令共 `48 passed`。下一阶段再增加 Run Inspector 展示和更严格的副作用工具恢复检查。
