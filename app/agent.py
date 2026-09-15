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
    prompt = "请基于以下缠论规则计算结果生成简洁、审慎的盘前分析。只能引用 evidence；可以解释已计算的笔、方向性L1中枢和同周期走势，但不能修改结构边界、补充中枢或走势、判断背驰或创造买卖点。"
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
    # Keep explanations grounded in confirmed formal L1 centers and computed
    # hierarchy movements.
    centers = [
        center for center in result.get("pen_centers", [])
        if center.get("role", "hierarchy") == "hierarchy"
        and int(center.get("level", 1) or 1) == 1
    ][-6:]
    movements = result.get("movements", [])[-4:]
    result["explanation"] = await explain({
        "symbol": symbol,
        "definition_version": result.get("definition_version"),
        "available": result.get("available", True),
        "stale_reason": result.get("stale_reason"),
        "pens": result.get("pens", [])[-8:],
        "pen_centers": centers,
        "movements": movements,
        "notice": "只能解释本周期笔、已确认的L1正式中枢及规则引擎已确认或标记为provisional的正式走势。模型不能修改结构、升级递归级别或产生买卖点。",
    }, question)
    return result
