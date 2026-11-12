# 币安跟单「熬鹰资本」操作监控 · GitHub Actions 近实时版

原理：币安带单员操作接口没有推送/webhook 机制，只能轮询。本方案用 GitHub Actions
每 5 分钟触发一次任务，每次任务内脚本**连续轮询 4 分钟（每 30 秒查一次）**后退出，
由下一个定时任务无缝接力 —— 等效 7×24 每 30 秒检查，新操作约 30 秒内推送到手机。

文件：
- `binance_lead_monitor.py`  监控脚本（纯 Python 3 标准库，无第三方依赖）
- `.github/workflows/monitor.yml`  GitHub Actions 定时接力配置
- `state.json`               状态文件（记录已通知到哪条；删除后会重推最近 20 条）

## 部署步骤（约 5 分钟）

1. 在 GitHub 新建一个 **Public（公开）** 仓库
   （公开仓库 Actions 分钟数免费不限量；Bark key 放 Secrets 不会暴露）
2. 上传三个文件：
   - `binance_lead_monitor.py`
   - `state.json`（用本地这份已初始化的，避免首跑推送 20 条历史）
   - `.github/workflows/monitor.yml`（注意目录结构要一致）
3. 仓库 Settings → Secrets and variables → Actions → New repository secret：
   - Name: `BARK_KEY`
   - Value: `v6xHSoTaFcSnBprc8pKU5d`
4. 到 Actions 页确认 `binance-copy-monitor` 工作流已启用；可点
   `Run workflow` 手动跑一次验证
5. 手动触发后 30 秒内若推送失败才需要检查；无新记录时是静默的。
   想立刻验证推送，可临时把 state.json 里的 lastOrderTime 改小一点再手动跑

## 可调参数
- 检查间隔 / 接力时长：改 workflow 里 `--loop 30 --duration 240`
  （cron 间隔须 ≥ duration + 约 60 秒余量）
- 换带单员：仓库 Secrets 加 `PORTFOLIO_ID` 和 `LEAD_NAME`，
  或直接改脚本顶部默认值
- 换推送 key：改 `BARK_KEY` secret
- 推送失败会自动在下一次轮询时重试，不会漏报

## 注意事项
- GitHub 的 cron 以 UTC 计时且可能有 1-3 分钟调度延迟（接力机制会自动兜住空窗）
- 想完全停掉：Actions 页面 Disable workflow 即可
- 脚本也可脱离 GitHub 在任何海外服务器运行：
  `python3 binance_lead_monitor.py --loop 30`（国内机器需设 HTTPS_PROXY）
