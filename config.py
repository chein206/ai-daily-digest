# -*- coding: utf-8 -*-
"""
소스 피드와 관심 태그 설정.
소스는 여기서 추가/삭제하면 됩니다. RSS가 깨지면 자동으로 건너뜁니다.
"""

# --- 수집할 RSS 소스 ---
# (이름, RSS URL) — 취향 안 맞으면 주석 처리하거나 지우세요.
FEEDS = [
    # 1층: 프론티어 랩 공식
    # (Anthropic은 공개 RSS가 없어 제외 — 소식은 HN/Latent Space/Simon Willison으로 유입)
    ("OpenAI",        "https://openai.com/blog/rss.xml"),
    ("Google DeepMind","https://deepmind.google/blog/rss.xml"),
    # 2층: 빌더/엔지니어 심화 (핵심)
    ("Simon Willison","https://simonwillison.net/atom/everything/"),
    ("Latent Space",  "https://www.latent.space/feed"),
    ("Import AI",     "https://importai.substack.com/feed"),
    ("Interconnects", "https://www.interconnects.ai/feed"),
    # 3층: 매일 훑기 + 원천 신호
    ("TLDR AI",       "https://tldr.tech/api/rss/ai"),
    ("Hacker News AI","https://hnrss.org/newest?q=AI&count=30"),
    # --- 옵션 (원하면 주석 해제) ---
    # ("MarkTechPost",  "https://www.marktechpost.com/feed/"),
    # ("VentureBeat AI","https://venturebeat.com/category/ai/feed/"),
]

# --- 관심 프로필 (요약 랭킹 기준) ---
# 랭커가 이 태그에 걸리는 기사를 우선 선택합니다.
INTERESTS_PRIORITY = [
    "에이전트 / 에이전트 프레임워크",
    "RAG (검색증강생성, reranker, pgvector)",
    "Eval / LLM 품질 측정",
    "프롬프트·컨텍스트 설계",
    "비용·라우팅·멀티모달",
    "Claude/Anthropic 생태계 (Claude Code, Skills, MCP)",
]

INTERESTS_TRENDING = [
    "MCP (Model Context Protocol)",
    "Claude Skills",
    "AI 코딩 / 바이브코딩",
    "추론 모델 / test-time compute",
    "음성 AI / 온디바이스 소형 모델",
    "응용 AI 사업·GTM 사례",
]

# 요약에서 낮게 다룰 것
DEEMPHASIZE = [
    "트랜스포머 내부 수학",
    "파인튜닝·모델 훈련 이론",
]

# --- 동작 설정 ---
LOOKBACK_HOURS = 72   # 최근 몇 시간 내 글만 후보로 (매일 돌리면 48로 낮춰도 됨)
MAX_ITEMS = 6         # 다이제스트에 담을 최대 기사 수
