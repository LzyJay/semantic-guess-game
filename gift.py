# 礼物接口（预留）
# ---------------------------------------------------------------------------
# 设计依据（2026-10-14 原版直播实拍 + 昨日实战推导）：
#   原版「随机猜词」类礼物 = 观众送一份礼物，系统替他发 N 个猜测词进弹幕池。
#   —— 这不是机器人，而是**付费代猜**。它绕开抖音弹幕的人工发送限流
#      （实测 0.6s 连发 3 条只中 1 条、累计 9 条触发冷却 0 送达），
#      这正是礼物商业逻辑的根：花钱买「绕过频率限制的发言次数」。
#
# 本文件**只定义数据结构与方法签名，不实现具体发词逻辑**。
# 接入抖音时由礼物事件（抖音开放平台 / 直播伴侣）解析后调用 GiftService.accept()，
# 再由上层决定何时把「N 个代猜词」喂给 game.submit()。
#
# 礼物名与词数来自原版画面底部「玩法介绍」直读（2026-10-15 复核 B1/B4）：
#   心心50 / 果汁100 / 糖糖450 / 牌牌150 / 关关25 / 链接25 / 赞赞×10→1（共 7 种）
#   —— 旧表中的 啤酒/锦鲤/礼物10 在原版玩法介绍里**不存在**，已删（B2）；
#      旧表中的 5 个倍率道具（百合词/白金Ⅳ/分身词/烟花/雪糕）系早期虚构，已删（B3）。
# ⚠️ 画面**无任何钻价**（B4）：表内 cost 为占位推测值，仅供内部参考，前端不展示。
# 段位表按 2026-10-15 复核 C6–C9 重写（删无证据的"喜悦"）：
#   实测出现：黑铁、青铜I、白银I/II、黄金I/III、白金III/IV、钻石I/VI、大师VII
#   cap 实测：黑铁/青铜 100 · 白银 200 · 白金 400 · 钻石 600；黄金/大师为相邻档插值（待验证）
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 1. 枚举式常量
# ---------------------------------------------------------------------------

# 礼物类型
GIFT_WORD = "word"        # 随机猜词：直接换 N 个代猜词
GIFT_PROPS = "props"      # 倍率道具：提高本局/本轮积分倍率
GIFT_RANK = "rank"        # 段位类：推进段位进度

# 道具品类（原版榜上会直接标出来的那几种）
PROP_MULTI = "multi"      # 倍率道具，直接乘在得分上


# ---------------------------------------------------------------------------
# 2. 段位表（预留：结算页实测值；积分累计驱动，非局内清零）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Rank:
    """一个段位档位。
    floor = 进入该档所需的累计积分；cap = 该档进度条容量。
    实测依据（2026-10-15 复核 C6–C9）：黑铁/青铜 cap=100、白银 cap=200、
    白金 cap=400、钻石 cap=600；**黄金 cap=300、大师 cap=800 为相邻档插值，
    未经实测，待验证**（第 5 批待办）。
    段位内罗马数字**序号越小越高**（白金IV→III 为升段）。
    """
    key: str
    name: str
    floor: int          # 进入该档的累计积分门槛
    cap: int            # 本档进度条容量（下一档门槛 - 本档门槛）
    color: str = "#888780"


