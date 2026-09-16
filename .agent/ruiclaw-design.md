# RuiClaw Run Correlation and Audit Design

## 1. 文档目的

本文定义 RuiClaw 第一阶段的运行关联与审计模型，重点回答四个问题：

1. 如何给 nanobot 已有的一次执行分配稳定身份？
2. 如何关联执行中的模型、工具、恢复和投递事件？
3. 如何从事件派生阶段耗时、Token、错误和副作用指标？
4. 如何关联中断前后的两次执行，而不改变现有恢复语义？

本文不是新的 Agent Loop 规格。Memory Backend、更深入的成本优化和 Evolver 会使用这里的运行数据，但普通请求不需要额外创建 Task 状态机。

## 2. 设计来源

RuiClaw 的模型不是照搬 Pico，而是组合两类已有能力：

- nanobot 提供异步消息总线、会话级串行化、跨会话并发、原生 Tool Call、工具安全并发、持久会话和未完成 Tool Call 恢复。
- Pico 提供显式 `TaskState`、每次执行的独立证据包、语义 checkpoint、关键文件新鲜度检查和可复现评测思路。

Pico 当前为一次 `ask()` 创建 `task_id`、`run_id` 和独立运行工件。RuiClaw 只参考其中“稳定 Run 身份与证据包”的思想；消息调度、并发和精确恢复继续使用 nanobot。只有显式 sustained goal 才需要额外的 Task 概念。

## 3. Runtime 是什么

这里的 Runtime 指 nanobot 已有的 Agent 执行链路，不新增第二套 Runtime。

RuiClaw 增加的是旁路的运行关联与审计层，负责：

- 为每次实际执行生成稳定 `run_id`；
- 将现有 `session_key`、`turn_id` 和可选长期目标关联到 Run；
- 让模型、工具、checkpoint 和 delivery 事件携带同一 Run 上下文；
- 将事件追加写入可离线聚合的运行账本；
- 在恢复时创建子 Run，并通过 `parent_run_id` 关联中断前后的执行；
- 从事件派生用量、耗时、错误和阶段指标。

nanobot 的 `AgentLoop` 和 `AgentRunner` 仍负责实际收消息、请求模型、执行工具和判断恢复安全性。RuiClaw 首选通过现有 Hook、EventSink、Session metadata 和 recovery 边界接入，不重写核心循环，不为了统计改变执行顺序。

## 4. 核心模型

```text
普通请求：Session → Turn → Run → Events
长期目标：Session → Task → Run #1 / Run #2 → Events

Events     ── model/tool/checkpoint/delivery 等已有阶段的结构化记录
Step View  ── 从 Events 派生，不是第一阶段的新执行状态机
Checkpoint ── 继续使用 nanobot 的恢复状态，并关联到 Run
```

### 4.1 Session

Session 是用户与 Agent 的会话容器，沿用 nanobot 现有 session key 和历史记录。

Session 负责保存：

- 消息历史；
- 渠道路由信息；
- 当前模型与工作区；
- 零个或一个显式 sustained Task；
- 当前 Run 的临时关联信息。

Session 不代表任务完成状态。一个 Session 可以先后包含多个 Task。

### 4.2 Task

Task 只表示显式的长期目标，不是每条普通消息都必须创建的对象。

例子：

- “分析这个测试为什么失败”直接创建 Turn 和 Run，不额外创建 Task；
- “修复测试并持续工作直到通过”可以显式创建 sustained Task；
- sustained Task 可以跨多个 Turn、Run 和 gateway 重启存在。

第一版规则：

- 普通用户请求沿用 nanobot 的 Turn，只创建 Run；
- 用户显式创建长期目标时，才创建 sustained Task；
- active Task 执行期间到达的补充消息默认附着到当前 Task；
- 明确改变目标的消息结束或取消旧 Task，再创建新 Task；
- 系统内部 continuation 不创建新 Task。

可选 sustained Task 的最小字段：

