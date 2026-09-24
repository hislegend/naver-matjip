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
import argparse, concurrent.futures as cf, hashlib, json, math, os, re, sys, time
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
          "{ total items { id name category priceCategory roadAddress address imageUrl newBusinessHours { status description } } } }")
Q_STATS = ("query stats($id: String, $businessType: String) { visitorReviewStats(input: "
           "{businessId: $id, businessType: $businessType}) { id review { avgRating totalCount } "
           "analysis { votedKeyword { details { displayName count } } menus { label count } } } }")
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


CACHE_DIR = os.path.expanduser(os.environ.get("NAVER_MATJIP_CACHE", "~/.cache/naver-matjip"))
CACHE_TTL = 24 * 3600


def cache_get(name):
    if os.environ.get("NAVER_MATJIP_NO_CACHE"):
        return None
    f = os.path.join(CACHE_DIR, name + ".json")
    try:
        if time.time() - os.path.getmtime(f) < CACHE_TTL:
            return json.load(open(f))
    except (OSError, ValueError):
        pass
    return None


def cache_put(name, value):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = os.path.join(CACHE_DIR, name + ".tmp")
        json.dump(value, open(tmp, "w"), ensure_ascii=False)
        os.replace(tmp, os.path.join(CACHE_DIR, name + ".json"))
    except OSError:
        pass