# 段位阶梯（从低到高；floor 逐档累加）。
# 实测出现的档位：黑铁、青铜I、白银I/II、黄金I/III、白金III/IV、钻石I/VI、大师VII；
# 其余中间档按「同系 cap 一致 + 罗马数字序号小者高」补全（标注 ★ 为插值档，未直接实测）。
RANKS = [
    Rank("iron",     "黑铁",    0,     100, "#888780"),
    Rank("bronze1",  "青铜I",   100,   100, "#CD7F32"),
    Rank("silver2",  "白银II",  200,   200, "#B4B2A9"),
    Rank("silver1",  "白银I",   400,   200, "#B4B2A9"),
    Rank("gold3",    "黄金III", 600,   300, "#EF9F27"),   # ★ cap 插值待验证
    Rank("gold2",    "黄金II",  900,   300, "#EF9F27"),   # ★ 整档插值
    Rank("gold1",    "黄金I",   1200,  300, "#EF9F27"),   # ★ cap 插值待验证
    Rank("plat4",    "白金IV",  1500,  400, "#7DDA58"),
    Rank("plat3",    "白金III", 1900,  400, "#7DDA58"),
    Rank("plat2",    "白金II",  2300,  400, "#7DDA58"),   # ★ 整档插值
    Rank("plat1",    "白金I",   2700,  400, "#7DDA58"),   # ★ 整档插值
    Rank("diamond6", "钻石VI",  3100,  600, "#85B7EB"),
    Rank("diamond5", "钻石V",   3700,  600, "#85B7EB"),   # ★ 整档插值
    Rank("diamond4", "钻石IV",  4300,  600, "#85B7EB"),   # ★ 整档插值
    Rank("diamond3", "钻石III", 4900,  600, "#85B7EB"),   # ★ 整档插值
    Rank("diamond2", "钻石II",  5500,  600, "#85B7EB"),   # ★ 整档插值
    Rank("diamond1", "钻石I",   6100,  600, "#85B7EB"),
    Rank("master7",  "大师VII", 6700,  800, "#B388FF"),   # ★ cap 插值待验证
    Rank("master6",  "大师VI",  7500,  800, "#B388FF"),   # ★ 整档插值
    Rank("master5",  "大师V",   8300,  800, "#B388FF"),   # ★ 整档插值
    Rank("master4",  "大师IV",  9100,  800, "#B388FF"),   # ★ 整档插值
    Rank("master3",  "大师III", 9900,  800, "#B388FF"),   # ★ 整档插值
    Rank("master2",  "大师II",  10700, 800, "#B388FF"),   # ★ 整档插值
    Rank("master1",  "大师I",   11500, 800, "#B388FF"),   # ★ 整档插值（当前封顶）
]
# ⚠️ 已知未决冲突（C9，第 5 批待验证）：积分榜实测「9127=钻石I / 9223=大师VII」
# 即大师VII 门槛≈9200，而本表累计 floor 仅 6700——说明「榜面积分」与「段位进度」
# 可能不是同一口径，或低段 cap/档数与真实值有出入。低段行为（黑铁~钻石，结算页
# 直读）可信；大师区 floor 仅供参考，待更多结算帧样本校准。


def rank_of(total_score: int) -> tuple:
    """按累计积分算当前段位。返回 (Rank, 距下一档还差多少分, 档内进度 0~1)。"""
    cur = RANKS[0]
    for r in RANKS:
        if total_score >= r.floor:
            cur = r
        else:
            break
    idx = RANKS.index(cur)
    nxt = RANKS[idx + 1] if idx + 1 < len(RANKS) else None
    if nxt is None:
        return cur, 0, 1.0
    left = max(0, nxt.floor - total_score)
    return cur, left, min(1.0, (total_score - cur.floor) / max(1, cur.cap))


# ---------------------------------------------------------------------------
# 3. 礼物定义
# ---------------------------------------------------------------------------

# 猜词参考度档（原版商业逻辑的另一半，2026-10-14 补；2026-10-15 按 B6 复核改标定）
# ---------------------------------------------------------------------------
# 设计动机：不同价值的付费礼物，其代猜词的「参考度」要有区分。
# 原版逻辑推演：礼物卖的是「代猜服务」，一份礼物发的词越少，每个词**越贴近答案**；
# 词多的礼物基本是噪声（大量低分 filler 混在榜里，看着热闹实则没用）。
# 这正好也解释了为什么会出现"一分钟刷 450 条"——那是**买发言量/刷屏**，
# 而 1~25 词的礼物才是真·买答案。
#
# ⚠️ 标定依据（2026-10-15 改）：只看**每份礼物词数**，不看价格——
#    原版画面无钻价（B4），cost 是推测值，原「每词单价」分级依据悬空。
#    映射：≥300 词→噪声 · 50~150 词→粗糙 · 25 词→精准 · 1 词→押注
#
# 三个参数决定"参考度"：
#   near_ratio —— 有多大比例的词从「答案近邻」里挑（其余为随机常用词）
#   min_score  —— 近邻词的分下限（保证挑出来的确实相关，不是勉强擦边）
#   win_p      —— 单个词直接蒙中答案的概率（词少的礼物才敢给高）
#   gap        —— 发词间隔秒数（越大越像"人手发"，越小越像系统灌）
TIER_RUBBLE = "rubble"   # 噪声档：词最多（≥300）· 买发言量 · 极低参考度
TIER_CHEAP = "cheap"     # 粗糙档：中量词（50~150）
TIER_FINE = "fine"       # 精准档：~25 词 · 少而准
TIER_PREMIUM = "premium"  # 押注档：1 词 · 可能直接命中

