# LASR 前端 — 听间 · 本地语音工作台

基于 Vue 3 + TypeScript + Vite 的纯静态前端。按住讲话、松手成文，所有音频采集在浏览器本地完成，通过 WebSocket 流式发送到后端识别。

---

## 目录结构

```
前端/
├── src/
│   ├── main.ts              # Vue 应用入口
│   ├── App.vue              # 根组件
│   ├── style.css            # 全局样式
│   ├── audio/               # 音频采集与处理
│   │   ├── capture.ts       # MediaRecorder / AudioWorklet 采集
│   │   ├── capture.worklet.ts  # AudioWorklet 处理器
│   │   ├── clock.ts         # 音频时钟同步
│   │   ├── fir.ts           # FIR 重采样滤波器
│   │   └── pipeline.ts      # 采集管线（采集→重采样→PCM 帧）
│   ├── components/          # Vue 组件
│   │   ├── HoldControls.vue # 按住讲话控件
│   │   ├── PairCard.vue     # 配对卡片
│   │   ├── StudioIcon.vue   # 应用图标
│   │   └── TranscriptList.vue  # 转写结果列表
│   ├── composables/
│   │   └── useStudio.ts     # 核心业务逻辑组合式函数
│   ├── protocol/            # 前后端通信协议
│   │   ├── audio.ts         # 音频帧编码
│   │   ├── messages.ts      # 消息类型定义
│   │   └── schema.ts        # 协议校验
│   ├── services/            # 后端连接服务
│   │   ├── api.ts           # HTTP API 封装
│   │   └── connection.ts    # WebSocket 连接管理
│   └── state/               # 状态管理
│       ├── input.ts         # 输入状态（按键、录音）
│       └── rounds.ts        # 轮次管理（识别结果）
├── tests/
│   ├── unit/                # Vitest 单元测试
│   ├── fixtures.ts          # 测试夹具
│   └── setup.ts             # 测试环境初始化
├── scripts/
│   └── tool.mjs             # 工具脚本（统一缓存隔离）
├── index.html               # SPA 入口 HTML
├── vite.config.ts           # Vite 构建与开发服务器配置
├── vitest.config.ts         # Vitest 测试配置
├── tsconfig.json            # TypeScript 配置
└── package.json             # 依赖与版本约束
```

---

## 环境要求

| 项目 | 版本 |
|------|------|
| Node.js | **≥ 22.12.0**（`engines.node` 强制约束） |
| 浏览器 | 支持 `getUserMedia`、`AudioWorklet`、`Secure Context` 的现代浏览器 |
| 后端服务 | 需要先启动后端（见 `后端/README.md`） |

---

## 一、依赖安装

### 方式一：使用 npm（标准方式）

```powershell
cd 前端
npm install
```

### 方式二：使用项目工具脚本（缓存隔离）

工具脚本会将 npm 缓存、临时目录、Playwright 浏览器等全部隔离到项目根目录的 `.cache/frontend/`，不污染用户 home 目录。

```powershell
node scripts/tool.mjs npm install
```

---

## 二、开发模式

开发模式下前端使用 Vite 开发服务器，通过代理将 API 和 WebSocket 请求转发到后端。

### 前置条件

1. **后端已在本地运行**（默认 `localhost:8765`）：
   ```powershell
   cd 后端
   .venv\Scripts\python.exe -m lasr serve --host localhost --port 8765 --dev-localhost --no-console
   ```

2. **HTTPS 证书已生成**（浏览器音频 API 要求 Secure Context）：

   使用 `mkcert` 生成本地受信任证书（推荐）：
   ```powershell
   # 安装 mkcert（如未安装）
   winget install FiloSottile.mkcert
   # 刷新 PATH 后执行
   mkcert -install
   
   # 在 前端/ 目录生成证书
   cd 前端
   mkcert -cert-file .cert.pem -key-file .cert-key.pem localhost 127.0.0.1 ::1
   ```

   > `mkcert` 会自动将根 CA 安装到系统信任存储，浏览器无需手动接受警告。
   > 如需局域网设备访问，在 `mkcert` 命令末尾追加局域网 IP（如 `192.168.1.100`）。IP 变化后需重新生成证书。

### 启动开发服务器

```powershell
cd 前端
npx vite
```

或使用工具脚本：

```powershell
node scripts/tool.mjs vite
```

**访问地址：**

| 设备 | 地址 |
|------|------|
| 本机 | `https://localhost:5173` |
| 局域网设备 | `https://<本机IP>:5173` |

> Vite 配置了 `server.host: '0.0.0.0'`，默认监听所有网卡。

### 代理配置

Vite 开发服务器会自动代理以下请求到后端：

| 路径 | 转发目标 | 说明 |
|------|----------|------|
| `/api/*` | `http://localhost:8765` | HTTP API（配对、状态） |
| `/ws` | `ws://localhost:8765` | WebSocket（音频流、识别结果） |

代理设置了 `changeOrigin: true`，确保 Host 头与后端匹配。后端在开发模式下会放宽 loopback 的 Origin 校验。

---