def cache_key(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def load_places(query, limit):
    """목록 + 키워드·메뉴 통계. 24시간 캐시. (total, places, from_cache)"""
    name = "places3-" + cache_key(query, limit)  # places3: 지번 주소(동) 추가 후 캐시
    hit = cache_get(name)
    if hit:
        return hit["total"], hit["places"], True
    total, places = list_places(query, limit)
    if places:
        fetch_stats(places)
        cache_put(name, {"total": total, "places": places})
    return total, places, False


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
                p["menus"] = [(m["label"], int(m["count"])) for m in ((st.get("analysis") or {}).get("menus") or [])]
                p["rating"] = (st.get("review") or {}).get("avgRating")
                p["review_total"] = (st.get("review") or {}).get("totalCount")


def fetch_reviews(places, size=30):
    """최근 방문자 리뷰 본문을 한 요청으로 가져와 places 에 채운다(Jev 판정용). 식당별 24시간 캐시."""
    todo = []
    for p in places:
        hit = cache_get("reviews-" + p["id"])
        if hit is not None:
            p["reviews"] = hit
        else:
            todo.append(p)
    places = todo
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
        cache_put("reviews-" + p["id"], p["reviews"])


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
OPP_MIN = 0.6  # 반대 근거로 볼 최소 확률 (eval: 0.5=탐지90%·과잉37, 0.6=80%·17, 0.7=50%·8)
VAGUE = 0.5    # 관련 키워드 최고 확률이 이보다 낮으면 "애매한 조건" → 리뷰 문장 보조 판정


def relevance(want, keyword_names, key):
    """손님의 말(want)과 네이버 방문자 키워드 각각의 관계를 한 요청으로 판정한다(투기적 팬아웃).
    (관련 {키워드: p}, 반대 {키워드: p})
    키워드 이름을 질문 문장에 직접 넣는다 — 배열 인덱스로 가리키면 판정이 흐려진다(2026-09-23 실측)."""
    q = {}
    for i, n in enumerate(keyword_names):
        q[f"k{i}"] = {
            "type": "noul",
            "instructions": f"식당 방문자들이 '{n}' 라는 키워드를 많이 골랐다. 이 사실이 손님의 요청"
                            f"(`customer_request`)에 맞는 식당이라는 직접적인 근거인가?",
            "criteria": {"true": f"'{n}' 는 손님이 원하는 바로 그 특징이다",
                         "false": f"'{n}' 는 손님 요청과 관계없는 다른 장점이다"},
        }
        q[f"o{i}"] = {
            "type": "noul",
            "instructions": f"식당 방문자들이 '{n}' 라는 키워드를 많이 골랐다. 이 사실이 손님의 요청"
                            f"(`customer_request`)과 반대되는 식당, 즉 손님이 피하고 싶어 할 식당이라는 근거인가?",
            "criteria": {"true": f"'{n}' 가 많은 식당은 손님이 원하는 것과 정반대다",
                         "false": f"'{n}' 는 손님 요청과 충돌하지 않는다"},
        }
    ans = jev_call({"customer_request": want}, q, key)
    rel = {n: ans[f"k{i}"]["noul"] for i, n in enumerate(keyword_names)}
    opp = {n: ans[f"o{i}"]["noul"] for i, n in enumerate(keyword_names)}
    return rel, opp


def map_want_to_keywords(want, keyword_names, key):
    """관련 확률 KW_MIN 이상 키워드(높은 순)와 반대 확률 OPP_MIN 이상 키워드. 관련이 더 크면 반대에서 뺀다."""
    rel, opp = relevance(want, keyword_names, key)
    pos = {n: v for n, v in sorted(rel.items(), key=lambda x: -x[1]) if v >= KW_MIN}
    neg = {n: v for n, v in sorted(opp.items(), key=lambda x: -x[1]) if v >= OPP_MIN and v > rel[n]}
    return pos, neg


def match_menus(menu, labels, key, chunk=150):
    """손님이 찾는 메뉴(menu)에 해당하는 리뷰 메뉴 라벨을 고른다. {라벨: p}"""
    labels = sorted(set(labels))
    out = {}

    def one(part):
        q = {f"m{i}": {
            "type": "noul",
            "instructions": f"리뷰에 '{l}' 라는 메뉴가 언급됐다. 이 메뉴는 손님이 찾는 음식(`wanted_menu`)에 해당하는가?",
            "criteria": {"true": f"'{l}' 는 손님이 찾는 그 음식이거나 그 음식의 한 종류다",
                         "false": f"'{l}' 는 다른 음식이다"},
        } for i, l in enumerate(part)}
        ans = jev_call({"wanted_menu": menu}, q, key)
        return {l: ans[f"m{i}"]["noul"] for i, l in enumerate(part)}

    parts = [labels[i:i + chunk] for i in range(0, len(labels), chunk)]
    with cf.ThreadPoolExecutor(4) as ex:
        for r in ex.map(one, parts):
            out.update(r)
    return {l: v for l, v in out.items() if v >= 0.5}


def review_fit(cands, want, key):
    """조건에 딱 맞는 키워드가 없을 때만 쓰는 보조 판정. 식당마다 '조건과 닿는 리뷰 문장'만 보내
    근거가 있는지 묻는다(무관한 문장이 섞이면 흐려진다 — 공식 한계 5번). {id: p}"""
    q = {"fit": {
        "type": "noul",
        "instructions": "`review_sentences` 에 이 식당이 손님의 요청(`customer_request`)에 맞는다는 직접적인 근거가 있는가?",
        "criteria": {"true": "요청한 상황·분위기에 잘 맞았다는 방문자 문장이 있다",
                     "false": "그런 문장이 없거나, 맞지 않았다는 문장이 있다"},
    }}

    def one(c):
        sents = [x.strip() for r in c.get("reviews", []) for x in re.split(r"[.!?\n~]+", r) if len(x.strip()) >= 8]
        if not sents:
            return c["id"], None
        return c["id"], jev_call({"customer_request": want, "review_sentences": sents[:25]}, q, key)["fit"]["noul"]

    with cf.ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, cands))


def condition_score(place, weights):
    """조건 키워드에 몰린 표의 비중(관련 확률로 가중). 0~1. 근거 키워드 목록도 돌려준다."""
    kw = dict(place.get("keywords") or [])
    total = sum(kw.values()) or 1
    hits = [(n, kw[n]) for n in weights if kw.get(n)]
    score = sum(weights[n] * c for n, c in hits) / total
    return score, sorted([h for h in hits if h[1] >= 5], key=lambda x: -x[1])


UNFIT_CUT = 0.7     # 업종이 조건과 안 맞을 확률이 이 이상이면 뺀다
REVIEW_FLAG = 0.7   # 리뷰 판정(이벤트·맛 변함·웨이팅·세부) 표시 기준
# 조건 세부 확인(⑤)에 쓸 보기. Jev 가 조건에 중요한 것만 고른다(최대 DETAIL_MAX).
DETAILS = {"아기의자": "👶", "놀이방·키즈존": "🧸", "주차": "🅿️", "룸·개별 공간": "🚪", "단체석": "👥",
           "조용한 분위기": "🤫", "반려견(강아지) 동반": "🐶", "혼자 앉기 좋은 바 좌석": "🪑", "콘센트·노트북": "💻",
           "창밖 뷰·야경": "🌃", "예약": "📅", "휠체어·유모차 출입": "♿", "무한리필": "♾️", "포장": "🥡"}
