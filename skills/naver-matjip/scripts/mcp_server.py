#!/usr/bin/env python3
"""naver-matjip MCP 서버 (stdio, 표준 라이브러리만).

Claude Code / Claude Desktop / Codex CLI / Cursor 등 MCP 를 지원하는 어떤 에이전트에서도
`recommend_restaurants` 도구 하나로 쓸 수 있다. 로직은 matjip.py 의 recommend() 를 그대로 쓴다.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matjip  # noqa: E402

TOOL = {
    "name": "recommend_restaurants",
    "description": (
        "네이버 플레이스에서 한 지역의 식당을 조회해, 방문자 키워드 투표 중 '…맛있어요' 표가 "
        "2위 키워드의 N배(기본 2배) 이상이고 최소 표수(기본 100) 이상인 곳만 추린다. "
        "JEV_API_KEY 가 설정돼 있으면 TypeSafe Jev 가 조건을 네이버 키워드로 번역(반대 키워드는 감점)하고, 메뉴를 고르고, 협찬 리뷰를 판정해 종합 점수로 순위를 매긴다. "
        "Korean restaurant finder based on Naver Place visitor keyword votes."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "area": {"type": "string", "description": "지역명. 예: 성수동, 을지로, 강남역"},
            "food_type": {"type": "string", "description": "음식 종류(선택). 예: 한식, 고깃집, 파스타"},
            "want": {"type": "string", "description": "조건(선택, Jev 판정에 쓰임). 예: 조용히 대화하기 좋은 곳"},
            "menu": {"type": "string", "description": "찾는 메뉴(선택). 예: 크림파스타 — 리뷰 메뉴 언급이 있는 곳만"},
            "open_now": {"type": "boolean", "default": False, "description": "지금 영업 중인 곳만"},
            "max_price": {"type": "number", "description": "가격대 상한(만원). 예: 3 → '3만원 대'까지"},
            "min_votes": {"type": "integer", "default": 100},
            "ratio": {"type": "number", "default": 2.0,
                      "description": "맛 표가 2위 키워드의 몇 배여야 하나(리뷰 1천 미만은 1.5배 더). 방문자 85%+ 맛있어요면 배수와 무관하게 통과"},
            "top": {"type": "integer", "default": 5},
            "max_places": {"type": "integer", "default": 100, "description": "조회할 식당 수(최대 200)"},
        },
        "required": ["area"],
    },
}


def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req):
    method, mid, params = req.get("method"), req.get("id"), req.get("params") or {}
    if mid is None:  # 알림(notifications/*)에는 답하지 않는다
        return
    if method == "initialize":
        reply(mid, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "naver-matjip", "version": "0.2.0"}})
    elif method == "ping":
        reply(mid, {})
    elif method == "tools/list":
        reply(mid, {"tools": [TOOL]})
    elif method == "tools/call":
        if params.get("name") != TOOL["name"]:
            return reply(mid, error={"code": -32602, "message": f"unknown tool: {params.get('name')}"})
        a = params.get("arguments") or {}
        try:
            res = matjip.recommend(
                a["area"], a.get("food_type", ""), a.get("want", ""), a.get("menu", ""),
                bool(a.get("open_now", False)),
                float(a["max_price"]) if a.get("max_price") is not None else None,
                int(a.get("min_votes", 100)), float(a.get("ratio", 2.0)), int(a.get("top", 5)),
                max(1, min(int(a.get("max_places", 100)), 200)))
            reply(mid, {"content": [{"type": "text", "text": matjip.format_text(res)}],
                        "structuredContent": res, "isError": res["checked"] == 0})
        except Exception as e:
            reply(mid, {"content": [{"type": "text", "text": f"조회 실패: {e}"}], "isError": True})
    else:
        reply(mid, error={"code": -32601, "message": f"method not found: {method}"})


for line in sys.stdin:
    line = line.strip()
    if line:
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})
