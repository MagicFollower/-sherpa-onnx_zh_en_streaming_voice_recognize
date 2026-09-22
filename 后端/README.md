# LASR 后端 — 本地离线实时语音识别服务

基于 FastAPI + sherpa-onnx 的中英双语流式语音转写后端。所有推理在本地 CPU 完成，不联网、不上报任何数据。

---

## 目录结构

```
后端/
├── lasr/                  # 核心 Python 包
│   ├── __main__.py        # python -m lasr 安全入口（Windows spawn 兼容）
│   ├── cli.py             # CLI 子命令定义（serve / prepare-models / certificates）
│   ├── app.py             # FastAPI 组合根（lifespan 管理）
│   ├── config.py          # Settings 数据类与校验
│   ├── routes.py          # HTTP / WebSocket 路由
│   ├── security.py        # Host / Origin 白名单中间件
│   ├── auth.py            # 配对口令与会话管理
│   ├── coordinator.py     # 连接协调与识别调度
│   ├── worker.py          # 识别器子进程封装
│   ├── supervisor.py      # 子进程生命周期监控
│   ├── recognizer.py      # sherpa-onnx 流式识别器
│   ├── protocol.py        # 前后端消息协议
│   ├── artifacts.py       # 模型制品约束与校验
│   ├── prepare_models.py  # 模型下载与完整性验证
│   ├── tls.py             # 项目私有 CA / 服务器证书生成
│   ├── console.py         # 终端配对口令显示
│   ├── state.py           # 会话状态
│   └── audio.py           # 音频帧解析
├── tests/                 # pytest 测试套件
├── tools/
│   └── prepare_env.py     # 虚拟环境一键准备脚本
└── pyproject.toml         # 包元数据、依赖、工具配置
```

---

## 环境要求

| 项目 | 版本 |
|------|------|
| Python | **3.11.x**（`requires-python = ">=3.11,<3.12"`） |
| 操作系统 | Windows（主要目标）/ Linux / macOS |
| 模型文件 | `models/zipformer-bilingual-98590b7e/`（见下方"模型准备"） |

---

## 一、依赖安装

### 方式一：使用项目脚本（推荐）

脚本会自动在 `后端/.venv/` 创建虚拟环境、安装全部运行时与开发依赖，并将 pip 缓存隔离到项目根目录的 `.cache/backend/`，不污染用户目录。

```powershell
cd 后端
python tools\prepare_env.py
```

> 若已存在 `requirements.lock`，可加 `--locked` 从锁文件安装：
> ```powershell
> python tools\prepare_env.py --locked
> ```

### 方式二：手动安装

```powershell
cd 后端
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

**命令解析：**

| 参数 | 作用 |
|------|------|
| `-m pip` | 以模块方式调用 pip，确保使用当前 `.venv` 解释器的 pip |
| `install` | pip 安装子命令 |
| `-e` | **可编辑安装**：不在 `site-packages` 中复制源码，而是创建链接指向 `后端/` 目录，代码改动即时生效无需重装 |
| `"."` | 当前目录，即 `pyproject.toml` 定义的 `lasr-local` 包 |
| `"[dev]"` | 额外安装 `[project.optional-dependencies]` 中 `dev` 组的全部依赖 |

实际安装的包：

- **运行时**（6 个）：fastapi、starlette、uvicorn、websockets、sherpa-onnx、numpy
- **开发**（7 个）：httpx（ASGI 测试客户端）、pytest、pytest-asyncio、pytest-cov、ruff、onnx（模型元数据解析）、cryptography（证书生成）

安装完成后会注册 `lasr` 控制台入口点（→ `lasr.cli:main`），并在 `.venv/Scripts/` 中生成 `lasr.exe`。

### 验证安装

```powershell
cd 后端

# 检查依赖完整性
.venv\Scripts\python.exe -m pip check

# 确认 sherpa-onnx 可用（应输出 1.13.8）
.venv\Scripts\python.exe -c "import sherpa_onnx; print(sherpa_onnx.__version__)"
```

**命令解析：**

| 命令 | 作用 | 预期结果 |
|------|------|----------|
| `pip check` | 遍历所有已安装包，检查依赖声明的版本约束是否都被满足 | 无输出 + 退出码 0 = 依赖完整 |
| `-c "import sherpa_onnx; ..."` | 直接执行 Python 代码，验证 sherpa-onnx 的 C++ 原生扩展可实际加载 | 输出 `1.13.8` |

> `pip check` 只验证依赖关系图，**不验证**原生扩展是否可加载。sherpa-onnx 是本项目唯一的 C++ 原生依赖，安装失败时 pip 可能不报错（纯 Python 回退），但 `import` 会暴露问题（`ModuleNotFoundError` 或 `DLL load failed`）。两条命令互补，缺一不可。

---

## 二、模型准备

模型不随代码自动下载，必须显式执行。模型文件来自 HuggingFace 锁定 commit，下载时逐文件校验长度与 SHA256。

### 下载模型

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr prepare-models
```

