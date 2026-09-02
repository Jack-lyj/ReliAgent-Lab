# 个人 Fork 与提交工作流

## 结论

建议先在 GitHub 上 fork 上游仓库，再在个人 fork 的功能分支中提交改进。这样可以保留原始
提交历史、清楚展示个人 diff，也能随时同步上游。不要把整个上游项目描述成自己的原创；面试
时应表述为“基于开源项目完成二次开发与测试加固”。

个人 Fork 已确定为 `Jack-lyj/KamaClaude`。本地改进已经从验证副本无损迁移到保留上游历史的
干净克隆，并创建 `feature/interview-hardening` 分支；`origin` 指向个人 Fork，`upstream`
指向原作者仓库。旧目录 `KamaClaude-improved` 保留为迁移备份。

## 推荐步骤

1. 在上游页面点击 **Fork**，创建 `你的账号/KamaClaude`。
2. 克隆个人 fork，并保留上游 remote：

```bash
git clone https://github.com/<你的账号>/KamaClaude.git
cd KamaClaude
git remote add upstream https://github.com/youngyangyang04/KamaClaude.git
git remote -v
```

3. 从最新上游主分支创建功能分支：

```bash
git fetch upstream
git switch main
git merge --ff-only upstream/main
git switch -c feature/interview-hardening
```

4. 将本地改进副本中的变更迁移到该分支，然后验证：

```bash
uv sync
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check src tests scripts
uv run mypy src
uv run python scripts/gen_protocol_doc.py --check
```

5. 建议拆成可讲清楚的提交，而不是一个“大杂烩”提交：

```text
fix(events): isolate run subscriptions and bound slow consumers
fix(tools): enforce canonical workspace boundaries
feat(session): restore persisted sessions and resume TUI reconnects
refactor(mcp): migrate client to official SDK transports
test: add security recovery and protocol regression coverage
docs: add test-engineering interview guide
```

6. 配置你自己的 Git 身份后提交和推送：

```bash
git config user.name "<你的 GitHub 昵称>"
git config user.email "<你的 GitHub 提交邮箱>"
git push -u origin feature/interview-hardening
```

最后在个人仓库创建 Pull Request：`feature/interview-hardening -> main`。PR 描述应包含问题、设计、
风险、测试证据和兼容性说明；即使不向上游提 PR，这个页面也是很好的面试展示材料。

## 面试中的贡献边界

可以说：

> 我基于 KamaClaude 学习了双进程 Agent 架构，并独立完成了一轮工程化加固：定位事件订阅
> 泄漏与跨 run 串流、文件工具路径逃逸、session 无法恢复、MCP 私有传输兼容性四类问题；
> 完成设计、实现和自动化回归，并用 unit、integration、lint、mypy 建立质量门禁。

不要说：

> 我从零独立开发了整个 KamaClaude。

面试官通常会继续追问 diff、失败用例和取舍。个人 fork、独立提交和测试记录就是最直接的证据。