```text
task_id
session_key
objective
status               # active | completed | blocked | cancelled
created_at
updated_at
active_run_id
result_summary
```

Task 状态机：

```text
active ───────→ completed
  │
  ├───────────→ blocked ─────→ active
  │
  └───────────→ cancelled
```

约束：

- Task 只有在用户目标真实完成时才进入 `completed`；
- 一次 Run 成功不必然代表 sustained Task 完成；
- `blocked` 表示需要用户输入或外部状态变化，不表示普通执行失败；
- terminal Task 不得被后台 continuation 静默恢复。

### 4.3 Run

Run 是一次实际执行的统一审计范围。普通 Turn 可以直接拥有 Run；如果存在 sustained Task，Run 也可以关联该 Task。初次执行与系统重启后的恢复分别使用不同 Run。

这样可以区分：

- Task 最终是否完成；
- 为完成它实际运行了几次；
- 哪一次 Run 失败或被中断；
- 总成本和成功 Run 成本；
- 恢复是否引入重复副作用。

Run 最小字段：

```text
run_id
task_id              # 可为空，仅 sustained Task 使用
parent_run_id        # 初次运行为空，恢复或重试时指向上一次 Run
trigger              # user | followup | resume | retry | continuation | automation
status               # queued | running | interrupted | succeeded | failed | cancelled
started_at
finished_at
stop_reason
checkpoint_id
```

Run 状态机：

```text
queued → running ─────────────→ succeeded
            │
            ├───────────────────────→ failed
            ├───────────────────────→ cancelled
            └───────────────────────→ interrupted
```

恢复规则：

- 恢复不复活旧 Run，而是从旧 Run 的 checkpoint 创建子 Run；
- 旧 Run 的历史状态不可覆盖，只允许补充完成时间和 stop reason；
- `retry` 表示同一输入重新尝试，`resume` 表示从已确认的 checkpoint 继续；
- 一个 Session 的并发与串行规则完全沿用 nanobot；
- 同一 Session 的写操作继续服从 nanobot 的会话级串行边界。

### 4.4 Step

Step 是从 Run 事件中派生的分析视图，用于计算耗时、错误、Token、工具副作用和事件顺序。第一阶段不让 Step 参与调度，也不单独维护 Step 状态机。

第一版 Step 类型：

```text
model_call
tool_call
checkpoint_restore
context_compaction
delivery
```

Step View 的建议字段：

```text
step_id              # 默认使用对应 event_id
run_id
sequence
type
status               # started | succeeded | failed | cancelled | unknown
started_at
finished_at
input_ref
output_ref
usage
error
```

并发工具规则：

- 每个工具调用有独立 Step；
- 同一批并行只读工具共享 `batch_id`；
- `sequence` 表示逻辑提交顺序，不依赖异步完成顺序；
- 写工具、独占工具和外部副作用工具不得与其他工具混入同一并发批次；
- 大输入和大输出保存为 artifact，Step 中只保留引用、摘要和哈希。

Step 不是模型的“思考步骤”，也不保存隐藏推理文本。

### 4.5 Turn

Turn 是聊天交互概念，不属于 Task 的执行层级。

一条用户消息和对应回复可以形成一个 Turn，但存在以下情况：

- 一个 Task 跨多个 Turn；
- 一个 Turn 触发多个内部 Run；
- automation Run 没有用户 Turn；
- follow-up Turn 可以注入正在运行的 Run。

因此 Turn 只作为关联信息存在：

```text
turn_id
session_key
task_id
run_id              # 尚未开始执行时可以为空
inbound_message_id
outbound_message_ids
```

### 4.6 Checkpoint

Checkpoint 不是第五层父子模型，而是某个时间点的持久快照。它必须回答两个不同问题：

1. 机器能否安全地继续执行？
2. 模型恢复后是否知道任务做到哪里？

