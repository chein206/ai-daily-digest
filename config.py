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
    # 4층: AI 활용·미디어 트렌드 (이미지·영상 생성, 오픈소스 생태계, 소비자 제품)
    ("Hugging Face",  "https://huggingface.co/blog/feed.xml"),
    ("The Verge AI",  "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    # 5층: 제품기획(PM) · 1인 사업 (2026-08-14 추가)
    ("Lenny's Newsletter","https://www.lennysnewsletter.com/feed"),   # PM 실무 정석
    ("Product Compass",  "https://www.productcompass.pm/feed"),       # AI PM 전용
    ("Show HN",          "https://hnrss.org/show?count=30"),          # 개인이 만든 제품 출시 원천 신호
    ("Benedict Evans",   "https://www.ben-evans.com/benedictevans?format=rss"),  # 시장·사업 관점
    # --- 옵션 (원하면 주석 해제) ---
    # ("Exponential View",   "https://www.exponentialview.co/feed"),
    # ("Pragmatic Engineer", "https://newsletter.pragmaticengineer.com/feed"),
    # ("Elena Verna",        "https://elenaverna.com/feed"),          # 그로스·PLG
    # ("smol.ai AI News",    "https://news.smol.ai/rss.xml"),         # TLDR과 겹침 주의
    # ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
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
    "AI 제품기획·PM (PRD, 유저 리서치, AI 기능 설계, 제품 사례·실패담)",
]

INTERESTS_TRENDING = [
    "MCP (Model Context Protocol)",
    "Claude Skills",
    "AI 코딩 / 바이브코딩",
    "추론 모델 / test-time compute",
    "음성 AI / 온디바이스 소형 모델",
    "응용 AI 사업·GTM 사례",
    "이미지·영상 생성 AI (Nano Banana, Midjourney, Sora 등 미디어 생성 툴)",
    "Hugging Face·오픈소스 모델 생태계",
    "AI 디자인·크리에이티브 활용 (디자인 툴, 콘텐츠 제작 워크플로우)",
    "화제가 된 AI 제품·바이럴 활용 사례",
    "AI 1인 사업·인디해커 (개인이 만든 AI 제품, 수익화, 자동화 파이프라인)",
    "따라 만들 수 있는 AI 기술 아이디어·스택 공유 (구현기, 튜토리얼, 비용 실측)",
]

# 요약에서 낮게 다룰 것
DEEMPHASIZE = [
    "트랜스포머 내부 수학",
    "파인튜닝·모델 훈련 이론",
]

# --- 동작 설정 ---
LOOKBACK_HOURS = 72   # 최근 몇 시간 내 글만 후보로 (매일 돌리면 48로 낮춰도 됨)
MAX_ITEMS = 9         # 다이제스트에 담을 최대 기사 수 (주제 확대로 6 → 9)
GLOSSARY_COUNT = 5    # 매일 소개할 AI 용어 개수
