# GitHub CLI 多账号自动路由实施计划

## 概要

为本机 GitHub 多账号建立稳定的自动路由：

- `git@github-new:...` 使用 `zenchantai`
- `git@github.com:...` 或已配置的 `git@github:...` 使用 `saberhaha`
- `git push` 继续由 SSH 别名选择密钥。
- PR、Issue 等 GitHub API 操作使用原生 `gh`，但根据当前仓库的 `origin` 临时注入对应账号的 token。
- 不执行全局 `gh auth switch`，避免不同终端和并行任务互相影响。
- 本次只配置和验证多账号路由，不创建 PR、不推送分支、不配置 GPG。

正式计划保存目标：

`docs/plans/2026-09-17-github-multi-account-gh-routing.md`

执行阶段首先将本计划原文写入该文件并回读核验；若文件已存在，则使用 `-2`、`-3` 递增文件名。

## 实施内容

### 1. 完成 `gh` 双账号认证

- 保留当前已登录的 `saberhaha`，记录当前激活账号。
- 通过 `gh auth login --hostname github.com --git-protocol ssh --web` 添加 `zenchantai`。
- 浏览器授权属于持久账号授权；执行到授权步骤时由用户确认并完成 `zenchantai` 登录。
- 验证两个账号均出现在 `gh auth status` 中，并确认所需仓库权限可用。
- 授权完成后恢复原来的全局激活账号；后续自动路由不依赖激活账号。
- 不打印、不保存明文 token，不把 token 写入仓库、脚本、日志或 shell 配置。

### 2. 建立自动路由工具

新增用户级可执行工具 `~/.local/bin/gha`，工作流程固定为：

1. 读取当前仓库的 push URL：`git remote get-url --push origin`。
2. 解析 SSH 主机别名和 `owner/repo`。
3. 按以下映射确定账号：

   | 远端主机 | `gh` 账号 |
   |---|---|
   | `github-new` | `zenchantai` |
   | `github.com` | `saberhaha` |
   | `github` | `saberhaha` |

4. 使用 `gh auth token --hostname github.com --user <账号>`从系统凭据存储中读取对应 token。
5. 仅对子进程设置 `GH_TOKEN`、`GH_HOST=github.com` 和 `GH_REPO=github.com/<owner>/<repo>`，然后调用原生 `gh`。
6. 未登录目标账号、没有 `origin`、URL 无法解析或主机不在映射表时立即失败并给出明确提示，不猜测账号、不回退到当前激活账号。

支持常见 SSH 地址：

- `git@github-new:owner/repo.git`
- `git@github.com:owner/repo.git`
- `git@github:owner/repo.git`
- 对应的 `ssh://git@.../...` 形式

HTTPS 或未知主机不做自动账号推断，避免使用错误身份。

### 3. 固化后续 Codex 执行规则

在 `chant_agent` 的仓库级 `AGENTS.md` 中记录 GitHub 操作约束；若文件不存在则创建，若存在则只追加对应章节：

- 执行 GitHub API 操作前必须读取 `origin` push URL。
- GitHub PR、Issue、Release、Actions 等命令优先使用 `gha`。
- `git push` 仍使用仓库现有 SSH remote。
- 禁止为了单次任务执行全局 `gh auth switch`。
- 执行前报告识别出的远端、仓库和账号。
- 创建 PR、评论、合并等对外写操作仍以用户明确授权为前提。
- 若 `gha` 不可用，则允许使用等价的原生 `gh` 临时环境变量方式，但必须遵守同一映射和失败策略。

不改写现有 `origin`，不修改现有 SSH 密钥和 `github-new` 配置。

## 验证方案

- 语法与安全：

  - 检查 `gha` shell 语法。
  - 确认输出中不出现 token。
  - 确认失败路径不会调用错误账号。

- `zenchantai` 路由：

  - 在 `chant_agent` 中识别 `git@github-new:zenchantai/chant_agent.git`。
  - 运行只读身份查询，返回 `zenchantai`。
  - 运行只读仓库查询，返回 `zenchantai/chant_agent`。
  - 验证 `ssh -T git@github-new` 仍认证为 `zenchantai`。

- `saberhaha` 路由：

  - 在临时 Git 仓库中配置 `git@github.com:saberhaha/<repo>.git` 形式的测试 remote。
  - 运行只读身份查询，返回 `saberhaha`。
  - 若本机存在并可用 `github` 别名，再验证 `git@github:...`；若不存在，只验证路由解析并明确记录 SSH 别名本身尚不可用于推送。

- 隔离性：

  - 路由调用前后比较 `gh auth status`，确认全局激活账号未被切换。
  - 确认 `chant_agent` 当前分支、工作区修改和远端地址均未变化。
  - 使用未知主机测试，确认工具明确拒绝执行。

## 假设与边界

- `github-new` 固定代表 `zenchantai`，`github.com`/`github` 固定代表 `saberhaha`。
- 当前 `chant_agent` 的远端继续保持 `git@github-new:zenchantai/chant_agent.git`。
- `gh` 的账号凭据继续由系统 keyring 管理。
- GPG 提交签名与本方案无关，不创建 GPG key。
- 本次不提交现有业务修改、不推送 `codex/chant-agent-260916-2`、不创建或更新任何 PR。
