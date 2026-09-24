#!/usr/bin/env python3
"""matjip 웹 — 한 줄로 맛집을 묻고 카드로 받는 작은 페이지 (사설망 전용 권장).

  python3 web/server.py                      # 기본: 127.0.0.1:8787
  MATJIP_WEB_HOST=<테일스케일 IP> python3 web/server.py   # 폰에서 쓰려면 사설망 주소로 (공인 IP 금지)

- 입력 문장은 규칙으로 지역·음식·메뉴·가격·조건으로 나눈다(즉시). 조건은 matjip 의 Jev 가 해석한다.
  (claude CLI 해석은 서버에서 20초+ 걸리고 봇 설정 파일을 건드려 뺐다 — 2026-09-24)
- 조회는 matjip.recommend() 그대로. 동시에 한 건만 돌린다(네이버 조회 제약).
- 테일스케일(100.64.0.0/10)과 로컬에서 온 요청만 받는다.
"""
import ipaddress, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "naver-matjip", "scripts"))
import card    # noqa: E402
import matjip  # noqa: E402

HOST = os.environ.get("MATJIP_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MATJIP_WEB_PORT", "8787"))
ALLOWED = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("127.0.0.0/8")]
RUN_LOCK = threading.Lock()

# 음식 종류로 볼 낱말. 네이버 검색어(«지역 종류 맛집»)에 들어간다.
FOOD_TYPES = """한식 일식 중식 양식 분식 한정식 백반 이자카야 술집 포차 주점 와인바 바 고깃집 삼겹살 갈비 소고기 한우
곱창 막창 대창 횟집 회 스시 초밥 오마카세 라멘 라면 우동 소바 돈카츠 돈까스 파스타 피자 버거 햄버거 스테이크
브런치 카페 디저트 베이커리 빵집 태국 태국음식 베트남 쌀국수 인도 커리 카레 멕시칸 타코 이탈리안 프렌치 중국집
짜장면 짬뽕 마라탕 훠궈 양꼬치 딤섬 샤브샤브 국밥 해장국 냉면 평양냉면 칼국수 수제비 족발 보쌈 치킨 닭갈비
닭한마리 순대 떡볶이 김밥 만두 전 막걸리 뷔페 오리 장어 아귀찜 해물 조개 게장 생선구이 수육 감자탕 부대찌개""".split()
AREA_SUFFIX = re.compile(r"(에서|에|쪽|근처|부근|주변)$")
PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*원?\s*(이하|이내|미만|아래|까지|대)?")
OPEN_WORDS = ("지금", "영업중", "지금영업", "열린", "문연")
DROP = {"맛집", "추천", "추천해줘", "찾아줘", "알려줘", "곳", "좀", "근처", "쪽", "에서", "영업", "중", "중인"}


def parse_query(q):
    """한 줄 요청 → 옵션 dict (규칙 기반, 즉시).
    첫 낱말 = 지역, «N만원» = 가격 상한, 음식 낱말 = 종류(길게 붙은 건 메뉴), «지금·영업중» = 영업 중만, 나머지 = 조건.
    조건은 matjip 이 Jev 로 네이버 키워드에 맞춰 번역한다."""
    q = q.strip()
    opt = {"area": "", "type": "", "menu": "", "want": "", "max_price": None, "open_now": False, "by": "rule"}
    m = PRICE.search(q)
    if m:
        opt["max_price"] = float(m.group(1))
        q = (q[:m.start()] + " " + q[m.end():]).strip()
    words = q.split()
    if not words:
        return opt
    opt["area"] = AREA_SUFFIX.sub("", words[0]) or words[0]
    rest = []
    for w in words[1:]:
        if any(w.startswith(o) for o in OPEN_WORDS):
            opt["open_now"] = True
            continue
        core = w if w in FOOD_TYPES else (re.sub(r"(맛집|집|으로|로|이랑|랑)$", "", w) or w)
        if not opt["type"] and core in FOOD_TYPES:
            opt["type"] = core
        elif not opt["menu"] and any(core.endswith(t) and len(core) > len(t) for t in FOOD_TYPES):
            opt["menu"] = core
        elif w not in DROP:
            rest.append(w)
    opt["want"] = " ".join(rest)
    if rest:
        jev_refine(opt, rest)
    return opt