第一阶段只关联 nanobot 已有的 Runtime Checkpoint，不改变其恢复判断。后续如果长期任务确实需要更紧凑的语义进度，再增加独立 Semantic Checkpoint。

#### Runtime Checkpoint

Runtime Checkpoint 保存精确执行状态，主要复用 nanobot recovery：

- provider conversation state；
- assistant tool calls；
- 已完成的 tool results；
- 尚未执行的 tool calls；
- pending follow-ups；
- checkpoint phase 和 schema version。

它的核心目标是避免恢复时重复外部副作用。

#### Semantic Checkpoint（后续候选）

Semantic Checkpoint 可保存任务语义进度，参考 Pico，但不替代 Runtime Checkpoint：

```text
checkpoint_id
task_id
run_id
created_at
objective
completed_items
current_blocker
next_step
key_files[]          # path + content hash
runtime_identity     # workspace/model/tool/config fingerprint
evidence_refs[]
summary
```

它的核心目标是让新的模型调用获得简洁、可信的继续工作上下文。

Checkpoint 恢复判定：

```text
full-valid           精确状态完整，关键文件与运行身份兼容
partial-stale        部分关键文件变化，只能保留未过期证据
runtime-mismatch     模型、工具、工作区或配置发生不兼容变化
unsafe-to-resume     工具结果不完整，可能重复不可逆副作用
```

约束：

- `unsafe-to-resume` 必须停止并请求人工处理；
- `partial-stale` 不得把过期文件摘要继续注入模型；
- checkpoint 必须版本化并经过运行时结构校验；
- checkpoint 只能引用已持久化的 Step 和 artifact；
- 写入顺序必须保证 checkpoint 不会宣称一个尚未落盘的工具结果已经完成。

## 5. 一次普通任务如何运行

```text
1. MessageBus 接收用户消息
2. Session 路由和会话锁确定执行归属
3. RuiClaw 为本次执行生成 `run_id`
4. 如果存在 sustained Task，只记录可选 `task_id` 关联
5. Context Builder、Provider、Tool Executor 和 delivery 继续按 nanobot 原流程工作
6. EventSink 与 Hook 为各阶段事件补充相同的 Run 上下文
7. nanobot 创建或更新恢复 checkpoint 时，RuiClaw 记录其引用
8. Run 结束后，从事件聚合 usage、耗时、错误和阶段指标
9. 生成本次 Run 的 report 和证据索引
```

## 6. 中断和恢复示例

用户要求修改文件并运行测试：

```text
Run R1: 修复登录测试
    ├── S1 model_call: 请求读取相关文件
    ├── S2 tool_call: read_file，成功
    ├── S3 model_call: 请求 patch_file
    ├── S4 tool_call: patch_file，成功并已落盘
    └── gateway 在运行测试前退出，Run = suspended
```

恢复时不能重新执行 S4。系统校验 Runtime Checkpoint、文件哈希和已完成工具结果后创建新 Run：

```text
Run R1: interrupted
└── Run R2: parent_run_id=R1, trigger=resume
    ├── S1 checkpoint_restore
    ├── S2 model_call: 从“修改已完成，下一步运行测试”继续
    └── S3 tool_call: 执行测试
```

## 7. 持久化布局

第一版建议保留 session 作为在线状态，将不可变运行证据按 Run 保存：

```text
.ruiclaw/
└── runs/<run_id>/
    ├── manifest.json
    ├── events.jsonl
    ├── artifacts/
    └── report.json
```

原则：

- manifest 保存 Run 身份、父 Run、Turn 和可选 Task 关联；
- events 采用追加记录，是指标聚合的事实来源；
- 大内容存入 artifact，通过内容哈希引用；
- report 是可重建的派生结果，不是恢复真相来源；
- session history、Runtime Checkpoint 和 tool result 才是恢复安全的真相来源。

## 8. 事件模型

第一阶段只定义稳定的核心事件：

```text
run_started
context_built
model_call_started
model_call_finished
tool_call_started
tool_call_finished
checkpoint_linked
delivery_finished
run_finished
```

