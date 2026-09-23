# naver-matjip — 에이전트 공통 안내 (Codex / GPT / 기타)

한국 맛집 추천 요청을 받으면 `skills/naver-matjip/SKILL.md` 의 절차를 따른다.
요약: `python3 skills/naver-matjip/scripts/matjip.py <지역> [--type 음식종류] [--want "조건"]` 를 실행하고,
결과의 식당명·배수·별점·링크를 그대로 전달한다. 요청 1건당 1~2회만 실행한다.
MCP 를 쓰는 환경이면 `skills/naver-matjip/scripts/mcp_server.py` 를 stdio MCP 서버로 등록해
`recommend_restaurants` 도구를 호출해도 된다.
