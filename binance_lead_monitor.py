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
# Bark 推送加密(AES-128-CBC): 与 App 内「推送加密」设置的 KEY 保持一致
BARK_AES_KEY = os.environ.get("BARK_AES_KEY", "7Kp2mQ9xT4wB8nE5")
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


SIDE_CN = {"BUY": "买入", "SELL": "卖出"}
POSSIDE_CN = {"SHORT": "开空/平多", "LONG": "开多/平空",
              "BOTH": "单向持仓"}


def fmt_order(o):
    t = time.strftime("%m-%d %H:%M:%S", time.localtime(o["orderTime"] / 1000))
    side = SIDE_CN.get(o.get("side"), o.get("side"))
    pos = POSSIDE_CN.get(o.get("positionSide"), o.get("positionSide"))
    return ("%s %s %s: %s %s 均价 %g USDT, 数量 %g %s, 价值约 %.0f USDT"
            % (t, side, o["symbol"], pos,
               o.get("type", ""),
               o["avgPrice"], o["executedQty"],
               o.get("baseAsset", ""),
               o["avgPrice"] * o["executedQty"]))


def bark_push(title, body):
    """加密推送: AES-128-CBC 加密 JSON 后作为 ciphertext 发送,
    IV 每次随机生成并随请求传递, App 端用保存的 KEY 解密。
    依赖系统 openssl 命令(Linux/macOS/Git Bash 均自带)。"""
    import base64
    import random
    import string
    import subprocess
    payload = json.dumps({"title": title, "body": body,
                          "group": BARK_GROUP, "sound": BARK_SOUND},
                         ensure_ascii=False)
    iv = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    # openssl 手动指定的 -K/-iv 需要 hex 编码, 而非 ASCII
    hex_key = BARK_AES_KEY.encode("utf-8").hex()
    hex_iv = iv.encode("utf-8").hex()
    p = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-K", hex_key, "-iv", hex_iv],
        input=payload.encode("utf-8"), capture_output=True, check=True)
    ciphertext = base64.b64encode(p.stdout).decode("ascii")
    qs = urllib.parse.urlencode({"ciphertext": ciphertext, "iv": iv})
    base_url = "https://api.day.app/" + BARK_KEY
    # 部分 IP 段(如云服务器)POST 会被拒 405, 自动降级 GET
    r = None
    try:
        req = urllib.request.Request(base_url, data=qs.encode("ascii"),
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            r = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code != 405:
            raise
        with urllib.request.urlopen(base_url + "?" + qs, timeout=15) as resp:
            r = json.loads(resp.read().decode("utf-8"))
    if r.get("code") != 200:
        raise RuntimeError("Bark 推送失败: %s" % r)


def check_once(state_path):
    state = load_state(state_path)
    orders = fetch_orders()
    if not orders:
        print("接口未返回记录(可能被限流), 保持状态不变")
        return
    last = state.get("lastOrderTime", 0)
    fresh = [o for o in orders if o["orderTime"] > last]
    # 去掉已通知过的(按 orderTime+symbol 去重)
    seen = set(state.get("notified", []))
    fresh = [o for o in fresh
             if "%s-%s-%s" % (o["orderTime"], o["symbol"], o["side"]) not in seen]
    if not fresh:
        newest = max(o["orderTime"] for o in orders)
        print("暂无新操作, 最新记录时间:",
              time.strftime("%Y-%m-%d %H:%M:%S",
                            time.localtime(newest / 1000)))
        return
    fresh.sort(key=lambda o: o["orderTime"])
    lines = [fmt_order(o) for o in fresh]
    print("发现 %d 条新操作:" % len(lines))
    for l in lines:
        print(" ", l)
    try:
        bark_push("%s 新操作 x%d" % (LEAD_NAME, len(lines)),
                  "; ".join(lines))
        print("Bark 推送成功")
    except Exception as e:
        print("Bark 推送失败(下次仍会重试该批):", e)
        return  # 推送失败不更新状态, 下次重试
    for o in fresh:
        seen.add("%s-%s-%s" % (o["orderTime"], o["symbol"], o["side"]))
    state["notified"] = sorted(seen)[-500:]
    state["lastOrderTime"] = max(o["orderTime"] for o in orders)
    save_state(state_path, state)
    print("状态已更新")


def main():
    state_path = default_state_path()
    args = sys.argv[1:]
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
