# 猜词游戏 · 压力测试
# ---------------------------------------------------------------------------
# 目标：
#   模拟大量用户同时涌入猜词，观察是否卡顿/崩溃，重点看：
#     1. 服务器响应延迟（P50 / P95 / P99 / max）
#     2. 内存占用（服务端 RSS + 客户端视角）
#     3. 并发处理能力（吞吐 QPS、错误率）
#   发现问题 → 记录现象 → 回改代码 → 复测对比。
#
# 用法：
#   python stress_test.py --url http://127.0.0.1:8791 --mode http   # 打真实 HTTP
#   python stress_test.py --mode local                             # 只压游戏内核（不过 HTTP）
#   python stress_test.py --users 200 --rounds 5                   # 200 并发 × 5 轮
# ---------------------------------------------------------------------------
import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

NAMES = ["路人甲", "弹幕侠", "夜猫子", "打赏王", "路过的风", "小透明", "沙发选手",
         "手速怪", "佛系观众", "键盘侠", "alice", "bob", "carol", "dave", "eve"]


# ---------------------------------------------------------------- 指标容器
class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat = []           # 每次请求延迟 ms
        self.err = defaultdict(int)
        self.ok = 0
        self.fail = 0
        self.status = defaultdict(int)

    def add(self, ms, ok, err="", code=0):
        with self.lock:
            self.lat.append(ms)
            if ok:
                self.ok += 1
            else:
                self.fail += 1
                self.err[err or "unknown"] += 1
            if code:
                self.status[code] += 1

    def report(self, elapsed):
        lat = sorted(self.lat)
        n = len(lat) or 1

        def pct(p):
            return lat[min(n - 1, int(n * p))] if lat else 0.0

        return {
            "requests": len(lat),
            "ok": self.ok, "fail": self.fail,
            "qps": round(len(lat) / elapsed, 1) if elapsed else 0,
            "p50_ms": round(pct(0.50), 2),
            "p95_ms": round(pct(0.95), 2),
            "p99_ms": round(pct(0.99), 2),
            "max_ms": round(lat[-1], 2) if lat else 0,
            "mean_ms": round(statistics.fmean(lat), 2) if lat else 0,
            "errors": dict(self.err),
            "status": dict(self.status),
        }


# ---------------------------------------------------------------- 词库准备
def load_words(n=4000):
    """从引擎词表取一批常用词当压测语料（真实词，走 similarity 热路径）。"""
    from engine import Engine
    e = Engine.get()
    common = set()
    for p in (os.path.join(BASE, "vectors.npz"),
              os.path.join(os.path.dirname(BASE), "02_处理中", "vectors.npz")):
        if os.path.exists(p):
            try:
                import numpy as np
                z = np.load(p)
                common = set(z["blob"].tobytes().decode("utf-8").split("\x00"))
                break
            except Exception:
                pass
    pool = [w for w in e.words
            if 2 <= len(w) <= 3 and w not in common or (len(w) == 2 and w in common)]
    pool = [w for w in pool if 2 <= len(w) <= 3]
    random.shuffle(pool)
    return pool[:n] or e.words[:n]


# ---------------------------------------------------------------- HTTP 打点
def http_post(url, body, timeout=15):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ms = (time.perf_counter() - t0) * 1000
            return ms, True, "", r.status
    except urllib.error.HTTPError as ex:
        ms = (time.perf_counter() - t0) * 1000
        return ms, False, f"http{ex.code}", ex.code
    except Exception as ex:
        ms = (time.perf_counter() - t0) * 1000
        return ms, False, type(ex).__name__, 0


def http_get(url, timeout=15):
    # 中文昵称必须百分号编码，否则 urllib 直接抛 UnicodeEncodeError
    # （踩过：/state?viewer=观众 全量报错，实为客户端 bug 不是服务端问题）
    from urllib.parse import urlsplit, urlunsplit, quote
    sp = urlsplit(url)
    safe = "/:?=&%"
    url = urlunsplit((sp.scheme, sp.netloc.encode("idna").decode() if sp.netloc else "",
                      quote(sp.path, safe=safe),
                      quote(sp.query, safe=safe + "+%"),
                      sp.fragment))
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
            return (time.perf_counter() - t0) * 1000, True, "", r.status
    except Exception as ex:
        return (time.perf_counter() - t0) * 1000, False, type(ex).__name__, 0


