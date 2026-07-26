# Era — AI Daily Digest

AI 산업 뉴스를 매일 아침 텔레그램으로 받아보는 개인 학습 도구.
**RSS 수집 → Claude가 관심사 기준으로 선별·한글 요약 → 텔레그램 전송 → 👍/👎 피드백 수거.**

서버 없이 GitHub Actions cron으로만 돈다.

## 왜 만들었나

AI 분야는 읽을 게 너무 많고, 대부분은 내게 안 중요하다.
뉴스레터를 구독하면 남의 관심사가 오고, 직접 훑으면 시간이 든다.

그래서 **내 관심 태그를 기준으로 매일 6개만 골라주는** 도구를 만들었다.
핵심은 "요약"이 아니라 **"선별"** 이다.

## 설계에서 내린 판단

### 1. 같은 얘기를 다시 보내지 않게 — 컨텍스트 주입

랭킹 프롬프트에 두 가지를 함께 넣는다.

- `load_sent_history(days=7)` — 최근 7일 발송 제목
- `load_covered_terms()` — 이미 설명한 용어집 항목

이걸 안 넣으면 같은 이슈가 표현만 바꿔 며칠 연속 올라온다.
**LLM에게 "무엇을 고를까"를 물을 때, "무엇을 이미 골랐는지"를 같이 줘야 한다.**

### 2. 피드백을 수거해 데이터로 쌓는다

발송 메시지마다 👍/👎 인라인 버튼을 붙이고,
다음 실행 때 `collect_feedback()`이 텔레그램 콜백을 수거해 `data/feedback.jsonl`에 적재한다.

```json
{"ts": "2026-07-04T03:18:08", "digest_date": "2026-07-04", "item_index": 1, "verdict": "up"}
```

발송 내역(`digest_log.jsonl`)과 `digest_date` + `item_index`로 조인되므로,
**"어떤 태그·소스의 기사가 실제로 좋다고 눌렸는가"** 를 나중에 집계할 수 있다.
선별 프롬프트를 고칠 때 감이 아니라 이 로그를 근거로 쓰려고 만든 구조다.

텔레그램 `offset`을 `data/tg_offset.json`에 저장해 이미 처리한 콜백을 건너뛴다.

### 3. 상태 저장소로 git을 쓴다

Actions가 실행될 때마다 `data/`에 쌓인 로그를 저장소에 되커밋한다.

```yaml
permissions:
  contents: write
```

DB를 붙일 만한 규모가 아니라, **저장소 자체를 상태 저장소로** 썼다.
파일이 곧 히스토리라 별도 백업도 필요 없다.

### 4. cron 시각은 정시를 피했다

```yaml
- cron: "47 22 * * *"   # 한국시간 07:47
```

정시(`:00`)는 GitHub 러너 러시아워라 지연이 심하다.
어중간한 분으로 두면 대기열 경쟁이 적어 **실제 도착이 8시 전후로 안정된다.**

## 파이프라인

```
GitHub Actions (매일 07:47 KST)
  │
  ├─ 0) collect_feedback()      지난 발송의 👍/👎 콜백 수거 → feedback.jsonl
  ├─ 1) fetch_entries()         RSS 수집 (최근 72시간, 기발송 링크 제외)
  ├─ 2) rank_and_summarize()    Claude — 관심 태그 기준 선별 + 한글 요약
  │                             (최근 발송 제목 · 기설명 용어를 컨텍스트로 주입)
  ├─ 3) send_digest()           텔레그램 전송 + 👍/👎 버튼 부착 → digest_log.jsonl
  ├─ 4) send_glossary()         AI 용어 5개 설명 → glossary_log.jsonl
  └─ 5) commit                  data/ 변경분을 저장소에 되커밋
```

## 구조

| 파일 | 역할 |
|---|---|
| `digest.py` | 파이프라인 전체 (443줄) |
| `config.py` | RSS 소스 · 관심 태그(우선/트렌딩/비중축소) · 동작 설정 |
| `.github/workflows/digest.yml` | cron + 시크릿 주입 + 로그 되커밋 |
| `data/digest_log.jsonl` | 발송 내역 |
| `data/feedback.jsonl` | 👍/👎 수거 결과 |
| `data/glossary_log.jsonl` | 설명한 용어 이력 |

관심사는 `config.py`에서 세 단계로 나눈다.

```python
INTERESTS_PRIORITY   # 최우선 — 반드시 담고 싶은 주제
INTERESTS_TRENDING   # 트렌드 — 여유 있으면
DEEMPHASIZE          # 비중 축소 — 있어도 후순위
```

## 실행

```bash
pip install -r requirements.txt
# .env 에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / ANTHROPIC_API_KEY
python digest.py
```

GitHub Actions로 돌리려면 저장소 시크릿 3개를 등록한다
(Settings → Secrets and variables → Actions).

## 로드맵

- [x] **L1** 수집·전송 파이프라인
- [x] **L2** 관심 태그 기반 랭킹 + 중복 회피 컨텍스트
- [x] **L3-a** 피드백 수거 (👍/👎 → Eval 데이터셋 축적)
- [ ] **L3-b** 수거한 피드백으로 선별 프롬프트 A/B 비교
- [ ] **L4** 뉴스 아카이브 RAG ("지난달 에이전트 관련 뭐 봤지?")

---

**Era을 포함한 전체 케이스 5건**은 포트폴리오에 정리돼 있습니다 — **[chein206.github.io](https://chein206.github.io)**
