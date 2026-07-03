# -*- coding: utf-8 -*-
"""
AI 데일리 다이제스트 — v0 (L1 수집·전송 + L2 관심도 랭킹)

흐름: RSS 수집 → 최근 글 필터 → Claude가 관심 태그 기준 top N 선별·한글 요약 → 텔레그램 전송

다음 단계(L3 Eval, L4 RAG)는 이 파일 위에 얹을 예정.
"""
import os
import sys
import json
import time
import html
import datetime as dt

import feedparser
import requests
from dotenv import load_dotenv
from anthropic import Anthropic

import config

# 윈도우 콘솔에서 한글/이모지 로그가 깨지지 않도록
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 1) 수집
def fetch_entries():
    """모든 피드에서 최근 LOOKBACK_HOURS 이내 글을 모은다. 실패한 피드는 건너뛴다."""
    cutoff = time.time() - config.LOOKBACK_HOURS * 3600
    items = []
    seen_links = set()

    for name, url in config.FEEDS:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                log(f"  ! {name}: 피드 파싱 실패 (건너뜀)")
                continue
            count = 0
            for e in feed.entries:
                # 발행 시각
                ts = None
                for key in ("published_parsed", "updated_parsed"):
                    if getattr(e, key, None):
                        ts = time.mktime(getattr(e, key))
                        break
                # 시각을 모르면 포함(신규 소스 초기), 알면 최근 것만
                if ts is not None and ts < cutoff:
                    continue
                link = getattr(e, "link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                summary = getattr(e, "summary", "") or ""
                # HTML 태그 대충 제거 + 길이 컷
                summary = _strip_html(summary)[:600]

                items.append({
                    "source": name,
                    "title": _strip_html(getattr(e, "title", "")).strip(),
                    "summary": summary,
                    "link": link,
                })
                count += 1
            log(f"  · {name}: {count}건")
        except Exception as ex:
            log(f"  ! {name}: 오류 {ex} (건너뜀)")

    return items


def _strip_html(s):
    import re
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


# ---------------------------------------------------------------- 2) 랭킹·요약
def rank_and_summarize(items):
    """Claude에게 후보를 주고 관심 태그 기준 top N을 한글 요약과 함께 받는다."""
    if not items:
        return []

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # 후보를 번호와 함께 텍스트로
    candidate_lines = []
    for i, it in enumerate(items):
        candidate_lines.append(
            f"[{i}] ({it['source']}) {it['title']}\n    {it['summary'][:300]}"
        )
    candidates_text = "\n\n".join(candidate_lines)

    prompt = f"""너는 AI 산업 트렌드를 매일 정리해 주는 큐레이터다.
아래 후보 기사들 중에서, 다음 독자의 관심사에 가장 잘 맞는 기사를 최대 {config.MAX_ITEMS}개 선택해라.

## 독자의 관심 (우선순위 높음)
{chr(10).join('- ' + t for t in config.INTERESTS_PRIORITY)}

## 요즘 뜨는 트렌드 (관심 있음)
{chr(10).join('- ' + t for t in config.INTERESTS_TRENDING)}

## 낮게 다룰 주제 (어지간히 중요하지 않으면 제외)
{chr(10).join('- ' + t for t in config.DEEMPHASIZE)}

## 후보 기사
{candidates_text}

## 출력 형식 (반드시 이 JSON 배열만, 다른 말 없이)
[
  {{
    "index": 후보번호(정수),
    "title_ko": "한글로 다듬은 제목",
    "summary_ko": "2~3문장 한글 요약. 이게 왜 중요한지/무엇이 새로운지 중심으로.",
    "tag": "이 기사가 걸린 관심사 한 개",
    "why": "독자에게 왜 볼 가치가 있는지 한 문장"
  }}
]

중요도 높은 순으로 정렬해라. 관심사에 걸리는 기사가 {config.MAX_ITEMS}개보다 적으면 그만큼만 선택해라."""

    log(f"  Claude({MODEL})에게 {len(items)}건 랭킹 요청...")
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,  # thinking 블록 + JSON 출력 여유 있게
        messages=[{"role": "user", "content": prompt}],
    )
    # 응답에 thinking 블록이 섞일 수 있으니 text 블록만 골라낸다
    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text = block.text
            break
    text = text.strip()

    # JSON 파싱 (코드펜스 제거)
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        selected = json.loads(text)
    except Exception as ex:
        log(f"  ! JSON 파싱 실패: {ex}")
        log(text[:500])
        return []

    # 원본 링크 붙이기
    out = []
    for s in selected:
        idx = s.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            s["link"] = items[idx]["link"]
            s["source"] = items[idx]["source"]
            out.append(s)
    return out


# ---------------------------------------------------------------- 3) 포맷·전송
def format_message(selected):
    today = dt.datetime.now().strftime("%Y-%m-%d (%a)")
    if not selected:
        return f"<b>🗞 AI 데일리 다이제스트</b> · {today}\n\n최근 {config.LOOKBACK_HOURS}시간 내 관심사에 맞는 새 글이 없었어요."

    lines = [f"<b>🗞 AI 데일리 다이제스트</b> · {today}", ""]
    for i, s in enumerate(selected, 1):
        title = html.escape(s.get("title_ko", ""))
        summary = html.escape(s.get("summary_ko", ""))
        tag = html.escape(s.get("tag", ""))
        why = html.escape(s.get("why", ""))
        src = html.escape(s.get("source", ""))
        link = s.get("link", "")
        lines.append(f"<b>{i}. {title}</b>  <i>[{tag}]</i>")
        lines.append(summary)
        if why:
            lines.append(f"💡 {why}")
        lines.append(f"🔗 <a href=\"{html.escape(link)}\">{src}</a>")
        lines.append("")
    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # 텔레그램 4096자 제한 → 넘으면 자른다(v0)
    if len(text) > 4000:
        text = text[:3990] + "\n…(생략)"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=30)
    data = r.json()
    if not data.get("ok"):
        log(f"  ! 텔레그램 전송 실패: {data}")
    else:
        log("  · 텔레그램 전송 완료")
    return data


# ---------------------------------------------------------------- main
def main():
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": CHAT_ID,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }.items() if not v]
    if missing:
        log(f"환경변수 누락: {', '.join(missing)} — .env 확인")
        sys.exit(1)

    log("1) RSS 수집 시작")
    items = fetch_entries()
    log(f"→ 후보 {len(items)}건")

    log("2) 랭킹·요약")
    selected = rank_and_summarize(items)
    log(f"→ 선택 {len(selected)}건")

    log("3) 전송")
    msg = format_message(selected)
    send_telegram(msg)
    log("완료")


if __name__ == "__main__":
    main()
