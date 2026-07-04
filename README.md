# Era 🗞 — AI Daily Digest

**Era (에라)**: "AI 시대(era)를 매일 읽는 도구". AI 산업 뉴스를 매일 아침 텔레그램(@era26bot)으로 받아보는 개인 학습 도구.

> RSS 수집 → Claude가 관심사 기준 선별·한글 요약 → 텔레그램 전송

## 구조

| 파일 | 역할 |
|---|---|
| `digest.py` | 메인 파이프라인 (수집 → 랭킹·요약 → 전송) |
| `config.py` | RSS 소스 목록 + 관심 태그 + 동작 설정 |
| `.github/workflows/digest.yml` | 매일 08:00 KST 자동 실행 (GitHub Actions) |

## 로컬 실행

```bash
pip install -r requirements.txt
# .env 에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / ANTHROPIC_API_KEY 설정
python digest.py
```

## 클라우드 실행 (GitHub Actions)

시크릿 3개 필요: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`
(Settings → Secrets and variables → Actions)

수동 실행: Actions 탭 → AI Daily Digest → Run workflow

## 로드맵 (학습 단계)

- [x] **L1** 수집·전송 파이프라인
- [x] **L2** 관심 태그 기반 랭킹
- [ ] **L3** 요약 품질 Eval 루프
- [ ] **L4** 뉴스 아카이브 RAG ("지난달 에이전트 관련 뭐 봤지?")
