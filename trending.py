# -*- coding: utf-8 -*-
"""
주간 GitHub 트렌딩 TOP 10 — 매주 1회.

흐름:
  1) github.com/trending?since=weekly 를 긁어 "이번 주 받은 스타" 순으로 다시 정렬
     (공식 API가 없다. 페이지 구조가 바뀌어 5개 미만이면 검색 API로 대체:
      최근 7일 안에 만들어진 저장소를 스타 순으로)
  2) 상위 10개의 README 앞부분을 받아 Claude가 한글 한 줄 요약
  3) 한 메시지로 모든 수신자에게 발송, data/trending_log.jsonl 에 기록
     (최근 2주 안에 이미 소개한 저장소는 🔁 표시)

digest.py 의 텔레그램·로그·모델 설정을 그대로 쓴다.
"""
import re
import sys
import json
import html
import datetime as dt

import requests
from anthropic import Anthropic

import digest
from digest import log, DATA_DIR, MODEL, SENT_EFFORT

TOP_N = 10
UA = {"User-Agent": "Mozilla/5.0 (ai-daily-digest weekly trending)"}
GH_API = "https://api.github.com"


def _gh_headers():
    h = {"Accept": "application/vnd.github+json", **UA}
    tok = digest.os.getenv("GITHUB_TOKEN")
    if tok:   # Actions 기본 토큰 — 시간당 호출 한도가 60 → 1000으로 늘어난다
        h["Authorization"] = f"Bearer {tok}"
    return h


def _num(s):
    s = (s or "").replace(",", "").strip()
    return int(s) if s.isdigit() else 0


# ---------------------------------------------------------------- 1) 수집
def fetch_trending_page():
    """트렌딩 페이지(주간)를 긁는다. 이번 주에 받은 스타 순으로 이미 정렬돼 있다."""
    r = requests.get("https://github.com/trending?since=weekly", headers=UA, timeout=30)
    r.raise_for_status()
    repos = []
    for block in re.findall(r'<article class="Box-row">(.*?)</article>', r.text, re.S):
        b = re.sub(r"\s+", " ", block)
        m = re.search(r'href="/([^/"]+/[^/"]+)/stargazers"', b)
        if not m:
            continue
        full = m.group(1)
        desc = re.search(r'<p class="[^"]*color-fg-muted[^"]*">(.*?)</p>', b)
        lang = re.search(r'itemprop="programmingLanguage">([^<]*)<', b)
        total = re.search(r'/stargazers"[^>]*>.*?</svg>\s*([\d,]+)', b)
        week = re.search(r'([\d,]+) stars this week', b)
        repos.append({
            "full_name": full,
            "description": html.unescape(re.sub(r"<[^>]+>", "", desc.group(1))).strip() if desc else "",
            "language": (lang.group(1).strip() if lang else ""),
            "stars": _num(total.group(1)) if total else 0,
            "stars_week": _num(week.group(1)) if week else 0,
            "url": f"https://github.com/{full}",
        })
    return repos


def fetch_new_hot_repos():
    """대체 경로: 최근 7일 안에 만들어진 저장소를 스타 순으로 (공식 검색 API)."""
    since = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    r = requests.get(f"{GH_API}/search/repositories", headers=_gh_headers(), timeout=30,
                     params={"q": f"created:>{since}", "sort": "stars", "order": "desc",
                             "per_page": TOP_N})
    r.raise_for_status()
    return [{
        "full_name": it["full_name"],
        "description": it.get("description") or "",
        "language": it.get("language") or "",
        "stars": it.get("stargazers_count", 0),
        "stars_week": it.get("stargazers_count", 0),   # 이번 주에 생겼으니 전부 이번 주 스타
        "url": it["html_url"],
    } for it in r.json().get("items", [])]


def fetch_readme_excerpt(full_name, limit=1200):
    """요약 품질용 README 앞부분. 실패해도 설명만으로 요약하면 되니 빈 문자열."""
    try:
        r = requests.get(f"{GH_API}/repos/{full_name}/readme", timeout=20,
                         headers={**_gh_headers(), "Accept": "application/vnd.github.raw"})
        if r.status_code != 200:
            return ""
        text = re.sub(r"<[^>]+>", " ", r.text)              # HTML 배지·태그
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)    # 이미지
        text = re.sub(r"\s+", " ", text)
        return text[:limit]
    except Exception:
        return ""


def collect():
    source = "trending"
    try:
        repos = fetch_trending_page()
    except Exception as ex:
        log(f"  ! 트렌딩 페이지 실패: {ex}")
        repos = []
    if len(repos) < 5:
        log(f"  ! 트렌딩 페이지에서 {len(repos)}개만 나옴 — 검색 API(최근 7일 신규)로 대체")
        source = "new"
        repos = fetch_new_hot_repos()
    # 트렌딩 페이지 순서는 GitHub 자체 알고리즘이라 "이번 주 스타"와 순위가 어긋난다
    # (예: 1위 ▲16,388 · 2위 ▲2,789 · 9위 ▲8,802). 독자가 숫자로 납득할 수 있게 스타 순으로.
    repos.sort(key=lambda r: r["stars_week"], reverse=True)
    return repos[:TOP_N], source