def jev_refine(opt, words):
    """규칙이 '조건'으로 남긴 낱말을 Jev 가 메뉴 / 조건 / 군더더기 중 하나로 다시 가른다(④).
    예) '문정 점심 맛집' → '점심'은 시간대라 뺀다. Jev 가 없거나 실패하면 규칙 결과 그대로."""
    k = matjip.jev_key()
    if not k:
        return
    q = {}
    for i, w in enumerate(words):
        q[f"m{i}"] = {"type": "noul", "instructions": f"맛집 검색 문장(`request`) 속 낱말 '{w}' 는 먹고 싶은 음식·메뉴 이름인가?",
                      "criteria": {"true": f"'{w}' 는 음식·메뉴 이름이다", "false": f"'{w}' 는 음식 이름이 아니다"}}
        q[f"c{i}"] = {"type": "noul", "instructions": f"맛집 검색 문장(`request`) 속 낱말 '{w}' 는 식당을 고르는 조건"
                                                        f"(누구와·분위기·상황·목적)을 나타내는가?",
                      "criteria": {"true": f"'{w}' 는 식당 선택 조건(예: 아이랑, 조용한, 회식, 데이트)이다",
                                   "false": f"'{w}' 는 식사 시간대(점심·저녁)·말버릇·군더더기 등 식당 선택과 무관한 말이다"}}
    try:
        ans = matjip.jev_call({"request": " ".join(words)}, q, k)
    except Exception:
        return
    keep, dropped = [], []
    for i, w in enumerate(words):
        pm, pc = ans[f"m{i}"]["noul"], ans[f"c{i}"]["noul"]
        if pm >= 0.6 and pm > pc and not opt["menu"] and not opt["type"]:
            opt["menu"] = w
        elif pc >= 0.5:
            keep.append(w)
        else:
            dropped.append(w)
    opt["want"], opt["dropped"], opt["by"] = " ".join(keep), dropped, "jev"


def search(q, open_now):
    t0 = time.time()
    opt = parse_query(q)
    open_now = open_now or opt["open_now"]
    if not opt["area"]:
        return {"error": "지역을 못 알아들었어요. 예: 을지로 조용한 한식"}
    with RUN_LOCK:
        res = matjip.recommend(opt["area"], opt["type"], opt["want"], opt["menu"], open_now, opt["max_price"])
    if not res["results"] and res["checked"]:
        # 규칙(70-matjip): 통과 0곳이면 기준을 1.5배로 낮춰 한 번 더 — 캐시라 네이버 재조회 없음
        with RUN_LOCK:
            res = matjip.recommend(opt["area"], opt["type"], opt["want"], opt["menu"], open_now,
                                   opt["max_price"], ratio=1.5)
        res["notes"].append("2배 기준 통과가 없어 1.5배로 낮춰 다시 봤습니다")
    return {"parsed": opt, "summary": card.summary(res), "notes": res["notes"] + res["errors"],
            "html": card.cards_html(res, matjip.SPONSORED_CUT, web=True),
            "seconds": round(time.time() - t0, 1)}


