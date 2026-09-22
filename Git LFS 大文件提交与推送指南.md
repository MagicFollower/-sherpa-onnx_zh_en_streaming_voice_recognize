# Git LFS 大文件提交与推送完整指南

本文档基于本项目遇到的真实场景：模型文件（`.onnx`，173 MB）超过 GitHub 100 MB 单文件限制，推送被拒绝。整理从问题诊断到最终解决的完整命令流程。

---

## 一、问题现象

执行 `git push` 时出现如下错误：

```
remote: error: File models/xxx.onnx is 173.47 MB; this exceeds GitHub's file size limit of 100.00 MB
remote: error: GH001: Large files detected. You may want to try Git Large File Storage - https://git-lfs.github.com.
 ! [remote rejected] main -> main (pre-receive hook declined)
error: failed to push some refs to 'https://github.com/...'
```

**根本原因**：GitHub 对单个文件大小限制为 100 MB，超出后拒绝接收。需要使用 Git LFS（Large File Storage）来管理大文件。

---

## 二、前置条件

### 2.1 确认 Git 版本

```bash
git --version
```

建议 Git 版本 ≥ 2.0。

### 2.2 安装 Git LFS

**Windows（推荐用 Scoop 或手动下载）**：

```powershell
# 方式一：通过 scoop
scoop install git-lfs

# 方式二：通过 winget
winget install Git.Git   # 新版 Git for Windows 已内置 git-lfs

# 方式三：手动下载安装
# 访问 https://git-lfs.com 下载安装包，安装后执行：
```

**macOS**：

```bash
brew install git-lfs
```

**Linux（Debian/Ubuntu）**：

```bash
sudo apt-get install git-lfs
```

### 2.3 验证安装

```bash
git lfs version
# 输出示例：git-lfs/3.7.1 (GitHub; windows amd64; go 1.25.1; git b84b3384)
```

---

## 三、场景分类与解决方案

### 场景 A：尚未提交大文件（预防阶段）

大文件在工作区中，还没有 `git commit`。这是最简单的情况。

```bash
# 1. 在仓库根目录初始化 Git LFS
git lfs install

# 2. 指定需要 LFS 追踪的文件类型（以 .onnx 为例）
git lfs track "*.onnx"

# 执行后会自动生成 .gitattributes 文件，内容类似：
# *.onnx filter=lfs diff=lfs merge=lfs -text

# 3. 如果需要追踪其他大文件类型，继续添加
git lfs track "*.bin"
git lfs track "*.pth"

# 4. 将 .gitattributes 加入提交（重要！确保协作者也生效）
git add .gitattributes

# 5. 正常添加和提交大文件
git add models/
git commit -m "添加模型文件"

# 6. 推送
git push origin main
```

---

### 场景 B：大文件已提交但未推送（本项目遇到的场景）

大文件已经 `git commit` 到本地历史中，但 `git push` 被远程拒绝。

#### 步骤 1：确认当前状态

```bash
# 查看最近的提交记录
git log --oneline -5

# 查看哪些文件超过了限制
git diff --stat HEAD~1   # 查看最近一次提交涉及的文件

# 确认远程状态
git status
# 可能显示：Your branch and 'origin/main' have diverged
```

#### 步骤 2：初始化 Git LFS

```bash
git lfs install
```

#### 步骤 3：使用 migrate 重写历史提交

这是关键步骤。`git lfs migrate import` 会扫描已有的提交历史，将匹配的文件转换为 LFS 指针文件。

```bash
# 将已提交历史中的所有 .onnx 文件转为 LFS 管理
# --include 指定要转换的文件模式
# --everything 表示重写所有分支的历史
git lfs migrate import --include="*.onnx" --everything
```

执行过程输出示例：

```
Sorting commits: ..., done.
Rewriting commits: 100% (2/2), done.
  main  744d941 -> affca25
Updating refs: ..., done.
Checkout: ..., done.
```

> **注意**：此命令会重写提交历史，所有受影响的 commit hash 都会改变。

#### 步骤 4：验证 LFS 追踪状态

```bash
# 查看当前被 LFS 管理的文件列表
git lfs ls-files
```

预期输出：

```
2e3b5ec371 - models/zipformer-bilingual-98590b7e/decoder-epoch-99-avg-1.onnx
8fa764187a - models/zipformer-bilingual-98590b7e/encoder-epoch-99-avg-1.int8.onnx
1ed689c5ed - models/zipformer-bilingual-98590b7e/joiner-epoch-99-avg-1.int8.onnx
```

#### 步骤 5：确认 .gitattributes 已生成

```bash
cat .gitattributes
# 输出：*.onnx filter=lfs diff=lfs merge=lfs -text
```

如果 `.gitattributes` 没有被自动创建，手动执行：

```bash
git lfs track "*.onnx"
```

#### 步骤 6：Force Push 到远程

由于历史被重写，本地和远程分支已经分叉，必须使用 `--force` 推送：

```bash
git push --force origin main
```

> **安全提示**：
> - Force push 会覆盖远程分支历史，仅在你确认远程没有其他人基于旧历史工作的情况下使用。
> - 如果是多人协作仓库，请先通知团队成员，推送后其他人需要重新 clone 或执行 `git fetch origin; git reset --hard origin/main`。

---

### 场景 C：大文件已推送到远程（历史已污染）

大文件已经进入远程仓库历史，即使后续用 LFS 也无法解决，因为远程已经存储了原始大文件。

#### 方案一：使用 git lfs migrate 清理远程（推荐）

