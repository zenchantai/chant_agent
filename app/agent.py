from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

from .period_structure import analyze_period

load_dotenv()


async def explain(payload: dict[str, Any], question: str | None = None) -> dict[str, Any]:
    # Prefer project-specific settings, then use the existing OpenAI environment
    # configuration so local app setup does not require duplicating credentials.
    endpoint = os.getenv("AI_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("AI_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    api_style = os.getenv("AI_API_STYLE", "responses").lower()
    prompt = "请基于以下缠论规则计算结果生成简洁、审慎的盘前分析。只能引用已持久化 evidence；可以解释规则引擎已计算的笔、L1/L2中枢、延伸/扩展、晋级候选和段证明，但不能创造走势、结构买卖点、背驰或更高层级中枢，也不能修改结构边界。"
    if question:
        prompt += f" 用户问题：{question}"
    if not api_key:
        return {"mode": "rules_only", "text": "AI 未配置。当前结果仅为规则引擎候选信号，请结合图表、失效价和人工确认，不构成投资建议。"}
    facts = json.dumps(payload, ensure_ascii=False)
    if api_style == "chat":
        url = endpoint.rstrip("/") + "/chat/completions"
        body = {"model": model, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": facts}]}
    else:
        url = endpoint.rstrip("/") + "/responses"
        body = {"model": model, "instructions": prompt, "input": facts}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body)
            response.raise_for_status()
            result = response.json()
            if api_style == "chat":
                content = result["choices"][0]["message"]["content"]
            else:
                content = result.get("output_text") or "\n".join(item.get("text", "") for block in result.get("output", []) for item in block.get("content", []) if item.get("type") == "output_text").strip()
            if not content:
                raise ValueError("empty model response")
            return {"mode": "model", "text": content}
    except httpx.HTTPStatusError as exc:
        return {"mode": "rules_only", "text": f"AI 服务不可用（HTTP {exc.response.status_code}），已回退规则结果。"}
    except Exception as exc:
        return {"mode": "rules_only", "text": f"AI 服务不可用，已回退规则结果：{type(exc).__name__}"}


async def run_agent(rows, symbol: str, question: str | None = None,
                    precomputed: dict[str, Any] | None = None):
    result = precomputed or analyze_period(rows, symbol, "d")
    result["as_of"] = datetime.now(timezone.utc).isoformat()
    result["status"] = "paper_only"
    result["signals"] = []
    structure = result.get("structure", {})
    meta = result.get("meta", {})
    centers = [center for center in structure.get("centers", []) if int(center.get("level", 1)) == 1][-6:]
    promotion_candidates = structure.get("promotion_candidates", [])[-8:]
    segment_proofs = structure.get("segment_proofs", [])[-12:]
    result["explanation"] = await explain({
        "symbol": symbol,
        "definition_version": meta.get("definition_version"),
        "available": bool(structure.get("pens")),
        "pens": structure.get("pens", [])[-8:],
        "centers": centers,
        "promotion_candidates": promotion_candidates,
        "segment_proofs": segment_proofs,
        "notice": "只能解释本周期规则引擎已经计算并持久化的笔、中枢和L1到L2证据。模型不能创造走势、买卖点、背驰或更高层级结构。",
    }, question)
    return result