DETAIL_MAX = 3


def category_unfit(want, categories, key):
    """업종 이름만 보고 조건과 안 맞는 업종을 고른다(①). {업종: 확률}"""
    q = {f"c{i}": {
        "type": "noul",
        "instructions": f"'{c}' 업종 식당은 손님의 요청(`customer_request`)에 어울리지 않는 곳인가?",
        "criteria": {"true": f"'{c}' 는 요청한 상황에 맞지 않는 업종이다(예: 아이와 가려는데 술집)",
                     "false": f"'{c}' 업종이라는 것만으로 요청과 충돌하지 않는다"},
    } for i, c in enumerate(categories)}
    ans = jev_call({"customer_request": want}, q, key)
    return {c: ans[f"c{i}"]["noul"] for i, c in enumerate(categories)}


def pick_details(want, key):
    """조건에 중요한 세부 정보 보기를 고른다(⑤). [보기, …]"""
    names = list(DETAILS)
    q = {f"d{i}": {
        "type": "noul",
        "instructions": f"손님의 요청(`customer_request`)대로 식당을 고를 때 '{d}' 여부가 중요한 정보인가?"
                        f" 요청에 없는 동행자(예: 아이와 간다는데 반려견)는 중요하지 않다.",
        "criteria": {"true": f"'{d}' 는 이 손님에게 꼭 알려줘야 할 정보다",
                     "false": f"'{d}' 는 이 요청과 별 관계없다"},
    } for i, d in enumerate(names)}
    ans = jev_call({"customer_request": want}, q, key)
    ranked = sorted(((ans[f"d{i}"]["noul"], d) for i, d in enumerate(names)), reverse=True)
    return [d for v, d in ranked if v >= 0.6][:DETAIL_MAX]


def judge_reviews(cands, key, details=()):
    """식당마다 최근 리뷰로 한 번에 판정한다(식당당 1요청, 병렬). {id: {질문: 확률}}
    협찬 · 리뷰 이벤트(②보강) · 맛 변함(②) · 웨이팅(③) · 조건 세부(⑤)."""
    q = {
        "sponsored": {"type": "noul",
                      "instructions": "`recent_reviews` 가 체험단·협찬으로 쓴 리뷰 위주인가?",
                      "criteria": {"true": "제공받음·체험단·과장된 홍보 문구가 반복되는 리뷰가 절반 이상이다",
                                   "false": "대부분 직접 방문한 손님의 평범한 후기다"}},
        "event": {"type": "noul",
                  "instructions": "`recent_reviews` 에 리뷰 이벤트(리뷰 쓰면 음료·서비스 제공)에 참여해 쓴 리뷰가 많은가?",
                  "criteria": {"true": "리뷰 이벤트·서비스 받고 작성했다는 리뷰가 여러 건이다",
                               "false": "리뷰 이벤트 참여를 밝힌 리뷰가 거의 없다"}},
        "declined": {"type": "noul",
                     "instructions": "`recent_reviews` 에 예전보다 맛이나 서비스가 나빠졌다는 말이 여러 건 있는가?",
                     "criteria": {"true": "예전만 못하다·맛이 변했다·주인이 바뀌었다 같은 불만이 둘 이상이다",
                                  "false": "그런 말이 없거나 한 건뿐이다"}},
        "waiting": {"type": "noul",
                    "instructions": "`recent_reviews` 에 웨이팅(대기)이 길다는 말이 여러 건 있는가?",
                    "criteria": {"true": "30분 넘게 기다렸다·줄이 길다는 리뷰가 둘 이상이다",
                                 "false": "대기 얘기가 없거나 짧았다고 한다"}},
    }
    for i, d in enumerate(details):
        q[f"d{i}"] = {"type": "noul",
                      "instructions": f"`recent_reviews` 에 이 식당에 '{d}' 이(가) 있다·좋았다는 언급이 있는가?",
                      "criteria": {"true": f"'{d}' 가 있다·편했다는 방문자 문장이 있다",
                                   "false": f"'{d}' 언급이 없거나 없다·불편했다고 한다"}}

    def one(c):
        if not c.get("reviews"):
            return c["id"], {}
        ans = jev_call({"recent_reviews": [r[:250] for r in c["reviews"][:12]]}, q, key)
        out = {k: ans[k]["noul"] for k in ("sponsored", "event", "declined", "waiting")}
        out["details"] = [d for i, d in enumerate(details) if ans[f"d{i}"]["noul"] >= REVIEW_FLAG]
        return c["id"], out

    with cf.ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, cands))


