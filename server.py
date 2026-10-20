# 猜词游戏 · 本地服务（Flask，零额外依赖：numpy + flask 已在隔离 venv）
# 路由：
#   GET  /              面板页面
#   GET  /state         局状态 JSON（?viewer=昵称 带"我的"成绩；含机器人状态）
#   GET  /feed?since=N  实时弹幕增量流（滚动刷新用；N = 上次拿到的 seq）
#   POST /guess         {player, word} 猜词
#   POST /danmaku       {player, word} —— 与 /guess 等价，抖音弹幕桥单条入口
#   POST /danmaku/batch {items:[[player,word],...]} 批量弹幕（礼物代猜/压测通路）
#   POST /like          点赞 +1（情报双条件里的"赞"）
#   POST /newround      强制开新局 {n_chars?, difficulty?}（演示用）
#   POST /bot           {on, mode, name} 开关随机词机器人
#   GET  /gifts         礼物目录 + 统计（预留接口，仅定义不实现发词）
#   POST /gift          {gift_key, sender, count} 送礼 → 生成代猜订单（预留）
import os
import sys

from flask import Flask, jsonify, request, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from engine import Engine          # noqa: E402
from wordpool import WordPool      # noqa: E402
from game import Game              # noqa: E402
from bot import WordBot            # noqa: E402
from gift import GiftService, GiftEvent, GiftStreamer, TIERS   # noqa: E402

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))

_engine = Engine.get()
_pool = WordPool(_engine)
# 样品期：情报只按时间解锁（require_likes=False）；上抖音改 True 即恢复"时间+赞"双条件
_game = Game(_engine, _pool, require_likes=False)
_bot = WordBot(_game, _engine)
_gifts = GiftService()


# ---------------------------------------------------------------------------
# 礼物代刷：逐条发射器接线（演示层）
# ---------------------------------------------------------------------------
# 为什么必须逐条：原版是一个人一条条发弹幕，礼物只是"买发言次数"。
# 一次性 batch 450 条会让榜单被单人淹没、前端看不到过程、失去节奏感。
# 这里用 GiftStreamer 起后台流，gap 由礼物档位决定（贵的礼物发得慢而准）。
def _gift_word_picker(tier, n, seen):
    """按参考度档给出一条候选词。tier 决定"多大概率往答案近邻里捞"。

    🔴 关键教训（实测踩过）：近邻池过滤太严时**不能直接回落纯随机**，
    否则"精准档"和"噪声档"实测均分一样（31.3 vs 32.6），分级形同虚设。
    正确做法 = **两级降级**：先按档位 min_score 挑；不够就取近邻里
    分数最高的那批（放宽下限），保证贵的礼物一定比便宜的"更相关"。
    """
    import random

    r = _game.round
    if r.status != "running":
        return []
    if random.random() < tier["win_p"]:                 # 贵礼物的小额直中概率
        if r.answer not in seen:
            return [r.answer]
    if random.random() < tier["near_ratio"]:            # 近邻（付费观众也往高分词堆）
        # 只算一次近邻，60 覆盖 95 分档足够
        near = [x for x in _engine.nearest(r.answer, k=60) if x[0] not in seen]
        strict = [w for w, s in near if s >= tier["min_score"] and _bot._is_nice(w)]
        if strict:
            return [random.choice(strict[:24])]
        # ---- 两级降级：取近邻里分数最高的一批（不放 _is_nice，礼物代刷要"相关"优先）
        if near:
            top = max(s for _, s in near)
            loose = [w for w, s in near if s >= max(top - 15.0, 30.0)]
            return [random.choice(loose[:20])]
    for _ in range(120):                # 其余走随机常用词（噪声档 majority 走这里）
        w = _bot._rand_word()
        if w and w not in seen:
            return [w]
    return []


def _gift_emit(player, word):
    """代刷流的单条提交。猜中答案或本轮结束就掐掉该sender 的流——
    不然会出现「已经赢了还在刷屏」的滑稽场面。"""
    res = _game.submit(player, word)
    if res.get("won") or res.get("round_over"):
        who = player[1:] if player.startswith("🎁") else player
        _streamer.stop_sender(who)
    return res


_streamer = GiftStreamer(
    emit=_gift_emit,
    word_picker=_gift_word_picker,
    round_alive=lambda: _game.round.status == "running",
)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/state")
def state():
    out = _game.state(request.args.get("viewer"))
    out["bot"] = _bot.status()
    return jsonify(out)


@app.get("/feed")
def feed():
    """实时弹幕增量流：返回 seq > since 的条目。环形缓冲已滚过则给最新 limit 条。"""
    since = request.args.get("since", default=0, type=int)
    limit = min(max(request.args.get("limit", default=60, type=int), 1), 200)
    items, cur = _game.feed_since(since, limit)
    return jsonify({"items": items, "seq": cur})


@app.post("/guess")
def guess():
    data = request.get_json(silent=True) or {}
    return jsonify(_game.submit(data.get("player"), data.get("word")))


