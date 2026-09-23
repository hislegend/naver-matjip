#!/usr/bin/env python3
"""조건→키워드 번역 채점. Jev 키 필요.

cases.json: 손님 말(want) → 반드시 골라야 할 키워드(must), 골라도 되는 키워드(ok), 반대 키워드(opposite).
vocab.json: 7개 지역 상위 100곳씩에서 모은 네이버 방문자 키워드 전체.

지표
- must 재현율: must 키워드 중 관련 확률 ≥ 0.5 로 뽑힌 비율
- 1순위 적중: 확률 1위 키워드가 must∪ok 안에 있는 비율
- 오답 선택: ≥ 0.5 로 뽑혔는데 must∪ok 밖인 키워드 수(합계)
- 반대 탐지: opposite 키워드 중 반대로 판정(≥ 0.5, 관련보다 큼)된 비율
- 반대 오탐: must∪ok(좋은 키워드)를 반대로 잘못 판정한 수(합계)

사용: python3 eval/run_eval.py [--repeat 2] [--show-misses]
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "naver-matjip", "scripts"))
import matjip  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--repeat", type=int, default=1, help="같은 채점을 몇 번 반복할지(흔들림 측정)")
ap.add_argument("--show-misses", action="store_true")
a = ap.parse_args()

key = matjip.jev_key()
if not key:
    sys.exit("JEV_API_KEY 또는 ~/.config/jev/api_key 필요")
cases = json.load(open(os.path.join(HERE, "cases.json")))
vocab = [v for v in json.load(open(os.path.join(HERE, "vocab.json"))) if not v.endswith("맛있어요")]

runs = []
for r in range(a.repeat):
    rec, top1, false_picks, opp_hit, opp_tot, opp_false, misses = [], 0, 0, 0, 0, 0, []
    for c in cases:
        rel, opp = matjip.relevance(c["want"], vocab, key)
        good = set(c["must"]) | set(c["ok"])
        picked = {k for k, v in rel.items() if v >= 0.5}
        rec.append(sum(1 for m in c["must"] if m in picked) / len(c["must"]))
        best = max(rel, key=rel.get)
        top1 += best in good
        fp = sorted(picked - good)
        false_picks += len(fp)
        if opp is not None and c["opposite"]:
            opp_tot += len(c["opposite"])
            opp_hit += sum(1 for o in c["opposite"] if opp.get(o, 0) >= matjip.OPP_MIN and opp[o] > rel[o])
        if opp is not None:
            opp_false += sum(1 for g in good if opp.get(g, 0) >= matjip.OPP_MIN and opp[g] > rel.get(g, 0))
        if fp or rec[-1] < 1 or best not in good:
            misses.append((c["want"], best, [m for m in c["must"] if m not in picked], fp))
    runs.append({"must_recall": sum(rec) / len(rec), "top1": top1 / len(cases),
                 "false_picks": false_picks,
                 "opposite_recall": (opp_hit / opp_tot) if opp_tot else None, "opposite_false": opp_false,
                 "misses": misses})

for i, r in enumerate(runs, 1):
    opp = f"{r['opposite_recall']:.0%}" if r["opposite_recall"] is not None else "-"
    print(f"[{i}] must 재현율 {r['must_recall']:.0%} · 1순위 적중 {r['top1']:.0%} · "
          f"오답 선택 {r['false_picks']}개 · 반대 탐지 {opp} · 반대 오탐 {r['opposite_false']}개  ({len(cases)}문항)")
if a.show_misses:
    for want, best, missed, fp in runs[-1]["misses"]:
        print(f"  - {want}: 1위={best} | 놓침={missed} | 오답={fp}")
