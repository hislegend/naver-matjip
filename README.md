# naver-matjip 🍜

**"맛집 추천해줘" 하면 블로그 긁어서 대충 걸리는 데 말고, 숫자로 거른 곳을 3초 만에.**

네이버 플레이스의 방문자 키워드 투표에서 **"음식이 맛있어요" 표가 2등 키워드의 2배 이상**인 식당만 추립니다.
인테리어·친절·뷰 때문이 아니라 **맛 때문에** 표가 몰린 집입니다.
[TypeSafe Jev](https://docs.typesafe.ai) API 키가 있으면 Jev가 리뷰를 읽고 **"조용히 대화하기 좋은 곳" 같은 조건에 맞는지**, **체험단·협찬 리뷰 위주인지**를 판정해 순서를 다시 매깁니다.

Claude Code · Codex(GPT) · Claude Desktop · Cursor 등 **MCP를 지원하는 모든 에이전트**, 또는 그냥 터미널에서 씁니다.

```
$ python3 matjip.py 을지로 --type 한식
을지로 한식 — 22곳 조회, 맛 투표 100표 이상 & 2위 키워드의 2배 이상 = 4곳 (4.9초)
Jev 미연결(키 없음) — 숫자 기준만 적용
1. 골수 (감자탕) — 음식이 맛있어요 570 / 2위 263 = 2.2배 · 별점 4.87
   https://m.place.naver.com/restaurant/1724618660/home
2. 주도락 을지로점 (요리주점) — 음식이 맛있어요 1427 / 2위 669 = 2.1배 · 별점 4.9
   https://m.place.naver.com/restaurant/2083227216/home
3. 고봉당 을지로점 (한식) — 음식이 맛있어요 622 / 2위 308 = 2.0배 · 별점 4.95
   https://m.place.naver.com/restaurant/2064119908/home
```

## 어떻게 고르나

1. **숫자로 거르기** — 지역 검색 결과의 식당(15~40곳)마다 방문자 키워드 투표를 읽습니다.
   - 맛 키워드: `음식이 맛있어요` (카페·빵집은 `커피가/빵이/디저트가 맛있어요`)
   - 통과 조건: 맛 키워드 **100표 이상** 그리고 **2등 키워드의 2배 이상** (`--min-votes`, `--ratio`로 조정)
2. **Jev로 판정하기** (선택) — 통과한 곳을 한 번의 요청으로 Jev에 보냅니다.
   - `Score` 5단계: 요청 조건(`--want`)에 얼마나 맞는가
   - `Noul`: 최근 리뷰가 체험단·협찬 위주인가 (0.7 이상이면 ⚠️ 표시 후 뒤로)
   - Jev 키가 없거나 호출이 실패하면 1단계 결과를 그대로 냅니다. 추천이 멈추지 않습니다.

숫자로 되는 건 코드가, 글을 읽어야 아는 건 Jev가. LLM을 깨우지 않고 판정합니다.

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
결과는 사람이 읽는 텍스트와 구조화된 JSON(`structuredContent`)을 함께 돌려줍니다.

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

키는 TypeSafe API(`api.typesafe.ai`) 외 어디로도 보내지 않습니다. Jev에는 식당 이름·분류·별점·키워드 상위 6개·최근 리뷰 6개(각 300자)만 보냅니다.

## 알아둘 점

- **비공식 도구입니다.** 네이버와 관계없고, 네이버 이용약관은 자동화된 수집을 제한합니다. 개인이 필요할 때 한두 번 조회하는 용도로 만들었습니다(요청 1회당 검색 2~3번 + 식당 최대 40곳). 반복·대량 수집에 쓰지 마세요. 사용 책임은 사용자에게 있습니다.
- 네이버 화면 구조가 바뀌면 조회가 깨질 수 있습니다. "0곳 조회"가 나오면 이슈로 알려주세요.
- 검색 첫 화면 기준이라 동네 전체를 훑지는 않습니다.
- 투표가 적은 집(100표 미만)은 숫자가 흔들려서 제외합니다.
- Jev 판정 품질은 아직 이 용도로 충분히 검증되지 않았습니다. 조건 적합도는 참고 신호로 보세요.

## English

Korean restaurant finder for AI agents. It reads Naver Place visitor keyword votes and keeps places whose "the food is delicious" votes are at least 2× the runner-up keyword (min. 100 votes). With a TypeSafe Jev API key, Jev scores each candidate against your request ("quiet place to talk") and flags sponsored-review-heavy places. Works as a Claude Code plugin, a Codex/any-MCP stdio server, or a plain CLI. Stdlib Python only. Unofficial; not affiliated with Naver — keep usage light and personal.

## License

MIT