MANIFEST = {"name": "맛집", "short_name": "맛집", "start_url": "/", "display": "standalone",
            "background_color": "#0f1113", "theme_color": "#0f1113",
            "icons": [{"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]}

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="맛집">
<meta name="theme-color" content="#0f1113">
<link rel="manifest" href="/manifest.json"><link rel="apple-touch-icon" href="/icon-180.png">
<title>맛집</title><style>__CSS__
body { width:auto; max-width:560px; margin:0 auto; padding:calc(14px + env(safe-area-inset-top)) 16px 32px; }
form { display:flex; gap:8px; margin:6px 0 8px; }
input[type=text] { flex:1; min-width:0; font-size:17px; padding:12px 14px; border-radius:12px;
  border:1px solid var(--line); background:var(--card); color:var(--text); outline:none; }
input[type=text]:focus { border-color:var(--green); }
button { font-size:16px; font-weight:800; padding:0 18px; border:0; border-radius:12px; background:var(--green); color:#fff; }
button:disabled { opacity:.5; }
label.chk { color:var(--sub); font-size:14px; display:flex; align-items:center; gap:6px; margin-bottom:12px; }
.parsed { font-size:13px; color:var(--sub); margin:0 4px 10px; }
.parsed b { color:var(--text); }
.status { color:var(--sub); padding:28px 4px; text-align:center; }
.err { color:var(--red); padding:16px 4px; }
</style></head><body>
<div class="head"><h1><b>N</b> 맛집</h1><p>네이버 «맛있어요» 투표 + Jev 판정</p></div>
<form id="f"><input id="q" type="text" placeholder="을지로 조용히 대화하기 좋은 한식" autocomplete="off" enterkeyhint="search">
<button id="b">찾기</button></form>
<label class="chk"><input id="open" type="checkbox"> 지금 영업 중인 곳만</label>
<div id="out"></div>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
try { $('q').value = localStorage.getItem('q') || ''; $('open').checked = localStorage.getItem('open') === '1'; } catch (e) {}
$('f').onsubmit = async ev => {
  ev.preventDefault();
  const q = $('q').value.trim(); if (!q) return;
  try { localStorage.setItem('q', q); localStorage.setItem('open', $('open').checked ? '1' : '0'); } catch (e) {}
  $('q').blur(); $('b').disabled = true;
  const t0 = Date.now();
  $('out').innerHTML = '<div class="status" id="st">찾는 중… 0초</div>';
  const tick = setInterval(() => { const s = $('st'); if (s) s.textContent = '찾는 중… ' + Math.round((Date.now() - t0) / 1000) + '초'; }, 1000);
  try {
    const r = await fetch('/api/search', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({q, open_now: $('open').checked})});
    const d = await r.json();
    if (d.error) { $('out').innerHTML = '<div class="err">' + esc(d.error) + '</div>'; return; }
    const p = d.parsed;
    const bits = [p.area, p.type, p.menu, p.want, p.max_price ? p.max_price + '만원 이하' : ''].filter(Boolean);
    $('out').innerHTML = '<div class="parsed">이렇게 이해했어요: <b>' + bits.map(esc).join(' / ') + '</b>'
      + (p.open_now ? ' / 지금 영업 중' : '')
      + (p.dropped && p.dropped.length ? ' (뺀 말: ' + p.dropped.map(esc).join(', ') + ')' : '') + '<br>' + esc(d.summary)
      + d.notes.map(n => '<br>' + esc(n)).join('') + ' · ' + d.seconds + '초</div>' + d.html;
  } catch (e) {
    $('out').innerHTML = '<div class="err">연결 실패 — 테일스케일이 켜져 있는지 확인해 주세요.</div>';
  } finally { clearInterval(tick); $('b').disabled = false; }
};
</script></body></html>""".replace("__CSS__", card.CSS)


class Handler(BaseHTTPRequestHandler):
    def _allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            return any(ip in n for n in ALLOWED)
        except ValueError:
            return False

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, "forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/manifest.json":
            return self._send(200, json.dumps(MANIFEST, ensure_ascii=False), "application/manifest+json")
        if path in ("/icon-180.png", "/icon-512.png", "/apple-touch-icon.png", "/favicon.ico"):
            f = os.path.join(HERE, "icon-512.png" if "512" in path else "icon-180.png")
            if os.path.exists(f):
                return self._send(200, open(f, "rb").read(), "image/png")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._allowed():
            return self._send(403, "forbidden", "text/plain")
        if self.path != "/api/search":
            return self._send(404, "not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            q = str(body.get("q") or "").strip()[:200]
            out = search(q, bool(body.get("open_now"))) if q else {"error": "검색어를 넣어 주세요"}
        except Exception as e:
            out = {"error": f"오류: {type(e).__name__}"}
            print(f"[{time.strftime('%F %T')}] 오류 {e!r}", flush=True)
        self._send(200, json.dumps(out, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%F %T')}] {self.client_address[0]} {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"matjip web: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