# ---------------------------------------------------------------- 2) 요약
def summarize(repos):
    """Claude가 저장소마다 한글 한 줄 요약 + 분류를 붙인다. 실패하면 영어 설명 그대로."""
    lines = []
    for i, r in enumerate(repos):
        lines.append(f"[{i}] {r['full_name']} ({r['language'] or '언어 미상'})\n"
                     f"설명: {r['description']}\nREADME: {r.get('readme', '')}")
    prompt = f"""아래는 이번 주 GitHub에서 스타를 가장 많이 받은 저장소들이다.
AI·개발 트렌드를 공부하는 한국 독자를 위해 저장소마다 정리해라.

{chr(10).join(lines)}

## 출력 형식 (이 JSON 객체만, 다른 말 없이)
{{"repos": [{{"index": 번호, "summary_ko": "무엇을 하는 저장소인지 한글 한 문장 (60자 이내)", "category": "분류 한 단어 (예: AI 에이전트, 개발 도구, 학습 자료, 인프라, 보안)"}}]}}
모든 저장소를 빠짐없이, 받은 순서대로."""

    client = Anthropic(api_key=digest.ANTHROPIC_API_KEY)
    log(f"  Claude({MODEL})에게 {len(repos)}개 요약 요청...")
    resp = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}],
        **({"output_config": {"effort": SENT_EFFORT}} if SENT_EFFORT else {}),
    )
    digest._log_usage(resp, job="trending")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        out = json.loads(text)["repos"]
    except Exception as ex:
        log(f"  ! 요약 파싱 실패: {ex} (stop_reason={resp.stop_reason}) — 영어 설명으로 보냄")
        return
    for o in out:
        i = o.get("index")
        if isinstance(i, int) and 0 <= i < len(repos):
            repos[i]["summary_ko"] = o.get("summary_ko", "")
            repos[i]["category"] = o.get("category", "")


# ---------------------------------------------------------------- 3) 발송·기록
def recent_repeats(weeks=2):
    f = DATA_DIR / "trending_log.jsonl"
    if not f.exists():
        return set()
    cutoff = (dt.date.today() - dt.timedelta(days=7 * weeks)).isoformat()
    seen = set()
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("week_of", "") >= cutoff:
            seen.add(e.get("full_name"))
    return seen


def _k(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def build_message(repos, source, repeats):
    today = dt.date.today()
    title = ("이번 주 GitHub 트렌딩 TOP 10" if source == "trending"
             else "이번 주 새로 뜬 GitHub 저장소 TOP 10")
    parts = [f"<b>🔥 {title}</b> · {today:%m/%d} 주",
             "<i>⭐ 전체 스타 · ▲ 이번 주 받은 스타 · 🔁 최근 2주 안에 소개됨</i>", ""]
    for i, r in enumerate(repos, 1):
        name = html.escape(r["full_name"])
        meta = [f"⭐ {_k(r['stars'])}"]
        if source == "trending" and r["stars_week"]:
            meta.append(f"▲ {r['stars_week']:,}")
        if r["language"]:
            meta.append(html.escape(r["language"]))
        cat = f"  <i>[{html.escape(r['category'])}]</i>" if r.get("category") else ""
        rep = " 🔁" if r["full_name"] in repeats else ""
        summary = html.escape(r.get("summary_ko") or r["description"] or "(설명 없음)")
        parts.append(f"<b>{i}. <a href=\"{html.escape(r['url'])}\">{name}</a></b>{rep}{cat}")
        parts.append(f"{summary}")
        parts.append(f"<code>{' · '.join(meta)}</code>")
        parts.append("")
    return "\n".join(parts).strip()


def main():
    digest._require({"TELEGRAM_BOT_TOKEN": digest.TELEGRAM_TOKEN,
                     "TELEGRAM_CHAT_ID": digest.CHAT_ID,
                     "ANTHROPIC_API_KEY": digest.ANTHROPIC_API_KEY})
    log("주간 GitHub 트렌딩")
    repos, source = collect()
    log(f"→ {len(repos)}개 ({source})")
    if not repos:
        digest._broadcast({"text": "<b>🔥 주간 GitHub 트렌딩</b>\n\n이번 주는 가져오지 못했어요 (수집 실패).",
                           "parse_mode": "HTML"})
        return

    for r in repos:
        r["readme"] = fetch_readme_excerpt(r["full_name"])
    try:
        summarize(repos)
    except Exception as ex:   # 요약이 죽어도 목록은 보낸다
        log(f"  ! 요약 실패: {ex} — 영어 설명으로 보냄")

    repeats = recent_repeats()
    text = build_message(repos, source, repeats)
    digest._broadcast({"text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    log(f"  · 발송 ({len(text)}자, 수신자 {len(digest.RECIPIENTS)}명)")

    week_of = dt.date.today().isoformat()
    for i, r in enumerate(repos, 1):
        digest._append_jsonl(DATA_DIR / "trending_log.jsonl", {
            "week_of": week_of, "rank": i, "source": source,
            "full_name": r["full_name"], "stars": r["stars"], "stars_week": r["stars_week"],
            "language": r["language"], "category": r.get("category", ""),
            "summary_ko": r.get("summary_ko", ""),
        })
    log("완료")


if __name__ == "__main__":
    main()