TIERS = {
    TIER_RUBBLE:  dict(tier=TIER_RUBBLE, label="噪声", near_ratio=0.10, min_score=40.0,
                       win_p=0.002, gap=0.16),
    TIER_CHEAP:   dict(tier=TIER_CHEAP, label="粗糙", near_ratio=0.28, min_score=55.0,
                       win_p=0.006, gap=0.20),
    TIER_FINE:    dict(tier=TIER_FINE, label="精准", near_ratio=0.50, min_score=65.0,
                       win_p=0.020, gap=0.26),
    TIER_PREMIUM: dict(tier=TIER_PREMIUM, label="押注", near_ratio=0.68, min_score=70.0,
                       win_p=0.060, gap=0.34),
}


@dataclass(frozen=True)
class GiftDef:
    """一种礼物的静态定义（幂等：同 key 覆盖加载，不影响已发出的词）。"""
    key: str            # 内部键，如 heart / juice / lollipop
    name: str           # 显示名，如 "❤ 心心"
    kind: str           # GIFT_WORD / GIFT_PROPS / GIFT_RANK
    cost: int           # 抖币（⚠️ 未实测——原版画面无钻价（B4），仅为占位推测；前端不展示）
    words: int = 0      # 随机猜词：一次代发几个词
    mult: float = 1.0   # 倍率道具：积分倍率
    desc: str = ""      # 画面上的一行说明
    icon: str = ""
    tier: str = TIER_CHEAP   # 猜词参考度档（见 TIERS，按每份词数标定）
    count_hint: int = 0 # 「N 连击换 1 词」类礼物：所需连击数（0=无此语义）。
                        # 用法（B5）：赞赞 = words=1 + count_hint=10，表示「×10 → 随机猜词×1」


# 原版价目表（2026-10-15 复核 B1：7 种全部来自玩法介绍画面直读；**钻价未实测**，cost 仅占位）
# tier 的分配依据 = **每份礼物词数**（B6，不用未实测的 cost）：
#   450 词 → 噪声档（买发言量）  50~150 词 → 粗糙档  25 词 → 精准档  1 词 → 押注档
# ⚠️ 旧表的 啤酒/锦鲤/礼物10 与 5 个倍率道具已全部删除（B2/B3：画面无此物，系早期误记/虚构）。
GIFTS = {
    "heart":    GiftDef("heart",    "❤ 心心",     GIFT_WORD,  1,  words=50,
                        desc="随机猜词 × 50", tier=TIER_CHEAP),
    "juice":    GiftDef("juice",    "🍹 果汁",    GIFT_WORD,  1,  words=100,
                        desc="随机猜词 × 100", tier=TIER_CHEAP),
    "candy":    GiftDef("candy",    "🌈 糖糖",    GIFT_WORD,  2,  words=450,
                        desc="随机猜词 × 450", tier=TIER_RUBBLE),
    "paipai":   GiftDef("paipai",   "💡 牌牌",    GIFT_WORD,  3,  words=150,
                        desc="随机猜词 × 150", tier=TIER_CHEAP),
    "peony":    GiftDef("peony",    "🔔 关关",    GIFT_WORD,  10, words=25,
                        desc="随机猜词 × 25", tier=TIER_FINE),
    "link":     GiftDef("link",     "🔗 链接",    GIFT_WORD,  9,  words=25,
                        desc="随机猜词 × 25", tier=TIER_FINE),
    # 赞赞：「赞赞 × 10 → 随机猜词 × 1」——10 连击换 1 词（B5 语义待确认，
    # 用 count_hint=10 表达；cost=0 占位：它是"赞"不是抖币礼物，价格体系不同）
    "zan":      GiftDef("zan",      "❤ 赞赞",     GIFT_WORD,  0,  words=1,
                        desc="赞赞 × 10 → 随机猜词 × 1", tier=TIER_PREMIUM,
                        count_hint=10),
}