OPEN_STATUSES = ("영업 중", "곧 영업 종료", "24시간 영업")
# 맛 합격(2026-09-25): 둘 중 하나만 걸려도 통과.
#  ① 배수 — 맛 표가 2위 키워드의 N배. 리뷰가 많을수록 다른 칭찬도 쌓여 배수가 낮아지므로
#     리뷰 1천 미만은 ratio×1.5(기본 3배), 1천 이상은 ratio(기본 2배). 국밥집처럼 "맛 말고 누를 게 없는 집"을 잡는다.
#  ② 비율 — 방문자리뷰 중 맛 표 비율 SHARE_MIN 이상. 맛·친절·신선 칭찬이 고루 쌓여 배수가 낮은 집을 잡는다.
LOW_REVIEWS = 1000
SHARE_MIN = 0.85
# 리뷰 수 그룹 — 그룹마다 따로 추천한다.
GROUPS = (("대형 맛집", 10000), ("검증된 맛집", LOW_REVIEWS), ("숨은 맛집", 0))
GROUP_TOP = 3
# 조건을 줬는데 그 조건 키워드 표가 이만큼도 없으면 뺀다(2026-09-25: "아이랑"에 근거 0표인 요리주점이 올라옴)
COND_MIN_VOTES = 10


def area_filter(places, area):
    """지번 주소의 동 이름이 지역과 맞는 곳만(⑦ 옆 동네 거르기). 지역이 동 이름이 아니면(예: 강남역·홍대)
    맞는 곳이 30% 미만이라 거르지 않는다. (남길 목록, 뺀 수)"""
    core = re.sub(r"(동|역|구|시|읍|면)$", "", area.split()[0]) if area else ""
    if len(core) < 2:
        return places, 0
    hit = [p for p in places if (p.get("address") or "").split(" ")[0].startswith(core)]
    if len(hit) < 0.3 * len(places):
        return places, 0
    return hit, len(places) - len(hit)


def review_group(n):
    return next(name for name, lo in GROUPS if (n or 0) >= lo)


def taste_pass(p, ratio):
    """통과 사유 목록 — [] 면 탈락."""
    need = ratio * 1.5 if (p.get("review_total") or 0) < LOW_REVIEWS else ratio
    why = []
    if p["ratio"] >= need:
        why.append("배수")
    if p["share"] >= SHARE_MIN:
        why.append("비율")
    return why
# 종합 점수 가중치(코드가 정한다 — 공식 권장 "Composite scoring"). 양수 항목은 쓰이는 것끼리 합이 1이 되게 다시 나눈다.
WEIGHTS = {"맛": 0.45, "별점": 0.2, "표 규모": 0.15, "조건": 0.5, "리뷰 문장": 0.25, "메뉴": 0.35}
PENALTY = 0.2          # 반대 키워드 감점 가중치
OPP_FULL = 0.25        # 반대 키워드 표가 전체 표의 이 비중이면 감점 최대
SPONSORED_CUT = 0.7    # 협찬 의심 이상이면 점수 ×0.6


def price_manwon(cat):
    """'3만원 대' → 3, '1만원 미만' → 0.9, 없으면 None"""
    if not cat:
        return None
    m = re.search(r"(\d+)\s*만\s*원", cat)
    if not m:
        return None
    n = int(m.group(1))
    return n - 0.1 if "미만" in cat else n


