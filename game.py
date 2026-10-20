# 游戏本体：局机制 / 榜单去重 / 三档情报 / 名次奖励 / 弹幕源接口
# 设计要点：
#   - 答案从种子词池抽取（分类已知）；提示词由引擎近邻生成（提示三 = 首字）
#   - 情报解锁 = 时间 + 点赞双条件（REQUIRE_LIKES 开关：样品=False，上抖音=True）
#   - 榜单按 (玩家, 词) 组合去重：同一玩家每个词占一行（原版芋泥小薯条同屏
#     第 4/6/9 名实证，2026-10-15 复核 A11）；同一玩家重复同词才回读
#   - 段位 = 每人独立的 (tier, prog) 状态 + 溢出结转晋升（原版结算页
#     「青铜I 73/100 +50 → 白银II 23/200」实证，复核 E4/E5）；
#     不是"总分查表"——floor 累计模型在结算场景会给出错误段位
#   - GuessSource 抽象：本地输入 / 抖音弹幕（后期）都走 game.submit()
#
# 🔴 2026-10-14 压测改造（详见 README「性能」节）：
#   1. guesses / history 改有界 deque —— 原 list 无上限，长局会持续涨内存
#   2. board 用 bisect 增量插入（维护降序不变量），**不再每次 submit 全量 sort**
#      （原实现 O(n log n) × 每次请求 = 高并发下必然塌方）
#   3. 消灭 board.index(e) 的 O(n) 线性扫描 —— 插入位置即名次，直接返回
#   4. intels() 单次算完复用；state() 不再重复调用 intels() 三次
#   5. 新增 feed 流：单调 seq + 环形缓冲，GET /feed?since=N 增量拉取（弹幕滚动刷新）
#   6. 答案向量预缓存进 Round.elapsed 热路径，避免每词重复查表
import bisect
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field

ROUND_SECS = 300          # 一局时长（原版约 5 分钟）
INTEL = [                 # (时刻秒, 需要的赞, 说明)
    (60, 0, "分类"),
    (120, 100, "提示一"),
    (180, 300, "提示二"),
    (240, 800, "提示三"),
]
RANK_REWARD = [200, 50, 25, 12, 6]     # 名次奖（1~5 名，z_award.jpg 直读 +200/+50/+25/+12/+6）
DIFFS = [("简单", 1.0), ("普通", 1.5), ("困难", 2.0)]

FEED_MAX = 300            # 弹幕流环形缓冲长度（防止长局无限涨内存）
GUESS_LOG_MAX = 2000      # 全部提交记录上限（只作留档，不对外暴露）
HISTORY_MAX = 50


@dataclass
class Entry:
    player: str
    word: str
    score: float
    ts: float
    oov: bool = False


@dataclass
class Round:
    rid: int
    answer: str
    category: str
    n_chars: int
    difficulty: str
    mult: float
    started_at: float = field(default_factory=time.time)
    likes: int = 0
    guesses: deque = field(default_factory=lambda: deque(maxlen=GUESS_LOG_MAX))
    board: list = field(default_factory=list)         # 去重后的榜单（Entry，降序）
    board_keys: list = field(default_factory=list)    # 并行列表 [-score, ts, word] 供 bisect
    # 🔴 去重粒度 = (玩家, 词) 组合（2026-10-15 复核 A11：原版同一玩家每个词独立占一行，
    #    芋泥小薯条同屏第 4/6/9 名实证）；words_seen 以组合为键存分数供重复提交 O(1) 回读。
    words_seen: dict = field(default_factory=dict)
    seen_words: set = field(default_factory=set)      # 本局出现过的所有词（提示词排除用）
    status: str = "running"                           # running | won | timeout
    winner: str = ""
    rewards: dict = field(default_factory=dict)
    tier_moves: list = field(default_factory=list)    # 结算页段位对比：[(player, 前, 后), ...]
    hints: dict = field(default_factory=dict)        # {"提示一": word, ...}
    settle_top: list = field(default_factory=list)   # 序14结算页：Top5 段位前后快照
    reveal_at: float = 0.0
    answer_vec: object = None                        # 答案单位向量（热路径预缓存）

    def elapsed(self):
        return time.time() - self.started_at