# ---------------------------------------------------------------------------
# 4. 礼物事件（运行时单条）
# ---------------------------------------------------------------------------
@dataclass
class GiftEvent:
    """一次送礼事件。sender 建议传 uid+昵称（榜单只认昵称但 uid 更稳）。"""
    gift_key: str
    sender: str
    count: int = 1                 # 连击数量
    ts: float = 0.0
    raw: Optional[dict] = None     # 平台原始 payload，透传留档


@dataclass
class GiftOrder:
    """服务端裁决后的代猜订单：告诉上层「该替谁发几个词」。"""
    order_id: int
    gift_key: str
    gift_name: str
    sender: str
    n_words: int                   # 实际应代发的词数 = def.words × count
    mult: float
    ts: float
    accepted: bool = True
    reason: str = ""

    def to_json(self):
        return {
            "order_id": self.order_id, "gift_key": self.gift_key,
            "gift_name": self.gift_name, "sender": self.sender,
            "n_words": self.n_words, "mult": self.mult, "accepted": self.accepted,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# 5. 服务
# ---------------------------------------------------------------------------
class GiftService:
    """礼物接口（预留实现）。

    已实现：定义表查询、事件→订单裁决、订单队列、累计统计。
    **未实现（有意留空）**：
        - do_guess_words(order)  —— 真正去发那 N 个词（需接词库 + 送词策略）
        - apply_mult(order)      —— 倍率道具生效（需接 game 侧计分）
    上层只需：接礼物事件 → accept() → 取 outbox 里的订单 → 自行决定何时消费。
    """

    # 单局代猜上限（防刷屏：原版 450 词/份，但一份礼物最多刷满一屏的量）
    MAX_WORDS_PER_ORDER = 500

    def __init__(self):
        self._oid = 0
        self._lock = __import__("threading").Lock()
        self.outbox = []                 # 已裁决未消费的订单
        self.stats = {}                  # gift_key → {sender: 累计词数}
        self.total_words = 0
        self.total_value = 0
        # 预留钩子：外部可注入真正发词的实现
        self.do_guess_words: Optional[Callable[[GiftOrder], int]] = None
        self.apply_mult: Optional[Callable[[GiftOrder], float]] = None

    # ---- 查询 ----
    @staticmethod
    def catalog():
        out = []
        for g in GIFTS.values():
            t = TIERS.get(g.tier, TIERS[TIER_CHEAP])
            out.append({
                "key": g.key, "name": g.name, "kind": g.kind, "cost": g.cost,
                "words": g.words, "mult": g.mult, "desc": g.desc,
                "tier": g.tier, "tier_label": t["label"],
                "count_hint": g.count_hint,
                # 每词单价（分/词）：cost 未实测（B4），仅供内部参考，前端不展示
                "unit_price": round(g.cost * 100 / g.words) if g.words else None,
                "near_ratio": t["near_ratio"], "win_p": t["win_p"],
            })
        return out

    @staticmethod
    def gift(gift_key):
        return GIFTS.get(gift_key)

    @staticmethod
    def tier_of(gift_key):
        """取某礼物的参考度参数（未知礼物回落到粗糙档）。"""
        g = GIFTS.get(gift_key)
        t = g.tier if g else TIER_CHEAP
        return dict(TIERS.get(t, TIERS[TIER_CHEAP]), tier=t)

    # ---- 事件裁决 ----
    def accept(self, ev: GiftEvent) -> GiftOrder:
        """把一次送礼转成代猜订单。未知礼物 / 数量非法一律拒单但不抛异常。"""
        import time
        d = GIFTS.get(ev.gift_key)
        if d is None:
            order = GiftOrder(0, ev.gift_key, ev.gift_key, ev.sender, 0, 1.0,
                              time.time(), False, "未知礼物")
            return order
        n = max(0, int(d.words * max(1, int(ev.count))))
        n = min(n, self.MAX_WORDS_PER_ORDER)
        mult = float(d.mult)
        with self._lock:
            self._oid += 1
            oid = self._oid
        order = GiftOrder(oid, d.key, d.name, ev.sender, n, mult, time.time())
        if not order.accepted:
            return order
        with self._lock:
            self.outbox.append(order)
            self.total_words += n
            self.total_value += d.cost * max(1, int(ev.count))
            s = self.stats.setdefault(d.key, {})
            s[ev.sender] = s.get(ev.sender, 0) + n
        return order

    # ---- 订单队列 ----
    def pop_orders(self, limit=None):
        """取出待消费订单（默认全取）。接了 do_guess_words 钩子则顺手调用。"""
        with self._lock:
            items = self.outbox[:limit] if limit else list(self.outbox)
            if limit:
                del self.outbox[:limit]
            else:
                self.outbox.clear()
        if self.do_guess_words:
            for o in items:
                try:
                    self.do_guess_words(o)
                except Exception:
                    pass          # 钩子失败不阻塞后续订单
        return items

    def pending(self):
        with self._lock:
            return len(self.outbox)

    def snapshot(self):
        return {
            "gifts": self.catalog(),
            "pending": self.pending(),
            "total_words": self.total_words,
            "total_value": self.total_value,
            "top_senders": sorted(
                {p: sum(v.values()) for p, v in self.stats.items()}.items(),
                key=lambda kv: -kv[1])[:5],
            "ranks": [{"key": r.key, "name": r.name, "floor": r.floor, "cap": r.cap} for r in RANKS],
        }

    def rank_of(self, player_score):
        r, need, prog = rank_of(player_score)
        return {"key": r.key, "name": r.name, "need_left": need, "progress": round(prog, 3)}


# ---------------------------------------------------------------------------
# 6. 逐条发射器（把「一次刷 450 条」改成「一条条往外蹦」）
# ---------------------------------------------------------------------------
# 为什么不能一次性 batch_submit：
#   原版是**一个人一条一条发**（哪怕是付费代猜也得过一遍弹幕队列），
#   一次性灌 450 条会：① 前端瞬间刷满、看不到过程；② 榜单被同一人的
#   几百个词淹没，看起来像作弊；③ 丢掉「节奏感」——直播最好看的就是
#   词一条条往上滚、分数一路爬升的那个过程。
#
# 本类只负责**节奏与选词**，真正的提交交给调用方注入的 emit(player, word)
# 回调（server.py 接game.submit），这样 gift.py 依旧不依赖 game。
import threading
import time


class GiftStreamer:
    """把一份礼物订单按 tier 参数**逐条**发射出去。

    用法：
        st = GiftStreamer(emit=game.submit, word_picker=my_pick)
        st.submit(order, sender_nick="张三")        # 立即返回，词在后台蹦
        st.pending_jobs()                # 供前端显示「正在刷…」

    选词策略由 word_picker(tier, n, seen) 提供；本类只管"何时发、发多少、
    什么时候停"。停止条件：局结束 / 停止标记 / 全部发完。
    """

    # 单个 sender 同时最多几条代刷流（防连点礼物叠成 450×N 条）
    MAX_JOBS_PER_SENDER = 3
    # 一波洪峰的目标总时长（秒）：间隔会被压到 total/FLOOD_SECONDS
    FLOOD_SECONDS = 25.0

    def __init__(self, emit=None, word_picker=None, round_alive=None,
                 max_words_job=None):
        self.emit = emit# callable(player, word) -> dict
        self.word_picker = word_picker      # callable(tier, n, seen) -> [word]
        self.round_alive = round_alive      # callable() -> bool，局是否还在跑
        self.max_words_job = max_words_job or GiftService.MAX_WORDS_PER_ORDER
        self._lock = threading.Lock()
        self._jobs = {}                # job_id -> dict(running/sent/total/…)
        self._jid = 0
        self._sender_jobs = {}         # sender -> set(job_id)
        self._stop = threading.Event()

    # ---- 提交一份订单为后台流 ----
    def submit(self, order, sender_nick=None, count=1):
        if not order.accepted or order.n_words <= 0:
            return {"ok": False, "reason": order.reason or "无需发词", "job": None}
        if self.emit is None or self.word_picker is None:
            return {"ok": False, "reason": "未注入 emit/word_picker", "job": None}
        tier = TIERS.get(self._tier_of(order.gift_key), TIERS[TIER_CHEAP])
        sender = sender_nick or order.sender
        total = min(order.n_words * max(1, int(count)), self.max_words_job)
        # 节奏 = 档位 gap，但**洪峰降速**：词数一多就把间隔压下来，
        # 让整波刷完大致落在 FLOOD_SECONDS 秒内（450 词 ≈ 25s 刷完，
        # 观感是"哗啦啦一片上滚"，而不是 90 秒吊着不发）。
        gap = min(float(tier["gap"]), max(0.05, self.FLOOD_SECONDS / max(1, total)))
        with self._lock:
            # 同sender 并发流数封顶
            cur = self._sender_jobs.get(sender, set())
            live = [j for j in cur if self._jobs.get(j, {}).get("running")]
            if len(live) >= self.MAX_JOBS_PER_SENDER:
                return {"ok": False, "reason": "刷得太快，先让上一波发完", "job": None}
            self._jid += 1
            jid = self._jid
            job = {"job_id": jid, "running": True, "sent": 0, "total": total,
                   "gift_key": order.gift_key, "gift_name": order.gift_name,
                   "sender": sender, "tier": tier, "gap": gap,
                   "started": time.time()}
            self._jobs[jid] = job
            self._sender_jobs.setdefault(sender, set()).add(jid)
        threading.Thread(target=self._run, args=(jid,),
                         daemon=True, name=f"gift-stream-{jid}").start()
        return {"ok": True, "job": self.public(job)}

    def _tier_of(self, gift_key):
        g = GIFTS.get(gift_key)
        return g.tier if g else TIER_CHEAP

    def _run(self, jid):
        job = self._jobs[jid]
        gap = float(job.get("gap") or job["tier"]["gap"])
        seen, sender = set(), job["sender"]
        player = "🎁" + sender
        while job["running"] and job["sent"] < job["total"]:
            if self._stop.is_set():
                break
            if self.round_alive and not self.round_alive():
                job["stop_reason"] = "本轮已结束"
                break
            # 一次取一批候选，取一个发一个（保证节奏受gap 控制）
            pool = self.word_picker(job["tier"], 1, seen) or []
            if not pool:
                job["stop_reason"] = "无可用词"
                break
            w = pool[0]
            if w in seen:
                break
            seen.add(w)
            try:
                self.emit(player, w)
            except Exception:
                job["stop_reason"] = "提交异常"
                break
            job["sent"] += 1
            if job["sent"] < job["total"]:
                # gap 加 ±30% 抖动，避免机械等间隔（更像真人在敲）
                time.sleep(gap * (0.7 + 0.6 * ((job["sent"] % 7) / 6.0)))
        job["running"] = False
        job["ended"] = time.time()
        with self._lock:
            self._sender_jobs.get(sender, set()).discard(jid)

    # ---- 查询 / 控制 ----
    @staticmethod
    def public(job):
        return {"job_id": job["job_id"], "running": job["running"],
                "sent": job["sent"], "total": job["total"],
                "gift_name": job["gift_name"], "sender": job["sender"],
                   "tier": job["tier"]["tier"], "tier_label": job["tier"]["label"],
                   "gap": round(float(job.get("gap") or job["tier"]["gap"]), 3),
                   "stop_reason": job.get("stop_reason", "")}

    def jobs(self):
        with self._lock:
            return [self.public(j) for j in self._jobs.values()]

    def active(self):
        return [j for j in self.jobs() if j["running"]]

    def stop_sender(self, sender):
        with self._lock:
            for jid in list(self._sender_jobs.get(sender, ())):
                j = self._jobs.get(jid)
                if j and j["running"]:
                    j["running"] = False
                    j["stop_reason"] = "已停止"

    def shutdown(self):
        self._stop.set()
        with self._lock:
            for j in self._jobs.values():
                j["running"] = False


# ---- 段位与礼物自测 ----
if __name__ == "__main__":
    g = GiftService()
    print("礼物目录（7 种·画面直读；⚠️ 钻价=占位推测·未实测（B4），前端不展示）：")
    for it in g.catalog():
        ch = f"（×{it['count_hint']}连击）" if it.get("count_hint") else ""
        print(f"  {it['name']:<10} {it['desc']:<22}{ch}"
              f"档={it['tier_label']}({it['tier']})"
              f"  近邻率={it['near_ratio']:.0%} 直中率={it['win_p']:.1%}")
    assert len(GIFTS) == 7, f"礼物应为 7 种，实际 {len(GIFTS)}"
    assert all(v.kind == GIFT_WORD for v in GIFTS.values()), "倍率道具应已全部删除"
    print("未知礼物：", g.accept(GiftEvent("nope", "张三")).reason)

    o = g.accept(GiftEvent("heart", "张三"))
    o2 = g.accept(GiftEvent("candy", "李四", count=2))
    print(f"心心→{o.n_words}词，糖糖×2→{o2.n_words}词（封顶 {GiftService.MAX_WORDS_PER_ORDER}）")
    assert o.n_words == 50 and o2.n_words == 500
    z = g.accept(GiftEvent("zan", "王五"))
    assert z.n_words == 1, "赞赞应为 1 词"
    print("出队：", [x.gift_name + f"×{x.n_words}" for x in g.pop_orders()])
    print("积分解段位：", [(s, g.rank_of(s)["name"]) for s in (0, 99, 100, 500, 2000, 6100, 7000)])
    assert g.rank_of(0)["name"] == "黑铁"
    assert g.rank_of(99)["name"] == "黑铁"
    assert g.rank_of(100)["name"] == "青铜I"
    assert g.rank_of(500)["name"] == "白银I"
    assert g.rank_of(2000)["name"] == "白金III"
    assert g.rank_of(6100)["name"] == "钻石I"
    assert g.rank_of(7000)["name"] == "大师VII"

    # --- 逐条发射自测：验证"一条条蹦"+ 节奏可控 ---
    import random
    sent = []

    def fake_emit(player, word):
        sent.append((player, word))
        return {"ok": True}

    def picker(tier, n, seen):
        # 模拟选词：从一个有限池里挑没见过的
        pool = [f"词{i}" for i in range(500)]
        return [w for w in pool if w not in seen][:n]

    st = GiftStreamer(emit=fake_emit, word_picker=picker, round_alive=lambda: True)
    j = st.submit(g.accept(GiftEvent("zan", "主播A")))   # 1 词，快
    time.sleep(0.4)
    assert len(sent) == 1, f"赞赞应只发 1 条，实际 {len(sent)}"
    sent.clear()
    j2 = st.submit(g.accept(GiftEvent("heart", "主播B")))     # 50 词，逐条
    time.sleep(1.2)# ≈0.2s/条 → 约 6 条，远未发完 50
    assert 3 <= len(sent) <= 12, f"heart 1.2s 应发 3~12 条，实际 {len(sent)}"
    assert len(sent) < 50, "不该一次性全发完"
    running = st.active()
    assert running and running[0]["running"], "流应仍在跑"
    st.stop_sender("主播B")
    time.sleep(0.3)
    assert not st.active(), "停止后不应有活跃流"
    print(f"逐条发射：赞赞=1 条✅  heart 1.2s 发 {len(sent)} 条(未一次发完)✅  可停止 ✅")
    st.shutdown()
    print("🎉 礼物接口自测通过（发词由GiftStreamer 逐条驱动；game 侧提交仍由上层注入）")
