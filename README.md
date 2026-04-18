---
title: BreakYourStudy
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: "4.44.0"
app_file: app.py
pinned: false
license: Apache License 2.0
---

# BreakYourStudy（课程作品 / Gradio）

创空间说明文档：<https://www.modelscope.cn/docs/%E5%88%9B%E7%A9%BA%E9%97%B4%E5%8D%A1%E7%89%87>

## 本地运行

1. 安装依赖

```bash
py -m pip install -r requirements.txt
```

2. 配置环境变量（OpenAI 兼容接口）

复制 `.env.example` 为 `.env` 并填写：

- `BASE_URL`: 兼容 OpenAI 的网关地址（例：`https://api.openai.com/v1` 或你的代理网关）
- `API_KEY`: 你的密钥（不要提交到仓库）
- `MODEL`: 模型名

3. 启动

```bash
py app.py
```

打开 `http://127.0.0.1:7860`。

> 说明：若未配置 `BASE_URL / API_KEY / MODEL`，生成按钮会提示缺少配置；此时仍可浏览界面与“正在学习/我的资料”等模块，但无法调用大模型生成内容。

### 首屏「开始生成」较慢时

信息页第一次提交会**串行**调用：书单多轮优化 + 大纲多轮优化（默认各 2 轮，即至少 4 次模型请求），因此首轮容易感觉慢。

- **`FIRST_SUBMIT_PASSES`**（默认 `2`）：改为 `1` 可明显缩短首次等待，代价是书单/大纲的二次润色减少，质量可能略降。在 `.env` 中设置，例如 `FIRST_SUBMIT_PASSES=1`。
- **`FIRST_SUBMIT_OVERLAP`**（默认关闭）：在 `FIRST_SUBMIT_PASSES>=2` 时，**书单第 2 轮起的润色**与**完整大纲生成（基于书单第 1 轮 JSON）**会**并行**执行，墙钟时间约缩短为「书单第 1 轮 + max(书单后续轮, 大纲全部轮)」；若最终书单与第 1 轮 JSON 不一致，会**多一次**轻量对齐请求。并发可能触发 API 限流，需自行在 `.env` 中写 `FIRST_SUBMIT_OVERLAP=1` 开启。
- **`WARMUP_ON_START`**：设为 `1` 时，进程启动后会尝试发一次极短 completion 预热连接（需已配置 API；失败则静默忽略）。

## 回归测试（harness）

离线契约测试（不调用大模型）：

```bash
py -m pytest -q
```

在线评测（会调用大模型，需先配置 `.env`）：

```bash
set HARNESS_MODE=online
py -m tests.harness_runner
```

在线评测 + LLM-as-judge 对比（baseline passes=1 vs multi passes=2）：

```bash
set HARNESS_MODE=online
set HARNESS_JUDGE=1
py -m tests.harness_runner
```

## 常见问题

### 运行后“无法和大模型对话”

这通常是因为 `.env` 没有创建或没有被读取。请确认：

1) 项目根目录存在 `d:\\break_yourStudy\\.env`（不是 `.env.example`）

2) `.env` 里至少有 3 行：

```env
BASE_URL=https://api.deepseek.com
API_KEY=你的key
MODEL=deepseek-chat
```

3) 重新启动 `py app.py`

## 魔搭创空间发布要点

- 入口：`app.py`
- 依赖：`requirements.txt`
- 密钥：在创空间的 **环境变量/密钥** 面板填写 `BASE_URL` / `API_KEY` / `MODEL`，不要把 `.env` 上传。
- 绑定地址：创空间需对外监听时，设置环境变量 `GRADIO_SERVER_NAME=0.0.0.0`（本地默认 `127.0.0.1`）。

#### Clone with HTTP

```bash
git clone https://www.modelscope.cn/studios/chengming614/break-restudy.git
```
