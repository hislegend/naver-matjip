# naver-matjip 🍜

**"맛집 추천해줘" 하면 블로그 긁어서 대충 걸리는 데 말고 — 네이버 방문자 100곳의 투표를 세고, Jev가 당신의 말을 키워드로 바꿔 골라줍니다. 4초.**

- **맛 검증**: 네이버 플레이스 방문자 키워드 투표에서 **"음식이 맛있어요" 표가 2등 키워드의 2배 이상**인 집만 남깁니다. 인테리어·친절·뷰가 아니라 **맛 때문에** 표가 몰린 집입니다.
- **조건 맞춤**: "조용히 대화하기 좋은 곳", "10명 회식", "혼자 한 끼" 같은 말을 [TypeSafe Jev](https://docs.typesafe.ai)가 **네이버 키워드로 번역**하고, 코드가 그 키워드에 몰린 표를 셉니다. 결과마다 **"대화하기 좋아요 89표"** 같은 근거가 붙습니다.
- **어디서나**: Claude Code · Codex(GPT) · Claude Desktop · Cursor 등 **MCP를 지원하는 모든 에이전트**, 또는 터미널. 파이썬 표준 라이브러리만 씁니다.

```
$ python3 matjip.py 을지로 --type 한식 --want "조용히 대화하기 좋은 곳"
을지로 한식 맛집 — 네이버 4,790곳 중 상위 100곳 조회, 맛 투표 100표 이상 & 2위 키워드의 2배 이상 = 35곳 (4.0초)
Jev 판정 적용(35곳) — '조용히 대화하기 좋은 곳' → 차분한 분위기예요(0.70), 대화하기 좋아요(0.63), 룸이 잘 되어있어요(0.45), 집중하기 좋아요(0.26)
1. 고봉당 을지로점 (한식) — 음식이 맛있어요 622 / 2위 308 = 2.0배 · 별점 4.95 · 근거: 아늑해요 102표, 차분한 분위기예요 91표, 대화하기 좋아요 89표
   https://m.place.naver.com/restaurant/2064119908/home
2. 장작집 을지로점 (한식) — 음식이 맛있어요 155 / 2위 70 = 2.2배 · 별점 4.7 · 근거: 대화하기 좋아요 31표
   https://m.place.naver.com/restaurant/1397313807/home
3. 용광쭈꾸미 을지로본점 (한식) — 음식이 맛있어요 4472 / 2위 1527 = 2.9배 · 별점 4.87 · 근거: 대화하기 좋아요 738표, 아늑해요 83표, 차분한 분위기예요 11표
   https://m.place.naver.com/restaurant/1030835435/home
(투표 100표 미만이라 제외: 9곳)
```

## Jev가 하는 일 — 당신의 말을 네이버 키워드로 바꾼다

네이버 방문자는 리뷰를 쓸 때 `대화하기 좋아요`, `단체모임 하기 좋아요`, `혼밥하기 좋아요` 같은 키워드를 고릅니다. 식당마다 이 표가 수백~수천 개 쌓여 있습니다.
문제는 사람이 "조용한 데"라고 말할 때 **어느 키워드를 봐야 하는지**입니다. 이건 규칙으로 못 짭니다. 그래서 Jev가 맡습니다.

실제 결과 (2026-09-23, 을지로 식당들에 나온 키워드 46개 중 Jev가 고른 것, 괄호는 관련 확률):

| 손님이 한 말 | Jev가 고른 네이버 키워드 |
|---|---|
| 🤫 "조용히 대화하기 좋은 곳" | 차분한 분위기예요 (0.70), 대화하기 좋아요 (0.63), 룸이 잘 되어있어요 (0.45) |
| 🍻 "10명 회식" | 단체모임 하기 좋아요 (0.75), 파티하기 좋아요 (0.26), 룸이 잘 되어있어요 (0.24) |
| 🍜 "혼자 가볍게 한 끼" | 혼밥하기 좋아요 (0.82), 음식이 빨리 나와요 (0.21) |
| 👶 "아이랑 같이" | 아이와 가기 좋아요 (0.66) |

그다음은 코드가 합니다. 식당마다 **전체 투표 중 그 키워드에 몰린 비중**(관련 확률로 가중)을 계산해 순서를 매깁니다.
같은 을지로 한식 35곳이 조건에 따라 이렇게 바뀝니다:

| 조건 | 1위 | 근거 |
|---|---|---|
| 조용히 대화 | 고봉당 을지로점 | 차분한 분위기예요 91표 · 대화하기 좋아요 89표 · 룸 86표 |
| 10명 회식 | 본고향맛집 을지로4가점 | 단체모임 하기 좋아요 87표 · 매장이 넓어요 106표 |
| 혼자 한 끼 | 꾸왁칼국수 | 혼밥하기 좋아요 290표 |

### 처음엔 다르게 했다가 바꿨습니다

처음엔 식당 리뷰를 통째로 Jev에 주고 "조용한 곳인가?"를 물었습니다. 후보가 4곳일 땐 그럴듯했는데, **100곳으로 늘리자 판정이 흐려졌습니다** — `차분한 분위기예요` 91표를 받은 집이 중간 점수를 받는 식으로요.
그래서 역할을 나눴습니다. **Jev는 "말 → 키워드" 번역만** (한 번, 0.4초), **표 세기와 순위는 코드가**. 판정이 선명해졌고, 결과마다 표 수라는 근거가 붙게 됐습니다.

### 왜 LLM이 아니라 Jev인가

| | 실측값 (2026-09-23) |
|---|---|
| 번역 속도 | 키워드 46개 판정 **한 번에 0.33~0.45초** |
| 비용 | 번역 1회 입력 약 6,000토큰 ≈ **$0.00026**. 협찬 판정까지 합쳐 추천 1회 **약 1원** |
| 출력 | 글을 생성하지 않고 **확률만** 돌려줌 → 파싱 실패 없음 |
| 환각 | 후보 식당·키워드는 코드가 네이버에서 가져온 것만. Jev는 **고르기만** 하므로 없는 식당·없는 키워드를 지어낼 수 없음 |
| 설명 가능 | 순위의 근거가 "모델이 그렇게 말했다"가 아니라 **방문자 표 수** |

### 협찬 리뷰 판별

통과한 식당마다 최근 리뷰를 Jev에 보내 "체험단·협찬 위주인가"를 판정하고, 0.7 이상이면 ⚠️를 붙여 뒤로 보냅니다.
- 전형적인 체험단 문구("제공받아 작성", "#협찬")로 만든 리뷰 묶음 → **0.98**, 평범한 후기 묶음 → **0.07**.
- 실제 성수동·홍대·부산 서면 통과 식당 73곳에서는 최고 0.30으로 걸린 곳이 없었습니다. 네이버 방문자 리뷰는 영수증 인증 기반이라 협찬 글이 적은 것으로 보입니다.

## 어떻게 동작하나

1. **목록** — 네이버 플레이스(지도)에서 "`지역` `음식종류` 맛집"으로 상위 100곳(`--max-places`)을 가져옵니다. 50곳씩 한 요청에 묶어 2회.
2. **맛 필터** — 25곳씩 묶어 방문자 키워드 투표를 가져오고(4회), `…맛있어요` 표가 **100표 이상 & 2등 키워드의 2배 이상**인 곳만 남깁니다 (`--min-votes`, `--ratio`). 카페·빵집은 `커피가/빵이/디저트가 맛있어요`로 봅니다.
3. **조건 맞춤** (`--want`, Jev 키 필요) — Jev가 조건을 키워드로 번역 → 코드가 키워드 표 비중으로 정렬.
4. **협찬 판정** (Jev 키 필요) — 통과한 곳의 최근 리뷰를 한 요청으로 가져와 식당별로 판정.

Jev 키가 없거나 Jev가 응답하지 않으면 2단계 결과를 그대로 냅니다. 추천이 멈추지 않습니다.
네이버 요청은 한 번 추천에 **7~8회**(묶음 요청)입니다.

## 설치

파이썬 3.9 이상만 있으면 됩니다. 외부 패키지 없음.

### Claude Code (플러그인 — 스킬 + MCP 한 번에)

```bash
claude plugin marketplace add hislegend/naver-matjip
claude plugin install naver-matjip@naver-matjip
```

### Codex CLI (GPT)

```bash
git clone https://github.com/hislegend/naver-matjip ~/naver-matjip
codex mcp add naver-matjip -- python3 ~/naver-matjip/skills/naver-matjip/scripts/mcp_server.py
cp -R ~/naver-matjip/skills/naver-matjip ~/.codex/skills/   # 스킬도 쓰려면
```

### 그 밖의 MCP 클라이언트 (Claude Desktop, Cursor 등)

```json
{
  "mcpServers": {
    "naver-matjip": {
      "command": "python3",
      "args": ["/절대경로/naver-matjip/skills/naver-matjip/scripts/mcp_server.py"]
    }
  }
}
```

도구 이름은 `recommend_restaurants` (인자: `area`, `food_type`, `want`, `min_votes`, `ratio`, `top`).
결과는 사람이 읽는 텍스트와 구조화된 JSON(`structuredContent`)을 함께 돌려줍니다. 인자 `max_places`(기본 100, 최대 200)로 조회 범위를 바꿀 수 있습니다.

### 터미널에서 바로

```bash
python3 skills/naver-matjip/scripts/matjip.py 성수동
python3 skills/naver-matjip/scripts/matjip.py 을지로 --type 한식 --want "조용히 대화하기 좋은 곳"
python3 skills/naver-matjip/scripts/matjip.py 강남역 --json
```

## Jev 연결 (선택)

[TypeSafe](https://docs.typesafe.ai)에서 API 키를 받아 둘 중 하나로 넣습니다.

```bash
export JEV_API_KEY=...                       # 환경변수
# 또는
mkdir -p ~/.config/jev && echo '...' > ~/.config/jev/api_key && chmod 600 ~/.config/jev/api_key
```

키는 TypeSafe API(`api.typesafe.ai`) 외 어디로도 보내지 않습니다. Jev에는 손님 조건 문장, 네이버 키워드 이름 목록, 후보 식당별 최근 리뷰 최대 8개(각 300자)만 보냅니다.

## 알아둘 점

- **비공식 도구입니다.** 네이버와 관계없고, 네이버 이용약관은 자동화된 수집을 제한합니다. 개인이 필요할 때 한두 번 조회하는 용도로 만들었습니다. 반복·대량 수집에 쓰지 마세요. 사용 책임은 사용자에게 있습니다.
- 네이버 지도의 내부 데이터 통로를 씁니다. 네이버가 이를 바꾸거나 막으면 조회가 깨질 수 있습니다. 요청이 많으면 `429`(요청 과다)가 날 수 있어 잠시 쉬었다 두 번까지 다시 시도합니다.
- 조회 범위는 검색 상위 100곳(최대 200)입니다. 동네 전체를 훑지는 않습니다.
- 투표가 적은 집(100표 미만)은 숫자가 흔들려서 제외합니다.
- 키워드 번역 확률은 호출마다 조금씩(±0.02 정도) 달라질 수 있습니다. 조건 순위는 참고 신호로 보세요.

## English

Korean restaurant finder for AI agents. It pulls the top 100 places for an area from Naver Place and keeps those whose "the food is delicious" visitor votes are at least 2× the runner-up keyword (min. 100 votes). With a TypeSafe Jev API key, Jev translates your request ("quiet place to talk", "dinner for 10") into Naver's visitor-keyword vocabulary in ~0.4 s, and the code ranks candidates by the share of votes on those keywords — so every result comes with vote-count evidence. Jev also flags sponsored-review-heavy places. Works as a Claude Code plugin, a Codex/any-MCP stdio server, or a plain CLI. Stdlib Python only. Unofficial; not affiliated with Naver — keep usage light and personal.

## License

MIT