def composite(c, maxes, use):
    """0~100 점과 항목별 기여(점)."""
    parts = {
        # 배수·비율 중 나은 쪽(합격도 둘 중 하나로 하므로). 비율 50%→0, 100%→1
        "맛": max(min(c["ratio"], 4.0) / 4.0, min(max((c["share"] - 0.5) / 0.5, 0.0), 1.0)),
        "별점": min(max(((c.get("rating") or 4.5) - 4.0) / 1.0, 0.0), 1.0),
        "표 규모": min(max(math.log10(max(c["taste_votes"], 1)) / math.log10(5000), 0.0), 1.0),
    }
    if "조건" in use:
        parts["조건"] = c.get("condition_score", 0) / maxes["조건"] if maxes["조건"] else 0.0
    if "리뷰 문장" in use:
        parts["리뷰 문장"] = c.get("review_fit") or 0.0
    if "메뉴" in use:
        parts["메뉴"] = c.get("menu_share", 0) / maxes["메뉴"] if maxes["메뉴"] else 0.0
    wsum = sum(WEIGHTS[k] for k in parts)
    pts = {k: 100 * WEIGHTS[k] / wsum * v for k, v in parts.items()}
    total = sum(pts.values())
    if c.get("opposite_score"):
        # 반대 표 비중 자체로 감점(전체 표의 OPP_FULL 이상이면 최대). 예전엔 조건 최고점으로 나눠
        # 조건 표가 적은 지역(아이랑 30표)에선 반대 표가 조금만 있어도 최대 감점이 됐다(맥도날드 31점).
        pen = 100 * PENALTY * min(c["opposite_score"] / OPP_FULL, 1.0)
        pts["반대 감점"] = -pen
        total -= pen
    if (c.get("jev_sponsored") or 0) >= SPONSORED_CUT:
        pts["협찬 의심"] = -0.4 * total
        total *= 0.6
    if (c.get("jev_declined") or 0) >= REVIEW_FLAG:
        pts["맛 변함 의심"] = -0.2 * total
        total *= 0.8
    return round(max(total, 0), 1), {k: round(v, 1) for k, v in pts.items()}


