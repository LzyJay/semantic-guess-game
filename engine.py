# 猜词引擎：词向量加载 + 相似度 + OOV 兜底（纯 numpy，无第三方依赖）
# 缓存优先级：big_vectors.npz（可选，200 万词大表） > vectors.npz（随仓库自带的 14 万词表）
# 没有缓存时回退到原始 word2vec 模型（light_tencent.bin 二进制 或 *.txt 文本），并自动生成缓存
# OOV 兜底：词表外词按「字符向量平均」估算，分数封顶 OOV_CAP（不拒绝输入，只给弱信号）
import os

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))

_NPZ_CANDIDATES = [
    os.environ.get("GUESS_NPZ", ""),
    os.path.join(BASE, "big_vectors.npz"),
    os.path.join(BASE, "vectors.npz"),
]
NPZ = next((p for p in _NPZ_CANDIDATES if p and os.path.exists(p)), _NPZ_CANDIDATES[-1])

_MODEL_CANDIDATES = [
    os.environ.get("GUESS_MODEL_BIN", ""),
    os.path.join(BASE, "light_tencent.bin"),
    os.path.join(BASE, "Tencent_AILab_ChineseEmbedding.txt"),
]
MODEL_BIN = next((p for p in _MODEL_CANDIDATES if p and os.path.exists(p)), _MODEL_CANDIDATES[-1])

OOV_CAP = 12.0   # 词表外词的分数上限（弱信号，避免误导）


def _sniff_text(path):
    """判断 word2vec 文件是文本格式还是二进制格式（看头部之后是否是可读浮点文本）。"""
    with open(path, "rb") as f:
        f.readline()
        peek = f.read(240)
    if not peek:
        return False
    if any(b < 0x09 for b in peek):        # 含控制字符 → 二进制
        return False
    try:
        s = peek.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return " " in s and any(ch.isdigit() for ch in s)


def load_w2v_bin(path):
    """word2vec 二进制（首行 '词数 维度'，随后 词+b' '+dim个float32）。"""
    data = open(path, "rb").read()
    nl = data.index(b"\n")
    n, dim = (int(x) for x in data[:nl].split())
    pos, idx = nl + 1, data.index
    words = [None] * n
    vecs = np.empty((n, dim), dtype=np.float32)
    for i in range(n):
        sp = idx(b" ", pos)
        words[i] = data[pos:sp].decode("utf-8")
        pos = sp + 1
        vecs[i] = np.frombuffer(data[pos:pos + dim * 4], dtype=np.float32)
        pos += dim * 4
    return words, vecs


def load_w2v_txt(path):
    """word2vec 文本（首行 '词数 维度'，随后每行 '词 v1 v2 ...'）。"""
    import warnings
    n = dim = 0
    words, vecs = [], []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        n, dim = (int(x) for x in f.readline().split())
        vecs = np.empty((n, dim), dtype=np.float32)
        i = 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # np.fromstring(sep=) 的 DeprecationWarning
            for line in f:
                sp = line.find(" ")
                if sp <= 0:
                    continue
                v = np.fromstring(line[sp + 1:], dtype=np.float32, sep=" ")
                if v.size != dim:
                    continue
                words.append(line[:sp])
                vecs[i] = v
                i += 1
                if i % 200000 == 0:
                    print(f"  解析中… {i}/{n}", flush=True)
    return words, vecs[:i]


def load_model(path):
    return load_w2v_txt(path) if _sniff_text(path) else load_w2v_bin(path)


def load_common_words():
    """随仓库自带的 14 万常用词表，作为「常用词」集合；取不到返回 None（调用方不过滤）。
    用于：提示词/机器人发词的生僻词过滤。"""
    import os

    import numpy as np
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "vectors.npz"), os.environ.get("GUESS_NPZ", "")):
        if p and os.path.exists(p):
            try:
                z = np.load(p)
                return set(z["blob"].tobytes().decode("utf-8").split("\x00"))
            except Exception:
                pass
    return None