@app.post("/danmaku")
def danmaku():
    """抖音弹幕桥入口：与 /guess 同逻辑，方便直播侧直接投喂。"""
    data = request.get_json(silent=True) or {}
    return jsonify(_game.submit(data.get("player"), data.get("word")))


@app.post("/danmaku/batch")
def danmaku_batch():
    """批量弹幕：礼物代猜（一份礼物刷 N 个词）与压测都走这里。
    body = {"items": [["昵称","词"], ...]}  也接受 [{"player":..,"word":..}, ...]"""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    norm = []
    for it in items:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            norm.append((it[0], it[1]))
        elif isinstance(it, dict):
            norm.append((it.get("player"), it.get("word")))
    if not norm:
        return jsonify({"ok": False, "reason": "items 为空"}), 400
    if len(norm) > 2000:
        return jsonify({"ok": False, "reason": "单批上限 2000"}), 400
    out = _game.batch_submit(norm)
    # 批量响应不逐条回明细（压测时省带宽），需要明细走 ?detail=1
    if not request.args.get("detail"):
        out.pop("results", None)
    return jsonify(out)


@app.post("/like")
def like():
    return jsonify({"likes": _game.like(1)})


@app.post("/newround")
def newround():
    data = request.get_json(silent=True) or {}
    n = data.get("n_chars")
    d = data.get("difficulty")
    n = int(n) if n in (2, 3, 4) else None
    d = d if d in ("简单", "普通", "困难") else None
    r = _game.new_round(n_chars=n, difficulty=d)
    return jsonify({"rid": r.rid, "n_chars": r.n_chars, "difficulty": r.difficulty})


@app.post("/bot")
def bot_ctl():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    name = (data.get("name") or "").strip()[:16]
    if name:
        _bot.name = name
    if mode in ("smart", "random"):
        _bot.mode = mode
    try:
        if "sharp" in data:
            _bot.p_sharp = min(max(float(data["sharp"]), 0.0), 1.0)
        if "win" in data:
            _bot.p_win = min(max(float(data["win"]), 0.0), 1.0)
    except (TypeError, ValueError):
        pass
    if data.get("on"):
        _bot.start()
    else:
        _bot.stop()
    return jsonify(_bot.status())


# ---------- 礼物接口（预留：只定义与裁决，不实现代发词） ----------
@app.get("/answer")
def answer():
    """主持人视图：返回当前 running 局答案+分类（本机派对游戏，主持人需看到词才能控场）。
    观众端 /state 仍对 running 局隐藏答案（猜词游戏设定）。仅 127.0.0.1 可达。"""
    r = _game.round
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"ok": False, "reason": "仅本机主持人可查看"}), 403
    if r.status != "running":
        return jsonify({"ok": False, "reason": "本轮已结束", "answer": r.answer,
                        "category": r.category})
    return jsonify({"ok": True, "rid": r.rid, "answer": r.answer,
                    "category": r.category, "n_chars": r.n_chars,
                    "difficulty": r.difficulty, "mult": r.mult,
                    "likes": r.likes, "remain": max(0, int(_game.round_secs - r.elapsed()))})


@app.get("/gifts")
def gifts():
    return jsonify(_gifts.snapshot())


@app.post("/gift")
def gift_send():
    """收一次送礼事件 → 裁决成代猜订单。
    注意：**本接口不代发词**（按要求预留）。接抖音时由礼物监听器消费 outbox。"""
    data = request.get_json(silent=True) or {}
    ev = GiftEvent(
        gift_key=(data.get("gift_key") or "").strip(),
        sender=(data.get("sender") or "").strip()[:32],
        count=int(data.get("count") or 1),
        raw=data.get("raw"),
    )
    order = _gifts.accept(ev)
    out = order.to_json()
    out["queue"] = _gifts.pending()
    out["implemented"] = False      # 明确告知调用方：代发词逻辑尚未实现
    return jsonify(out)


@app.post("/gift/pop")
def gift_pop():
    """取出待消费的代猜订单（预留；调用方自行决定何时真正发词）。"""
    return jsonify({"orders": [o.to_json() for o in _gifts.pop_orders()]})