def recommend(area, food_type="", want="", menu="", open_now=False, max_price=None,
              min_votes=100, ratio=2.0, top=5, max_places=100):
    """조회 → 거르기(영업·가격·맛) → Jev(조건·반대·메뉴·협찬) → 종합 점수. CLI·MCP 공용."""
    t0 = time.time()
    query = " ".join(x for x in (area, food_type or menu, "맛집") if x)
    errors, notes = [], []
    try:
        total, places, cached = load_places(query, max_places)
    except Exception as e:
        total, places, cached = 0, [], False
        errors.append(f"조회 실패: {type(e).__name__} {e}")
    listed = len(places)
    places, off_area = area_filter(places, area)
    for p in places:
        p["url"] = f"https://m.place.naver.com/restaurant/{p['id']}/home"
        p["status"] = ((p.get("newBusinessHours") or {}).get("status")) or None
        p["price"] = p.get("priceCategory")

    excluded = {"옆 동네": off_area, "카페": 0, "영업 안 함·정보 없음": 0, "가격 초과": 0, "투표 적음": 0, "맛 기준 미달": 0,
                "메뉴 언급 없음": 0, "조건 근거 없음": 0, "업종이 조건과 안 맞음": 0, "이벤트 리뷰로 부푼 비율": 0}
    passed = []
    no_cafe = not food_type and not menu   # "맛집"만 물으면 카페는 뺀다
    for p in places:
        if no_cafe and "카페" in (p.get("category") or ""):
            excluded["카페"] += 1
            continue
        if open_now and p["status"] not in OPEN_STATUSES:
            excluded["영업 안 함·정보 없음"] += 1
            continue
        pm = price_manwon(p["price"])
        if max_price is not None and pm is not None and pm > max_price:
            excluded["가격 초과"] += 1
            continue
        key, t, rest, r = taste_ratio(p)
        p.update(taste_key=key, taste_votes=t, second_votes=rest, ratio=r,
                 share=t / max(p.get("review_total") or 0, 1), group=review_group(p.get("review_total")))
        if key is None:
            continue
        if t < min_votes:
            excluded["투표 적음"] += 1
            continue
        p["pass_by"] = taste_pass(p, ratio)
        if p["pass_by"]:
            passed.append(p)
        else:
            excluded["맛 기준 미달"] += 1

    # Jev 는 상위 JEV_MAX 곳만 보므로 숫자 점수 순으로 먼저 줄 세운다(네이버 노출 순 X)
    passed.sort(key=lambda p: -composite(p, {"조건": 0, "메뉴": 0}, set())[0])
    k = jev_key()
    use, maxes, want_keywords, opposite_keywords, menu_labels = set(), {"조건": 0, "메뉴": 0}, {}, {}, {}
    jev = {"used": False, "note": "Jev 미연결(키 없음) — 숫자 기준만 적용" if not k else "Jev 판정할 후보 없음"}
    if k and passed:
        cands = passed[:JEV_MAX]
        try:
            if want:
                names = sorted({n for p in cands for n, _ in p["keywords"] if not n.endswith("맛있어요")})
                rel, opp = relevance(want, names, k)
                want_keywords = {n: v for n, v in sorted(rel.items(), key=lambda x: -x[1]) if v >= KW_MIN}
                opposite_keywords = {n: v for n, v in sorted(opp.items(), key=lambda x: -x[1])
                                     if v >= OPP_MIN and v > rel[n]}
                for c in cands:
                    c["condition_score"], c["condition_evidence"] = condition_score(c, want_keywords)
                    c["opposite_score"], c["opposite_evidence"] = condition_score(c, opposite_keywords)
                use.add("조건")
                if max(rel.values() or [0]) < VAGUE:
                    fetch_reviews(cands)
                    fits = review_fit(cands, want, k)
                    for c in cands:
                        c["review_fit"] = fits.get(c["id"])
                    use.add("리뷰 문장")
                    notes.append(f"'{want}' 에 딱 맞는 네이버 키워드가 없어(최고 {max(rel.values()):.2f}) 리뷰 문장으로 보조 판정")
                if want:
                    unfit = category_unfit(want, sorted({c.get("category") or "" for c in cands} - {""}), k)
                    keep = [c for c in cands if unfit.get(c.get("category") or "", 0) < UNFIT_CUT]
                    excluded["업종이 조건과 안 맞음"] = len(cands) - len(keep)
                    bad = sorted(u for u, v in unfit.items() if v >= UNFIT_CUT)
                    if bad:
                        notes.append(f"'{want}' 에 안 맞는 업종 제외: {', '.join(bad)}")
                    cands = keep
                    passed = keep + passed[JEV_MAX:]
                if "리뷰 문장" in use:
                    pass
                elif want_keywords:
                    # 키워드로 판정되는 조건이면 근거 표가 있는 곳만 남긴다
                    keep = [c for c in cands if any(n >= COND_MIN_VOTES for _, n in c["condition_evidence"])]
                    excluded["조건 근거 없음"] = len(cands) - len(keep) + len(passed[JEV_MAX:])
                    cands = keep
                    passed = keep
                maxes["조건"] = max([c["condition_score"] for c in cands] or [0])
            if menu:
                labels = {l for c in cands for l, _ in c.get("menus", [])[:30]}
                menu_labels = match_menus(menu, labels, k) if labels else {}
                keep = []
                for c in cands:
                    hits = sorted([(l, n) for l, n in c.get("menus", []) if l in menu_labels], key=lambda x: -x[1])
                    c["menu_evidence"] = hits
                    c["menu_share"] = sum(n for _, n in hits) / max(c.get("review_total") or 1, 1)
                    if sum(n for _, n in hits) >= 3:
                        keep.append(c)
                    else:
                        excluded["메뉴 언급 없음"] += 1
                cands = keep
                passed = keep + passed[JEV_MAX:]
                maxes["메뉴"] = max([c["menu_share"] for c in cands] or [0])
                use.add("메뉴")
            fetch_reviews(cands)
            details = pick_details(want, k) if want else []
            judged = judge_reviews(cands, k, details)
            keep = []
            for c in cands:
                j = judged.get(c["id"]) or {}
                c["jev_sponsored"] = j.get("sponsored")
                c["jev_event"], c["jev_declined"], c["jev_waiting"] = j.get("event"), j.get("declined"), j.get("waiting")
                c["details"] = [(d, DETAILS[d]) for d in j.get("details", [])]
                # 리뷰 이벤트가 많으면 방문자 비율이 부풀므로 비율 통과를 인정하지 않는다(배수 통과만)
                if (c["jev_event"] or 0) >= REVIEW_FLAG and "비율" in c["pass_by"]:
                    c["pass_by"] = [x for x in c["pass_by"] if x != "비율"]
                    if not c["pass_by"]:
                        excluded["이벤트 리뷰로 부푼 비율"] += 1
                        continue
                keep.append(c)
            gone = {c["id"] for c in cands} - {c["id"] for c in keep}
            passed = [p for p in passed if p["id"] not in gone]
            cands = keep
            jev = {"used": True, "note": f"Jev 판정 적용({len(cands)}곳)"}
        except Exception as e:
            jev = {"used": False, "note": f"Jev 실패({type(e).__name__}) — 숫자 기준만 적용"}
            use.clear()

    for p in passed:
        p["score"], p["score_parts"] = composite(p, maxes, use)
    # 배수가 주 규칙(회장님 2026-09-25): 배수 통과가 먼저, 비율로만 붙은 곳은 그 뒤를 채운다
    passed.sort(key=lambda p: ("배수" not in p["pass_by"], -p["score"]))
    groups = {name: [p for p in passed if p["group"] == name][:GROUP_TOP] for name, _ in GROUPS}
    for p in passed:
        p.pop("reviews", None)  # 출력에는 리뷰 원문을 싣지 않는다
        p["keywords"] = p.get("keywords", [])[:6]
        p["menus"] = p.get("menus", [])[:6]
        p.pop("newBusinessHours", None)
        p.pop("priceCategory", None)
    return {"area": area, "food_type": food_type, "want": want, "menu": menu,
            "filters": {"open_now": open_now, "max_price_manwon": max_price},
            "rule": {"min_votes": min_votes, "ratio": ratio, "ratio_low_reviews": ratio * 1.5,
                     "share_min": SHARE_MIN},
            "query": query, "naver_total": total, "cached": cached,
            "want_keywords": want_keywords, "opposite_keywords": opposite_keywords, "menu_labels": menu_labels,
            "checked": listed, "passed_count": len(passed), "results": passed[:top],
            "groups": {k: v for k, v in groups.items() if v},
            "excluded": {k: v for k, v in excluded.items() if v}, "jev": jev, "notes": notes,
            "errors": errors, "seconds": round(time.time() - t0, 1)}


