"""card.py — matjip 추천 결과를 네이버 플레이스 목록처럼 생긴 카드 PNG 로 찍는다.

AI 이미지 생성이 아니다: 결과 JSON 을 HTML 에 채워 넣고 헤드리스 크롬(playwright)으로 스크린샷한다.
matjip.py 는 표준 라이브러리만 쓰므로 playwright 는 이 파일에서만, 쓸 때만 불러온다.

사용:
  python3 matjip.py 성수동 --card /tmp/matjip.png        # 텍스트 결과 + 카드 PNG
  python3 card.py result.json -o card.png                # matjip.py --json 결과로 카드만
"""
import base64, concurrent.futures as cf, html, json, sys, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
THUMB = "https://search.pstatic.net/common/?src={src}&type=f240_240"  # 네이버 썸네일 프록시(원본 1/4 크기)
WIDTH = 420

CSS = """
:root { --bg:#0f1113; --card:#1a1d21; --line:#2a2e33; --text:#eceef0; --sub:#9aa1a9;
        --green:#03c75a; --red:#ff5a5f; --amber:#f5a524; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text); width:%dpx; padding:14px 12px 16px;
       font-family:"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif; font-size:15px; line-height:1.4; word-break:keep-all; }
.head { padding:2px 4px 12px; }
.head h1 { font-size:19px; font-weight:800; }
.head h1 b { color:var(--green); }
.head p { color:var(--sub); font-size:13px; margin-top:3px; }
a.card { color:inherit; text-decoration:none; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px;
        display:flex; gap:12px; margin-bottom:10px; }
.ph { position:relative; flex:0 0 96px; height:96px; border-radius:10px; overflow:hidden; background:#262a2f; }
.ph img { width:100%%; height:100%%; object-fit:cover; display:block; }
.ph .no { width:100%%; height:100%%; display:flex; align-items:center; justify-content:center; font-size:34px; }
.rank { position:absolute; top:0; left:0; background:var(--green); color:#fff; font-weight:800;
        font-size:13px; padding:2px 7px; border-bottom-right-radius:8px; }
.body { flex:1; min-width:0; }
.t { display:flex; align-items:baseline; gap:6px; }
.name { font-size:17px; font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cat { color:var(--sub); font-size:13px; white-space:nowrap; }
.score { margin-left:auto; flex:none; background:rgba(3,199,90,.15); color:var(--green);
         font-weight:800; font-size:13px; padding:2px 8px; border-radius:99px; }
.row { font-size:13px; color:var(--sub); margin-top:3px; }
.row b { color:var(--text); font-weight:700; }
.star { color:var(--amber); }
.taste { margin-top:6px; font-size:14px; }
.taste b { color:var(--green); }
.ev { font-size:12.5px; color:var(--sub); margin-top:3px; }
.open { color:var(--green); font-weight:700; }
.closed { color:var(--red); font-weight:700; }
.warn { color:var(--red); font-size:12.5px; font-weight:700; margin-top:3px; }
.empty { color:var(--sub); padding:24px 4px; text-align:center; }
""" % WIDTH


def thumb_url(url):
    """브라우저가 직접 불러올 썸네일 주소(웹 페이지용)."""
    return THUMB.format(src=urllib.parse.quote(url, safe="")) if url else None


def _image(url):
    """썸네일(없으면 원본)을 data URI 로. 실패하면 None — 사진 없이 그린다."""
    if not url:
        return None
    for u in (THUMB.format(src=urllib.parse.quote(url, safe="")), url):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://m.place.naver.com/"})
            r = urllib.request.urlopen(req, timeout=10)
            ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            if ctype.startswith("image/"):
                return f"data:{ctype};base64," + base64.b64encode(r.read()).decode()
        except Exception:
            continue
    return None