# ---------------------------------------------------------------- 场景
def scenario_http(url, users, rounds, batch, words, m):
    """并发观众持续灌入。每轮每人发 batch 条（batch>1 时走 /danmaku/batch 模拟礼物代猜）。"""
    endpoint = url.rstrip("/") + ("/danmaku/batch" if batch > 1 else "/danmaku")
    barrier = threading.Barrier(users)
    errors_seen = defaultdict(int)

    def worker(uid):
        rng = random.Random(uid * 7919 + 13)
        name = NAMES[uid % len(NAMES)] + str(uid)
        barrier.wait()
        for _ in range(rounds):
            items = [[name, rng.choice(words)] for _ in range(batch)]
            if batch > 1:
                ms, ok, err, code = http_post(endpoint, {"items": items})
            else:
                ms, ok, err, code = 0, True, "", 0
                for it in items:
                    a, ok2, e2, c2 = http_post(endpoint, {"player": it[0], "word": it[1]})
                    ms += a
                    ok, err, code = ok2, e2, c2
                    if not ok2:
                        break
            m.add(ms, ok, err, code)
            if not ok:
                errors_seen[err] += 1

    ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return time.perf_counter() - t0


def scenario_local(users, rounds, batch, words, m):
    """只压游戏内核：跳过 HTTP，测 game.submit / batch_submit 的纯吞吐与延迟。"""
    from engine import Engine
    from wordpool import WordPool
    from game import Game
    e = Engine.get()
    pool = WordPool(e)
    game = Game(e, pool, require_likes=False)
    barrier = threading.Barrier(users)

    def worker(uid):
        rng = random.Random(uid * 104729 + 7)
        name = NAMES[uid % len(NAMES)] + str(uid)
        barrier.wait()
        for _ in range(rounds):
            items = [(name, rng.choice(words)) for _ in range(batch)]
            t0 = time.perf_counter()
            game.batch_submit(items)      # 内部一次抢锁
            ms = (time.perf_counter() - t0) * 1000
            m.add(ms, True, "", 0)

    ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    el = time.perf_counter() - t0
    st = game.state()
    m.extra = {"board": st["stats"]["board"], "guesses": st["stats"]["guesses"],
               "unique": st["stats"]["unique"]}
    return el


def poll_mix(url, users, rounds, words, m):
    """混合压测：观众灌词 + 观众狂刷 /state 和 /feed（模拟前端轮询风暴）。"""
    n_poller = users // 2
    # barrier 必须是**实际参与线程数**（users 个灌词 + n_poller 个轮询），
    # 否则 Barrier 永远等不齐 → 全体死锁（踩过一次）
    barrier = threading.Barrier(users + n_poller)

    def guesser(uid):
        rng = random.Random(uid * 31337 + 3)
        name = NAMES[uid % len(NAMES)] + str(uid)
        barrier.wait()
        for _ in range(rounds):
            ms, ok, err, code = http_post(
                url.rstrip("/") + "/danmaku", {"player": name, "word": rng.choice(words)})
            m.add(ms, ok, err, code)

    def poller(uid):
        rng = random.Random(uid * 15485863 + 11)
        barrier.wait()
        for _ in range(rounds):
            url2 = url.rstrip("/") + ("/state?viewer=观众" if uid % 2 else "/feed?since=0&limit=200")
            ms, ok, err, code = http_get(url2)
            m.add(ms, ok, err, code)

    ts = []
    for i in range(users):
        ts.append(threading.Thread(target=guesser, args=(i,), daemon=True))
    for i in range(n_poller):
        ts.append(threading.Thread(target=poller, args=(10_000 + i,), daemon=True))
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return time.perf_counter() - t0