```bash
# 1. 初始化 LFS 并追踪目标文件类型
git lfs install
git lfs track "*.onnx"
git add .gitattributes
git commit -m "chore: 将 .onnx 文件迁移到 Git LFS"

# 2. 重写所有分支和所有历史
git lfs migrate import --include="*.onnx" --everything

# 3. Force push
git push --force origin main
```

#### 方案二：使用 BFG Repo-Cleaner（适合超大仓库）

BFG 比 `git filter-repo` 快 10-720 倍，适合历史很长的大型仓库。

```bash
# 1. 安装 BFG（Windows 可用 scoop）
scoop install bfg

# 2. 克隆一份裸仓库（mirror clone）
git clone --mirror https://github.com/username/repo.git repo-clean

# 3. 用 BFG 将超过 100MB 的文件转为 LFS
cd repo-clean
bfg --convert-to-git-lfs '*.onnx' --no-blob-protection

# 4. 清理并强制推送
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push --force origin main
```

---

## 四、常用 Git LFS 命令速查

| 命令 | 说明 |
|---|---|
| `git lfs install` | 在当前用户的全局 Git 配置中安装 LFS hooks |
| `git lfs track "*.ext"` | 添加文件模式到 LFS 追踪（写入 .gitattributes） |
| `git lfs untrack "*.ext"` | 取消追踪某种文件类型 |
| `git lfs ls-files` | 列出当前被 LFS 管理的所有文件 |
| `git lfs status` | 显示待推送的 LFS 文件状态 |
| `git lfs log` | 查看 LFS 传输日志 |
| `git lfs fetch` | 手动下载 LFS 文件内容（clone 后若大文件缺失时执行） |
| `git lfs pull` | 下载 LFS 文件并检出到工作区 |
| `git lfs push` | 手动推送 LFS 文件到远程 |
| `git lfs migrate import --include="PATTERN" --everything` | 将已有历史中的匹配文件转为 LFS |
| `git lfs migrate export --include="PATTERN" --everything` | 将 LFS 文件从历史中还原为普通文件 |

---

## 五、.gitattributes 配置详解

执行 `git lfs track` 后生成的 `.gitattributes` 示例：

```gitattributes
*.onnx filter=lfs diff=lfs merge=lfs -text
```

各字段含义：

| 字段 | 说明 |
|---|---|
| `*.onnx` | 匹配所有 .onnx 文件 |
| `filter=lfs` | 提交/检出时通过 LFS 过滤器处理 |
| `diff=lfs` | diff 时使用 LFS 专用比较方式 |
| `merge=lfs` | 合并时使用 LFS 处理 |
| `-text` | 标记为非文本文件，避免行尾转换 |

---

## 六、团队协作注意事项

### 6.1 协作者克隆仓库后

协作者执行 `git clone` 时，Git LFS 会自动下载大文件。但需要确保他们本地也安装了 git-lfs：

```bash
# 协作者需要执行
git lfs install
git clone https://github.com/username/repo.git
# clone 过程中会自动下载 LFS 文件
```

### 6.2 协作者拉取更新后 LFS 文件缺失

如果 clone 后大文件显示为指针文本而非实际内容：

```bash
git lfs fetch --all
git lfs pull
```

### 6.3 .gitattributes 必须提交

`.gitattributes` 文件必须随仓库提交，这样所有协作者 clone 后 LFS 规则自动生效。不要将其加入 `.gitignore`。

---

## 七、常见问题排查

### Q1：push 仍然报大文件错误

```bash
# 检查是否有遗漏的大文件
git lfs ls-files --all

# 确认 .gitattributes 是否已提交
git log --oneline -- .gitattributes

# 如果 .gitattributes 未提交
git add .gitattributes
git commit -m "chore: 添加 LFS 追踪规则"
```

### Q2：clone 后大文件变成了文本指针

```bash
# 确认 git-lfs 已安装
git lfs version

# 重新拉取 LFS 文件
git lfs fetch --all
git lfs checkout
```

### Q3：想查看 LFS 占用的本地缓存空间

```bash
# 查看 LFS 缓存大小
git lfs env

# 清理不再需要的 LFS 缓存
git lfs prune
```

### Q4：想取消 LFS 追踪某种文件

```bash
# 取消追踪（不影响已推送的文件）
git lfs untrack "*.onnx"

# 手动编辑 .gitattributes 删除对应行
# 然后提交变更
git add .gitattributes
git commit -m "chore: 取消 .onnx 的 LFS 追踪"
```

---

## 八、GitHub 的存储限制参考

| 限制项 | 限制值 |
|---|---|
| 单个文件大小 | 100 MB |
| 仓库推荐大小 | < 5 GB |
| 仓库硬限制 | 5 GB（超出可能被封禁） |
| LFS 免费存储 | 1 GB（超出需购买存储包） |
| LFS 免费带宽 | 每月 1 GB |

> 如果模型文件总量较大，可以考虑在 GitHub 仓库 Settings → Billing 中购买额外的 LFS 存储包（$5/月/50 GB）。

---

## 九、本项目实际操作回顾

以下是本项目从遇到问题解决的完整命令序列：

```bash
# 1. 确认 git-lfs 已安装
git lfs version

# 2. 初始化 LFS
git lfs install

# 3. 重写历史，将 .onnx 文件转为 LFS 管理
git lfs migrate import --include="*.onnx" --everything

# 4. 验证
git lfs ls-files

# 5. Force push（因历史被重写，必须 force）
git push --force origin main
```

执行完毕后，`.onnx` 模型文件将通过 LFS 存储，不再受 GitHub 100 MB 限制，仓库可正常推送和克隆。