公共事件字段：

```text
event_id
event_type
occurred_at
session_key
turn_id
task_id             # 没有 sustained Task 时为空
run_id
parent_run_id       # 非恢复 Run 为空
sequence
schema_version
payload
```

第一版直接令 `trace_id = run_id`，避免创建含义重复的标识。未来接入跨进程 OpenTelemetry 时，再增加独立 trace/span identity。

## 9. 核心不变量

实现和测试必须保护以下不变量：

1. RuiClaw 不改变 nanobot 的会话锁、工具排序和恢复语义。
2. 同一 Run 的所有事件携带相同 `run_id`。
3. 同一 Run 的事件 sequence 唯一且单调递增。
4. terminal Run 不得重新进入 running；恢复必须创建带 `parent_run_id` 的新 Run。
5. 已成功记录的不可逆工具调用不得因审计层而自动重复。
6. 并发工具完成顺序不得改变 nanobot 原有的结果提交顺序。
7. Run usage 等于其全部物理模型调用 usage 的可重建聚合。
8. report 可以删除后重建；恢复状态不能依赖 RuiClaw report。
9. 审计写入失败不得静默改变 Agent 的任务结果。
10. 不保存隐藏推理文本，敏感输入输出必须脱敏或使用受控 artifact 引用。

## 10. 与现有 nanobot 的集成边界

优先扩展位置：

- Agent Hook / EventSink：关联 Run 与现有阶段事件；
- Session metadata：仅保存恢复所需的 Run 关联，不复制 recovery 状态；
- `llm_usage`：按 run_id 关联物理模型调用；
- Tool execution：记录 tool event、batch_id 和副作用分类；
- Context governance：输出 Context Ledger；
- WebUI coordinator/channel adapter：展示状态，不承载核心状态机。

原则上不优先修改：

- MessageBus 的基本队列语义；
- AgentRunner 的模型工具循环；
- Provider 的原生 Tool Call 协议；
- workspace、SSRF 和 shell sandbox 安全边界。

只有现有 Hook、EventSink 或事件上下文无法携带 `run_id` 时，才对 `agent/loop.py` 或 `agent/runner.py` 做最小改动。

## 11. 第一阶段范围

第一阶段实现：

- Run identity 与最小 Run 状态；
- 复用现有 `session_key` 和 `turn_id`；
- sustained goal 存在时记录可选 `task_id`；
- Run 事件追加落盘；
- 物理模型调用、工具调用、checkpoint 和 delivery 的 Run 关联；
- 重启恢复时创建带 `parent_run_id` 的新 Run；
- 最小 report 重建。

第一阶段不实现：

- 向量数据库或外部长期记忆；
- 自动提示词搜索；
- 自动上线候选；
- 分布式任务队列；
- 完整 OpenTelemetry 后端；
- 多 Agent DAG 调度。
- 普通请求的 Task 状态机；
- 独立 Step 调度或 Step 状态机；
- 替换 nanobot 现有 recovery 的语义 checkpoint。

## 12. 验收标准

1. 普通用户 Turn 产生一个 Run，相关阶段事件具有相同 `run_id`。
2. 每次物理模型调用和工具调用都能归属到唯一 Run。
3. gateway 恢复执行时创建新 Run，并正确关联 `parent_run_id`。
4. 启用审计层前后，工具执行次数、顺序和用户结果保持一致。
5. 两个 Session 仍可并行执行，同一 Session 仍保持原有串行语义。
6. 并行只读工具完成顺序不影响模型看到的结果顺序。
7. 删除 `report.json` 后可以从 manifest、events、Usage 和 checkpoint 引用重建。
8. 可以按 Run 计算端到端耗时、阶段耗时、物理调用数、Token 和错误分布。

## 13. 待确认决策

以下决策在参考更多项目后再冻结：

