# ReliAgent 测试策略

## 1. 目标

测试体系优先控制 Agent 运行时的高风险问题：异步任务永久挂起、跨 run 事件串流、
工作区逃逸、会话状态损坏、IPC/MCP 协议不兼容，以及后台任务异常被静默吞掉。
覆盖率是风险覆盖的辅助指标，不以无差别追求 100% 为目标。

## 2. 测试分层

| 标记 | 边界 | 默认依赖 | 典型对象 |
| --- | --- | --- | --- |
| `unit` | 单模块或单对象 | 内存、临时目录、mock | 数据模型、策略、事件和工具 |
| `component` | 多个进程内组件 | fake provider、真实文件 | Runner、权限流和会话管理 |
| `integration` | 跨进程/Socket/传输边界 | daemon、回环网络、临时 HOME | IPC、恢复和双客户端 |
| `e2e` | 完整用户链路 | 可选真实模型凭据 | 模型调用到工具结果回填 |
| `platform` | 操作系统语义 | Windows junction / POSIX symlink | 工作区路径边界 |
| `slow` | 压力或长时间场景 | 独立 CI/手工触发 | 并发、稳定性和性能基线 |

目录层级会自动为 `tests/unit` 和 `tests/integration` 添加对应标记；更窄的标记由测试
显式声明。启用 `--strict-markers`，未注册或拼写错误的标记会直接失败。

## 3. 执行入口

```bash
# 快速反馈
uv run pytest -m unit -q

# 不依赖真实模型的集成测试
uv run pytest -m "integration and not e2e" -q

# 完整测试（未配置 API Key 时真实模型用例会明确 skip）
uv run pytest tests -q

# 分支覆盖率
uv run pytest tests -q --cov=kama_claude --cov-branch --cov-report=term-missing
```

## 4. CI 准入标准

- Ruff 和 mypy strict 必须通过。
- Linux 与 Windows 测试必须通过；不得用 rerun 隐藏不稳定用例。
- Linux 收集语句与分支覆盖率，并执行最低门禁。
- CI 输出 JUnit XML、coverage XML/HTML；失败日志通过 artifact 保留。
- 平台 skip 必须写明替代覆盖关系；缺少凭据的真实模型 E2E 不能计为 pass。
- 每个异步、进程或网络测试必须有有界超时和确定性清理。

## 5. 风险与验证矩阵

| 风险 | 主要验证层 | 关键断言 |
| --- | --- | --- |
| 多 run 事件串流 | unit/component | 事件只到达匹配 `run_id` 的订阅者 |
| 慢客户端拖垮广播 | unit/integration | 超时后移除客户端，其他客户端继续接收 |
| 工作区路径逃逸 | unit/platform | 拒绝绝对路径、遍历、symlink 与 junction |
| daemon 重启丢失会话 | unit/integration | 合法状态恢复，损坏状态隔离，状态机合法 |
| 后台写入失败导致死锁 | unit | 异常传播且 `stop()` 在超时内结束 |
| MCP 传输异常 | unit/integration | 超时、错误结果和分页保持类型化语义 |

## 6. 当前基线与演进

- Windows 本地：306 passed，4 skipped（296 个单元测试、10 个集成测试通过）。
- 原语句覆盖率基线：61%。
- 第一阶段引入分支覆盖率后，基线从 58.2% 提升到 61.3%，CI 门禁设为 60%；
  该指标同时计算条件分支，严格于原 60% 语句覆盖率门禁。后续逐步提升到 65% 和 70%。
- 后续优先补 Core daemon、MCP、Socket Server、配置解析和关键 TUI 交互，不通过
  排除真实业务模块来虚增覆盖率。