# ---- 演示用「点一下就刷屏」：把预留订单真的消费掉（接抖音时删掉本段即可） ----
@app.post("/gift/sim")
def gift_sim():
    """模拟原版「付费礼物代猜」：一次礼物 → **后台逐条**往弹幕流蹦 N 个词。

    与 /gift 的关系：/gift 只做**裁决**（预留接口，不发词）；
    本端点额外起一个 GiftStreamer 流，词由后台线程按档位节奏一条条发出，
    前端的 500ms 增量拉流会自然看到"一个词一个词往上滚"的效果。

    参考度分级：按**每份礼物词数**定档（与未经实测的钻价无关）——
      词多（450 词）→ 买发言量（近邻率 10%，噪声档）
      中量（100/50 词）→ 多而糙（近邻率 28%，粗糙档）
      25 词 → 少而准（近邻率 50%，分下限 65，精准档）
      1 词 → 单词押注（近邻率 68%，6% 直中，押注档）
    """
    data = request.get_json(silent=True) or {}
    key = (data.get("gift_key") or "").strip()
    sender = (data.get("sender") or "路人").strip()[:12] or "路人"
    cnt = max(1, min(int(data.get("count") or 1), 20))

    r = _game.round
    if r.status != "running":
        return jsonify({"ok": False, "reason": "本轮已结束"}), 409

    d = _gifts.gift(key)
    if d is None:
        return jsonify({"ok": False, "reason": "未知礼物"}), 400

    # 道具类礼物分支：当前礼物表已**不含**任何 props（2026-10-15 复核 B3：
    # 画面上的倍率道具系早期虚构，已全部删除）。分支保留作未来兼容，不发词只上系统弹幕。
    if d.kind == "props":
        with _game._lock:
            _game._push_feed("system", f"🎁 {sender} 送出 {d.name}（{d.desc}）")
        return jsonify({"ok": True, "kind": "props", "gift": d.name,
                        "n_words": 0, "job": None})

    order = _gifts.accept(GiftEvent(key, sender, cnt))
    res = _streamer.submit(order, sender_nick=sender, count=1)
    if not res.get("ok"):
        return jsonify({"ok": False, "reason": res.get("reason", "无法发起")}), 409

    tier = TIERS[d.tier]
    with _game._lock:
        _game._push_feed(
            "system",
            f"🎁 {sender} 送出 {d.name} ×{cnt}（{tier['label']}档·{res['job']['total']}词）")
    return jsonify({"ok": True, "kind": "word", "gift": d.name,
                    "tier": d.tier, "tier_label": tier["label"],
                    "n_words": res["job"]["total"], "job": res["job"]})


@app.get("/gift/jobs")
def gift_jobs():
    """正在跑的代刷流（前端用来显示「🎁 张三 正在刷 12/50」）。"""
    return jsonify({"jobs": _streamer.jobs(), "active": _streamer.active()})


@app.post("/gift/stop")
def gift_stop():
    """停止某人的代刷流（sender 省略则停掉全部）。"""
    data = request.get_json(silent=True) or {}
    who = (data.get("sender") or "").strip()
    if who:
        _streamer.stop_sender(who)
    else:
        for j in _streamer.active():
            _streamer.stop_sender(j["sender"])
    return jsonify({"ok": True, "active": _streamer.active()})


@app.get("/health")
def health():
    """健康检查 + 服务端进程内存（压测时看内存增长必须用这个，
    不能靠 tasklist 汇总——那会把压测客户端自己的 800MB 词表也算进去）。"""
    import gc
    import os
    rss_mb = None
    try:
        import psutil
        rss_mb = round(psutil.Process(os.getpid()).memory_info().rss / 1048576, 1)
    except Exception:
        try:
            # 🔴 ctypes 调 Win32 **必须显式声明 argtypes**：不声明时 HANDLE 被按
            #    32 位 int 传，GetCurrentProcess() 返回的 -1 指针被截断 → 调用静默返回 0
            #    → 读出来永远是 0.0MB（踩过，argtypes 一加立刻正常）
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32
            fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.restype = wintypes.BOOL
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                rss_mb = round(c.WorkingSetSize / 1048576, 1)
        except Exception:
            rss_mb = None
    gc.collect()
    return jsonify({
        "ok": True, "pid": os.getpid(),
        "rss_mb": rss_mb,
        "threads": __import__("threading").active_count(),
        "seq": _game._seq,
        "board": len(_game.round.board),
        "feeds": len(_game.feed),
        "guess_log": len(_game.round.guesses),
    })


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8791")))
    ap.add_argument("--serve", default="dev", choices=["dev", "waitress"],
                    help="dev=Flask 开发服务器(演示) / waitress=生产 WSGI(压测与上直播用)")
    ap.add_argument("--threads", type=int, default=32)
    ap.add_argument("--host", default="127.0.0.1")
    # 序20：上抖音开关——开启后三段提示恢复「时间+赞」双条件（原版 A6 实证）。
    # 样品期默认 False（只看时间解锁，方便本地体验）；上直播必须加此 flag。
    ap.add_argument("--require-likes", action="store_true",
                    help="提示解锁要求「时间+赞」双条件（上抖音直播时必加）")
    args = ap.parse_args()
    _game.require_likes = args.require_likes
    if args.require_likes:
        print("🔒 已开启「时间+赞」双条件（上直播模式）")

    if args.serve == "waitress":
        # ⚠️ 单进程多线程（**不要开多进程**）：Game 是进程内单例，多 worker 会各持一份
        #    局状态/榜单/弹幕流 → 观众词会打到不同副本，榜单直接分裂。
        #    需要横向扩展时必须先把状态外置（Redis / DB）。
        from waitress import serve
        print(f"猜词游戏 → http://{args.host}:{args.port}  [waitress, {args.threads} 线程]")
        serve(app, host=args.host, port=args.port, threads=args.threads,
              _quiet=True, max_request_body_size=4 * 1024 * 1024)
    else:
        print(f"猜词游戏样品 → http://{args.host}:{args.port}  [Flask dev, Ctrl+C 停止]")
        print("⚠️ dev server 是演示级性能，压测/上直播请用 --serve waitress")
        app.run(host=args.host, port=args.port, threaded=True)
