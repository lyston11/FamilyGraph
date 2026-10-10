"""P5 决策探针：contextual chunking（分段前缀）在本语料上是否有增益。

## 结论（2026-10-10）：**无增益，且 naive 前缀反而更差**。因此不实现。

## 为什么必须实测而不是照搬

contextual retrieval（在分段前拼接来源级上下文再编码）是流行的「最佳实践」。
但它有明确的代价与失效模式：

- 前缀对同一来源的所有分段**相同**，因此它给每个向量加了一个常量分量，
  会**稀释**分段自身的内容差异——这正好是实测观察到的失效；
- 真正有效的版本需要**每个分段单独生成**上下文（LLM 调用），成本与延迟都真实存在，
  且必须经 provider gateway（egress 审计）。

## 两次测量

### v1：真实 golden set（无效的测量）

golden set 的 20 条记忆平均 **20 字符**，每条 chunk 只有 **1 个分段**。前缀没有
提供任何分段内不存在的信息，因此两次结果完全相同（pass=11/15, recall=0.733）。
这个测量**不能**用来支持或否定 contextual chunking——它没有可测量的空间。

### v2：长文档多分段（有效的测量）

构造 3 篇长「人物传记」（每篇含大量与查询无关的填充句），切成多段，答案落在
**不含姓名的分段**里，问题点名具体的人。这正是 contextual retrieval 声称要解决的
场景。8 个查询，比较「top-1 分段所属文档是否正确」：

```text
plain (现状)        7/8
contextual prefix   6/8   ← 更差
```

naive 前缀让 `谁的胃不好？` 从命中「舅舅传」变成「外婆传」、`谁对花生过敏？`
从「舅舅传」变成「表姐传」——前缀把各文档的向量拉近，削弱了分段级区分度。

## 何时重新考虑

以下**全部**成立时才值得再测：

1. golden set 里有真实的**长文档**用例（当前只有短记忆，没有可测量的空间）；
2. 前缀由**每个分段单独生成**（LLM），而不是来源级常量；
3. 该 LLM 调用经 provider gateway，且成本已计入预算；
4. 在同一份 golden set 上证明增益，再谈默认开启。

在此之前不实现——为无收益的复杂度买单，而且实测显示它会**降低**质量。

## 用法

    PGTEST_DSN=... EMBEDDING_BASE_URL=http://... \
        python scripts/migration-proof/contextual_chunking_probe.py
"""

import asyncio
import math
import os
import sys

sys.path.insert(0, "backend")
os.environ.setdefault("DATA_DIR", "/tmp/fg-p5b")
os.environ.setdefault("EMBEDDING_BASE_URL", "http://172.28.0.2:8090")
os.environ.setdefault("RAG_EMBEDDING_DIMENSION", "512")
os.environ.setdefault("RAG_EMBEDDING_MODEL", "bge-small-zh-v1.5")

from app.services import embedding_chunking, embedding_client  # noqa: E402

FILLER = (
    "家里人都说她做事细致，凡事喜欢提前打算，从不愿意麻烦别人。"
    "年轻时候的照片还留着，黑白的，边角已经发黄。"
    "每年清明大家都会一起回去看看老房子，顺便在巷口吃一碗面。"
    "这些年家里添了不少人，聚一次要摆两大桌，孩子们在院子里跑来跑去。"
)

DOCS = {
    "外婆传": "外婆周秀英一九三八年出生在扬州。她年轻时在扬州的中学教语文，学生很多。"
    "后来她跟着外公搬到了苏州，在平江路附近买了一处老宅。院子里种了一棵桂花树。"
    + FILLER
    + "每年秋天桂花开的时候，她会摘下来做桂花糖藕，这是全家人最期待的味道。"
    "她最拿手的菜其实是红烧狮子头，只是年纪大了以后很少做了。"
    + FILLER
    + "她一直有高血压，医生嘱咐要少吃咸的，所以她做菜越来越清淡。"
    "她习惯被人称为阿婆，外面的人叫她周老师。她最喜欢喝龙井茶，早上一定要泡一杯。",
    "舅舅传": "舅舅陈建国一九七零年生。他在南京的一家设计院工作，做结构设计，一干就是二十年。"
    + FILLER
    + "后来他调到了杭州，现在在市政设计院。他有个老毛病是胃不好，常年吃小米粥养胃。"
    + FILLER
    + "他对花生过敏，聚餐时不能点带花生的菜，这一点家里人都记得。"
    "他喜欢钓鱼，周末常去城郊的水库。他最近换了一辆深灰色的车。",
    "表姐传": "表姐陈雨桐是九八年生的。她在武汉大学读研究生，专业是生物医学，导师做肿瘤方向。"
    + FILLER
    + "她本科是在南京读的，学的是生物技术。她一个人在武汉租房子住，离学校三站地铁。"
    + FILLER
    + "她不太会做饭，常在外面吃。她养了一只橘猫叫元宝。",
}

CASES = [
    ("外婆年轻的时候在扬州做什么？", "外婆传"),
    ("谁有高血压？", "外婆传"),
    ("舅舅在哪里工作？", "舅舅传"),
    ("谁对花生过敏？", "舅舅传"),
    ("表姐在哪里读研究生？", "表姐传"),
    ("元宝是谁养的？", "表姐传"),
    ("谁最喜欢喝龙井茶？", "外婆传"),
    ("谁的胃不好？", "舅舅传"),
]


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def embed_many(texts):
    out = []
    for i in range(0, len(texts), 16):
        r = await embedding_client.embed_documents(texts[i : i + 16])
        if not r.usable:
            raise SystemExit(f"embed failed: {r.reason}")
        out.extend(r.vectors)
    return out


segments, plain, ctx, owners = [], [], [], []
for title, body in DOCS.items():
    for seg in embedding_chunking.segment_text(body):
        segments.append(seg.text)
        plain.append(seg.text)
        ctx.append(f"《{title}》：{seg.text}")
        owners.append(title)
print(f"docs={len(DOCS)} segments={len(segments)}")
for i, s in enumerate(segments):
    print(f"  [{owners[i]}] {s[:40]}")

pv = asyncio.run(embed_many(plain))
cv = asyncio.run(embed_many(ctx))


def evaluate(vecs, label):
    hit = 0
    for question, want in CASES:
        q = asyncio.run(embedding_client.embed_query(question)).vectors[0]
        best = max(range(len(vecs)), key=lambda i: cos(q, vecs[i]))
        ok = owners[best] == want
        hit += int(ok)
        if not ok:
            print(f"    MISS {question:22} want={want} got={owners[best]}")
    print(f"{label:22} top1_chunk_owner_correct={hit}/{len(CASES)}")


print()
evaluate(pv, "plain (现状)")
evaluate(cv, "contextual prefix")
