#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安跟单带单员「熬鹰资本」最新操作记录监控 → Bark 手机推送
独立运行，不依赖 ZCode / 浏览器。

用法:
  单次检查(适合 cron / 云函数调用):
      python3 binance_lead_monitor.py
  常驻循环模式(适合小服务器):
      python3 binance_lead_monitor.py --loop 300
  限时循环模式(适合 GitHub Actions 接力: 连续轮询 240 秒后退出,
  由下一个定时任务接力, 等效 7x24 每 30 秒检查一次):
      python3 binance_lead_monitor.py --loop 30 --duration 240

环境变量(可选):
  HTTPS_PROXY   例如 http://127.0.0.1:7897 (仅在需代理的网络环境下设置;
                海外服务器直连即可, 不要设置)
  MONITOR_STATE 状态文件路径, 默认脚本同目录 state.json
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ================= 配置区 =================
# 均可用同名环境变量覆盖(推荐在 CI 中用 Secrets 注入 BARK_KEY)
PORTFOLIO_ID = os.environ.get("PORTFOLIO_ID", "5075281354358777856")
LEAD_NAME = os.environ.get("LEAD_NAME", "熬鹰资本")
BARK_KEY = os.environ.get("BARK_KEY", "v6xHSoTaFcSnBprc8pKU5d")
BARK_GROUP = os.environ.get("BARK_GROUP", "Binance跟单监控")
BARK_SOUND = os.environ.get("BARK_SOUND", "bell")
# 备用推送通道 ntfy.sh(Bark 服务器拦截数据中心 IP 时自动降级)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "binance-aoying-9x7k2")
API_URL = ("https://www.binance.com/bapi/futures/v1/friendly/future/"
           "copy-trade/lead-portfolio/order-history")
PAGE_SIZE = 20                                  # 每次拉取条数
# ==========================================

HEADERS = {
    "Content-Type": "application/json",
    "clienttype": "web",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.binance.com/zh-CN/copy-trading/lead-details/"
               + PORTFOLIO_ID,
}


def default_state_path():
    return os.environ.get(
        "MONITOR_STATE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))


def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"lastOrderTime": 0, "notified": []}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def auto_commit(state_path):
    """推送成功后立即把状态提交回仓库(设置 AUTO_COMMIT=1 时生效),
    防止任务被取消时丢失进度导致下一轮重复推送。"""
    if os.environ.get("AUTO_COMMIT") != "1":
        return
    import subprocess
    repo = os.path.dirname(os.path.abspath(state_path))
    for cmd in (
        ["git", "add", os.path.basename(state_path)],
        ["git", "-c", "user.name=monitor-bot",
         "-c", "user.email=monitor-bot@users.noreply.github.com",
         "commit", "-m", "update state [skip ci]"],
        ["git", "pull", "--rebase", "--autostash", "origin", "main"],
        ["git", "push"],
    ):
        subprocess.run(cmd, cwd=repo, capture_output=True)


def http_post_json(url, payload, timeout=20):
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        handler = urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS, method="POST")
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_orders():
    data = http_post_json(API_URL, {
        "portfolioId": PORTFOLIO_ID, "pageNum": 1, "pageSize": PAGE_SIZE})
    if data.get("code") != "000000":
        raise RuntimeError("API 返回异常: %s" % data.get("message"))
    return data["data"].get("list") or []


ACTION_CN = {
    ("SELL", "SHORT"): "卖出开空",   # 做空方向加仓
    ("BUY", "SHORT"): "买入平空",    # 空头止盈/止损
    ("BUY", "LONG"): "买入开多",     # 做多方向加仓
    ("SELL", "LONG"): "卖出平多",    # 多头止盈/止损
}


def _fmt_value(usd):
    if usd >= 10000:
        return "%.2f 万 USDT" % (usd / 10000)
    return "%.0f USDT" % usd


def fmt_order(o):
    t = time.strftime("%m-%d %H:%M:%S", time.localtime(o["orderTime"] / 1000))
    act = ACTION_CN.get((o.get("side"), o.get("positionSide")),
                        "%s %s" % (o.get("side"), o.get("positionSide")))
    return ("%s %s %s 永续，数量 %g %s，均价 %g，约 %s"
            % (t, act, o["symbol"], o["executedQty"],
               o.get("baseAsset", ""), o["avgPrice"],
               _fmt_value(o["avgPrice"] * o["executedQty"])))


def bark_push(title, body):
    """优先 Bark 加密推送; Bark 被拦截时自动降级 ntfy.sh 备用通道。"""
    try:
        _bark_push(title, body)
    except Exception as e:
        print("Bark 推送失败(%s), 改用 ntfy 备用通道" % e)
        _ntfy_push(title, body)