- 一条 follow-up 是注入当前 Run，还是触发新 Run；
- Run 的起点采用“消息被接纳”还是“获得会话锁”；
- 审计写入失败采用 fail-open 还是 fail-closed；
- artifact 是否采用内容寻址存储；
- Run 索引使用 JSON/JSONL 还是 SQLite；
- 是否需要把事件离线投影成独立 Step 表。

## 14. 当前实现进度

### Phase 1A：Run Identity（已完成）

- 每次新执行生成不可复用的 `run_id`；
- `RuntimeEventContext` 显式携带 `turn_id`、`run_id` 和 `parent_run_id`；
- `RequestContext` 将同一身份传递给上下文提供器和工具执行边界；
- sustained goal 的内部 continuation 复用原 `run_id`；
- Runtime Checkpoint 保存中断 Run 身份，显式恢复创建新 Run，并设置
  `parent_run_id`；
- 外部消息不能通过伪造 metadata 指定 Run 身份，只有受信任的内部 continuation
  和 recovery sender 可以传递关联字段。

### Phase 1B：Run Event Ledger（已完成）

- 定义最小 `run_started`、模型调用、工具调用和 `run_finished` 事件；
- 通过 scoped EventSink / Hook 旁路采集，不改变 Runner 执行顺序；
- 追加写入 `events.jsonl`，并验证同一 Run 的 sequence 单调且唯一；
- 从事件重建首版 `report.json`。

当前安全边界：台账只保存 Run 身份、模型名、工具名、调用 ID、状态、耗时和
Token 用量，不保存 Prompt、模型输出正文、工具参数、工具结果正文或隐藏推理。
Ephemeral Run 不落盘；审计写入失败采用 fail-open，只记录日志，不改变 Agent 结果。

### Phase 1C：Context Ledger 首版（已完成）

- 在每次实际 Provider 请求发出前记录 `model_context_built` 事件；
- 使用 `model_call_id` 将上下文快照、模型调用开始及结束事件关联起来；
- 记录请求消息数量，以及 system、user、assistant、tool result、other 和工具定义的
  Token 估算；
- 记录上下文窗口、输入预算、利用率、是否发生压缩，以及本次注入的 runtime context
  source 名称；
- 从事件重建请求次数、最大上下文 Token、最大利用率和压缩请求数，并写入 Run report。

当前统计边界：Context Ledger 观测的是经过 Context Governance 处理后、真正交给
Provider 的请求结构；Token 拆分是本地一致性估算，不等同于供应商最终计费。首版不会
保存消息正文，也不会把 system 消息进一步猜测性拆成 Memory、Skills 和 bootstrap 的
“精确 Token”。后续应通过结构化上下文组装，在源头标记各 Context Source，才能形成
可信的逐来源 Token 台账。

### Phase 1D：ContextSource Lite（已完成）

- 将上下文归入固定的 `bootstrap`、`memory`、`skills`、`session_summary`、
  `session_history`、`user_input`、`tool_result`、`tool_definitions` 和 `other` 来源；
- ContextBuilder 只在内部 metadata 写入来源和 Token 权重，不复制 Prompt 正文，Provider
  发出请求前仍会移除 `_meta`；
- 每个 `model_context_built` 事件记录本次请求的 `source_tokens`；
- Run report 输出各来源在所有模型请求中的峰值 `max_source_tokens`，且单次请求的来源
  Token 之和与其 `total_tokens` 一致。

该版本定位为轻量的本地实现：System Prompt 依据现有稳定章节边界识别 Memory、Skills
和归档摘要，再按本地 Token 权重分配；它不是供应商计费或生产级 Prompt tracing 系统。

### Phase 1E：Run Inspector（已完成）