def format_text(res):
    r = res["rule"]
    head = (f"{res['query']} — 네이버 {res['naver_total']:,}곳 중 상위 {res['checked']}곳"
            f"{'(캐시)' if res['cached'] else ''} → 통과 {res['passed_count']}곳 ({res['seconds']}초)")
    out = [head, f"기준: 맛 투표 {r['min_votes']}표 이상 & (2위 키워드의 {r['ratio']:g}배 이상"
           f"[리뷰 1천 미만은 {r['ratio_low_reviews']:g}배] 또는 방문자 {r['share_min']:.0%} 이상이 맛있어요)"
           + (" · 지금 영업 중" if res["filters"]["open_now"] else "")
           + (f" · {res['filters']['max_price_manwon']:g}만원 대 이하" if res["filters"]["max_price_manwon"] else ""),
           res["jev"]["note"]]
    if res["want_keywords"]:
        out.append(f"'{res['want']}' → " + ", ".join(f"{n}({v:.2f})" for n, v in list(res["want_keywords"].items())[:4])
                   + (" / 반대: " + ", ".join(list(res["opposite_keywords"])[:3]) if res["opposite_keywords"] else ""))
    if res["menu_labels"]:
        out.append(f"'{res['menu']}' 메뉴 → " + ", ".join(list(res["menu_labels"])[:6]))
    out += res["notes"]
    sections = [(f"■ {g} (리뷰 {lo:,}개 이상)" if lo else f"■ {g} (리뷰 1천 미만)", ps)
                for g, lo in GROUPS for gg, ps in res.get("groups", {}).items() if gg == g] \
        or [("", res["results"])]
    for title, items in sections:
        if title:
            out.append(title)
        out += [format_place(n, p) for n, p in enumerate(items, 1)]
    if res["checked"] == 0:
        out.append(f"'{res['query']}' 검색 결과에서 식당을 찾지 못했다."
                   + (f" ({'; '.join(res['errors'])})" if res["errors"] else ""))
    elif not res["results"]:
        out.append("통과한 곳이 없다. --ratio 1.5 로 낮추거나 조건·필터를 줄여 볼 것.")
    if res["excluded"]:
        out.append("(제외: " + ", ".join(f"{k} {v}곳" for k, v in res["excluded"].items()) + ")")
    return "\n".join(out)


