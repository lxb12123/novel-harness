#!/usr/bin/env python3
"""`supports_stream_usage` 的审计探针 —— 一条路由在**流式**下报不报 usage。

问题：能力表里那个 `None` 的意思是「**没这么问过**」，不是「问过、不给」
（`ProviderCapabilities` 对没审计过的东西一律 fail-closed）。而 2026-08-12
起草变成可中断之后，作者自己那条路由（DeepSeek）上每一稿都走流式、
且**不要** usage ⇒ 每一稿的 token 数退成「未记录」。

判据只有一个，而且必须是实测：**真发一次，真收到**。

    收到 usage  → `supports_stream_usage=True`：取舍消失，
                  「停」按钮和 token 数两个都要得到（改一行 + 删两条测试）。
    没收到      → 改成 `False`（审计过、确认不报）。那时才轮到产品裁决：
                  认了，还是只对报用量的端点开可中断。

三臂，缺一不可：

    A  流式 + `stream_options={"include_usage": True}`   ← 要审的那一档
    B  流式，**不要** usage                              ← 有的家不问也给
    C  非流式                                            ← 对照组：A/B 空的时候，
                                                           证明不是这次调用本身坏了

**用仓库自己的 `_build_client` 发、`_from_stream` 收**，不手搓第二份：
「接了线」不等于「量得到」——解析器认不出的 usage 和端点根本没送，在屏幕上
长得一模一样，而这个探针要分开的正是这两件事。

用法（钥匙从设置页读，**一个字符都不打印**）：

    uv run python scripts/probe_stream_usage.py
    uv run python scripts/probe_stream_usage.py --base-url ... --model ...

⚠️ **会真的调模型、真的花钱**（`max_tokens=16`，三次，量级是几厘）。
作者永远不敲这条 —— 同 `synth/build.py`，它是维护者的仪器。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from novel_harness.draft import provider as prov
from novel_harness.draft.capabilities import resolve_capabilities

PROMPT = [{"role": "user", "content": "说一个字：好"}]
MAX_TOKENS = 16


def _settings() -> dict[str, Any]:
    """读设置页那份 JSON。**路径判据和 `settings.py` 一致**，不在这儿写第二份默认值。"""
    from novel_harness.settings import DEFAULT_PATH

    override = os.environ.get("NH_SETTINGS_PATH")
    path = Path(override) if override else DEFAULT_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _report(result: prov.CompletionResult) -> str:
    cache = result.cache
    cache_s = (
        "None（端点没报）"
        if cache is None
        else f"{cache.shape} read={cache.read_tokens} written={cache.written_tokens}"
    )
    return (
        f"      prompt_tokens     = {result.prompt_tokens}\n"
        f"      completion_tokens = {result.completion_tokens}\n"
        f"      cache             = {cache_s}\n"
        f"      finish_reason     = {result.finish_reason}\n"
        f"      text              = {result.text[:40]!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--provider",
        default="",
        help=(
            "OpenRouter 专用：把上游锁死在这一家（如 deepseek）。"
            "**产品今天不发这个字段** —— 探针锁得住，`nh` 锁不住，见脚本头部。"
        ),
    )
    args = parser.parse_args()

    saved = _settings()
    base_url = args.base_url or saved.get("base_url") or os.environ.get("NH_LLM_BASE_URL", "")
    model = args.model or saved.get("model") or os.environ.get("NH_LLM_MODEL", "")

    # **钥匙必须属于这条路由。** 指到别家去审计时（`--base-url`），设置页那把是上一家的，
    # 拿它发过去只会换回一个 401 —— 而 401 在这个脚本的三臂里长得和「不报 usage」
    # 一模一样（对照组也失败），读的人会以为审计做完了。所以这一档只认环境变量。
    same_route = not args.base_url or args.base_url == saved.get("base_url")
    key = (saved.get("api_key") if same_route else "") or os.environ.get("NH_LLM_API_KEY", "")
    if not (base_url and model and key):
        print("设置不全（base_url / model / api_key 缺一），审计不了。")
        if not same_route:
            print("换路由审计时钥匙只从 NH_LLM_API_KEY 读 —— 设置页那把属于另一家。")
        else:
            print("去顶栏「AI 设置」填，或者给 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。")
        return 2

    print(f"路由：{base_url}  /  {model}")
    declared = resolve_capabilities(base_url, model).supports_stream_usage
    print(f"能力表当前声明：supports_stream_usage = {declared!r}\n")

    config = prov.ProviderConfig(base_url=base_url, model=model, api_key=key)
    client = prov._build_client(config)
    base: dict[str, Any] = {
        "model": model,
        "messages": PROMPT,
        "max_tokens": MAX_TOKENS,
    }
    # 关掉思考：别让一次审计变贵。方言照抄 `_wire_kwargs` 的 OFF 分支，
    # **不在这儿发明第三种写法**（探针发的东西越像产品，结论越能当数）。
    extra: dict[str, Any] = {}
    if "openrouter" in base_url:
        extra["reasoning"] = {"effort": "none", "exclude": True}
    elif "deepseek" in base_url:
        extra["thinking"] = {"type": "disabled"}
    if args.provider:
        # ⚠️ **产品今天不发这个。** 锁上游是 OpenRouter 的扩展字段，而 `_wire_kwargs`
        # 的 OPENROUTER 分支只写 `reasoning`。所以这条探针问出来的是「官方上游怎么答」，
        # 不是「作者今天配上去会怎样」—— 后者是 19 家里随机挑一家。
        # 要让产品也锁得住，那是一次 wire 改动 + 一条新的注册表登记。
        extra["provider"] = {"order": [args.provider], "allow_fallbacks": False}
        print(f"上游已锁：{args.provider}（探针专用，产品不发这个字段）\n")
    if extra:
        base["extra_body"] = extra

    arms: list[tuple[str, str, dict[str, Any], bool]] = [
        (
            "A",
            "流式 + stream_options{include_usage}",
            {**base, "stream": True, "stream_options": {"include_usage": True}},
            True,
        ),
        ("B", "流式，不要 usage", {**base, "stream": True}, True),
        ("C", "非流式（对照组）", {**base, "stream": False}, False),
    ]

    got: dict[str, bool] = {}
    for tag, label, kwargs, streaming in arms:
        print(f"── {tag}  {label} " + "─" * max(0, 48 - len(label)))
        try:
            resp = client.chat.completions.create(**kwargs)
            result = (
                prov._from_stream(resp, model)
                if streaming
                else prov._from_non_streaming(resp, model)
            )
        except Exception as exc:  # 探针：任何异常都是结论的一部分，不许吞
            print(f"      ✗ {type(exc).__name__}: {str(exc)[:200]}\n")
            got[tag] = False
            continue
        print(_report(result) + "\n")
        got[tag] = result.prompt_tokens is not None

    print("═" * 62)
    if not got.get("C"):
        print("对照组就没拿到 usage —— **先别下结论**：是这次调用/这条路由本身没通，")
        print("不是「流式下不报」。修好网络或钥匙再跑。")
        return 1
    if got.get("A"):
        print(f"✅ 这条路由在流式下**报 usage**。⇒ {model} 的 supports_stream_usage")
        print("   从 None 改成 True，取舍消失：停按钮 + token 数两个都要得到。")
        print("   同时删掉 tests/test_cache_metering.py 里那两条（它们自己写着遗嘱）。")
        if got.get("B"):
            print("   ⚠️ B 也拿到了 —— 它不问也给，stream_options 只是保险。")
    else:
        print(f"❌ 要了也不给。⇒ {model} 的 supports_stream_usage 从 None 改成 **False**")
        print("   （审计过、确认不报）。那时才轮到产品裁决：认了，还是只对报用量的端点开可中断。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