- 增加共享只读查询层，读取 Run manifest、report 和最多 500 条最新事件；
- `nanobot runs list` 展示近期 Run 的状态、模型、耗时、Token 和工具调用数；
- `nanobot runs show <run_id>` 展示 Context 来源分布和执行时间线；
- Gateway 增加鉴权后的 `/api/webui/runs` 与 `/api/webui/runs/{run_id}` 查询接口；
- WebUI Settings 增加 Runs 页面，展示 Run 列表、核心指标、Context 来源条形图和事件时间线；
- Run ID 必须是文件系统安全名称，查询拒绝路径穿越和符号链接，并忽略损坏的列表记录。

该阶段形成了“运行采集 → 本地落盘 → 聚合报告 → CLI/API 查询 → WebUI 可视化”的
完整运行闭环，不引入数据库、检索索引或生产级 Trace 后端。

### Phase 1F：RuiClaw Bench v1（已完成）

- 参考 PicoBench 的固定任务、隔离 Fixture、脚本化模型输出、步数预算、确定性
  Verifier 和结果 Artifact 设计；
- 使用 nanobot 自己的 `AgentRunner`、`ToolRegistry`、文件工具和 RuiClaw
  `RunLedger` 执行任务，不复制 Pico Runtime；
- 每个任务使用全新的 Fixture 副本与 Run 目录，原始 Fixture 保持不变；
- Verifier 采用受限的结构化规则，不从 Benchmark JSON 执行任意 Shell；
- `nanobot bench run` 输出任务通过率、预算内完成率和 Verifier 通过率，并保存
  Commit、Fixture 哈希、模型身份、逐任务 Run ID、失败分类与 Run report；
- v1 固定任务共 6 个，覆盖文档修改、文本编辑、非法工具参数恢复、路径越界防护、
  重复读取治理和证据包完整性。

2026-09-13 本地确定性实测：6/6 任务通过，通过率、预算内完成率和 Verifier
通过率均为 100%。模型身份为 `ruiclaw-scripted/deterministic-v1`，该数据只说明
Harness 回归路径稳定，不用于表示真实模型 Coding 能力。证据文件为
`benchmarks/results/ruiclaw-bench-v1/result.json`。

### Phase 1G：Recovery Bench v1（已完成）

- 使用可抛出 `CancelledError` 的确定性 Hook 在 `awaiting_tools`、
  `tools_completed` 和 `final_response` checkpoint 之后模拟进程中断；
- 故障路径实际经过 `AgentLoop`、Runtime Checkpoint、`SessionManager`、
  `RecoveryCoordinator` 和 RuiClaw `RunLedger`；
- 对不确定 Tool Call 验证恢复扫描不会自动执行副作用，并保留中断 Run 身份供人工确认；
- 对已完成 Tool Call 验证结果只物化一次、继续执行创建子 Run，且子 Run manifest 的
  `parent_run_id` 指向中断 Run；
- 对已持久化最终答案验证重启直接恢复答案，不再请求模型；
- `nanobot bench recovery` 一键运行固定恢复场景并保存逐场景 Run ID、恢复原因、
  副作用次数和父子关联结果。

2026-09-13 本地确定性故障注入实测：3/3 场景通过，恢复成功率 100%，父子 Run
关联率 100%，重复副作用 0；不确定 Tool Call 执行次数为 0，已完成副作用总执行次数
为 1，最终答案恢复新增模型调用为 0。完整证据文件为
`benchmarks/results/ruiclaw-recovery-v1/result.json`。

### Phase 1H：Model Cost Ledger v1（已完成）

- 复用 Provider 已有的 `LLMCallObserver`，通过 asyncio task-local collector 将每次
  retry-managed 物理请求关联到当前 Run，不把消息正文、Prompt 或工具参数写入成本账本；
- Run Ledger 新增 `provider_call_finished`，记录 provider、model、耗时、stream、
  finish reason、错误分类以及标准化 usage；逻辑 `model_call_finished` 继续保留，用于区分
  Agent 轮次与真实 Provider 请求数；
- `report.json` 在存在物理请求时以其为 token 与成本依据；未启用 observer 时明确记录
  `basis=logical_model_calls`，不静默混用两种口径；
