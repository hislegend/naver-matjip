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
  옵션: --min-votes 100  --ratio 2.0  --top 5  --max-places 100  --json

조회: 네이버 플레이스(지도) GraphQL — 목록 2회 + 키워드 통계 4회(25곳씩 묶음) + 리뷰 1회(통과한 곳만).
주의: 네이버 약관상 자동 수집 제약이 있다. 사용자 요청 1건당 1~2회만 실행하고,
      반복 실행·대량 조회에 쓰지 않는다.
"""
import argparse, concurrent.futures as cf, json, os, sys, time
import urllib.error, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
GQL_URL = "https://pcmap-api.place.naver.com/graphql"
PAGE_SIZE = 50          # 목록 1회 조회 크기(네이버 최대 50)
STATS_BATCH = 25        # 키워드 통계를 한 요청에 묶는 개수
JEV_MAX = 40            # Jev 로 판정할 최대 후보 수(리뷰 조회 1회에 묶임)
JEV_URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_KEY_FILE = os.path.expanduser("~/.config/jev/api_key")

Q_LIST = ("query getRestaurants($input: RestaurantListInput) { restaurants: restaurantList(input: $input) "
          "{ total items { id name category visitorReviewCount } } }")
Q_STATS = ("query stats($id: String, $businessType: String) { visitorReviewStats(input: "
           "{businessId: $id, businessType: $businessType}) { id review { avgRating totalCount } "
           "analysis { votedKeyword { details { displayName count } } } } }")
Q_REVIEWS = ("query reviews($input: VisitorReviewsInput) { visitorReviews(input: $input) "
             "{ items { body } } }")


def gql(ops, timeout=20):
    """네이버 플레이스 GraphQL 에 여러 쿼리를 한 번에 보낸다(배치). 응답은 같은 순서의 리스트.
    429(요청 과다)면 잠깐 쉬었다가 두 번까지 다시 시도한다."""
    body = json.dumps(ops).encode()
    headers = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "*/*",
               "Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://pcmap.place.naver.com/"}
    for attempt in range(3):
        try:
            req = urllib.request.Request(GQL_URL, data=body, headers=headers)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise


def list_places(query, limit):
    """검색어로 식당 목록을 가져온다. limit 개까지(50개 단위)."""
    ops = [{"operationName": "getRestaurants", "query": Q_LIST, "variables": {"input": {
        "query": query, "start": start, "display": PAGE_SIZE, "isNmap": True}}}
        for start in range(1, limit + 1, PAGE_SIZE)]
    total, items = 0, []
    for r in gql(ops):
        data = ((r.get("data") or {}).get("restaurants") or {})
        total = max(total, data.get("total") or 0)
        items += data.get("items") or []
    seen, out = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return total, out[:limit]


def fetch_stats(places):
    """키워드 투표·별점을 STATS_BATCH 개씩 묶어 조회해 places 에 채운다."""
    chunks = [places[i:i + STATS_BATCH] for i in range(0, len(places), STATS_BATCH)]

    def one(chunk):
        return chunk, gql([{"operationName": "stats", "query": Q_STATS,
                            "variables": {"id": p["id"], "businessType": "restaurant"}} for p in chunk])

    with cf.ThreadPoolExecutor(4) as ex:
        for chunk, res in ex.map(one, chunks):
            for p, r in zip(chunk, res):
                st = ((r.get("data") or {}).get("visitorReviewStats") or {})
                kw = (((st.get("analysis") or {}).get("votedKeyword") or {}).get("details")) or []
                p["keywords"] = [(k["displayName"], int(k["count"])) for k in kw]
                p["rating"] = (st.get("review") or {}).get("avgRating")
                p["review_total"] = (st.get("review") or {}).get("totalCount")


def fetch_reviews(places, size=30):
    """최근 방문자 리뷰 본문을 한 요청으로 가져와 places 에 채운다(Jev 판정용)."""
    if not places:
        return
    res = gql([{"operationName": "reviews", "query": Q_REVIEWS, "variables": {"input": {
        "businessId": p["id"], "businessType": "restaurant", "page": 1, "size": size,
        "includeContent": True}}} for p in places])
    for p, r in zip(places, res):
        items = (((r.get("data") or {}).get("visitorReviews") or {}).get("items")) or []
        bodies = [i["body"].strip() for i in items if i.get("body")]
        # "맛있어요" 한 줄짜리는 조건 판단 근거가 안 된다 — 긴 리뷰부터 쓴다
        p["reviews"] = sorted([b for b in bodies if len(b) >= 20], key=len, reverse=True)


def taste_ratio(place):
    """맛 키워드("…맛있어요" 중 최다) 표수 / 그 외 키워드 최다 표수."""
    kw = place.get("keywords") or []
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


def jev_call(state, questions, key):
    body = json.dumps({"model": "jev-latest", "state": state, "questions": questions}).encode()
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


KW_MIN = 0.2   # 조건 관련성이 이 값 미만인 키워드는 무시(잡음)


def map_want_to_keywords(want, keyword_names, key):
    """손님의 말(want)을 네이버 방문자 키워드로 번역한다. {키워드: 관련 확률}.
    키워드 이름을 질문 문장에 직접 넣는다 — 배열 인덱스로 가리키면 판정이 흐려진다(2026-09-23 실측)."""
    q = {f"k{i}": {
        "type": "noul",
        "instructions": f"식당 방문자들이 '{n}' 라는 키워드를 많이 골랐다. 이 사실이 손님의 요청"
                        f"(`customer_request`)에 맞는 식당이라는 직접적인 근거인가?",
        "criteria": {"true": f"'{n}' 는 손님이 원하는 바로 그 특징이다",
                     "false": f"'{n}' 는 손님 요청과 관계없는 다른 장점이다"},
    } for i, n in enumerate(keyword_names)}
    ans = jev_call({"customer_request": want}, q, key)
    rel = {n: ans[f"k{i}"]["noul"] for i, n in enumerate(keyword_names)}
    return {n: v for n, v in sorted(rel.items(), key=lambda x: -x[1]) if v >= KW_MIN}


def condition_score(place, weights):
    """조건 키워드에 몰린 표의 비중(관련 확률로 가중). 0~1. 근거 키워드 목록도 돌려준다."""
    kw = dict(place.get("keywords") or [])
    total = sum(kw.values()) or 1
    hits = [(n, kw[n]) for n in weights if kw.get(n)]
    score = sum(weights[n] * c for n, c in hits) / total
    return score, sorted([h for h in hits if h[1] >= 5], key=lambda x: -x[1])


def judge_sponsored(cands, key):
    """식당마다 최근 리뷰로 체험단·협찬 위주인지 판정(식당당 1요청, 병렬). {id: 확률}."""
    q = {"sponsored": {
        "type": "noul",
        "instructions": "`recent_reviews` 가 체험단·협찬·이벤트 참여로 쓴 리뷰 위주인가?",
        "criteria": {
            "true": "제공받음·체험단·이벤트 참여·과장된 홍보 문구가 반복되는 리뷰가 절반 이상이다",
            "false": "대부분 직접 방문한 손님의 평범한 후기다",
        },
    }}

    def one(c):
        if not c.get("reviews"):
            return c["id"], None
        return c["id"], jev_call({"recent_reviews": [r[:300] for r in c["reviews"][:8]]}, q, key)["sponsored"]["noul"]

    with cf.ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, cands))


def recommend(area, food_type="", want="", min_votes=100, ratio=2.0, top=5, max_places=100):
    """조회→1차 숫자 필터→(가능하면) Jev 판정. 결과 dict 를 돌려준다. CLI·MCP 공용."""
    t0 = time.time()
    query = f"{area} {food_type} 맛집".replace("  ", " ") if food_type else f"{area} 맛집"
    errors = []
    try:
        total, places = list_places(query, max_places)
    except Exception as e:
        total, places = 0, []
        errors.append(f"목록 조회 실패: {type(e).__name__} {e}")
    if places:
        try:
            fetch_stats(places)
        except Exception as e:
            errors.append(f"키워드 통계 조회 실패: {type(e).__name__} {e}")
    for p in places:
        p["url"] = f"https://m.place.naver.com/restaurant/{p['id']}/home"

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

    k = jev_key()
    jev = {"used": False, "note": "Jev 미연결(키 없음) — 숫자 기준만 적용" if not k else "Jev 판정할 후보 없음"}
    want_keywords = {}
    if k and passed:
        cands = passed[:JEV_MAX]
        try:
            if want:
                names = sorted({n for p in cands for n, _ in p["keywords"] if not n.endswith("맛있어요")})
                want_keywords = map_want_to_keywords(want, names, k)
                for c in cands:
                    c["condition_score"], c["condition_evidence"] = condition_score(c, want_keywords)
            fetch_reviews(cands)
            spons = judge_sponsored(cands, k)
            for c in cands:
                c["jev_sponsored"] = spons.get(c["id"])
            # 협찬 의심(0.7 이상)은 뒤로 → 조건 점수 높은 순 → 맛 배수 순
            passed.sort(key=lambda p: ((p.get("jev_sponsored") or 0) >= 0.7,
                                       -(p.get("condition_score") or 0), -p["ratio"]))
            note = f"Jev 판정 적용({len(cands)}곳)"
            if want:
                note += (f" — '{want}' → " + ", ".join(f"{n}({v:.2f})" for n, v in list(want_keywords.items())[:4])
                         if want_keywords else f" — '{want}' 에 맞는 네이버 키워드를 찾지 못함, 맛 순서 유지")
            jev = {"used": True, "note": note}
        except Exception as e:
            jev = {"used": False, "note": f"Jev 실패({type(e).__name__}) — 숫자 기준만 적용"}

    for p in passed:
        p.pop("reviews", None)  # 출력에는 리뷰 원문을 싣지 않는다
        p["keywords"] = p.get("keywords", [])[:6]
        p.pop("visitorReviewCount", None)
    return {"area": area, "food_type": food_type, "want": want,
            "rule": {"min_votes": min_votes, "ratio": ratio},
            "query": query, "naver_total": total, "want_keywords": want_keywords,
            "checked": len(places), "passed_count": len(passed), "results": passed[:top],
            "excluded_few_votes": few_votes, "jev": jev, "errors": errors,
            "seconds": round(time.time() - t0, 1)}


def format_text(res):
    r = res["rule"]
    out = [f"{res['query']} — 네이버 {res['naver_total']:,}곳 중 상위 {res['checked']}곳 조회, "
           f"맛 투표 {r['min_votes']}표 이상 & 2위 키워드의 {r['ratio']:g}배 이상 = {res['passed_count']}곳 "
           f"({res['seconds']}초)", res["jev"]["note"]]
    for n, p in enumerate(res["results"], 1):
        line = (f"{n}. {p['name']} ({p['category']}) — {p['taste_key']} {p['taste_votes']} / "
                f"2위 {p['second_votes']} = {p['ratio']:.1f}배")
        if p.get("rating"):
            line += f" · 별점 {p['rating']}"
        if p.get("condition_evidence"):
            line += " · 근거: " + ", ".join(f"{n} {c}표" for n, c in p["condition_evidence"][:3])
        if (p.get("jev_sponsored") or 0) >= 0.7:
            line += " · ⚠️협찬 리뷰 의심"
        out += [line, f"   {p['url']}"]
    if res["checked"] == 0:
        out.append(f"'{res['query']}' 검색 결과에서 식당을 찾지 못했다."
                   + (f" ({'; '.join(res['errors'])})" if res["errors"] else ""))
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
    ap.add_argument("--max-places", type=int, default=100, help="조회할 식당 수(최대 200)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = recommend(" ".join(a.area), a.type, a.want, a.min_votes, a.ratio, a.top,
                    max(1, min(a.max_places, 200)))
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else format_text(res))
    return 0 if res["checked"] else 1


if __name__ == "__main__":
    sys.exit(main())
