# KamaClaude 测试岗面试知识手册

## 一分钟项目介绍

> KamaClaude 是一个本地 AI Agent 运行时。TUI/CLI 与常驻 daemon 分离，通过 TCP 上的
> NDJSON + JSON-RPC 2.0 通信；daemon 内部由 AgentLoop 驱动 LLM 和工具调用，EventBus
> 将 token、工具、权限、run 状态推送到前端并写入事件文件，SessionStore 保存多轮上下文。
> 我是在开源项目基础上做二次工程化和测试加固，重点解决四类问题：跨 run 事件串流和订阅
> 泄漏、文件工具路径逃逸、daemon 重启后的 session 恢复、MCP 私有协议实现的兼容性。
> 我为每项改动先构造失败用例，再实现修复，并使用单元、IPC 集成、静态检查和类型检查组成
> 质量门禁。

如果面试官只给 30 秒，就说清三点：它是什么、你具体改了什么、你如何证明改对了。

## 当前验证结果

在 Windows + Python 3.12 环境完成以下验证：

```text
pytest tests -q                              306 passed, 4 skipped  # Windows 本地
ruff check src tests scripts                 All checks passed
mypy src                                     86 source files, no issues
python scripts/gen_protocol_doc.py --check   WIRE_PROTOCOL.md is up to date
```

跳过用例来自跨平台分工（Linux 验证 symlink、Windows 验证 junction）或需要真实 Anthropic
API 的可选 E2E，不能把 skip 混同于 pass；CI 会同时运行 Linux/Windows，真实模型 smoke
则应通过受控密钥单独触发。

## 个人贡献怎么讲

| 问题 | 风险 | 改进 | 验证证据 |
|---|---|---|---|
| EventBus 只增不减，共享 bus 上事件未按 run 隔离 | 内存泄漏、事件写错文件、CLI 被其他 run 提前结束 | 可幂等退订句柄、run_id filter、上下文退出自动解绑、CLI 目标 run 路由 | 并发 run、退出解绑、错误 run.finished 不触发退出 |
| 文件工具只检查字符串中是否含 `..` | 绝对路径、反斜杠、symlink、junction 可逃逸 | 同时解析 POSIX/Windows 语法，canonical path 必须位于捕获的 workspace | 参数化路径、symlink、junction、写入无副作用 |
| Session 只写磁盘但启动不加载，TUI 重连总新建 | 历史存在但不可用、上下文割裂 | 启动扫描 meta、坏记录隔离、active 状态恢复、`session.resume` | store/manager 单测、TUI fake client、IPC roundtrip |
| 自写 MCP 行协议和 raw TCP | 协议版本、分页、生命周期和富结果兼容风险 | 官方 SDK、stdio/Streamable HTTP、分页、isError/structured/image 映射 | 官方类型构造测试、配置迁移测试、strict typing |

推荐使用“发现问题 → 风险分析 → 设计方案 → 测试证据 → 剩余风险”结构回答，不要只背功能列表。

## 测试岗位核心知识点

### 1. 测试分层与测试替身

- 单元测试：验证纯逻辑和单组件状态，如路径解析、事件过滤、MCP 结果映射。
- 组件测试：真实 `SessionStore + SessionManager + EventBus`，只替换 LLM runner。
- 契约测试：Pydantic command/event 模型、JSON-RPC error code、生成的 `WIRE_PROTOCOL.md`。
- 集成测试：启动真实 daemon，客户端走真实 TCP/NDJSON，验证请求、响应、广播和重连。
- E2E：接真实 LLM，只做少量冒烟；受费用、网络和模型非确定性影响，不应成为主要回归层。

常用替身区别：

- Stub：返回固定结果，例如固定 `LlmResponse`。
- Mock：除返回结果外，还断言调用参数、次数和顺序。
- Fake：有可工作的简化实现，例如写真实临时目录的 fake runner。
- Spy：保留真实行为，同时记录调用。

回答“为什么不全测真实 LLM”：慢、贵、不稳定、难构造边界。核心状态机用确定性替身，真实接口仅做
少量契约/E2E 验证。

### 2. 异步与并发测试

要会讲这些风险：

- Race condition：事件可能在 `agent.run` 响应返回 run_id 之前到达，所以 CLI 先缓存再按 run_id 回放。
- 资源泄漏：订阅、task、socket、文件句柄必须在取消和异常路径释放。
- Backpressure：慢客户端的 `writer.drain()` 不能无限阻塞全部事件分发；设置超时并清理连接。
- 顺序性：同一订阅者通常要求事件有序；提高并发时不能破坏 `run.started → step → run.finished`。
- 取消传播：`CancelledError` 不能被普通异常吞掉，退出后仍要生成一致状态或完成清理。
- Flaky test：不要用固定 `sleep` 猜状态，优先等待 `asyncio.Event`、响应或最终条件，并设置总超时。

典型追问：“为什么 EventBus 不直接 `gather` 所有 handler？”