def _card(n, p, img, link=False):
    e = html.escape
    ph = f'<img src="{e(img)}" loading="lazy">' if img else '<div class="no">🍽️</div>'
    rating = f'<span class="star">★</span> <b>{p["rating"]}</b> · ' if p.get("rating") else ""
    reviews = f'방문자리뷰 {p["review_total"]:,}' if p.get("review_total") else ""
    st = p.get("status") or ""
    st_cls = "open" if st in ("영업 중", "곧 영업 종료", "24시간 영업") else "closed"
    info = " · ".join(x for x in (f'<span class="{st_cls}">{e(st)}</span>' if st else "",
                                  e(p.get("price") or ""), e(p.get("roadAddress") or "")) if x)
    ev = []
    if p.get("condition_evidence"):
        ev.append("👍 " + ", ".join(f"{e(k)} {c:,}표" for k, c in p["condition_evidence"][:2]))
    if p.get("menu_evidence"):
        ev.append("🍴 " + ", ".join(f"{e(k)} {c:,}회" for k, c in p["menu_evidence"][:2]))
    if p.get("opposite_evidence"):
        ev.append("👎 " + ", ".join(f"{e(k)} {c:,}표" for k, c in p["opposite_evidence"][:1]))
    warn = '<div class="warn">⚠️ 협찬 리뷰 의심</div>' if p.get("sponsored_flag") else ""
    tag, end = (f'<a class="card" href="{e(p["url"])}" target="_blank" rel="noopener">', "</a>") if link \
        else ('<div class="card">', "</div>")
    return f"""{tag}<div class="ph">{ph}<div class="rank">{n}</div></div><div class="body">
<div class="t"><span class="name">{e(p["name"])}</span><span class="cat">{e(p.get("category") or "")}</span>
<span class="score">{p["score"]:.0f}점</span></div>
<div class="row">{rating}{reviews}</div>
<div class="taste">😋 {e(p["taste_key"])} <b>{p["taste_votes"]:,}표</b> · 2위의 {p["ratio"]:.1f}배</div>
{"".join(f'<div class="ev">{x}</div>' for x in ev)}{warn}
<div class="row">{info}</div></div>{end}"""


def summary(res):
    """결과 머리 한 줄(통과 수·조건·Jev 상태)."""
    sub = [f'네이버 상위 {res.get("checked", 0)}곳 중 {res.get("passed_count", 0)}곳 통과',
           (res.get("jev") or {}).get("note", "")]
    if res.get("want"):
        sub.insert(1, f'조건: {res["want"]}')
    return " · ".join(s for s in sub if s)


def cards_html(res, sponsored_cut=0.7, web=False):
    """카드 목록 HTML 조각. web=True 면 카드가 링크가 되고 사진은 브라우저가 직접 불러온다."""
    items = res.get("results") or []
    for p in items:
        p["sponsored_flag"] = (p.get("jev_sponsored") or 0) >= sponsored_cut
    if web:
        imgs = [thumb_url(p.get("imageUrl")) for p in items]
    else:
        with cf.ThreadPoolExecutor(6) as ex:
            imgs = list(ex.map(lambda p: _image(p.get("imageUrl")), items))
    return "".join(_card(n, p, i, link=web) for n, (p, i) in enumerate(zip(items, imgs), 1)) \
        or '<div class="empty">기준을 통과한 곳이 없습니다.</div>'


def build_html(res, sponsored_cut=0.7):
    e = html.escape
    title = e(res.get("query") or "")
    cards = cards_html(res, sponsored_cut)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="head"><h1><b>N</b> {title}</h1><p>{e(summary(res))}</p></div>
{cards}</body></html>"""


def render_png(res, out_path, sponsored_cut=0.7):
    """결과 dict → PNG 파일. 경로를 돌려준다."""
    from playwright.sync_api import sync_playwright  # 선택 의존성
    page_html = build_html(res, sponsored_cut)
    with sync_playwright() as pw:
        browser, err = None, None
        # 플레이라이트 번들 브라우저가 없거나 깨졌으면 설치된 크롬 → 전체 크로미엄 순으로
        for channel in (None, "chrome", "chromium"):
            try:
                browser = pw.chromium.launch(channel=channel) if channel else pw.chromium.launch()
                break
            except Exception as e:
                err = e
        if browser is None:
            raise err
        page = browser.new_page(viewport={"width": WIDTH, "height": 400}, device_scale_factor=2)
        page.set_content(page_html, wait_until="load")
        page.screenshot(path=out_path, full_page=True)
        browser.close()
    return out_path


def main():
    import argparse
    ap = argparse.ArgumentParser(description="matjip --json 결과 → 카드 PNG")
    ap.add_argument("json_file", help="matjip.py --json 출력 파일('-' 면 stdin)")
    ap.add_argument("-o", "--out", default="matjip-card.png")
    a = ap.parse_args()
    res = json.load(sys.stdin if a.json_file == "-" else open(a.json_file))
    print(render_png(res, a.out))


if __name__ == "__main__":
    main()