- 增加 `nanobot/observability/pricing/v1.json` 版本化价格快照，保存采集日期、币种、
  适用范围和官方来源；计算器按普通输入、cache read、cache write、output 分项计算；
- 成本状态分为 `estimated`、`partial`、`unknown`。未知 provider/model、缺失 usage 或
  缺少实际发生的缓存费率时，总成本保持 `null`，禁止把未知成本表示为 0；
- Run Inspector 的 CLI、鉴权 API 和 WebUI 共用报告投影，展示物理请求数和估算成本；
  RuiClaw Bench 汇总模型请求数、input/output Token、价格快照和单位成功任务成本。

2026-09-13 本地实测：定价公式、缓存分项、未知模型和部分计价测试全部通过；固定 Bench
6/6 任务通过，共关联 20 次物理请求、49,321 input Token、2,211 output Token。由于
`ruiclaw-scripted` 不对应真实供应商价格，6/6 任务成本均为 `cost_unknown`，总成本和
单位成功任务成本保持 `null`。该结果证明未知成本保护生效，不表示脚本模型免费。价格
快照 `openai-standard-2026-09-13-v1` 的来源为 OpenAI 官方 API Pricing 页面。

### Phase 1I：Tool Execution Governance v1（已完成）

- 复用 nanobot 已有的 `read_only`、`concurrency_safe`、`exclusive` 和稳定结果顺序，
  不另建工具调度框架；
- `AgentDefaults` 增加 `max_concurrent_tools=4` 与 `tool_timeout_seconds=120`，并由
  `AgentLoop` 传入共享 `AgentRunner`；直接使用 Runner 时仍有相同默认值；
- 并行批次通过 Semaphore 限制真实在执行的工具数，写工具、独占工具与未声明并发
  安全的未知插件继续形成单调用批次；
- 在 Tool 基类增加 `effect_type`：只读工具默认 `read_only`，未声明的写能力默认
  `unknown`；文件写入、编辑和补丁明确标记为 `workspace_write`，消息发送、定时任务与
  图像生成明确标记为 `external_side_effect`；未知类型默认不并行，插件仍可通过既有的
  `concurrency_safe` 契约显式声明安全并发；
- 所有 Tool、MCP 和插件调用增加统一 asyncio 外层超时。工具自己的更短超时继续生效；
  外层超时取消协程并等待其清理逻辑完成，再向模型返回可恢复错误；
- 治理元数据通过 Hook Context 的旁路字段传递，不改变原有 `tool_events` 结果契约；
  Run Ledger 的 `tool_call_finished` 记录副作用类型、批次、排队/执行耗时、超时与取消，
  `report.json` 聚合各类型数量、批次数、超时数、取消数和峰值耗时；
- 增加 `nanobot bench tools`，直接驱动生产 `execute_tool_calls` 与 Run Ledger，验证有界
  并发、写入串行和超时清理。

2026-09-13 本地确定性实测：3/3 场景通过；4 路各 40 ms 的读取在并发上限 2 时，
观察执行峰值为 2，串行耗时 164 ms、并行耗时 82 ms、加速 2.00x；3 次工作区写入
观察峰值为 1、重叠写入 0；1 次超时调用被记录为 `timed_out`，工具 `finally` 清理完成，
活动任务残留 0。完整证据为
`benchmarks/results/ruiclaw-tool-governance-v1/result.json`。

### Phase 1J：Working Memory Retrieval v1（已完成）

- 不替换 nanobot 原有的 Session History、Summary Checkpoint 和 Dream；Dream 继续负责
  `SOUL.md`、`USER.md` 与 `memory/MEMORY.md` 的长期提炼，Working Memory 只保存可重新
  验证的任务工作集；
- 成功的 `read_file` 通过现有 Agent Hook 生成最多 500 字符的短摘要，明显的 key、token、
  password 和 secret 值在落盘前脱敏；仅接受当前工作区内的普通文件；