class Engine:
    """单例；unit = 归一化向量，similarity = cosine × 100（一位小数）。"""

    _inst = None

    def __init__(self):
        if os.path.exists(NPZ):
            z = np.load(NPZ)
            words = z["blob"].tobytes().decode("utf-8").split("\x00")
            # 缓存可能是 float16 存的（体积减半、相似度与 float32 一致到 0.1 分），统一升到 float32 计算
            vecs = np.asarray(z["vecs"], dtype=np.float32)
            src = f"缓存 {os.path.basename(NPZ)}"
        else:
            if not os.path.exists(MODEL_BIN):
                raise SystemExit(
                    "找不到词向量：仓库自带的 vectors.npz 缺失，也没有 light_tencent.bin。\n"
                    "请先看 README「词向量」一节，或运行 python tools/build_vectors.py --help")
            words, vecs = load_model(MODEL_BIN)
            blob = "\x00".join(words).encode("utf-8")
            np.savez(NPZ, blob=np.frombuffer(blob, dtype=np.uint8), vecs=vecs)
            src = f"模型 {os.path.basename(MODEL_BIN)}"
        self.words = words
        self.index = {w: i for i, w in enumerate(words)}
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.unit = (vecs / norms).astype(np.float32)
        print(f"[engine] {src}｜词表 {len(words):,}｜维度 {vecs.shape[1]}", flush=True)

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def has(self, w):
        return w in self.index

    def similarity(self, a, b):
        ia, ib = self.index.get(a), self.index.get(b)
        if ia is None or ib is None:
            return None
        return round(float(np.dot(self.unit[ia], self.unit[ib])) * 100, 1)

    def oov_vector(self, word):
        """词表外词的字符向量平均（无已知字符则返回 None）。"""
        vs = [self.unit[self.index[c]] for c in word if c in self.index]
        if not vs:
            return None
        v = np.mean(vs, axis=0)
        n = np.linalg.norm(v)
        return (v / n) if n else None

    def similarity_oov(self, guess, answer):
        """guess 不在词表时的兜底：字符向量平均 vs 答案向量，封顶 OOV_CAP。"""
        ia = self.index.get(answer)
        if ia is None:
            return None
        v = self.oov_vector(guess)
        if v is None:
            return None
        return round(min(float(np.dot(v, self.unit[ia])) * 100, OOV_CAP), 1)

    def nearest(self, w, k=10):
        i = self.index.get(w)
        if i is None:
            return []
        sims = self.unit @ self.unit[i]
        top = np.argpartition(-sims, k + 1)[: k + 1]
        top = sorted(top, key=lambda j: -sims[j])
        return [(self.words[j], round(float(sims[j]) * 100, 1)) for j in top if j != i][:k]


# ---- 校准自测：用 2026-10-13 晚实录锚点做回归 + 词表覆盖报告 ----
ANCHORS = [
    ("进度", "进展", 90.7), ("头发", "发根", 90.0), ("夕阳", "斜阳", 90.0),
    ("黑铁", "金环蛇", 90.0), ("检查", "审阅", 63.9), ("大衣", "上衣", 78.8),
    ("月圆", "圆月", 71.5), ("争吵", "纠纷", 38.4), ("唐嫣", "李沁", 90.0),
    ("商议", "商讨", 52.9), ("专卖店", "店铺", 60.9),
]
COVERAGE_WORDS = ["夕阳", "斜阳", "黑铁", "金环蛇", "月圆", "壁花", "筹备", "旗舰店", "袖口", "花冠"]

if __name__ == "__main__":
    e = Engine.get()
    print(f"词表规模: {len(e.words):,}")
    print("--- 锚点校准（实录值 → 本引擎值）---")
    for a, b, ref in ANCHORS:
        s = e.similarity(a, b)
        print(f"{a}↔{b}: 实录={ref} 本引擎={s}")
    print("--- 词表覆盖（昨晚缺失的词现在在不在）---")
    for w in COVERAGE_WORDS:
        print(f"{w}: {'✅在表' if e.has(w) else '❌不在表'}")
    print("--- OOV 兜底 ---")
    for w in ["斜阳", "锕锕锕", "𠮷𠮷"]:
        s = e.similarity_oov(w, "进展") if not e.has(w) else None
        print(f"{w}（表外）→ {s}")
    print("--- 抽查近邻 ---")
    for w in ["进展", "争议", "口红"]:
        print(w, "->", e.nearest(w, 5))