class Game:
    def __init__(self, engine, pool, require_likes=False, round_secs=ROUND_SECS):
        self.engine = engine
        self.pool = pool
        self.require_likes = require_likes
        self.round_secs = round_secs
        self.round = None
        self.history = deque(maxlen=HISTORY_MAX)   # 已结束的局（简要）
        self.scores = {}           # 玩家累计积分
        self._rid = 0
        self._lock = threading.RLock()   # RLock：batch_submit 持锁后再调 submit 不会自锁
        # 实时弹幕流：单调递增 seq + 环形缓冲 + 玩家积分榜缓存
        self._seq = 0
        self.feed = deque(maxlen=FEED_MAX)
        self._mine = {}            # player -> [ {word,score,oov} ] 本局上榜词
        self._tiers = {}           # player -> {"idx": RANKS下标, "prog": 档内进度}
        self.new_round()

    # ---------- 局管理 ----------
    def new_round(self, n_chars=None, difficulty=None):
        with self._lock:
            if self.round and self.round.status == "running":
                self._close("timeout")
            self._rid += 1
            pool = self.pool.seed_pool(n_chars or random.choice([2, 2, 2, 3]))
            if not pool:
                pool = self.pool.seed_pool(2)
            answer, cat = random.choice(pool)
            if difficulty is None:
                dname, mult = random.choice(DIFFS)
            else:
                dname, mult = next((d, m) for d, m in DIFFS if d == difficulty)
            r = Round(self._rid, answer, cat, len(answer), dname, mult)
            i = self.engine.index.get(answer)
            r.answer_vec = self.engine.unit[i] if i is not None else None
            self.round = r
            self._mine = {}
            self._seq = 0          # 新局重置序号（前端据此判断是否换局）
            self.feed.clear()
            self._push_feed("system", f"第 {r.rid} 局开始 · {r.n_chars} 字 · {r.difficulty}×{r.mult}")
            return r

    def _close(self, status):
        r = self.round
        if r.status != "running":
            return
        r.status = status
        r.reveal_at = time.time()
        if status == "won":
            # A2（2026-10-15 复核）：原版无独立"猜中奖励"，猜中者所得 = 名次奖第 1 名
            r.rewards[r.winner] = r.rewards.get(r.winner, 0) + int(RANK_REWARD[0] * r.mult)
        else:
            for i, e in enumerate(r.board[: len(RANK_REWARD)]):
                r.rewards[e.player] = r.rewards.get(e.player, 0) + int(RANK_REWARD[i] * r.mult)
        # 段位推进（2026-10-15 复核 E4/E5）：每人独立 (tier_idx, prog) 状态，
        # 溢出**完整结转**到下一档（原版结算页实证：青铜I 73/100 +50 → 白银II 23/200）。
        # 注意 gift.rank_of(total_score) 的 floor 累计模型**不能**复现此行为，
        # 必须用状态机；rank_of 仅保留给 gift.py 自测/快照用。
        from gift import RANKS as _RANKS
        for p, v in r.rewards.items():
            before = self._tier_state(p)
            st = self._tiers.setdefault(p, {"idx": 0, "prog": 0})
            st["prog"] += v
            while st["idx"] < len(_RANKS) - 1 and st["prog"] >= _RANKS[st["idx"]].cap:
                st["prog"] -= _RANKS[st["idx"]].cap
                st["idx"] += 1
            after = self._tier_state(p)
            if before != after:
                r.tier_moves.append({"player": p, "before": before, "after": after})
            self.scores[p] = self.scores.get(p, 0) + v
        # 序14：结算页 Top5 快照（player/word/score/reward + 段前→段后；无迁移者前后相同）
        # 🔧 口径修正（2026-10-16 对照 z_over_top.jpg）：原版结算行内显**相似度分**（蜜蜂 100.0%），
        # 名次奖单独显示在段位行尾（+200 金字）——两者并存，不是二选一。
        # score=相似度（行内词后）、reward=名次奖（段位行尾 +N）；累计榜 self.scores 按 reward 累加。
        moves = {m["player"]: m for m in r.tier_moves}
        board_map = {e.player: e for e in r.board}
        for p, v in sorted(r.rewards.items(), key=lambda kv: -kv[1])[:5]:
            e = board_map.get(p)
            m = moves.get(p)
            before = m["before"] if m else self._tier_state(p)
            after = m["after"] if m else self._tier_state(p)
            r.settle_top.append({"player": p, "word": e.word if e else "",
                                 "score": round(e.score, 1) if e else 0,
                                 "reward": v, "before": before, "after": after})
        self.history.append({
            "rid": r.rid, "answer": r.answer, "category": r.category,
            "winner": r.winner, "status": r.status,
            "top": [(e.word, e.score) for e in r.board[:3]],
            "tier_moves": list(r.tier_moves),
            "settle_top": list(r.settle_top),
        })
        tag = "🎉" if status == "won" else "⏰"
        self._push_feed("system", f"{tag} 本轮结束 · 答案「{r.answer}」· " +
                                   (f"{r.winner} 猜中" if r.winner else "无人猜中"))
        # 收尾清理：跑整场直播会连开几百局，words_seen / board_keys / hints 若不清
        # 会跨局累积（实测一局 2 万词约 +2.6MB，几百局就是几百 MB）。
        # 保留 board（结算页要显示 Top）与 guesses 环形缓冲（有上限），只清无界的。
        r.words_seen = {}
        r.seen_words = set()
        r.board_keys = []
        r.guesses.clear()
        r.hints.clear()

    def _tier_state(self, player):
        """读某玩家当前段位（含插值档的 prog 展示）。返回 dict 快照。"""
        from gift import RANKS as _RANKS
        st = self._tiers.get(player) or {"idx": 0, "prog": 0}
        rk = _RANKS[st["idx"]]
        return {"tier": rk.name, "prog": st["prog"], "cap": rk.cap}

    # ---------- 弹幕流 ----------
    def _push_feed(self, kind, text, extra=None):
        """写入弹幕流。调用方须已持锁（或在构造期）。"""
        self._seq += 1
        self.feed.append({
            "seq": self._seq, "ts": time.time(), "kind": kind, "text": text,
            **(extra or {}),
        })

    def feed_since(self, since=0, limit=60):
        """增量拉取：返回 seq > since 的条目。ring 已被覆盖时用 oldest 对齐。"""
        items = list(self.feed)
        if not items:
            return [], self._seq
        oldest = items[0]["seq"]
        if since + 1 < oldest:       # 客户端落后太多（缓冲已滚过）→ 给全量最新
            return items[-limit:], self._seq
        out = [x for x in items if x["seq"] > since]
        return out[-limit:], self._seq

    # ---------- 核心动作 ----------
    def _record(self, e, is_new, rank=None):
        """把一条 Entry 写进 guesses / feed / 榜单（调用方须持锁）。返回名次(1起)。"""
        r = self.round
        r.guesses.append(e)
        self._push_feed("guess", e.word, {
            "player": e.player, "score": e.score, "oov": e.oov, "is_new": is_new,
        })
        if not is_new:
            return None
        r.words_seen[(e.player, e.word)] = e.score   # 组合键：同玩家同词才判重
        r.seen_words.add(e.word)
        # 榜单增量插入：board_keys 保持升序（-score 升序 = score 降序）
        key = (-e.score, e.ts, e.word)
        pos = bisect.bisect_right(r.board_keys, key)
        r.board.insert(pos, e)
        r.board_keys.insert(pos, key)
        if self._mine is not None:
            self._mine.setdefault(e.player, []).append(
                {"word": e.word, "score": e.score, "oov": e.oov})
        return pos + 1

    def submit(self, player, word):
        """一次猜词。返回 dict（ok/score/is_new/rank/won/round_over）。"""
        player = (player or "").strip()[:16] or "匿名"
        word = (word or "").strip()
        if not word:
            return {"ok": False, "reason": "空词"}
        with self._lock:
            r = self.round
            if r.status != "running":
                return {"ok": False, "reason": "本轮已结束", "round_over": True}
            if r.elapsed() >= self.round_secs:
                self._close("timeout")
                return {"ok": False, "reason": "本轮已结束", "round_over": True}
            # 组合去重（2026-10-15 复核 A11）：仅"同玩家+同词"才回读分数（O(1)）。
            # 同词不同玩家 = 新条目各占一行（原版榜单铁证：芋泥小薯条同屏 3 行）。
            prev = r.words_seen.get((player, word))
            if prev is not None:
                self._push_feed("guess", word, {
                    "player": player, "score": prev, "is_new": False,
                })
                return {"ok": True, "score": prev, "is_new": False, "dup_of": player}
            if word == r.answer:
                e = Entry(player, word, 100.0, time.time())
                self._record(e, True)
                r.winner = player
                self._close("won")
                return {"ok": True, "score": 100.0, "is_new": True, "won": True,
                        "winner": player, "round_over": True}
            if not self.engine.has(word):
                score = self.engine.similarity_oov(word, r.answer)
                if score is None:
                    return {"ok": False, "reason": "无法识别这个词"}
                e = Entry(player, word, score, time.time(), oov=True)
                rank = self._record(e, True)
                return {"ok": True, "score": score, "is_new": True, "oov": True,
                        "rank": rank}
            score = self.engine.similarity(word, r.answer)
            if score is None:
                return {"ok": False, "reason": "无法计算相似度"}
            e = Entry(player, word, score, time.time())
            rank = self._record(e, True)
            # 名次 = 插入位置 + 1（降序维护，插入即名次，无需再排序/查找）
            return {"ok": True, "score": score, "is_new": True, "rank": rank}

    def like(self, n=1):
        with self._lock:
            self.round.likes += n
            return self.round.likes

    def batch_submit(self, items):
        """批量投喂（礼物代猜 / 压测用）。items = [(player, word), ...]
        返回汇总 dict：ok 成功数 / reject 被拒数 / won 是否有人猜中 / results 明细。
        注意：本方法一次性抢锁跑完，避免 N 次锁往返（压测关键路径）。"""
        results = []
        with self._lock:
            for player, word in items:
                results.append(self.submit(player, word))
        won = next((r for r in results if r.get("won")), None)
        return {
            "n": len(results),
            "ok": sum(1 for r in results if r.get("ok")),
            "reject": sum(1 for r in results if not r.get("ok")),
            "is_new": sum(1 for r in results if r.get("is_new")),
            "won": bool(won),
            "winner": won.get("winner") if won else "",
            "results": results,
        }

    # ---------- 情报 ----------
    def intels(self):
        r = self.round
        el, out = r.elapsed(), {}
        for at, need_likes, name in INTEL:
            if el < at:
                out[name] = {"ready": False, "remain": int(at - el)}
                continue
            if self.require_likes and r.likes < need_likes:
                out[name] = {"ready": False, "need_likes": need_likes, "likes": r.likes}
                continue
            if name not in r.hints:
                if name == "分类":
                    r.hints[name] = r.category
                elif name == "提示三":
                    r.hints[name] = r.answer[0]      # 原版口径：给答案的一个字（首字）
                else:
                    # 提示一/二：按分数段给提示词（提示一 60–70、提示二 70–80，逐级逼近）
                    lo, hi = (60.0, 70.0) if name == "提示一" else (70.0, 80.0)
                    # 已给过的提示不再重复（否则两档可能给出同一个上位词）
                    prev = {str(v).split(" ")[0] for v in r.hints.values()}
                    hw = self.pool.hint_word_in_band(r.answer, r.category,
                                                     exclude=set(r.seen_words) | prev, lo=lo, hi=hi)
                    if hw:
                        # hint_word_in_band 统一返回 (词, 分数)，这里必须解包——直接拼 f-string
                        # 会把 tuple 当成词，前端就会看到 "龙眼,64.9" 这种半截字符串。
                        word, sc = hw
                        r.hints[name] = f"{word} {sc}%"
                    else:
                        r.hints[name] = "（无合适提示词）"
            out[name] = {"ready": True, "value": r.hints[name]}
        return out

    # ---------- 状态快照（给 UI / 弹幕桥） ----------
    def _sweep(self):
        """惰性扫账：超时局在任意读路径上自动结算（UI 轮询即触发）。"""
        if self.round.status == "running" and self.round.elapsed() >= self.round_secs:
            self._close("timeout")

    def state(self, viewer=None):
        with self._lock:
            self._sweep()
            r = self.round
            # intels() 只算一次（原实现在 state 里调了 3 次，每次重跑提示词搜索）
            ints = self.intels()
            board = [{"player": e.player, "word": e.word, "score": e.score,
                      "oov": e.oov} for e in r.board[:15]]
            mine = list(self._mine.get(viewer, [])) if viewer else None
            # 序9：积分榜独立输出（累计榜 + 每人段位徽章，前端据此与本轮榜分栏展示）
            score_board = [
                {"player": p, "score": s, **self._tier_state(p)}
                for p, s in sorted(self.scores.items(), key=lambda kv: -kv[1])[:20]
            ]
            recent = [{"player": e.player, "word": e.word, "score": e.score,
                       "oov": e.oov} for e in list(r.guesses)[-12:]]
            return {
                "rid": r.rid, "seq": self._seq, "status": r.status,
                "remain": max(0, int(self.round_secs - r.elapsed())),
                "n_chars": r.n_chars, "difficulty": r.difficulty, "mult": r.mult,
                "likes": r.likes, "intels": ints,
                "board": board, "mine": mine,
                "score_board": score_board,
                "recent": recent,
                "tier_moves": list(r.tier_moves),   # 结算页「段前→段后」对比数据源
                "settle_top": list(r.settle_top),   # 序14：结算页 Top5 段位快照
                "winner": r.winner,
                "answer": r.answer if r.status != "running" else None,
                "category": r.category if (r.status != "running" or
                                           ints.get("分类", {}).get("ready")) else None,
                "history": list(self.history)[-5:],
                "stats": {"guesses": len(r.guesses), "unique": len(r.seen_words),
                          "board": len(r.board)},
            }