- 当前快照原子写入 `.ruiclaw/memory/index.json`，更新记录追加到 `entries.jsonl`；索引最多
  保留 100 条，当前版本不引入数据库、Embedding 或外部 Memory 服务；
- 用户请求按文件路径、标签和关键词重合进行可解释排序，最多选择 3 条；中文按单个汉字
  参与词法匹配，路径命中、标签命中和正文关键词分别产生明确 reason；
- 文件摘要保存 SHA-256 内容哈希。只有和请求相关的候选才进行 freshness 校验，文件被修改、
  删除或不可读时计入 `stale_rejected_count` 并拒绝注入；重新读取后用同一 memory ID 更新；
- ContextBuilder 将命中项放入独立 `Relevant Working Memory` 段，ContextSource Lite 将其
  Token 归入 `working_memory`；Provider 请求发出前内部 metadata 仍会被清除；
- `model_context_built` 记录候选数、命中数、过期拒绝数，以及命中项的 ID、score、reason
  和相对来源路径，不记录摘要正文；`report.json` 聚合有命中的请求数和单次峰值；
- 增加 `nanobot bench memory`，直接驱动生产 Store 和 ContextBuilder，对比 memory-on/off
  的重复读取，并验证 Top-1 来源和 stale suppression。

2026-09-14 本地确定性实测：6 个文件依赖任务全部命中正确 Top-1，并成功注入 Context；
另 1 个文件变化场景正确拒绝旧摘要，合计 7/7 通过。命中率、Top-1 准确率与 stale
suppression 均为 100%；消融口径下 memory-on 重复读取 0 次、memory-off 6 次，避免模拟
回读 7,043 字符。该固定实验只验证 Memory Harness 机制，不表示真实模型长对话能力。
完整证据为 `benchmarks/results/ruiclaw-memory-v1/result.json`。

### Phase 1K：Evolver Lite（已完成）

- 参考 Pico 的固定任务、候选隔离、封闭测试和人工确认思路，不假装复用了 Pico 仓库中
  不存在的 Evolver 源码；RuiClaw 在现有 Bench 基础上实现自己的离线策略比较器；
- `ContextBuilder` 增加默认值仍为 3 的 `working_memory_limit` 注入点，已有 Agent 行为保持
  不变；首个候选只比较一个真实生产参数：Working Memory Top-K 3 与 Top-K 1；
- baseline 与 candidate 分别在独立工作区建立相同的 6 条 Memory，不共享索引或运行状态；
  4 条任务作为训练集、2 条未参与选择的任务作为封闭测试集，另执行 1 个文件变化后的
  stale safety 场景；
- 门禁要求训练正确率不下降、封闭集正确率不下降、过期记忆抑制不下降，以及 Working
  Memory 注入字符至少减少 20%；任一条件失败即输出 `rejected`；
- `nanobot bench evolve` 一键生成 `comparison.json` 与 `comparison.md`，记录候选预算、
  策略参数、逐任务召回来源、训练/封闭集结果、安全结果、效率差异和各项门禁；
- 即使全部门禁通过也只输出 `recommended_for_manual_review`，`automatic_promotion` 固定为
  `false`。当前版本不会自动修改配置、提交代码或部署候选；
- 由于封闭测试仅 2 题，结果明确标记 `insufficient_small_holdout`，它证明受控演进机制
  可运行，不构成真实模型质量或统计显著性的结论。

2026-09-14 本地确定性实测：候选预算 1，baseline 与 candidate 的 4 个训练任务、2 个
封闭测试任务和 stale safety 场景均为 100% 通过；Top-K 1 将 Working Memory 注入量从
1,136 字符降至 490 字符，减少 56.87%，四项硬门禁全部通过，最终状态为
`recommended_for_manual_review`，没有自动发布。完整证据为
`benchmarks/results/ruiclaw-evolver-v1/comparison.json` 与 `comparison.md`。
