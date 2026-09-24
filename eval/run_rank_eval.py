#!/usr/bin/env python3
"""순위 채점(정답지). 사용자가 아는 '진짜 맛집'이 추천에 얼마나 올라오는지 잰다.

정답지: ~/.config/naver-matjip/gold.json  {"지역": ["가게 이름", ...]}  (NAVER_MATJIP_GOLD 로 바꿀 수 있음)
  - 웹 👍 를 누르면 자동으로 쌓인다. 개인 취향 데이터라 레포에 넣지 않는다.
지표
- 보임: 그룹별 먼저 보이는 3곳(카드) 안에 든 비율 — 사용자가 실제로 보는 것
- 통과: 기준을 통과해 어느 그룹에든 든 비율(더 보기 포함)
- 탈락 사유: 통과 못 했으면 목록에 없었는지 / 기준에서 빠졌는지

사용: python3 eval/run_rank_eval.py [--area 문정]   (같은 검색은 24시간 캐시라 네이버 재조회 없음)
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "naver-matjip", "scripts"))
import matjip  # noqa: E402

GOLD = os.path.expanduser(os.environ.get("NAVER_MATJIP_GOLD", "~/.config/naver-matjip/gold.json"))
ap = argparse.ArgumentParser()
ap.add_argument("--area", help="이 지역만")
a = ap.parse_args()
try:
    gold = json.load(open(GOLD))
except FileNotFoundError:
    sys.exit(f"정답지 없음: {GOLD}  예) {{\"문정\": [\"온달토종순대국\"]}}")

n = shown = passed = 0
for area, names in gold.items():
    if a.area and area != a.area:
        continue
    res = matjip.recommend(area, top=200)
    where = {}
    for g, ps in (res.get("groups") or {}).items():
        for i, p in enumerate(ps, 1):
            where[p["name"]] = (g, i)
    passed_names = {p["name"] for p in res["results"]}
    print(f"■ {area} — 통과 {res['passed_count']}곳{' (캐시)' if res['cached'] else ''}")
    for name in names:
        n += 1
        if name in where:
            g, i = where[name]
            ok = i <= 3
            shown += ok
            passed += 1
            print(f"  {'✅' if ok else '🔸'} {name}: {g} {i}위{'' if ok else ' (더 보기)'}")
        elif name in passed_names:
            passed += 1
            print(f"  🔸 {name}: 통과했지만 그룹 상위 {matjip.GROUP_MORE} 밖")
        else:
            print(f"  ❌ {name}: 통과 못 함 (제외 사유 합계: {res['excluded']})")
if n:
    print(f"\n보임 {shown}/{n} ({shown / n:.0%}) · 통과 {passed}/{n} ({passed / n:.0%})")
