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
import hashlib
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
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")          # 소유자(본인). 피드백 is_owner 판정 기준
# 지인 추가 수신자: 콤마로 구분한 chat_id 목록 (예: "123456789,987654321")
# 상대가 봇에 /start 를 한 번 눌러야 봇이 말을 걸 수 있다. chat_id는 /start 시 자동 안내됨.
_EXTRA_CHAT_IDS = os.getenv("TELEGRAM_EXTRA_CHAT_IDS", "")
RECIPIENTS = list(dict.fromkeys(
    c.strip() for c in ([CHAT_ID] + _EXTRA_CHAT_IDS.split(",")) if c and c.strip()
))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
DATA_DIR = Path(__file__).parent / "data"


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _user_key(uid):
    """텔레그램 user_id를 공개 저장소에 남겨도 되는 익명 키로 바꾼다.
    봇 토큰을 소금으로 써서 토큰을 모르면 원본 id를 되찾을 수 없다."""
    if uid is None:
        return None
    h = hashlib.sha256(f"{TELEGRAM_TOKEN}:{uid}".encode("utf-8")).hexdigest()
    return h[:12]


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
            # /start · /test 등 명령어 처리 (구독은 자동이 아님 —
            # 소유자가 TELEGRAM_EXTRA_CHAT_IDS에 넣어줘야 발송된다)
            _handle_command(u.get("message") or {})
            continue
        data = cq.get("data", "")
        # 형식: fb|2026-07-04|3|up
        parts = data.split("|")
        if len(parts) != 4 or parts[0] != "fb":
            continue
        _, date, idx, verdict = parts
        # 버튼 누른 사람 — 수신자가 여럿일 때 취향이 섞이지 않게 구분한다.
        # 저장소가 public이라 원본 텔레그램 id/이름은 남기지 않고 해시만 쓴다.
        uid = (cq.get("from") or {}).get("id")
        _append_jsonl(DATA_DIR / "feedback.jsonl", {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "digest_date": date,
            "item_index": int(idx),
            "verdict": verdict,          # "up" | "down"
            "user": _user_key(uid),      # 익명 키 (같은 사람=같은 값, 역추적 불가)
            "is_owner": str(uid) == str(CHAT_ID),   # 랭킹 기준으로 쓸 피드백은 본인 것만
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


def _handle_command(msg):
    """봇에게 온 명령어 처리. 현재 지원: /start (구독 요청), /test (전송 점검).
    다이제스트 발송이나 Claude 호출은 하지 않는다 — 가벼운 폴링에서도 안전하게 돌아간다."""
    text = str(msg.get("text", "")).strip()
    chat = msg.get("chat") or {}
    cid = chat.get("id")
    if cid is None:
        return False

    if text.startswith("/start"):
        _handle_start(chat, cid)
        return True
    if text.startswith("/test"):
        _handle_test(chat, cid)
        return True
    return False


def _display_name(chat):
    return chat.get("username") or " ".join(
        x for x in (chat.get("first_name"), chat.get("last_name")) if x
    ) or "이름없음"


def _handle_start(chat, cid):
    """새 사람이 /start 를 누르면 본인에게는 안내를, 소유자에게는 등록용 chat_id를 보낸다.
    chat_id는 공개 Actions 로그에 찍지 않고 텔레그램 DM으로만 전달한다."""
    lines = [
        "<b>🗞 AI 데일리 다이제스트</b>",
        "",
        "매일 아침 AI 소식을 골라 보내드립니다.",
        f"구독 등록용 번호: <code>{cid}</code>",
        "이 번호를 운영자에게 보내주시면 다음 발송부터 받아보실 수 있어요.",
        "",
        "잘 도착하는지 확인하려면 <code>/test</code> 를 보내보세요.",
    ]
    _tg_send({"chat_id": cid, "text": "\n".join(lines), "parse_mode": "HTML"})

    if str(cid) != str(CHAT_ID):
        notice = [
            f"👤 새 구독 요청: <b>{html.escape(str(_display_name(chat)))}</b>",
            f"chat_id: <code>{cid}</code>",
            "TELEGRAM_EXTRA_CHAT_IDS 시크릿에 콤마로 추가하면 발송됩니다.",
        ]
        _tg_send({"chat_id": CHAT_ID, "text": "\n".join(notice), "parse_mode": "HTML"})
    log("  · /start 1건 처리 (chat_id는 DM으로만 전달)")


def _handle_test(chat, cid):
    """/test — 이 사람에게 실제로 메시지가 닿는지, 구독자 명단에 있는지 즉시 알려준다."""
    registered = str(cid) in [str(r) for r in RECIPIENTS]
    is_owner = str(cid) == str(CHAT_ID)

    if registered:
        status = "✅ 구독 등록됨 — 매일 아침 발송 대상입니다."
    else:
        status = ("⚠️ 아직 등록 전입니다. 메시지는 닿지만 매일 발송 대상은 아니에요.\n"
                  f"운영자에게 이 번호를 전해주세요: <code>{cid}</code>")

    lines = [
        "<b>🔔 전송 테스트</b>",
        "",
        "이 메시지가 보이면 봇 → 나 방향은 정상입니다.",
        status,
    ]
    if is_owner:
        others = [r for r in RECIPIENTS if str(r) != str(CHAT_ID)]
        lines += ["", f"현재 수신자 {len(RECIPIENTS)}명 (나 + 지인 {len(others)}명)"]

    _tg_send({"chat_id": cid, "text": "\n".join(lines), "parse_mode": "HTML"})

    if not is_owner:
        _tg_send({
            "chat_id": CHAT_ID,
            "text": (f"🔔 /test 수신: <b>{html.escape(str(_display_name(chat)))}</b>\n"
                     f"chat_id: <code>{cid}</code> · "
                     + ("등록됨" if registered else "미등록")),
            "parse_mode": "HTML",
        })
    log("  · /test 1건 처리")


# ---------------------------------------------------------------- 발송 이력
def load_covered_terms():
    """glossary_log.jsonl에서 이미 소개한 용어 목록을 읽는다. (중복 소개 방지)"""
    f = DATA_DIR / "glossary_log.jsonl"
    terms = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                terms.append(json.loads(line)["term"])
            except Exception:
                continue
    return terms


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
def rank_and_summarize(items, recent_titles=None, covered_terms=None):
    """Claude에게 후보를 주고 top N 기사 + 오늘의 용어 2개를 받는다."""
    if not items:
        return [], []

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

## 추가 임무: 오늘의 AI 용어 {config.GLOSSARY_COUNT}개
AI 산업을 공부하는 독자를 위해 실무 AI 용어 {config.GLOSSARY_COUNT}개를 골라 쉽게 설명해라.
- 가능하면 위에서 선택한 기사에 등장하는 용어를 우선해라 (기사와 연결되면 기억에 남는다)
- 설명은 비유를 섞어 2~3문장, 전문지식 없이 이해되게
- 이미 소개한 용어 (다시 고르지 마라): {', '.join(covered_terms) if covered_terms else '(아직 없음)'}

## 출력 형식 (반드시 이 JSON 객체만, 다른 말 없이)
{{
  "articles": [
    {{
      "index": 후보번호(정수),
      "title_ko": "한글로 다듬은 제목",
      "summary_ko": "2~3문장 한글 요약. 이게 왜 중요한지/무엇이 새로운지 중심으로.",
      "tag": "이 기사가 걸린 관심사 한 개",
      "why": "독자에게 왜 볼 가치가 있는지 한 문장"
    }}
  ],
  "glossary": [
    {{
      "term": "용어 (영문 원어 병기)",
      "explanation": "쉬운 한글 설명 2~3문장, 비유 포함",
      "in_article": 이 용어가 등장한 기사의 후보번호(위 articles의 index와 동일한 정수). 선택한 기사에 없으면 null
    }}
  ]
}}

articles는 중요도 높은 순으로 정렬해라. 관심사에 걸리는 기사가 {config.MAX_ITEMS}개보다 적으면 그만큼만 선택해라.
glossary는 {config.GLOSSARY_COUNT}개 (새로 소개할 용어가 부족하면 그만큼만). in_article은 반드시 위 articles에 실제로 선택된 기사의 index여야 한다."""

    log(f"  Claude({MODEL})에게 {len(items)}건 랭킹 요청...")
    resp = client.messages.create(
        model=MODEL,
        max_tokens=12000,  # thinking 블록 + JSON 출력 여유 있게 (MAX_ITEMS 9로 늘려 상향)
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
        parsed = json.loads(text)
    except Exception as ex:
        log(f"  ! JSON 파싱 실패: {ex}")
        log(text[:500])
        return [], []

    # 구버전(배열) 응답도 허용
    if isinstance(parsed, list):
        articles, glossary = parsed, []
    else:
        articles = parsed.get("articles", [])
        glossary = parsed.get("glossary", [])

    out = []
    for s in articles:
        idx = s.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            s["link"] = items[idx]["link"]
            s["source"] = items[idx]["source"]
            out.append(s)

    # 용어의 in_article(후보번호)을 실제 표시 번호(1..N)로 변환. 매칭 안 되면 참고 제거.
    idx_to_pos = {}
    for pos, s in enumerate(out, 1):
        ci = s.get("index")
        if isinstance(ci, int):
            idx_to_pos[ci] = pos
    for g in glossary:
        ia = g.get("in_article")
        g["in_article"] = idx_to_pos.get(ia) if isinstance(ia, int) else None

    return out, glossary


# ---------------------------------------------------------------- 3) 전송·기록
def _tg_send(payload):
    r = requests.post(f"{API}/sendMessage", json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        log(f"  ! 텔레그램 전송 실패: {data}")
    return data


def _broadcast(payload):
    """같은 메시지를 모든 수신자에게. 한 명이 실패해도(차단·탈퇴 등) 나머지는 계속 간다."""
    for cid in RECIPIENTS:
        try:
            data = _tg_send(dict(payload, chat_id=cid))
            if not data.get("ok"):
                log(f"    ({cid} 실패 — 나머지 계속)")
        except Exception as ex:
            log(f"  ! {cid} 전송 오류: {ex} (나머지 계속)")
        if len(RECIPIENTS) > 1:
            time.sleep(0.15)   # 텔레그램 초당 30건 제한 여유


def send_digest(selected):
    """헤더 1건 + 기사별 메시지(👍/👎 버튼)로 전송하고 발송 내역을 기록한다."""
    today = dt.datetime.now().strftime("%Y-%m-%d")
    today_label = dt.datetime.now().strftime("%Y-%m-%d (%a)")

    if not selected:
        _broadcast({
            "text": f"<b>🗞 AI 데일리 다이제스트</b> · {today_label}\n\n최근 {config.LOOKBACK_HOURS}시간 내 관심사에 맞는 새 글이 없었어요.",
            "parse_mode": "HTML",
        })
        return

    _broadcast({
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

        _broadcast({
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


def send_glossary(glossary):
    """오늘의 용어 메시지를 보내고 glossary_log.jsonl에 기록한다."""
    if not glossary:
        return
    today = dt.datetime.now().strftime("%Y-%m-%d")
    lines = ["<b>📚 오늘의 AI 용어</b>", ""]
    for g in glossary:
        term = html.escape(str(g.get("term", "")))
        expl = html.escape(str(g.get("explanation", "")))
        in_art = g.get("in_article")
        ref = f"  <i>(오늘 {in_art}번 기사 참고)</i>" if in_art else ""
        lines.append(f"<b>· {term}</b>{ref}")
        lines.append(expl)
        lines.append("")
        _append_jsonl(DATA_DIR / "glossary_log.jsonl", {
            "digest_date": today,
            "term": g.get("term", ""),
            "explanation": g.get("explanation", ""),
        })
    _broadcast({
        "text": "\n".join(lines).strip(),
        "parse_mode": "HTML",
    })
    log(f"  · 용어 {len(glossary)}개 전송·기록")


# ---------------------------------------------------------------- main
def _require(keys):
    missing = [k for k, v in keys.items() if not v]
    if missing:
        log(f"환경변수 누락: {', '.join(missing)} — .env 확인")
        sys.exit(1)


def poll_only():
    """명령어(/start·/test)와 피드백 버튼만 수거한다.
    RSS도, Claude 호출도, 다이제스트 발송도 하지 않는다 — 자주 돌려도 비용이 없다."""
    _require({"TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN, "TELEGRAM_CHAT_ID": CHAT_ID})
    log("폴링 전용 실행 (발송 없음)")
    collect_feedback()
    log("완료")


def main():
    _require({
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": CHAT_ID,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    })

    log("0) 지난 피드백 수거")
    log(f"  · 수신자 {len(RECIPIENTS)}명")
    collect_feedback()

    sent_links, recent_titles = load_sent_history()
    covered_terms = load_covered_terms()
    log(f"  · 최근 7일 발송 이력: 링크 {len(sent_links)}건 (후보에서 제외) / 소개한 용어 {len(covered_terms)}개")

    log("1) RSS 수집 시작")
    items = fetch_entries(exclude_links=sent_links)
    log(f"→ 후보 {len(items)}건")

    log("2) 랭킹·요약")
    selected, glossary = rank_and_summarize(items, recent_titles=recent_titles,
                                            covered_terms=covered_terms)
    log(f"→ 선택 {len(selected)}건 + 용어 {len(glossary)}개")

    log("3) 전송·기록")
    send_digest(selected)
    send_glossary(glossary)
    log("완료")


if __name__ == "__main__":
    if "--poll" in sys.argv:
        poll_only()
    else:
        main()