**命令解析：**

| 参数 | 作用 |
|------|------|
| `-m lasr` | 以模块方式运行 `lasr` 包，触发 `__main__.py`（先调 `freeze_support()` 防止 Windows 子进程递归启动） |
| `prepare-models` | CLI 子命令，进入模型准备分支 |

**执行流程：**

1. 联网获取 HuggingFace 制品树（锁定 commit `98590b7e`，API 响应限 1MB）
2. 创建暂存目录 `models/zipformer-bilingual-98590b7e.partial/`
3. 逐文件下载 4 个模型文件，每个文件执行三重校验：
   - HTTP `Content-Length` 头与冻结大小比对
   - 字节计数不超限
   - SHA256 摘要（`.onnx` 文件同时校验上游 LFS 摘要与本地摘要一致性）
4. 下载写入 `.partial` 临时文件，完成后 `fsync` 落盘再原子 `rename`
5. 解析 encoder ONNX 的 protobuf 元数据（`decode_chunk_len`、`T`），校验范围
6. 生成 `manifest.json`，对暂存目录执行二次完整校验
7. 暂存目录原子重命名为目标目录
8. 输出 JSON（`revision` + `metadata`）后返回——**不加载 recognizer，不执行推理**

**安全机制：** 只允许 HTTPS 重定向；下载中断只留 `.partial` 文件不会被运行时启用；已存在的已验证目录直接复核，不重复下载（幂等）。

产物位于 `models/zipformer-bilingual-98590b7e/`，包含：

| 文件 | 大小 | 说明 |
|------|------|------|
| `encoder-epoch-99-avg-1.int8.onnx` | ~182 MB | 编码器（int8 量化） |
| `decoder-epoch-99-avg-1.onnx` | ~14 MB | 解码器 |
| `joiner-epoch-99-avg-1.int8.onnx` | ~3.2 MB | 联结器（int8 量化） |
| `tokens.txt` | ~56 KB | 词表 |
| `manifest.json` | — | 制品清单（自动生成） |

### 仅校验已有模型

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr prepare-models --verify-only
```

成功输出 JSON（含 `revision` 和 `metadata`）；校验失败会报错并拒绝启动。

### 下载演示音频（可选）

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr prepare-models --audio
```

音频保存到 `.cache/backend/test-audio/`，仅用于冒烟测试。

---

## 三、启动服务

后端有两种运行模式：**开发模式**（HTTP 明文，仅本机）和**局域网模式**（HTTPS，可局域网设备访问）。

### 模式 A：开发模式（HTTP 明文，仅 localhost）

最简单的启动方式，适合本机开发调试。前端通过 Vite 代理转发请求到后端，后端无需对外开放。

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr serve --host localhost --port 8765 --dev-localhost --no-console
```

**参数说明：**

| 参数 | 作用 |
|------|------|
| `--host localhost` | 绑定环回地址 |
| `--port 8765` | 监听端口 |
| `--dev-localhost` | 启用开发模式：HTTP 明文、放宽 Origin 校验为 loopback、cookie 不使用 Secure 和 `__Host-` 前缀 |
| `--no-console` | 不从 stdin 读取；配对口令仍会在终端打印一次 |

启动后终端会打印配对口令：

```
LASR_PAIRING_CODE=xxxxxxxx
```

前端开发服务器会代理 `/api` 和 `/ws` 请求到 `localhost:8765`，因此开发模式下后端不需要证书。

### 模式 B：局域网 HTTPS 模式（生产 / 手机访问）

局域网设备（如手机）需要通过 HTTPS 访问，因为浏览器的 `getUserMedia` / `AudioWorklet` API 要求 Secure Context。

#### 步骤 1：生成证书

使用后端内置的证书生成工具创建项目私有 CA 和服务器证书：

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr certificates --san localhost --san 192.168.1.100 --days 90
```

- `--san` 可重复指定 DNS 名称或 IP 地址（填写实际要访问的局域网 IP）
- `--days` 证书有效期（默认 90 天，最长 365 天）
- 产物输出到 `.cache/backend/tls/`：
  ```
  .cache/backend/tls/
  ├── ca/
  │   ├── ca-key.pem      # CA 私钥（0600 权限）
  │   └── ca-cert.pem     # CA 证书（分发给客户端信任）
  ├── server/
  │   ├── server-key.pem   # 服务器私钥（0600 权限）
  │   └── server-chain.pem # 服务器证书链（服务器证书 + CA 证书）
  └── certificate-metadata.json  # 证书元信息（含 SHA256 指纹）
  ```

