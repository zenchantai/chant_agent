# Repository Instructions

## GitHub 多账号操作

- 执行 GitHub API 操作前，必须先读取 `git remote get-url --push origin`。
- SSH 主机别名 `github-new` 使用 GitHub 账号 `zenchantai`；`github.com` 和 `github` 使用账号 `saberhaha`。
- PR、Issue、Release、Actions 等 GitHub API 命令优先使用 `gha`，由它根据 `origin` 临时选择账号凭据。
- `git push` 继续使用仓库现有 SSH remote；不得为 GitHub API 操作改写 `origin`。
- 禁止为了单次任务执行全局 `gh auth switch`，避免影响其他仓库、终端或并行任务。
- 执行前报告识别出的 origin、目标仓库和 GitHub 账号。
- 创建 PR、评论、合并等对外写操作，仍须有用户对该操作的明确授权。
- 如果 `gha` 不可用，可以调用原生 `gh`，但必须按同一映射通过临时 `GH_TOKEN`、`GH_HOST`、`GH_REPO` 环境变量执行；不得回退到当前全局激活账号。
