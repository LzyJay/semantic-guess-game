#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 word2vec 格式的中文词向量转成游戏用的 .npz 缓存。

仓库自带的 `vectors.npz`（14.4 万词 / 200 维 / float16）就是为了「克隆即跑」。
想换更大的词表（比如腾讯 d100-Small 的 200 万词）时用这个脚本重建缓存。

用法：
    python tools/build_vectors.py --model path/to/light_tencent.bin
    python tools/build_vectors.py --model path/to/Tencent_AILab_ChineseEmbedding.txt --limit 500000
    python tools/build_vectors.py --model big.bin --out big_vectors.npz --dtype float16

支持两种输入：
  * word2vec 二进制（首行 "词数 维度"，随后 词 + b' ' + dim 个 float32）
  * word2vec 文本（首行 "词数 维度"，随后每行 "词 v1 v2 ..."）
格式自动嗅探，无需指定。

注意：脚本不下载任何模型。词向量模型请自行到官方渠道获取（见 README「词向量」一节）。
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sniff_text(path):
    """文本格式返回 True，二进制返回 False。"""
    with open(path, "rb") as f:
        f.readline()
        peek = f.read(240)
    if not peek or any(b < 0x09 for b in peek):
        return False
    try:
        s = peek.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return " " in s and any(ch.isdigit() for ch in s)


def read_bin(path, limit=0):
    data = open(path, "rb").read()
    nl = data.index(b"\n")
    n, dim = (int(x) for x in data[:nl].split())
    if limit:
        n = min(n, limit)
    print(f"二进制模型：{n:,} 词 × {dim} 维，{os.path.getsize(path) / 1048576:.0f} MB")
    pos, idx = nl + 1, data.index
    words = []
    vecs = np.empty((n, dim), dtype=np.float32)
    for i in range(n):
        sp = idx(b" ", pos)
        words.append(data[pos:sp].decode("utf-8"))
        pos = sp + 1
        vecs[i] = np.frombuffer(data[pos:pos + dim * 4], dtype=np.float32)
        pos += dim * 4
        if (i + 1) % 500000 == 0:
            print(f"  解析中… {i + 1:,}/{n:,}", flush=True)
    return words, vecs


def read_txt(path, limit=0):
    import warnings
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        n, dim = (int(x) for x in f.readline().split())
        if limit:
            n = min(n, limit)
        print(f"文本模型：取 {n:,} 词 × {dim} 维，{os.path.getsize(path) / 1048576:.0f} MB")
        vecs = np.empty((n, dim), dtype=np.float32)
        words, i = [], 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # np.fromstring(sep=) 的 DeprecationWarning
            for line in f:
                if i >= n:
                    break
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
                    print(f"  解析中… {i:,}/{n:,}", flush=True)
        return words, vecs[:i]


def main():
    ap = argparse.ArgumentParser(description="word2vec → 游戏用 npz 缓存")
    ap.add_argument("--model", required=True, help="word2vec 模型路径（.bin 或 .txt）")
    ap.add_argument("--out", default=None, help="输出 npz（默认 <项目根>/vectors.npz）")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 个词（词表按词频排序时很有用）")
    ap.add_argument("--dtype", default="float16", choices=["float16", "float32"],
                    help="向量存储精度：float16 体积减半，相似度与 float32 一致到 0.1 分")
    ap.add_argument("--no-compress", action="store_true", help="不做压缩（保存更快、体积更大）")
    args = ap.parse_args()

    if not os.path.exists(args.model):
        sys.exit(f"找不到模型：{args.model}")

    t0 = time.time()
    words, vecs = (read_txt if sniff_text(args.model) else read_bin)(args.model, args.limit)
    print(f"解析完成：{len(words):,} 词 × {vecs.shape[1]} 维，{time.time() - t0:.1f}s")
    vecs = vecs.astype(args.dtype)

    blob = "\x00".join(words).encode("utf-8")
    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vectors.npz")
    save = np.savez if args.no_compress else np.savez_compressed
    save(out, blob=np.frombuffer(blob, dtype=np.uint8), vecs=vecs)
    print(f"已写出：{out}（{os.path.getsize(out) / 1048576:.1f} MB）")
    print("启动服务时会优先加载它；想验证相似度可跑：python engine.py")


if __name__ == "__main__":
    main()