# ---------- 弹幕源抽象（后期接抖音） ----------
class GuessSource:
    """猜词来源。本地版由页面输入框直接 POST /guess；抖音版实现 poll() 读弹幕喂给 submit()。"""
    def start(self, game):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class DanmakuSourceStub(GuessSource):
    """预留：抖音直播弹幕源。两条接法——
    ① 借已登录的浏览器标签抓聊天区文本 → 正则取「昵称 ： 词」→ game.submit(昵称, 词)；
    ② 走抖音开放平台直播弹幕 WebSocket。
    轮询间隔建议 3-5 秒；本样品不启动。"""
    NAME = "danmaku-stub"

    def start(self, game):
        raise NotImplementedError("样品阶段未启用；上抖音时实现 poll() 后调用 game.submit()")

    def stop(self):
        pass


# ---------- 自测：模拟一局，断言机制 ----------
def _selftest():
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from engine import Engine
    from wordpool import WordPool

    e = Engine.get()
    pool = WordPool(e)
    g = Game(e, pool, require_likes=True, round_secs=600)

    # 1) 出题
    r = g.round
    assert r.answer in dict(pool.seed_words), "答案必须在种子池"
    assert r.n_chars == len(r.answer)
    print(f"出题: {r.answer}（{r.category} / {r.n_chars}字 / {r.difficulty}×{r.mult}）")

    # 2) OOV 兜底 + 普通猜词 + 榜单去重
    oov = g.submit("阿云", "进进展展")     # 字符都认识、词不在表 → 兜底封顶
    assert oov["ok"] and oov.get("oov") and oov["score"] <= 12, f"OOV 应兜底并封顶: {oov}"
    bad = g.submit("阿云", "\U000200bb\U000200bb")   # 完全无法识别 → 才拒绝
    assert bad["ok"] is False, "完全无法识别的词才拒绝"
    w1 = e.nearest(r.answer, 5)[0][0]
    a = g.submit("阿云", w1)
    assert a["ok"] and a["is_new"], f"首个有效词应上榜: {a}"
    # A11（2026-10-15）：同词**不同玩家** = 各自独立占行（不再全局去重）
    b = g.submit("小北", w1)
    assert b["ok"] and b["is_new"], f"同词不同玩家应各自占一行: {b}"
    # 同玩家同词 = 组合去重，O(1) 回读
    c = g.submit("阿云", w1)
    assert c["ok"] and not c["is_new"] and c["dup_of"] == "阿云", f"同玩家同词应判重: {c}"
    assert len(g.round.board) == 3, f"OOV+阿云+小北共3条: {len(g.round.board)}"
    print(f"组合去重: 同词不同玩家各占一行 ✅ / 同玩家同词回读 ✅")

    # 2b) 榜单降序不变量 + 名次正确性（bisect 增量插入的回归点）
    #  从近邻表里取**不同下标**的词，保证 30 条都是新词（否则全被去重，测不到插入路径）
    nbrs = [w for w, _ in e.nearest(r.answer, k=60)]
    assert len(nbrs) >= 30, f"近邻不足 30，无法覆盖插入路径: {len(nbrs)}"
    picked, used = [], set()
    for w in nbrs:                       # 跳过已上榜的词（答案本身/前面用过的）
        if w == r.answer or w in g.round.seen_words or w in used:
            continue
        picked.append(w)
        used.add(w)
        if len(picked) == 30:
            break
    assert len(picked) == 30, f"可用近邻不足 30: {len(picked)}"
    for i, w in enumerate(picked):
        g.submit(f"机器人{i}", w)
    assert len(g.round.board) == 33, f"应有 33 条（3 + 30），实际 {len(g.round.board)}"
    scores = [x.score for x in g.round.board]
    assert scores == sorted(scores, reverse=True), "榜单必须严格降序（bisect 插入未破坏不变量）"
    for en in list(g.round.board):          # 原玩家重复自己的词 → 不新增、不打乱榜单
        chk = g.submit(en.player, en.word)
        assert chk["is_new"] is False
    assert [x.score for x in g.round.board] == scores, "重复提交不得改变榜单顺序"
    print(f"榜单: {len(g.round.board)} 条严格降序 ✅（名次=插入位，无全量排序）")

    # 2c) 实时弹幕流：seq 单调 + 增量可拉
    f0 = g.state()["seq"]
    items, cur = g.feed_since(f0 - 1)
    assert items and items[-1]["seq"] == cur, "feed 增量应能拉到最新一条"
    assert [x["seq"] for x in items] == sorted(x["seq"] for x in items), "seq 必须单调"
    print(f"弹幕流: seq={cur} 增量 {len(items)} 条 ✅")

    # 2d) 批量投喂（礼物代猜 / 压测通路）
    g.new_round()
    r0 = g.round
    payload = [(f"刷手{i}", e.nearest(r0.answer, 20 + i)[0][0]) for i in range(10)]
    rep = g.batch_submit(payload)
    assert rep["n"] == 10 and rep["ok"] >= 1, f"批量应全部受理: {rep}"
    print(f"批量投喂: {rep['ok']}/{rep['n']} 受理 ✅")

    # 3) 情报：点赞门槛
    r = g.round                       # 2d 已换新局，这里必须重新取当前局
    g.like(50)
    r.started_at -= 200  # 快进 200 秒（分类/提示一/二的时间条件满足）
    g.like(300)
    intel = g.intels()
    assert intel["分类"]["ready"] and intel["分类"]["value"] == r.category, "分类应解锁"
    assert intel["提示一"]["ready"] and intel["提示一"]["value"], "提示一应给出方向词"
    assert intel["提示三"]["ready"] is False, "提示三时间未到不应出"
    r.started_at -= 100  # 再快进 100 秒（240s）
    g.like(500)
    assert g.intels()["提示三"]["value"] == r.answer[0], "提示三必须是首字"
    print("情报: 分类/提示一/提示三(首字) 全部按条件解锁 ✅")

    # 4) 猜中结算
    # ⚠️ A11 组合去重后，小北在前一局（2d new_round 触发的超时结算）已凭 w1 占行
    #    拿过名次奖——这正是原版行为（每人每词占行 + 局末按榜发奖），所以此处
    #    只能断言"猜中这一局的增量"，不能断言累计值。
    before_xb = g.scores.get("小北", 0)
    win = g.submit("小北", r.answer)
    assert win["won"] and g.round.status == "won" and g.round.winner == "小北"
    assert g.scores["小北"] - before_xb == int(RANK_REWARD[0] * r.mult), \
        f"猜中奖励应为 200×倍率: {g.scores}"
    print(f"猜中: 小北 100% 结算 +{g.scores['小北'] - before_xb} ✅")

    # 5) 超时名次奖
    g.new_round()
    for i, p in enumerate(["甲", "乙", "丙", "丁", "戊", "己"]):
        g.submit(p, e.nearest(g.round.answer, 10 + i * 3)[0][0])
    r2 = g.round
    r2.started_at -= g.round_secs + 1
    st = g.state("甲")
    assert st["status"] == "timeout", "超时必须自动结算"
    assert g.scores.get("甲") == int(200 * r2.mult), f"第一名 200×倍率: {g.scores}"
    print(f"超时名次奖: 甲+200 乙+50 … ✅（答案 {r2.answer}）")

    # 6) 段位溢出结转（2026-10-15 复核 E4/E5，原版结算页实证固化）：
    #    青铜I 73/100 +50 → 白银II 23/200；白金IV 398/400 +200 → 白金III 198/400。
    #    注意不能用 gift.rank_of(total) 的 floor 模型验——123 分按 floor 模型落在青铜I，
    #    原版行为是溢出结转，必须用 _tiers 状态机验证。
    g2 = Game(e, pool, round_secs=600)
    from gift import RANKS as _RK
    g2._tiers["乙"] = {"idx": 1, "prog": 73}     # 青铜I（idx=1, cap=100）73/100
    g2._tiers["甲"] = {"idx": 7, "prog": 398}    # 白金IV（idx=7, cap=400）398/400
    g2.new_round(difficulty="简单")                # 固定 ×1.0，断言值才可预期
    ans = g2.round.answer
    hi = e.nearest(ans, 3)[0][0]
    lo = [w for w, _ in e.nearest(ans, 20) if w != ans and w != hi][-1]
    g2.submit("乙", hi)
    g2.submit("甲", lo)
    g2.round.started_at -= 601                   # 触发超时结算
    g2.state()
    mv = {m["player"]: m for m in g2.round.tier_moves}
    assert mv["乙"]["before"] == {"tier": "青铜I", "prog": 73, "cap": 100}, mv["乙"]
    # 乙第1名 +200：73+200=273 → 青铜I(cap100) 溢出 173 → 白银II 173/200（溢出结转实证）
    assert mv["乙"]["after"] == {"tier": "白银II", "prog": 173, "cap": 200}, mv["乙"]
    assert mv["甲"]["before"] == {"tier": "白金IV", "prog": 398, "cap": 400}, mv["甲"]
    assert mv["甲"]["after"] == {"tier": "白金III", "prog": 48, "cap": 400}, mv["甲"]
    # 甲第2名 +50：398 + 50 = 448 → 白金IV(cap400) 溢出 48 → 白金III 48/400
    print(f"段位结转: 乙 青铜I 73/100 +200 → {mv['乙']['after']['tier']} {mv['乙']['after']['prog']}/{mv['乙']['after']['cap']} ✅")
    print(f"段位结转: 甲 白金IV 398/400 +50 → {mv['甲']['after']['tier']} {mv['甲']['after']['prog']}/{mv['甲']['after']['cap']} ✅")
    # 积分榜独立输出（序9）：带段位徽章
    sb = g2.state()["score_board"]
    assert sb and "tier" in sb[0] and "prog" in sb[0], f"score_board 应含段位: {sb[0]}"
    print(f"积分榜: {[(x['player'], x['score'], x['tier']) for x in sb[:3]]} ✅")
    print("🎉 自测全部通过")


if __name__ == "__main__":
    _selftest()
