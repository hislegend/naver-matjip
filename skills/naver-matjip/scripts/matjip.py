#!/usr/bin/env python3
"""matjip.py — 네이버 플레이스 맛집 추리기 (naver-matjip)

1차(숫자): 방문자 키워드 투표에서 "…맛있어요" 표가 2위 키워드의 N배(기본 2배) 이상인 식당만 남긴다.
2차(Jev):  JEV_API_KEY 가 있으면 통과한 곳을 TypeSafe Jev 로 판정한다.
           - 조건 적합도(--want 가 있을 때, Score 5단계)
           - 체험단·협찬 리뷰 의심(Noul)
           Jev 가 없거나 실패하면 1차 결과 그대로 낸다(폴백 불변식).

사용:
  python3 matjip.py 성수동
  python3 matjip.py 을지로 --type 한식 --want "조용히 대화하기 좋은 곳"
  옵션: --min-votes 100  --ratio 2.0  --top 5  --json

주의: 네이버 약관상 자동 수집 제약이 있다. 요청 1회당 검색 2~3번 + 식당 최대 40곳만 조회한다.
      반복 실행·대량 조회에 쓰지 않는다.
"""
import argparse, concurrent.futures as cf, html, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
MAX_PLACES = 40
JEV_URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_KEY_FILE = os.path.expanduser("~/.config/jev/api_key")


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def search_ids(query):
    s = get("https://m.search.naver.com/search.naver?where=m&query=" + urllib.parse.quote(query))
    return list(dict.fromkeys(re.findall(r"/restaurant/(\d+)", s)))


def unjson(s):
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def fetch_place(pid):
    p = get(f"https://m.place.naver.com/restaurant/{pid}/review/visitor")
    kw = [(html.unescape(n), int(c)) for n, c in re.findall(
        r'<span class="sP19k">&quot;<!-- -->([^<]+)<!-- -->&quot;</span>'
        r'<span class="CUoLy"><span class="place_blind">[^<]*</span>(\d+)', p)]
    name = re.search(r'"PlaceDetailBase:%s":\{"__typename":"PlaceDetailBase","id":"%s","name":"([^"]+)"' % (pid, pid), p)
    cat = re.search(r'"category":"([^"]{1,30})"', p)
    score = re.search(r'"visitorReviewsScore":([\d.]+)', p)
    total = re.search(r'"visitorReviewsTotal":(\d+)', p)
    reviews = [unjson(b) for b in re.findall(
        r'"VisitorReview:[^"]+":\{"__typename":"VisitorReview".{0,400}?"body":"((?:[^"\\]|\\.){10,600})"', p)][:10]
    return {
        "id": pid,
        "name": unjson(name.group(1)) if name else "?",
        "category": cat.group(1) if cat else "",
        "rating": float(score.group(1)) if score else None,
        "review_total": int(total.group(1)) if total else None,
        "keywords": kw,
        "reviews": reviews,
        "url": f"https://m.place.naver.com/restaurant/{pid}/home",
    }


def taste_ratio(place):
    """맛 키워드("…맛있어요" 중 최다) 표수 / 그 외 키워드 최다 표수."""
    kw = place["keywords"]
    tastes = [(n, c) for n, c in kw if n.endswith("맛있어요")]
    if not tastes:
        return None, 0, 0, 0.0
    key, top = max(tastes, key=lambda x: x[1])
    rest = max([c for n, c in kw if n != key] or [0])
    return key, top, rest, top / (rest or 1)


# ── Jev ──────────────────────────────────────────────────────────
def jev_key():
    k = os.environ.get("JEV_API_KEY", "").strip()
    if not k and os.path.exists(JEV_KEY_FILE):
        k = open(JEV_KEY_FILE).read().strip()
    return k


FIT_LEVELS = [
    "조건과 반대다 — 요청한 조건을 명확히 어긴다는 리뷰가 있다",
    "조건과 맞지 않는 편이다",
    "알 수 없다 — 리뷰에 조건 관련 내용이 거의 없다",
    "조건에 맞는 편이다 — 관련 언급이 일부 있다",
    "조건에 잘 맞는다 — 여러 리뷰가 조건을 직접 뒷받침한다",
]


