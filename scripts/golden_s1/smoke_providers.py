"""冒烟测试：探测 GLM 与腾讯混元的可用端点 / key / 模型名。

用 httpx 直接发请求（而非 SDK），这样能看到原始 HTTP 状态码与报错原文，
便于区分失败原因是「端点不通」「key 无效」还是「模型名不对」。

用法：
    python3 scripts/golden_s1/smoke_providers.py
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ENV = Path("/home/suntianhao/construction_agent/.env")
load_dotenv(ENV, override=True)

TIMEOUT = 90.0
MINIMAL = "你好"
# 推理模型（GLM-5.3 等）会先吐 thinking 块，64 token 会被思考吃光导致没有 text 块，
# 故冒烟也用足够大的预算（正式运行用 16384）。
SMOKE_MAX_TOKENS = 1024
GLM_MODELS = ["glm-5.3", "glm-5.2", "glm-5.1", "glm-4.7", "glm-4.6"]


def anthropic_body(model: str) -> dict:
    return {"model": model, "max_tokens": SMOKE_MAX_TOKENS,
            "messages": [{"role": "user", "content": MINIMAL}]}


def openai_body(model: str) -> dict:
    return {"model": model, "max_tokens": SMOKE_MAX_TOKENS,
            "messages": [{"role": "user", "content": MINIMAL}]}


def probe(tag: str, url: str, headers: dict, body: dict, style: str) -> dict:
    t0 = time.time()
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {tag:34s} 连接失败 {type(e).__name__}: {str(e)[:110]}")
        return {"tag": tag, "ok": False, "err": str(e)[:200]}
    el = time.time() - t0
    txt = r.text or ""
    ok = False
    snippet = ""
    blocks_desc = ""
    try:
        j = r.json()
        if style == "anthropic":
            blocks = j.get("content") or []
            kinds = [b.get("type") for b in blocks if isinstance(b, dict)]
            blocks_desc = "+".join(kinds) or "无 content 块"
            texts = [b.get("text", "") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text"]
            ok = r.status_code == 200 and any(t.strip() for t in texts)
            snippet = (" ".join(texts).strip() or
                       " ".join(str(b.get("thinking", "")) for b in blocks
                                if isinstance(b, dict) and b.get("type") == "thinking"))[:90]
        else:
            ch = (j.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            blocks_desc = "content" + ("+reasoning_content" if msg.get("reasoning_content") else "")
            ok = r.status_code == 200 and bool(str(msg.get("content") or "").strip())
            snippet = str(msg.get("content") or msg.get("reasoning_content") or "")[:90]
    except Exception:  # noqa: BLE001
        snippet = txt[:150]
    mark = "✓" if ok else "✗"
    print(f"  {mark} {tag:32s} HTTP {r.status_code} {el:5.1f}s [{blocks_desc}] {snippet or txt[:110]!r}")
    return {"tag": tag, "ok": ok, "status": r.status_code, "sec": round(el, 1),
            "blocks": blocks_desc, "body": txt[:400]}


def main():
    out = []
    print("=" * 96)
    print("冒烟测试：GLM（智谱）与 腾讯混元")
    print("=" * 96)

    # ── 1. 混元：Anthropic 兼容，逐个候选端点试 ──
    hy_key = os.getenv("HUNYUAN_API_KEY", "")
    HY_ENDPOINTS = [
        os.getenv("HUNYUAN_BASE_URL", "https://tokenhub-intl.tencentmaas.com"),
        "https://tokenhub.tencentmaas.com",              # 国内站（无 -intl）
        "https://api.hunyuan.cloud.tencent.com",         # 混元原生域名
        "https://api.hunyuan.cloud.tencent.com/anthropic",
        "https://hunyuan.tencentcloudapi.com",
    ]
    print("\n[1] 混元 Anthropic 兼容（逐个候选端点）")
    if not hy_key:
        print("  ✗ 未读到 HUNYUAN_API_KEY")
    else:
        seen = set()
        for base in HY_ENDPOINTS:
            b = base.rstrip("/")
            if not b or b in seen:
                continue
            seen.add(b)
            out.append(probe(f"hy3 @ {b[:46]}", f"{b}/v1/messages",
                             {"Content-Type": "application/json",
                              "anthropic-version": "2023-06-01",
                              "x-api-key": hy_key},
                             anthropic_body("hy3"), "anthropic"))

    # ── 2. GLM：Anthropic 兼容端点，两个 key × 候选模型名 ──
    glm_base = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    for key_name in ("GLM_API_KEY", "ZHIPU_API_KEY"):
        key = os.getenv(key_name, "")
        print(f"\n[2] GLM Anthropic 兼容  端点={glm_base}  key={key_name}"
              f"({'已配置' if key else '缺失'})")
        if not key:
            continue
        for m in GLM_MODELS:
            out.append(probe(f"glm/{m} [{key_name}]",
                             f"{glm_base.rstrip('/')}/v1/messages",
                             {"Content-Type": "application/json",
                              "anthropic-version": "2023-06-01",
                              "x-api-key": key},
                             anthropic_body(m), "anthropic"))

    # ── 3. GLM：已配置的 OpenAI 兼容端点（兜底路径）──
    oa_base = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    for key_name in ("GLM_API_KEY", "ZHIPU_API_KEY"):
        key = os.getenv(key_name, "")
        print(f"\n[3] GLM OpenAI 兼容（兜底）  端点={oa_base}  key={key_name}")
        if not key:
            continue
        for m in GLM_MODELS:
            out.append(probe(f"openai/{m} [{key_name}]",
                             f"{oa_base.rstrip('/')}/chat/completions",
                             {"Content-Type": "application/json",
                              "Authorization": f"Bearer {key}"},
                             openai_body(m), "openai"))

    ok = [x for x in out if x["ok"]]
    print("\n" + "=" * 96)
    print(f"可用组合 {len(ok)} / 尝试 {len(out)}")
    for x in ok:
        print(f"  ✓ {x['tag']}")
    if not ok:
        print("  全部失败——请看上面各条的 HTTP 状态与报错原文")
    Path("/tmp/smoke_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n明细 → /tmp/smoke_result.json")


if __name__ == "__main__":
    main()
