# Binance 带单员操作监控 · 手机秒推（Bark 加密）

监控任意币安合约跟单带单员的「最新操作记录」，有新开仓/平仓时 **约 30 秒内**
推送到 iPhone/安卓手机。免费运行在 GitHub Actions 上，7×24 无人值守，
关闭自己电脑也照常工作。

原理：GitHub 的内置定时器不可靠（实测 24 小时只触发 2 次），所以用免费的
[cron-job.org](https://cron-job.org) 每 5 分钟从外部触发一次 Action，
Action 内部以 30 秒间隔连续轮询 4 分钟，形成接力，等效 30 秒级监控。

## 功能

- 每 30 秒检查一次带单员最新操作（开多/开空/平仓，含价格、数量、金额）
- Bark 推送（AES-128-CBC 加密，服务器看不到内容）
- 推送成功立即提交 state.json，任务被取消也不丢进度、不重复推送
- 同时监控多个带单员：复制 workflow 多份、各用不同 `PORTFOLIO_ID` 与状态文件即可

## 复制部署（约 10 分钟）

### 1. 复制本仓库文件到你的仓库
把 `binance_lead_monitor.py`、`.github/workflows/monitor.yml` 复制到
**你自己的公开仓库**（公开仓库 Actions 免费不限量）。

### 2. 改配置
编辑 `binance_lead_monitor.py` 顶部默认值，或用环境变量/Secrets 覆盖：

| 配置 | 说明 | 怎么获取 |
|---|---|---|
| `PORTFOLIO_ID` | 带单员 ID | 带单页 URL `lead-details/` 后面那串数字 |
| `LEAD_NAME` | 推送里显示的名字 | 随意起 |
| `BARK_KEY` | Bark 设备 key | Bark App 首页示例 URL 里那段 |
| `BARK_AES_KEY` | 加密密钥(16 字符) | 自己随机编 16 个字母数字 |

**Bark 加密设置**：Bark App → 推送加密 → 加密设置 → 算法选 AES128，
KEY 填你上面编的 `BARK_AES_KEY`（两端一致即可，IV 随机生成会随请求传递）。

> 建议：fork 后把 key 只放仓库 Settings → Secrets，workflow 已支持
> `BARK_KEY` 注入；不想加密可把 `BARK_AES_KEY` 设为空并自行删改推送函数。

### 3. 初始化状态
在仓库根目录创建 `state.json`，先手动跑一次脚本让它写入，或直接：
```json
{"lastOrderTime": 0, "notified": []}
```
（首次运行会把最近 20 条当新记录推给你，当作测试也行；想跳过就把
`lastOrderTime` 设为最近一条记录的毫秒时间戳。）

### 4. 外部定时触发（关键，别依赖 GitHub 自带 cron）
1. GitHub → Settings → Developer settings → Fine-grained personal access tokens
   → 新建：只授权本仓库，权限给 **Actions: Read and write**
2. 注册 [cron-job.org](https://cron-job.org)（免费），创建任务：
   - URL：`https://api.github.com/repos/<你的用户名>/<仓库名>/actions/workflows/monitor.yml/dispatches`
   - Method：**POST**，Body：`{"ref":"main"}`
   - Headers：`Authorization: Bearer <你的token>`、
     `Accept: application/vnd.github+json`、`Content-Type: application/json`
   - 频率：**Every 5 minutes**
   - 建议开启失败通知（token 到期会邮件提醒，fine-grained token 默认 90 天）

### 5. 验证
仓库 Actions 页 → `binance-copy-monitor` → Run workflow → 勾选
**test_push** 跑一次，手机应立刻收到「推送通道测试」。

## 本地运行（可选）
```bash
python3 binance_lead_monitor.py            # 单次检查
python3 binance_lead_monitor.py --loop 30  # 常驻模式
python3 binance_lead_monitor.py --test-push  # 测试推送
```
国内网络需先 `export HTTPS_PROXY=...`。

## 常见问题
- **收不到推送**：用 `--test-push` 单独测；Actions 日志会打印 Bark
  真实响应，若被拦截会自动在下一轮(30 秒后)重试，不会丢消息
- **收到重复推送**：确保仓库里 state.json 正常提交（Actions 需要
  Settings → Actions → General → Workflow permissions 选 **Read and write**）
- **想停止**：cron-job.org 删除任务 + Actions 页 Disable workflow

---
*仅供学习研究。跟单有风险，本工具只是信息提醒，不构成投资建议。*
