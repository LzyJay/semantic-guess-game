# 随机词机器人：模拟直播间观众持续猜词（同时演示 /danmaku 通路）
# 关键约束：
#   1. 固定 5 秒发一个词
#   2. 只发两字常用词
#   3. 绝大多数时候是"普通玩家"：随机探词 + 基于自己最佳词探索，分数从低到高自然爬坡（不透题）
#   4. 但有 P_SHARP 概率"灵光一闪"：从答案高分区（≥70）捞一个词——像直播间里偶尔
#      开窍的 sharp 玩家；其中 P_WIN 概率直接命中答案（偶尔让它赢一手，增加节目效果）。
#      **灵光猜中的词不当"自己的锚点"**（运气归运气，理解归理解）——否则会从一次灵光
#      滚成场场透题。P_SHARP/P_WIN 可经 POST /bot {"sharp":x,"win":y} 实时调。
import random
import re
import threading

CJK = re.compile(r"^[\u4e00-\u9fff]+$")

P_SHARP = 0.008  # 每手"灵光一闪"概率（一局约 50 手 → 约 1/4 的局会出现一次）
P_WIN = 0.08     # 灵光一闪时直接命中答案的概率


def _load_common_words():
    """light 子集（14 万常用词）作为'常用词'过滤集，让机器人只发常见词；取不到就不过滤。"""
    import os

    import numpy as np
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "vectors.npz"),
              os.path.join(os.path.dirname(here), "02_处理中", "vectors.npz")):
        if os.path.exists(p):
            try:
                z = np.load(p)
                return set(z["blob"].tobytes().decode("utf-8").split("\x00"))
            except Exception:
                pass
    return None


class WordBot:
    """常驻线程 + 开关。on=True 时每 5 秒自动猜一个两字词。"""

    def __init__(self, game, engine, name="机器人·小猜", mode="smart", interval=5.0):
        self.game, self.engine = game, engine
        self.name, self.mode = name, mode
        self.interval = interval          # 固定 5 秒/词
        self.on = False
        self.sent = 0
        self.p_sharp = P_SHARP            # 可经 /bot 实时调
        self.p_win = P_WIN
        self.common = _load_common_words()   # 常用词过滤集（light 子集）
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True, name="word-bot")
        self._rid = None                  # 局号变化时重置"自己的最佳词"
        self.best_word = None
        self.best_score = -1.0

    def start(self):
        if not self._t.is_alive():
            self._t.start()
        self.on = True

    def stop(self):
        self.on = False

    def status(self):
        return {"on": self.on, "mode": self.mode, "name": self.name,
                "sent": self.sent, "interval": self.interval,
                "sharp": self.p_sharp, "win": self.p_win}

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            if not self.on:
                continue
            r = self.game.round
            if r.status != "running":
                continue
            if r.rid != self._rid:                       # 新局：忘掉旧局的最佳词
                self._rid, self.best_word, self.best_score = r.rid, None, -1.0
            word, sharp = self._pick(r)
            if word and word not in r.words_seen:
                res = self.game.submit(self.name, word)
                if res.get("ok"):
                    self.sent += 1
                    # 只有"自己领悟"的词才当锚点；灵光一闪的不当（防止从一次运气滚成透题）
                    if not sharp and res.get("score", 0) > self.best_score:
                        self.best_score = res["score"]
                        self.best_word = word

    def _is_nice(self, w):
        """两字、纯中文、且在常用词表里（过滤生僻词/繁体/人名）。"""
        if len(w) != 2 or not CJK.match(w):
            return False
        return not self.common or w in self.common

    def _pick(self, r):
        # 灵光一闪（低概率）：从答案高分区捞词——偶尔开窍的 sharp 玩家
        if random.random() < self.p_sharp:
            if len(r.answer) == 2 and random.random() < self.p_win:
                return r.answer, True                 # 偶尔直接命中（让它赢一手）
            nb = self.engine.nearest(r.answer, k=60)
            pool = [w for w, s in nb if s >= 70 and self._is_nice(w) and w not in r.words_seen]
            if pool:
                return random.choice(pool[:30]), True  # 高分那一档
        # 常规：60% 从"自己得分最高的词"的近邻里找下一个（正常解题策略，不看答案）
        if self.mode == "smart" and self.best_word and random.random() < 0.6:
            nb = self.engine.nearest(self.best_word, k=40)
            pool = [w for w, _ in nb if self._is_nice(w) and w not in r.words_seen]
            if pool:
                return random.choice(pool), False
        return self._rand_word(), False

    def _rand_word(self):
        ws = self.engine.words
        for _ in range(200):
            w = random.choice(ws)
            if self._is_nice(w):
                return w
        return None