def jev_judge(cands, want, key):
    """후보 전체를 한 state 에 넣고 질문을 병렬로 묻는다(1요청). 실패 시 예외."""
    state = {
        "request": want or "",
        "candidates": [{
            "name": c["name"], "category": c["category"], "rating": c["rating"],
            "top_keywords": [f"{n} {n2}" for n, n2 in c["keywords"][:6]],
            "recent_reviews": [r[:300] for r in c["reviews"][:6]],
        } for c in cands],
    }
    q = {}
    for i, _ in enumerate(cands):
        if want:
            q[f"fit_{i}"] = {
                "type": "score",
                "instructions": f"`candidates[{i}]` 식당이 `request` 에 적힌 손님의 조건에 얼마나 맞는가? "
                                f"`candidates[{i}].recent_reviews` 와 `top_keywords` 만 근거로 판단한다.",
                "criteria": FIT_LEVELS,
            }
        q[f"ad_{i}"] = {
            "type": "noul",
            "instructions": f"`candidates[{i}].recent_reviews` 가 체험단·협찬·이벤트 참여로 쓴 리뷰 위주인가?",
            "criteria": {
                "true": "제공받음·체험단·이벤트 참여·과장된 홍보 문구가 반복되는 리뷰가 절반 이상이다",
                "false": "대부분 직접 방문한 손님의 평범한 후기다",
            },
        }
    body = json.dumps({"model": "jev-latest", "state": state, "questions": q}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(JEV_URL, data=body, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=20).read())["answers"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise


def recommend(area, food_type="", want="", min_votes=100, ratio=2.0, top=5):
    """조회→1차 숫자 필터→(가능하면) Jev 판정. 결과 dict 를 돌려준다. CLI·MCP 공용."""
    t0 = time.time()
    queries = [f"{area} {food_type} 맛집".replace("  ", " "), f"{area} 맛집 추천"]
    if food_type:
        queries.append(f"{area} {food_type}")
    ids, errors = [], []
    for qq in queries:
        try:
            ids += [i for i in search_ids(qq) if i not in ids]
        except Exception as e:
            errors.append(f"검색 실패: {qq} ({e})")
    ids = ids[:MAX_PLACES]

    places = []
    with cf.ThreadPoolExecutor(6) as ex:
        for f in cf.as_completed([ex.submit(fetch_place, i) for i in ids]):
            try:
                places.append(f.result())
            except Exception:
                pass

    passed, few_votes = [], 0
    for p in places:
        key, t, rest, r = taste_ratio(p)
        p.update(taste_key=key, taste_votes=t, second_votes=rest, ratio=r)
        if key is None:
            continue
        if t < min_votes:
            few_votes += 1
            continue
        if r >= ratio:
            passed.append(p)
    passed.sort(key=lambda p: (-p["ratio"], -p["taste_votes"]))

    jev = {"used": False, "note": "Jev 미연결(키 없음) — 숫자 기준만 적용"}
    k = jev_key()
    if k and passed:
        cands = passed[:20]
        try:
            ans = jev_judge(cands, want, k)
            for i, c in enumerate(cands):
                fit, ad = ans.get(f"fit_{i}"), ans.get(f"ad_{i}")
                c["jev_fit"] = fit["score"] if fit else None
                c["jev_fit_confidence"] = fit.get("confidence") if fit else None
                c["jev_sponsored"] = ad["noul"] if ad else None
            # 협찬 의심(0.7 이상)은 뒤로, 조건 적합도 높은 순
            passed.sort(key=lambda p: ((p.get("jev_sponsored") or 0) >= 0.7,
                                       -(p.get("jev_fit") or 0), -p["ratio"]))
            jev = {"used": True, "note": "Jev 판정 적용" + (f" — 조건: {want}" if want else " — 협찬 의심만")}
        except Exception as e:
            jev = {"used": False, "note": f"Jev 실패({type(e).__name__}) — 숫자 기준만 적용"}

    for p in passed:
        p.pop("reviews", None)  # 출력에는 리뷰 원문을 싣지 않는다
        p["keywords"] = p["keywords"][:6]
    return {"area": area, "food_type": food_type, "want": want,
            "rule": {"min_votes": min_votes, "ratio": ratio},
            "checked": len(places), "passed_count": len(passed), "results": passed[:top],
            "excluded_few_votes": few_votes, "jev": jev, "errors": errors,
            "seconds": round(time.time() - t0, 1)}


def format_text(res):
    r = res["rule"]
    out = [f"{res['area']}{' ' + res['food_type'] if res['food_type'] else ''} — {res['checked']}곳 조회, "
           f"맛 투표 {r['min_votes']}표 이상 & 2위 키워드의 {r['ratio']:g}배 이상 = {res['passed_count']}곳 "
           f"({res['seconds']}초)", res["jev"]["note"]]
    for n, p in enumerate(res["results"], 1):
        line = (f"{n}. {p['name']} ({p['category']}) — {p['taste_key']} {p['taste_votes']} / "
                f"2위 {p['second_votes']} = {p['ratio']:.1f}배")
        if p.get("rating"):
            line += f" · 별점 {p['rating']}"
        if p.get("jev_fit") is not None:
            line += f" · 조건적합 {p['jev_fit']:.1f}/4"
        if (p.get("jev_sponsored") or 0) >= 0.7:
            line += " · ⚠️협찬 리뷰 의심"
        out += [line, f"   {p['url']}"]
    if res["checked"] == 0:
        out.append(f"'{res['area']}' 검색 결과에서 식당을 찾지 못했다.")
    elif not res["results"]:
        out.append("통과한 곳이 없다. --ratio 1.5 로 낮춰 보거나 지역을 넓혀 볼 것.")
    if res["excluded_few_votes"]:
        out.append(f"(투표 {r['min_votes']}표 미만이라 제외: {res['excluded_few_votes']}곳)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="네이버 플레이스 맛집 추리기")
    ap.add_argument("area", nargs="+", help="지역(예: 성수동)")
    ap.add_argument("--type", default="", help="음식 종류(예: 한식, 고깃집)")
    ap.add_argument("--want", default="", help="조건(예: 조용히 대화하기 좋은 곳) — Jev 판정에 씀")
    ap.add_argument("--min-votes", type=int, default=100)
    ap.add_argument("--ratio", type=float, default=2.0)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = recommend(" ".join(a.area), a.type, a.want, a.min_votes, a.ratio, a.top)
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else format_text(res))
    return 0 if res["checked"] else 1


if __name__ == "__main__":
    sys.exit(main())