## 三、生产构建

构建产物输出到 `前端/dist/`，后端默认从此目录提供静态文件。

### 执行构建

```powershell
cd 前端

# 方式一：直接构建（跳过类型检查）
npx vite build

# 方式二：先类型检查再构建（推荐）
node scripts/tool.mjs build
```

`tool.mjs build` 会先运行 `vue-tsc --noEmit` 进行类型检查，通过后再执行 `vite build`。

### 构建配置

- 输出目录：`dist/`
- 目标：ES2022
- 不生成 sourcemap
- Worker 格式：ES Module

### 使用后端提供静态文件

构建完成后，直接启动后端即可同时提供 API 和静态文件：

```powershell
cd 后端
.venv\Scripts\python.exe -m lasr serve ^
    --host 0.0.0.0 --port 8765 ^
    --tls-cert .cache\backend\tls\server\server-chain.pem ^
    --tls-key .cache\backend\tls\server\server-key.pem ^
    --allowed-host <你的IP>:8765 ^
    --allowed-origin https://<你的IP>:8765 ^
    --no-console
```

访问 `https://<后端IP>:8765` 即可使用完整应用。

---

## 四、预览构建产物

本地预览生产构建结果（用于部署前验证）：

```powershell
cd 前端
npx vite preview
```

或使用工具脚本：

```powershell
node scripts/tool.mjs vite preview
```

预览服务器配置：
- 地址：`0.0.0.0:4173`
- 同样使用 `.cert.pem` / `.cert-key.pem` 提供 HTTPS

---

## 五、自动化测试

```powershell
cd 前端

# 运行全部单元测试
npx vitest run

# 使用工具脚本（缓存隔离）
node scripts/tool.mjs vitest run

# 带覆盖率
npx vitest run --coverage
# 覆盖率报告输出到 coverage/
```

测试配置：
- 测试框架：Vitest
- 运行环境：jsdom
- 测试文件：`tests/unit/**/*.test.ts`
- 覆盖率提供者：v8

---

## 六、类型检查

```powershell
cd 前端

# Vue TypeScript 类型检查
npx vue-tsc --noEmit

# 或使用工具脚本
node scripts/tool.mjs typecheck
```

TypeScript 配置要点：
- 目标：ES2022
- 严格模式：开启
- 未使用变量/参数：报错
- 索引访问检查：开启（`noUncheckedIndexedAccess`）

---

## 七、工具脚本说明

`scripts/tool.mjs` 是一个统一的工具启动器，所有子进程的缓存和临时目录都被隔离到 `.cache/frontend/`。

**用法：**

```powershell
node scripts/tool.mjs <命令> [参数]
```

**可用命令：**

| 命令 | 作用 |
|------|------|
| `npm <args>` | 运行 npm（缓存隔离） |
| `vite [args]` | 启动 Vite 开发服务器 |
| `vitest [args]` | 运行测试 |
| `typecheck` | 运行 `vue-tsc --noEmit` |
| `build` | 先类型检查，再构建 |
| `playwright [args]` | 运行 Playwright |

---

## 八、HTTPS 与局域网访问

### 为什么开发模式也需要 HTTPS

浏览器的 `getUserMedia`（麦克风访问）和 `AudioWorklet`（音频处理）API 要求 **Secure Context**。只有 `https://localhost` 和 `https://127.0.0.1` 被视为安全上下文；使用 `http://<局域网IP>` 会导致这些 API 不可用。

### 获取正确的局域网 IP

Windows 上不能直接取 `Get-NetIPAddress` 首条结果——VMware / Hyper-V 等虚拟网卡会排在前面。需按实际网络接口过滤：

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.InterfaceAlias -match 'WLAN|以太网' -and $_.PrefixOrigin -eq 'Dhcp' } |
  Select-Object -ExpandProperty IPAddress
```

> IP 变化后需用 `mkcert` 重新生成证书。

### 手机端首次访问

1. 手机与电脑连接同一局域网
2. 手机浏览器访问 `https://<电脑IP>:5173`
3. 接受一次自签证书警告（如使用 mkcert 则无需此步）
4. 授权麦克风权限后即可使用

---

## 九、常见问题

### Q: 浏览器报"无法可靠确认松手采样边界"

通常是 HTTP 访问导致 `window.isSecureContext` 为 `false`，音频 API 不可用。确保使用 `https://` 访问。

### Q: 配对失败，提示"配对响应异常"

1. 确认后端已启动并打印了配对口令
2. 确认 Vite 代理配置正确（`/api` → `localhost:8765`）
3. 检查浏览器控制台是否有 `fetch` 相关异常

### Q: 局域网设备无法访问

1. 确认 `vite.config.ts` 的 `server.host` 为 `'0.0.0.0'`
2. 确认证书 SAN 包含当前局域网 IP
3. 确认防火墙允许 5173 端口

### Q: 构建后访问后端返回 404

后端默认从 `前端/dist/` 提供静态文件。确认：
1. 已执行 `npx vite build` 生成 `dist/` 目录
2. 后端启动时 `--static-dir` 指向正确路径
