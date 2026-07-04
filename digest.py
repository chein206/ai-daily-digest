# -*- coding: utf-8 -*-
"""
AI 데일리 다이제스트 — v1 (L1 수집·전송 + L2 관심도 랭킹 + L3 피드백 수집)

흐름:
  0) 지난 발송의 👍/👎 버튼 클릭 수거 → data/feedback.jsonl (Eval 데이터셋)
  1) RSS 수집 → 최근 글 필터
  2) Claude가 관심 태그 기준 top N 선별·한글 요약
  3) 기사별 메시지 + 피드백 버튼 전송, 발송 내역을 data/digest_log.jsonl에 기록

다음 단계: L3 Eval 리포트(피드백 기반 선별 품질 측정), L4 아카이브 RAG.
"""
import os
import sys
import json
import time
import html
import datetime as dt
from pathlib import Path

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

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
DATA_DIR = Path(__file__).parent / "data"


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _append_jsonl(path, obj):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 0) 피드백 수거
def collect_feedback():
    """지난 발송 이후 눌린 👍/👎 콜백을 수거해 feedback.jsonl에 기록한다."""
    offset_file = DATA_DIR / "tg_offset.json"
    offset = 0
    if offset_file.exists():
        offset = json.loads(offset_file.read_text(encoding="utf-8")).get("offset", 0)

    try:
        r = requests.get(f"{API}/getUpdates",
                         params={"offset": offset + 1, "timeout": 0}, timeout=30)
        updates = r.json().get("result", [])
    except Exception as ex:
        log(f"  ! getUpdates 실패: {ex} (피드백 수거 건너뜀)")
        return

    collected = 0
    max_id = offset
    for u in updates:
        max_id = max(max_id, u.get("update_id", 0))
        cq = u.get("callback_query")
        if not cq:
            continue
        data = cq.get("data", "")
        # 형식: fb|2026-07-04|3|up
        parts = data.split("|")
        if len(parts) != 4 or parts[0] != "fb":
            continue
        _, date, idx, verdict = parts
        _append_jsonl(DATA_DIR / "feedback.jsonl", {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "digest_date": date,
            "item_index": int(idx),
            "verdict": verdict,          # "up" | "down"
        })
        collected += 1
        # 버튼 누른 사람에게 즉시 토스트 응답
        try:
            requests.post(f"{API}/answerCallbackQuery", json={
                "callback_query_id": cq["id"],
                "text": "기록했어요 ✓" if verdict == "up" else "기록했어요 ✓ (다음 선별에 반영)",
            }, timeout=15)
        except Exception:
            pass

    if max_id > offset:
        DATA_DIR.mkdir(exist_ok=True)
        offset_file.write_text(json.dumps({"offset": max_id}), encoding="utf-8")

    log(f"  · 피드백 {collected}건 수거")


# ---------------------------------------------------------------- 발송 이력
def load_sent_history(days=7):
    """digest_log.jsonl에서 최근 발송 이력을 읽는다. (링크 중복 제거 + 주제 중복 판단용)"""
    log_file = DATA_DIR / "digest_log.jsonl"
    sent_links, recent_titles = set(), []
    if not log_file.exists():
        return sent_links, recent_titles
    cutoff_date = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    for line in log_file.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("digest_date", "") >= cutoff_date:
            if e.get("link"):
                sent_links.add(e["link"])
            if e.get("title_ko"):
                recent_titles.append(f"[{e['digest_date']}] {e['title_ko']}")
    return sent_links, recent_titles


