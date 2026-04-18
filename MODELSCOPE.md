# 魔搭创空间部署（本仓库）

本项目的入口是根目录的 `app.py`，依赖见 `requirements.txt`。创空间会从仓库拉代码并安装依赖后启动应用。

## 1. 在魔搭创建创空间并拿到 Git 地址

在 [ModelScope 创空间](https://www.modelscope.cn) 创建 Studio，选择从 Git 同步（或按平台向导创建空仓库）。记下平台给出的 **HTTPS 克隆地址**（形如 `https://www.modelscope.cn/studios/<用户>/<仓库>.git`）。

## 2. 本地推送本仓库（推荐：不把令牌写进仓库）

在**本机**项目目录执行（把占位符换成你的创空间地址与 [访问令牌](https://www.modelscope.cn/my/myaccesstoken)）：

```bash
git lfs install
# 仅首次：添加魔搭远程（令牌只出现在本机命令行，勿提交到任何文件）
git remote add modelscope https://oauth2:<你的令牌>@www.modelscope.cn/studios/<用户>/<仓库>.git
# 若控制台给出的是 http://…，以控制台为准，把 https 改成 http 即可。
git push -u modelscope main
```

若你默认分支是 `master`，把上面 `main` 改成 `master`，或与创空间默认分支保持一致。

**安全**：不要把 `oauth2:...@` 整段 URL 写进 README、issue 或提交历史；令牌泄露后请立即在魔搭后台作废并换新。

## 3. 创空间环境变量（与 `.env.example` 一致）

在创空间 **环境变量 / 密钥** 配置里填写（不要提交 `.env` 到 Git，本仓库已在 `.gitignore` 中忽略）：

| 变量 | 说明 |
|------|------|
| `BASE_URL` | 主通道 OpenAI 兼容网关 Base URL |
| `API_KEY` | 主通道 API Key / Bearer |
| `MODEL` | 主通道模型名 |
| `QA_MODEL` | 可选；小节问答、关联问答默认 `ecnu-max`（见 `.env.example`） |
| `LLM_PARALLEL_*` | 可选；并行第二通道（DeepSeek 等） |
| `GRADIO_SERVER_NAME` | **线上必设** `0.0.0.0`，否则外网无法访问 |
| `GRADIO_IFRAME_SAFE` | 可选 `1`：在 iframe 里禁用易炸布局（默认在路径含 `/studio_service/` 时自动开启） |
| `PORT` | 一般 `7860`；若平台注入 `PORT` 则以其为准（`app.py` 会读取） |
| `REQUEST_TIMEOUT_S` / `MAX_RETRIES` | 可选 |

应用启动逻辑见 `app.py` 末尾：`GRADIO_SERVER_NAME`、`PORT` / `GRADIO_SERVER_PORT` 已被读取。

## 4. 数据与持久化

运行时生成的用户数据在本地 `data/` 目录（已被 `.gitignore` 忽略）。创空间实例重启后，若未挂载持久卷，**`data/` 可能丢失**；若魔搭提供持久化目录，请把该路径通过平台文档中的方式映射到应用可写目录（需按平台说明自行对接）。

## 5. 与官方「最小示例」的差异

官方示例只有几行 `gr.Interface`；本仓库是完整 Gradio 应用，**无需**再替换为示例代码，只要仓库根目录保留 `app.py` 且启动命令为运行该文件即可（多数创空间默认 `python app.py`）。

## 6. 故障排查

- **白屏**：浏览器无痕模式或 F12 控制台看报错；确认 `GRADIO_SERVER_NAME=0.0.0.0`。
- **依赖版本**：若平台预装 Gradio 与 `requirements.txt` 冲突，以创空间「指定 requirements」或锁定版本为准。
- **429 / 限流**：可降低 `FIRST_SUBMIT_PASSES`、`TEEN_EXPAND_MAX_WORKERS`，或关闭 `FIRST_SUBMIT_OVERLAP`，见 `.env.example` 注释。