def review_flags(p, details=True):
    """리뷰 판정 표시(카드·텍스트 공용)."""
    out = []
    if (p.get("jev_sponsored") or 0) >= SPONSORED_CUT:
        out.append("⚠️협찬 리뷰 의심")
    if (p.get("jev_declined") or 0) >= REVIEW_FLAG:
        out.append("⚠️요즘 맛·서비스가 변했다는 리뷰")
    if (p.get("jev_waiting") or 0) >= REVIEW_FLAG:
        out.append("⏱웨이팅 길다는 리뷰")
    if (p.get("jev_event") or 0) >= REVIEW_FLAG:
        out.append("🎁리뷰 이벤트 리뷰 많음")
    if details:
        out += [f"{icon}{d} 언급" for d, icon in p.get("details") or []]
    return out


def taste_reason(p):
    """통과 사유 한 줄 — 배수로 붙었는지, 비율로 붙었는지."""
    bits = []
    if "배수" in p.get("pass_by", []):
        bits.append(f"2위의 {p['ratio']:.1f}배")
    if "비율" in p.get("pass_by", []) or not bits:
        bits.append(f"방문자 {p['share']:.0%}")
    return " · ".join(bits)


def format_place(n, p):
    out = []
    line = f"{n}. {p['name']} ({p['category']}) · 종합 {p['score']:.0f}점 — {p['taste_key']} {p['taste_votes']:,}표({taste_reason(p)})"
    if p.get("rating"):
        line += f" · 별점 {p['rating']}"
    extra = []
    if p.get("condition_evidence"):
        extra.append("근거: " + ", ".join(f"{k} {c}표" for k, c in p["condition_evidence"][:3]))
    if p.get("review_fit") is not None:
        extra.append(f"리뷰 문장 판정 {p['review_fit']:.2f}")
    if p.get("menu_evidence"):
        extra.append("메뉴: " + ", ".join(f"{k} {c}회" for k, c in p["menu_evidence"][:3]))
    if p.get("opposite_evidence"):
        extra.append("감점: " + ", ".join(f"{k} {c}표" for k, c in p["opposite_evidence"][:2]))
    extra += review_flags(p)
    info = " · ".join(x for x in (p.get("status"), p.get("price")) if x)
    out.append(line)
    if extra:
        out.append("   " + " · ".join(extra))
    out.append(f"   {info + ' · ' if info else ''}{p['url']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="네이버 플레이스 맛집 추리기")
    ap.add_argument("area", nargs="+", help="지역(예: 성수동)")
    ap.add_argument("--type", default="", help="음식 종류(예: 한식, 고깃집)")
    ap.add_argument("--want", default="", help="조건(예: 조용히 대화하기 좋은 곳) — Jev 판정에 씀")
    ap.add_argument("--menu", default="", help="찾는 메뉴(예: 크림파스타) — 리뷰 메뉴 언급으로 거름")
    ap.add_argument("--open-now", action="store_true", help="지금 영업 중인 곳만")
    ap.add_argument("--max-price", type=float, default=None, help="가격대 상한(만원). 예: 3 → '3만원 대'까지")
    ap.add_argument("--min-votes", type=int, default=100)
    ap.add_argument("--ratio", type=float, default=2.0)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--max-places", type=int, default=100, help="조회할 식당 수(최대 200)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--card", metavar="PNG", help="결과를 네이버 플레이스 목록 같은 카드 PNG 로도 저장(playwright 필요)")
    a = ap.parse_args()
    res = recommend(" ".join(a.area), a.type, a.want, a.menu, a.open_now, a.max_price,
                    a.min_votes, a.ratio, a.top, max(1, min(a.max_places, 200)))
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else format_text(res))
    if a.card:
        try:
            import card
            print(f"카드: {card.render_png(res, a.card, SPONSORED_CUT)}", file=sys.stderr)
        except Exception as e:
            print(f"카드 생성 실패({type(e).__name__}: {e}) — 텍스트 결과만 쓸 것", file=sys.stderr)
    return 0 if res["checked"] else 1


if __name__ == "__main__":
    sys.exit(main())