# ---------------------------------------------------------------- 内存
def server_rss(url):
    """读**服务端进程**内存。

    ⚠️ 不要用 tasklist 汇总所有 python.exe —— 那会把压测客户端自己那份
    800MB 词表算进去，看起来像服务端泄漏 +778MB（踩过一次，纯误判）。
    正确姿势：问服务端自己要（GET /health，由 psutil / psapi 读自身 RSS）。
    """
    try:
        ms, ok, err, code = http_get(url.rstrip("/") + "/health")
        if not ok:
            return {"error": f"health {err}"}
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=10) as r:
            j = json.loads(r.read())
        return {"rss_mb": j.get("rss_mb"), "pid": j.get("pid"),
                "board": j.get("board"), "feeds": j.get("feeds"),
                "guess_log": j.get("guess_log"), "threads": j.get("threads")}
    except Exception as ex:
        return {"error": str(ex)}


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8791")
    ap.add_argument("--mode", default="http", choices=["http", "local", "mix"])
    ap.add_argument("--users", type=int, default=50)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--batch", type=int, default=1)
    args = ap.parse_args()

    print("加载语料…", flush=True)
    words = load_words(4000)
    print(f"语料 {len(words)} 词 | 模式={args.mode} 并发={args.users} 轮数={args.rounds} "
          f"每轮批量={args.batch}\n", flush=True)

    m = Metrics()
    m.extra = {}
    rss0 = server_rss(args.url) if args.mode != "local" else {}

    t0 = time.perf_counter()
    if args.mode == "http":
        el = scenario_http(args.url, args.users, args.rounds, args.batch, words, m)
    elif args.mode == "mix":
        el = poll_mix(args.url, args.users, args.rounds, words, m)
    else:
        el = scenario_local(args.users, args.rounds, args.batch, words, m)
    wall = time.perf_counter() - t0

    rss1 = server_rss(args.url) if args.mode != "local" else {}
    rep = m.report(el)
    total_words = rep["requests"] * args.batch

    print("=" * 62)
    print(f"【压测结果】模式={args.mode}  并发={args.users}  时长={el:.2f}s")
    print("=" * 62)
    print(f"  请求数        {rep['requests']}  （每请求 {args.batch} 词 → 约 {total_words} 词）")
    print(f"  成功 / 失败   {rep['ok']} / {rep['fail']}   错误率 "
          f"{(rep['fail']/max(1,rep['requests']))*100:.2f}%")
    print(f"  吞吐 QPS      {rep['qps']}  （≈{total_words/max(el,0.001):.0f} 词/秒）")
    print(f"  延迟 P50      {rep['p50_ms']} ms")
    print(f"  延迟 P95      {rep['p95_ms']} ms")
    print(f"  延迟 P99      {rep['p99_ms']} ms")
    print(f"  延迟 max      {rep['max_ms']} ms")
    print(f"  延迟 mean     {rep['mean_ms']} ms")
    if rep["errors"]:
        print(f"  错误分布      {rep['errors']}")
    if rep["status"]:
        print(f"  HTTP 状态     {rep['status']}")
    if rss0 or rss1:
        print(f"  服务端内存(前)  {rss0}")
        print(f"  服务端内存(后)  {rss1}")
        a = rss0.get("rss_mb"); b = rss1.get("rss_mb")
        if a and b:
            print(f"  服务端内存增量  {b-a:+.1f} MB")
    if m.extra:
        print(f"  内核状态      {m.extra}")
    print("=" * 62)

    # 判定：给明确的健康结论
    verdict = []
    p95 = rep["p95_ms"]
    err_rate = rep["fail"] / max(1, rep["requests"])
    if err_rate > 0.01:
        verdict.append(f"❌ 错误率 {err_rate*100:.1f}% 过高")
    if p95 > 500:
        verdict.append(f"❌ P95 {p95}ms 过大（>500ms）")
    elif p95 > 200:
        verdict.append(f"⚠️ P95 {p95}ms 偏高（>200ms）")
    else:
        verdict.append(f"✅ P95 {p95}ms 健康")
    if rss0.get("rss_mb") and rss1.get("rss_mb"):
        d = rss1["rss_mb"] - rss0["rss_mb"]
        verdict.append("✅ 服务端内存无异常增长" if d < 100 else f"⚠️ 服务端内存 +{d}MB 偏高")
    print("  结论          " + "；".join(verdict))

    out = {"mode": args.mode, "users": args.users, "rounds": args.rounds,
           "batch": args.batch, "elapsed_s": round(el, 3), "wall_s": round(wall, 3),
           "report": rep, "rss_before": rss0, "rss_after": rss1, "extra": m.extra}
    path = os.path.join(BASE, "stress_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  结果已写入 {path}")


if __name__ == "__main__":
    main()