#### 步骤 2：启动 HTTPS 服务

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr serve ^
    --host 0.0.0.0 --port 8765 ^
    --tls-cert .cache\backend\tls\server\server-chain.pem ^
    --tls-key .cache\backend\tls\server\server-key.pem ^
    --allowed-host localhost:8765 ^
    --allowed-host 192.168.1.100:8765 ^
    --allowed-origin https://localhost:8765 ^
    --allowed-origin https://192.168.1.100:8765 ^
    --no-console
```

**参数说明：**

| 参数 | 作用 |
|------|------|
| `--host 0.0.0.0` | 监听所有网卡，允许局域网访问 |
| `--tls-cert` | 服务器证书链路径 |
| `--tls-key` | 服务器私钥路径 |
| `--allowed-host` | Host 头白名单（可重复），必须包含端口 |
| `--allowed-origin` | Origin 头白名单（可重复），scheme 必须为 https |
| `--cpu-threads` | 推理线程数（默认 1） |
| `--static-dir` | 前端构建产物目录（默认 `前端/dist`），后端从此提供静态文件 |

#### 步骤 3：客户端信任 CA（可选）

后端生成的 CA 不会自动安装到系统信任存储。如需消除浏览器证书警告：

- **iOS**：将 `ca-cert.pem` 传到手机 → 设置 → 已下载描述文件 → 安装 → 证书信任设置 → 启用完全信任
- **Android**：设置 → 安全 → 加密与凭据 → 安装证书 → CA 证书 → 选择 `ca-cert.pem`
- **桌面浏览器**：接受一次自签警告后 `window.isSecureContext` 即为 `true`，功能可正常使用

> **提示**：开发阶段推荐使用 `mkcert` 工具（自动安装根 CA 到系统信任存储），可省去手动导入步骤。

---

## 四、自动化测试

```powershell
cd 后端

# 运行全部测试（默认排除需要真实模型的 real_asr 标记用例）
.venv\Scripts\python.exe -m pytest tests/ -v

# 带覆盖率报告
.venv\Scripts\python.exe -m pytest tests/ --cov=lasr --cov-report=html
# 覆盖率报告输出到 .cache/backend/coverage-html/index.html
```

---

## 五、代码检查

```powershell
cd 后端

# Ruff Lint
.venv\Scripts\python.exe -m ruff check lasr/ tests/

# 自动修复
.venv\Scripts\python.exe -m ruff check --fix lasr/ tests/
```

---

## 六、CLI 命令速查

后端通过 `python -m lasr <子命令>` 提供三个子命令：

| 子命令 | 用途 |
|--------|------|
| `serve` | 启动识别服务（单 Uvicorn 进程） |
| `prepare-models` | 联网下载模型 / 仅校验本地模型 |
| `certificates` | 生成项目私有 CA 和服务器证书 |

### `serve` 完整参数

```
--host TEXT           绑定地址（默认 localhost）
--port INT            监听端口（默认 8765）
--dev-localhost       开发模式：HTTP 明文 + loopback 放宽
--no-console          不读取 stdin
--allowed-host TEXT   Host 白名单（可重复）
--allowed-origin TEXT Origin 白名单（可重复）
--model-dir PATH      模型目录（默认 models/zipformer-bilingual-98590b7e/）
--static-dir PATH     前端静态文件目录（默认 前端/dist）
--cpu-threads INT     推理线程数（默认 1）
--tls-cert PATH       TLS 证书链路径（生产模式必填）
--tls-key PATH        TLS 私钥路径（生产模式必填）
```

### `prepare-models` 参数

```
--model-dir PATH    模型目标目录
--audio             同时下载官方演示音频
--verify-only       仅校验本地模型，不联网
```

### `certificates` 参数

```
--output PATH    输出目录（默认 .cache/backend/tls）
--san TEXT       SAN 主机名或 IP（必填，可重复）
--days INT       证书有效期天数（默认 90，最大 365）
```

---

## 七、配对流程简介

1. 后端启动时在终端打印 `LASR_PAIRING_CODE=xxxxxxxx`
2. 前端页面要求用户输入该口令
3. 前端 POST `/api/pair` 提交口令，后端验证通过后下发会话 cookie
4. 后续请求携带 cookie 完成鉴权；WebSocket 连接同样依赖此 cookie

---

## 八、常见问题

### Q: 启动报"本地模型缺失或校验失败"

模型未下载或文件不完整。执行：

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr prepare-models
```

### Q: 局域网设备访问返回 403

Host 或 Origin 不在白名单中。确保 `--allowed-host` 和 `--allowed-origin` 包含设备的实际访问地址（含端口）。

### Q: 生产模式启动报"必须配置TLS证书链和私钥"

非开发模式下必须提供 `--tls-cert` 和 `--tls-key`。先用 `certificates` 子命令生成证书。

### Q: 虚拟环境损坏如何重建

删除 `.venv` 目录后重新运行 `python tools\prepare_env.py`，或手动 `python -m venv .venv` 后安装依赖。
