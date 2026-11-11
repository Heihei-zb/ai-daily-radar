# AI 行业日报（ai-daily-radar）

每天定时抓取 AI 行业公开信息（中英文），大模型打分筛选并译成中文 → 生成单文件可视化网页 → 推送企业微信。

- 页面：`docs/YYYY-MM-DD.html`，同时覆盖 `docs/index.html` 作为最新一期
- 归档：`docs/archive.html`
- 推送：企业微信机器人 markdown 消息（TOP 5 + 摘要 + 网页链接）

## 目录

```
config/     settings.yaml 运行参数 · sources.yaml 信源 · prompt_*.md 提示词
src/fetch/  rss / hn / arxiv / github 四类适配器
src/        clean 清洗 · rank 打分 · render 渲染 · push_wecom 推送 · deploy 发布 · store 状态
templates/  daily.html.j2 日报页 · archive.html.j2 归档页
docs/       产物，GitHub Pages 从这里发布
data/       state.db 与 raw/ 原始快照
logs/       每日运行日志
```

## 推送通道

`config/settings.yaml` 的 `push.channel`：

| 值 | 说明 |
|---|---|
| `cli`（默认） | 走已授权的企业微信机器人，不需要 webhook；目标会话在 `.env` 的 `WECOM_CHAT_NAME` 指定 |
| `webhook` | 走群机器人地址，需要在 `.env` 填 `WECOM_WEBHOOK_URL` |
| `both` | 两个都发 |

## 接入网页托管（GitHub Pages）

**1. 建仓库**

打开 https://github.com/new ，Repository name 填 `ai-daily-radar`，可见性选 **Public**（免费账号的 Pages 只支持公开仓库），不要勾选任何初始化文件，点 Create repository。

**2. 推送代码**

在项目根目录（本仓库所在文件夹）下执行（把 `<账号>` 换成你的 GitHub 用户名）：

```bash
git remote add origin https://github.com/<账号>/ai-daily-radar.git
git branch -M main
git push -u origin main
```

首次推送会要求登录：密码填 **Personal Access Token**（GitHub → Settings → Developer settings → Personal access tokens → Tokens(classic) → Generate new token，勾选 `repo`），不是账号密码。

**3. 开 Pages**

仓库 → Settings → Pages → Build and deployment → Source 选 **Deploy from a branch** → Branch 选 `main`、目录选 **/docs** → Save。等 1 分钟左右，地址是 `https://<账号>.github.io/ai-daily-radar/`。

**4. 回填地址**

`config/settings.yaml`：

```yaml
site:
  base_url: "https://<账号>.github.io/ai-daily-radar"
```

之后企微消息里就会带「查看完整日报」的链接。

## 常用命令

```bash
python run.py                      # 正式跑：抓取 → 渲染 → 推送（有远端则发布）
python run.py --dry-run            # 预览：不发布不推送
python run.py --dry-run --no-llm   # 没配 key 时验证全链路
python run.py --date 2026-11-10    # 补跑指定日期
python run.py --no-push            # 只更新网页
python run.py --push-only          # 页面不动，把当期简报重发一次
python run.py --force-push         # 当天已推送过也再发一条
```

## 定时

由 WorkBuddy 的定时任务触发，每天 10:00 执行 `python run.py`，需要 WorkBuddy 处于运行状态。
脚本内置当天去重：同一天重复执行只更新网页，不会重复发群消息，补发用 `--force-push`。

如果希望脱离 WorkBuddy、由系统直接调度，双击 `install_task.bat` 可注册一个同时间的 Windows 计划任务（任务名 `AI-Daily-Radar`），二选一即可。

```bash
schtasks /Query /TN "AI-Daily-Radar"      # 查看
schtasks /Delete /TN "AI-Daily-Radar" /F  # 删除
```

## 信源与口径

| 信源 | 方式 | 说明 |
|---|---|---|
| 量子位 | RSS | 中文 AI 媒体，时效好 |
| IT之家 / 雷峰网 / 钛媒体 | RSS + AI 关键词 | 综合科技站，只留命中关键词的条目 |
| OpenAI / Google AI / Google DeepMind | RSS | 官方一手，英文，经大模型翻成中文 |
| Hacker News | Algolia API | 26 小时内 60 分以上的帖子，再按关键词过滤 |
| arXiv | 官方 API | cs.AI / cs.CL / cs.LG，每日取 3 篇，标题与摘要译为中文 |
| GitHub | Search API | 按四个方向检索：汽车数据（OBD / telematics）、新片剪辑（近 150 天新建）、数据分析（text-to-SQL）、高热 AI（topic:llm） |

口径：26 小时时效窗（`app.window_hours`）；`app.language: all` 中英文全收，英文条目由大模型翻译；同一事件多源转载只保留一条；GitHub 项目每类保底 1 个、10 天内不重复；原始快照存 `data/raw/`。

已剔除：机器之心（`/rss` 返回 HTML、首页纯 JS）、36 氪（`/feed` 非 RSS）、Anthropic 官方 RSS（404）。

## 已知限制

- **github.com 页面在部分网络下不可达**，GitHub 榜走 Search API，按主题词检索，不是官方 Trending。
- 没有 `LLM_API_KEY` 时走规则兜底：能分类排序，但摘要就是原文首段、没有「为什么重要」，英文条目保持英文原文。接上 key 后自动翻译并成稿。
- 企微 CLI 消息上限 20480 字节，单条足够；webhook 通道上限 4096 字节，代码按 3500 字节自动拆条。
- 网页只发链接，不直接发文件；链接打不开时本地 `docs/` 里有完整页面。