> 并发可降低单个 handler 的阻塞，但会引入顺序、异常隔离和无限 task 三个问题。本项目保留核心
> 总线顺序语义，在外部 IPC 边界给 drain 加超时；如果进一步扩展，会为每个订阅者使用有界队列
> 和独立 worker，并定义队满时丢弃、断开或降级策略。

### 3. 协议测试：JSON-RPC + NDJSON

必须记住：

- NDJSON 用换行分帧，优点是简单和流式；风险是超长行、半包、坏 JSON、编码和连接中断。
- JSON-RPC 2.0 用 `id` 关联 request/response；notification 没有响应；错误应有稳定 code。
- Pydantic discriminated union 通过 `type` 做命令/事件判别，能尽早拒绝非法结构。
- 契约测试至少覆盖：正常请求、未知 method、非法 JSON、非法 params、重复/未知 id、超长 frame。
- 协议文档应从模型生成并在 CI 用 `--check` 防止“代码变了，文档没变”。

### 4. 文件安全测试

常见漏洞和边界：

- Path traversal：`../` 与 `..\`。
- Absolute path：`/etc/passwd`、`C:\Windows\...`、`C:relative`、UNC 路径。
- 编码/规范化：重复分隔符、`.`、大小写、Unicode 名称。
- Symlink/junction escape：输入看似在 workspace，真实目标在外部。
- TOCTOU：校验后、使用前路径被替换；高安全场景要考虑基于目录句柄的原子操作。
- 副作用断言：只断言抛异常不够，还要确认外部文件没有被创建或修改。

本项目策略是“输入必须为相对路径 + resolve 后 `is_relative_to(workspace)`”。它显著提高安全性，
但不是操作系统级沙箱；`bash` 工具仍需权限审批和进程隔离。

### 5. 持久化与故障恢复测试

Session 是状态机：

```text
active → waiting_for_input → active → ... → closed
```

需要覆盖：

- 正常创建、发消息、等待、关闭。
- daemon 在 active 时崩溃，重启后 chat 恢复为可继续状态。
- one-shot 的遗留 active 不能被错误续跑，应关闭。
- `meta.json` 损坏只能隔离当前记录，不能导致所有 session 无法启动。
- `thread.jsonl` 尾部孤立 tool_use 要裁掉，避免下一次 LLM 请求违反消息配对约束。
- 同一 session 并发消息应返回 busy，而不是交叉写入历史。
- 恢复已关闭、不存在或正在运行的 session 应有稳定错误码。

测试持久化不能只测“写后能读”，还要测 crash consistency、局部坏数据和幂等恢复。

### 6. MCP 与第三方协议测试

MCP 可以理解为 Agent 连接外部工具的标准协议。需要掌握：

- 标准 client/server 能力协商与版本协商。
- stdio 适合本地子进程；Streamable HTTP 是远程标准 transport。
- `tools/list` 可能分页，不能只取第一页。
- `tools/call` 的 `isError=true` 是应用层失败，不等同于连接断开，也不应盲目重试。
- 结果不仅是 text，还可能有 `structuredContent`、image、audio、resource/link。
- 凭据不要写入仓库；配置只保存环境变量名，运行时解析 header。
- 第三方 server 不可信：schema、描述和 annotations 都是输入，仍需权限与大小限制。

测试策略：协议对象转换做单元测试；本地最小 MCP server 做 transport 集成测试；真实第三方 server
只做可选 smoke test。

### 7. LLM/Agent 系统怎么测

传统断言“输出必须等于某句话”不适合非确定模型。可分三层：

- 确定性工程层：协议、状态机、权限、重试、事件、持久化，使用严格断言。
- 半确定 Agent 行为层：固定模型/temperature，断言是否选择正确工具、参数是否合法、步骤是否超限。
- 质量评估层：任务集 + rubric，统计成功率、工具错误率、延迟、token 成本和人工/模型评分。

关键指标：

- Task success rate。
- Tool-call precision / schema error rate。
- 首 token 延迟、完整响应延迟、P95/P99。
- 每任务输入/输出 token 与缓存命中。
- 重试率、超时率、权限拒绝后恢复率。
- Session 恢复成功率和事件丢失/重复率。

### 8. 可观测性如何帮助测试

事件和 trace 不只是日志，也是测试 oracle：

- `events.jsonl` 验证业务顺序和最终状态。
- trace 关联 client、run、LLM、tool，定位失败发生在哪一层。
- run_id/tool_use_id/session_id 是关联键。
- 日志要结构化但不能泄露 API key、Authorization header 或完整敏感文件。
- 测试失败时应保留 seed、输入、事件时间线和环境版本，便于复现 flaky case。

## 高价值测试用例

| 优先级 | 场景 | 操作 | 预期 | 自动化层 |
|---|---|---|---|---|
| P0 | 跨 run 隔离 | 两个 run 共用 bus，并发发布结束事件 | 每个 writer/CLI 只处理自己的 run | 单元/组件 |
| P0 | 订阅释放 | run 正常、异常、取消后统计订阅 | 订阅数回到基线，关闭文件不再被调用 | 单元 |
| P0 | 路径遍历 | 输入 `../x`、`..\x` | 三个文件工具均拒绝 | 单元 |
| P0 | 链接逃逸 | workspace 内链接指向 outside | 拒绝且 outside 无副作用 | 安全组件 |
| P0 | Session 重启恢复 | 写 active chat meta，重建 manager | 恢复为 waiting，可读历史和继续消息 | 组件 |
| P0 | MCP 应用错误 | server 返回 `isError=true` | 标记 tool_error，不误判连接失败、不盲目重试 | 单元/契约 |
| P1 | 慢客户端 | drain 永不完成 | 超时后清理该连接，其他订阅可继续 | 单元 |
| P1 | run_id 响应竞态 | 完成事件先于 RPC response | 事件被缓存，绑定 ID 后正确交付 | 单元 |
| P1 | 损坏 meta | 一个 session 的 JSON 截断 | 跳过坏记录，其他 session 正常恢复 | 单元 |
| P1 | MCP 分页 | tools/list 返回两页 | 所有工具均被注册且无重复 | 单元/契约 |
| P1 | IPC 非法输入 | 坏 JSON、未知方法、非法 params | 返回标准错误且 daemon 不退出 | 集成 |
| P1 | TUI 重连 | socket 断开后重新连接 | 先 resume 原 ID；closed/not-found 才 create | 组件 |
| P2 | 富媒体结果 | text + image + structured | TUI/事件有摘要，LLM 收到支持的图片和结构化文本 | 单元 |
| P2 | 超大结果 | 超过 frame/tool result 上限 | 截断或拒绝，不 OOM，不破坏下一帧 | 性能/安全 |
| P2 | 断电写入 | thread 最后一行半写 | 跳过坏行并保留此前完整消息 | 故障注入 |

## 高频追问与参考回答

### 为什么单元测试多，集成测试少？

因为错误定位成本和执行稳定性不同。纯逻辑在单元层穷举边界，真实 socket/进程只验证跨层契约；
真实 LLM 最少。测试金字塔不是固定比例，而是把失败尽量推到更快、更确定的层。

### 如何减少 flaky test？

不用固定 sleep 等事件；等待明确条件并设置总超时。每个用例使用随机空闲端口和独立临时 HOME；
清理 subprocess/task/socket；固定随机种子；失败时输出 daemon stderr 和事件文件。

### Mock 太多会有什么问题？

Mock 可能复刻了错误假设，导致实现和测试一起“自洽但不真实”。所以对 Pydantic 协议和官方 MCP 类型
做契约测试，对 TCP 和 daemon 做少量真实集成，并让 fake 尽量复用真实存储格式。

### 什么用例应该优先自动化？

高频回归、稳定预期、人工成本高、风险高的用例优先，如协议、权限、路径安全、状态机和并发边界。
视觉体验、探索性场景和真实模型主观质量可保留人工或评估集。

### 覆盖率越高越好吗？

覆盖率只能说明代码被执行，不说明断言有效。更关注分支、异常路径、状态迁移和 mutation testing；
100% 行覆盖仍可能漏掉竞态、错误 oracle 和跨组件契约。

### 这个项目还有哪些剩余风险？

- 文件工具的规范化校验不是内核沙箱，仍可能存在极窄 TOCTOU 窗口。
- EventBus 保留顺序调用；高负载时可演进为每订阅者有界队列。
- MCP 富媒体目前只把 Claude 支持的图片类型原样传入，audio/resource blob 只给文本摘要。
- Session meta 写入尚可进一步升级为临时文件 + fsync + 原子替换。
- 真实 LLM 的系统性 eval、性能基线和故障注入仍可继续建设。

主动说剩余风险不会减分，前提是能给出优先级和演进方案。

## 背诵方法

不要逐题背长答案。每个知识点只背四句话：

1. 定义：它是什么。
2. 风险：不用或用错会怎样。
3. 项目：KamaClaude 哪里用了它。
4. 测试：你如何构造输入和 oracle 证明它正确。

例如“幂等”：重复执行一次和执行多次结果一致；否则重连/重试会制造重复副作用；本项目退订和恢复
需要幂等；测试重复 unsubscribe/resume 并检查订阅数、事件数和持久状态。

七天准备顺序：

- 第 1 天：画架构链路，练 30 秒/1 分钟/3 分钟介绍。
- 第 2 天：事件、asyncio、竞态、取消、背压。
- 第 3 天：JSON-RPC、NDJSON、Pydantic、契约测试。
- 第 4 天：路径安全、权限、重试与幂等。
- 第 5 天：Session 状态机、持久化、故障恢复。
- 第 6 天：MCP、LLM 测试、测试替身、质量指标。
- 第 7 天：只看自己的 diff 和失败用例，做两轮追问式模拟面试。

真正需要背的不是 50 道孤立八股，而是“一个工程问题如何被测试发现、修复并证明”的完整故事。