def _ntfy_push(title, body):
    """ntfy.sh 备用推送(JSON 发布到根路径, topic 放在 JSON 内)"""
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps({"topic": NTFY_TOPIC, "title": title,
                         "message": body, "tags": ["bell"]},
                        ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if '"event":"message"' not in raw:
        raise RuntimeError("ntfy 推送失败: %.120s" % raw)
    print("ntfy 备用通道推送成功")


def _bark_push(title, body):
    """明文推送(App 内已关闭加密): POST 参数, 405 时降级 GET。"""
    qs = urllib.parse.urlencode({
        "title": title, "body": body,
        "group": BARK_GROUP, "sound": BARK_SOUND})
    base_url = "https://api.day.app/" + BARK_KEY
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/120.0 Safari/537.36")
    method = "POST"
    try:
        req = urllib.request.Request(base_url, data=qs.encode("utf-8"),
                                     headers={"User-Agent": ua},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code != 405:
            raise
        method = "GET"
        with urllib.request.urlopen(
                urllib.request.Request(
                    base_url + "?" + qs, headers={"User-Agent": ua}),
                timeout=15) as resp:
            raw = resp.read().decode("utf-8", "replace")
    print("Bark %s 响应: %.200s" % (method, raw.strip()))
    if raw.strip() == "ok":
        # "ok" 非 bark-server 标准响应(正常返回 JSON code:200),
        # 疑似被前置 CDN/WAF 拦截吞掉, 按失败处理走 ntfy 兜底
        raise RuntimeError("可疑响应 ok(疑似被拦截)")
    try:
        r = json.loads(raw)
    except ValueError:
        raise RuntimeError("Bark 响应异常: %.120s" % raw)
    if r.get("code") != 200:
        raise RuntimeError("Bark 推送失败: %s" % r)


def _okey(o):
    return "%s-%s-%s" % (o["orderTime"], o["symbol"], o["side"])


def _minimal(o):
    return {k: o.get(k) for k in ("orderTime", "symbol", "baseAsset",
                                  "side", "positionSide",
                                  "executedQty", "avgPrice")}


def check_once(state_path):
    state = load_state(state_path)
    orders = fetch_orders()
    if not orders:
        print("接口未返回记录(可能被限流), 保持状态不变")
        return
    changed = False
    last = state.get("lastOrderTime", 0)
    seen = set(state.get("notified", []))
    fresh = [o for o in orders
             if o["orderTime"] > last and _okey(o) not in seen]
    pending = list(state.get("barkPending", []))

    if not fresh and not pending:
        newest = max(o["orderTime"] for o in orders)
        print("暂无新操作, 最新记录时间:",
              time.strftime("%Y-%m-%d %H:%M:%S",
                            time.localtime(newest / 1000)))
        return

    fresh.sort(key=lambda o: o["orderTime"])
    if fresh:
        lines = [fmt_order(o) for o in fresh]
        print("发现 %d 条新操作:" % len(lines))
        for l in lines:
            print(" ", l)

    # --- Bark 通道: 新操作 + 历史未送达(补投)的一起尝试 ---
    bark_batch = list({_okey(o): _minimal(o)
                       for o in (pending + fresh)}.values())
    bark_batch.sort(key=lambda o: o["orderTime"])
    bark_ok = False
    suffix = "（含补投）" if pending else ""
    title = "%s 新操作 x%d%s" % (LEAD_NAME, len(bark_batch), suffix)
    try:
        _bark_push(title, "; ".join(fmt_order(o) for o in bark_batch))
        bark_ok = True
        print("Bark 推送成功(含补投共 %d 条)" % len(bark_batch))
    except Exception as e:
        print("Bark 推送失败: %s" % e)

    if bark_ok:
        state["barkPending"] = []
        state["barkLastOrderTime"] = bark_batch[-1]["orderTime"]
        changed = True
    else:
        state["barkPending"] = bark_batch[-20:]

    # --- 新操作至少要有一条通道送达 ---
    if fresh:
        if not bark_ok:
            try:
                _ntfy_push("%s 新操作 x%d" % (LEAD_NAME, len(fresh)),
                           "; ".join(fmt_order(o) for o in fresh))
            except Exception as e:
                print("ntfy 备用通道也失败(%s), 下轮整体重试" % e)
                save_state(state_path, state)
                auto_commit(state_path)
                return
        for o in fresh:
            seen.add(_okey(o))
        state["notified"] = sorted(seen)[-500:]
        state["lastOrderTime"] = max(o["orderTime"] for o in orders)
        changed = True

    if changed:
        save_state(state_path, state)
        print("状态已更新")
        auto_commit(state_path)
    elif not bark_ok and pending:
        # 仅补投未成功: 只存本地, 不提交仓库
        save_state(state_path, state)


def main():
    state_path = default_state_path()
    args = sys.argv[1:]
    if args and args[0] == "--test-push":
        bark_push("推送通道测试", "这是一条测试推送(不改变监控状态)")
        print("测试推送流程完成")
        return
    if args and args[0] in ("--loop", "-l"):
        interval = int(args[1]) if len(args) > 1 else 300
        duration = 0
        if "--duration" in args:
            duration = int(args[args.index("--duration") + 1])
        deadline = time.time() + duration if duration > 0 else None
        print("循环模式启动, 每 %d 秒检查一次%s (Ctrl+C 退出)"
              % (interval, ", 限时 %d 秒" % duration if duration else ""))
        while deadline is None or time.time() < deadline:
            try:
                check_once(state_path)
            except Exception as e:
                print("检查出错:", e)
            # 睡眠但不越过截止时间
            if deadline is None:
                time.sleep(interval)
            else:
                time.sleep(min(interval, max(0, deadline - time.time())))
    else:
        check_once(state_path)


if __name__ == "__main__":
    main()