# ---------------------------------------------------------------- 1) 수집
def fetch_entries(exclude_links=None):
    """모든 피드에서 최근 LOOKBACK_HOURS 이내 글을 모은다. 실패한 피드는 건너뛴다."""
    cutoff = time.time() - config.LOOKBACK_HOURS * 3600
    items = []
    seen_links = set(exclude_links or set())

    for name, url in config.FEEDS:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                log(f"  ! {name}: 피드 파싱 실패 (건너뜀)")
                continue
            count = 0
            for e in feed.entries:
                ts = None
                for key in ("published_parsed", "updated_parsed"):
                    if getattr(e, key, None):
                        ts = time.mktime(getattr(e, key))
                        break
                if ts is not None and ts < cutoff:
                    continue
                link = getattr(e, "link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                summary = _strip_html(getattr(e, "summary", "") or "")[:600]
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


def _recent_block(recent_titles):
    if not recent_titles:
        return ""
    titles = chr(10).join('- ' + t for t in recent_titles[-30:])
    return f"""
## 최근 이미 발송한 기사 (주제 중복 주의)
{titles}

위 목록과 같은 주제를 단순 반복하는 기사는 제외해라.
단, 이미 다룬 주제라도 중요한 새 전개·후속 소식이면 선정하되 summary_ko에 무엇이 새로운지를 명시해라.
"""


# ---------------------------------------------------------------- 2) 랭킹·요약
def rank_and_summarize(items, recent_titles=None):
    """Claude에게 후보를 주고 관심 태그 기준 top N을 한글 요약과 함께 받는다."""
    if not items:
        return []

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

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
{_recent_block(recent_titles)}
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

    out = []
    for s in selected:
        idx = s.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            s["link"] = items[idx]["link"]
            s["source"] = items[idx]["source"]
            out.append(s)
    return out


# ---------------------------------------------------------------- 3) 전송·기록
def _tg_send(payload):
    r = requests.post(f"{API}/sendMessage", json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        log(f"  ! 텔레그램 전송 실패: {data}")
    return data


def send_digest(selected):
    """헤더 1건 + 기사별 메시지(👍/👎 버튼)로 전송하고 발송 내역을 기록한다."""
    today = dt.datetime.now().strftime("%Y-%m-%d")
    today_label = dt.datetime.now().strftime("%Y-%m-%d (%a)")

    if not selected:
        _tg_send({
            "chat_id": CHAT_ID,
            "text": f"<b>🗞 AI 데일리 다이제스트</b> · {today_label}\n\n최근 {config.LOOKBACK_HOURS}시간 내 관심사에 맞는 새 글이 없었어요.",
            "parse_mode": "HTML",
        })
        return

    _tg_send({
        "chat_id": CHAT_ID,
        "text": f"<b>🗞 AI 데일리 다이제스트</b> · {today_label} · {len(selected)}건\n각 기사의 👍/👎 로 선별 품질을 알려주세요 — Eval 데이터가 됩니다.",
        "parse_mode": "HTML",
    })

    for i, s in enumerate(selected, 1):
        title = html.escape(s.get("title_ko", ""))
        summary = html.escape(s.get("summary_ko", ""))
        tag = html.escape(s.get("tag", ""))
        why = html.escape(s.get("why", ""))
        src = html.escape(s.get("source", ""))
        link = s.get("link", "")

        text = (f"<b>{i}. {title}</b>  <i>[{tag}]</i>\n"
                f"{summary}\n"
                + (f"💡 {why}\n" if why else "")
                + f"🔗 <a href=\"{html.escape(link)}\">{src}</a>")

        _tg_send({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "👍 좋은 선별", "callback_data": f"fb|{today}|{i}|up"},
                    {"text": "👎 별로",     "callback_data": f"fb|{today}|{i}|down"},
                ]]
            },
        })

        # 발송 내역 기록 (피드백과 조인해 Eval 데이터셋이 됨)
        _append_jsonl(DATA_DIR / "digest_log.jsonl", {
            "digest_date": today,
            "item_index": i,
            "title_ko": s.get("title_ko", ""),
            "tag": s.get("tag", ""),
            "source": s.get("source", ""),
            "link": link,
        })

    log(f"  · 텔레그램 전송 완료 ({len(selected)}건 + 헤더)")


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

    log("0) 지난 피드백 수거")
    collect_feedback()

    sent_links, recent_titles = load_sent_history()
    log(f"  · 최근 7일 발송 이력: 링크 {len(sent_links)}건 (후보에서 제외)")

    log("1) RSS 수집 시작")
    items = fetch_entries(exclude_links=sent_links)
    log(f"→ 후보 {len(items)}건")

    log("2) 랭킹·요약")
    selected = rank_and_summarize(items, recent_titles=recent_titles)
    log(f"→ 선택 {len(selected)}건")

    log("3) 전송·기록")
    send_digest(selected)
    log("완료")


if __name__ == "__main__":
    main()
