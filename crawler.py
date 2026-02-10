import os
import requests
import feedparser
from urllib.parse import quote
from datetime import datetime
from collections import defaultdict
from db_mysql import save_articles, canonicalize_url
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

from rules import (
    INCLUDE_SW,
    INCLUDE_SEC_REG_INCIDENT,
    EXCLUDE
)

# -----------------------------
# 1) Article grouping / tagging
# -----------------------------
def group_by_tag_combo(articles: list[dict]) -> dict[tuple[str, ...], list[dict]]:
    grouped = defaultdict(list)

    for a in articles:
        tags = a.get("tags") or []
        # 순서를 고정(보안/규제/사고/SW/기타 순)
        order = {"보안": 0, "규제": 1, "사고": 2, "SW": 3, "기타": 4}
        tags_sorted = tuple(sorted(tags, key=lambda x: order.get(x, 99)))

        # 태그에 미 포함 시 기타로
        if not tags_sorted:
            tags_sorted = ("기타",)

        grouped[tags_sorted].append(a)

    return grouped


def classify_tags(title: str) -> list[str]:
    t = (title or "").lower()
    tags = []

    if any(x.lower() in t for x in INCLUDE_SW):
        tags.append("SW")

    if any(x.lower() in t for x in INCLUDE_SEC_REG_INCIDENT):
        if any(k in t for k in ["보안", "사이버", "해킹", "취약점", "공격", "랜섬웨어"]):
            tags.append("보안")
        elif any(k in t for k in ["unece", "r155", "r156", "iso", "규제", "법규", "인증"]):
            tags.append("규제")
        elif any(k in t for k in ["사고", "화재", "리콜", "결함"]):
            tags.append("사고")
        else:
            tags.append("기타")

    return tags


def is_relevant_article(title: str, keyword: str) -> bool:
    t = (title or "").lower()

    # 제외 키워드 먼저 걸러냄
    if any(x.lower() in t for x in EXCLUDE):
        return False

    key_tokens = ["자동차sw", "자동차 sw", "차량 소프트웨어", "sdv"]
    tt = t.replace(" ", "")
    if any(k.replace(" ", "") in tt for k in key_tokens):
        return True

    has_sw = any(x.lower().replace(" ", "") in tt for x in INCLUDE_SW)
    has_sec_reg_inc = any(x.lower().replace(" ", "") in tt for x in INCLUDE_SEC_REG_INCIDENT)
    return has_sw or has_sec_reg_inc

# --------------------------------------------------------------
# 다중 포털에서 같은 기사 중복 제외 (같은 기사 여러 포털사이트 출력)
# --------------------------------------------------------------
import re

def normalize_title(title: str) -> str:
    t = (title or "").strip().lower()
    t = t.replace("＜", "<").replace("＞", ">")
    t = re.sub(r"[“”\"'‘’]", "", t)

    # 끝에 붙는 포털/언론사명 제거
    t = re.sub(r"\s*[-|:]\s*[^-|:]{2,30}\s*$", "", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w가-힣\s]", "", t)

    return t.strip()

def dedup_near_same_title(articles: list[dict]) -> list[dict]:
    seen = set()
    out = []

    for a in articles:
        key = normalize_title(a.get("title", ""))
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(a)

    return out

# -----------------------------
# 2) RSS crawling
# -----------------------------
def google_news_rss(keyword: str, count: int = 50):
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    feed = feedparser.parse(url)

    articles = []
    for entry in feed.entries[:count]:
        articles.append({
            "title": entry.title,
            "url": entry.link,
            "published": getattr(entry, "published", "")
        })
    return articles


# -----------------------------
# 3) Slack BOT (API) posting
# -----------------------------
def slack_post_message(text: str) -> str:
    """채널에 메인 메시지를 올리고 ts 반환"""
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    if not token or not channel:
        raise RuntimeError("SLACK_BOT_TOKEN / SLACK_CHANNEL_ID 환경변수 미 존재.")

    #지정한 SLACK_CHANNEL_ID 와 BOT_TOKEN 으로 탑 3개를 제외한 나머지 URL은 쓰레드 처리
    api_url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "channel": channel,
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }

    r = requests.post(api_url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()

    print("Slack API(main) ok:", data.get("ok"), "error:", data.get("error"))
    if not data.get("ok"):
        raise RuntimeError(f"Slack API 실패: {data.get('error')}")

    return data["ts"]


def slack_post_thread(text: str, thread_ts: str) -> None:
    """메인 메시지의 스레드(댓글)로 추가 메시지 올리기"""
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    if not token or not channel:
        raise RuntimeError("SLACK_BOT_TOKEN / SLACK_CHANNEL_ID 환경변수 미 존재.")

    api_url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "channel": channel,
        "text": text,
        "thread_ts": thread_ts,
        "unfurl_links": False,
        "unfurl_media": False,
    }

    r = requests.post(api_url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    data = r.json() 

    print("Slack API(thread) ok:", data.get("ok"), "error:", data.get("error"))
    if not data.get("ok"):
        raise RuntimeError(f"Slack API(thread) 실패: {data.get('error')}")

# -----------------------------
# 4) Message building
# -----------------------------
def _tag_label(a: dict) -> str:
    tags = a.get("tags", []) or []
    order = {"보안": 0, "규제": 1, "사고": 2, "SW": 3, "기타": 4}
    tags = sorted(set(tags), key=lambda x: order.get(x, 99))
    return "".join(f"[{t}]" for t in tags)

def article_key(a: dict) -> str:   #기사 URL 중복 제거용 키
    url = (a.get("url") or "").strip().lower()
    title = (a.get("title") or "").strip().lower()
    return url or title  # url 있으면 url 우선, 없으면 title

def make_message(keyword: str, articles: list[dict], max_per_group: int = 3):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grouped = group_by_tag_combo(articles)

    order = {"보안": 0, "규제": 1, "사고": 2, "SW": 3, "기타": 4}
    def combo_sort_key(combo):
        return (order.get(combo[0], 99), len(combo), combo)

    combos = sorted(grouped.keys(), key=combo_sort_key)

    lines = [
        "📌 *Daily Auto SW News*",
        f"- 키워드: *{keyword_title}*",
        f"- 시간: {now}",
        f"- 신규 기사: {len(articles)}건",
        ""
    ]
    shown_keys = set()

    for combo in combos:
        items = grouped[combo]

        # 정렬 기준을 고정 
        items = sorted(
            items,
            key=lambda a: ((a.get("published") or ""), (a.get("title") or "")),
            reverse=True
        )

        header = "".join([f"[{t}]" for t in combo])
        lines.append(f"*{header}* ({len(items)})")

        main_items = items[:max_per_group]
        for i, a in enumerate(main_items, 1):
            shown_keys.add(article_key(a))
            title = (a.get("title") or "").replace("<", "＜").replace(">", "＞")
            url = (a.get("url") or "").strip()
            lines.append(f"{i}. <{url}|{title}>")

        if len(items) > max_per_group:
            lines.append(f"… 외 {len(items) - max_per_group}건 (스레드 참고)")

        lines.append("")

    return "\n".join(lines), shown_keys

def make_thread_message(articles: list[dict], shown_keys: set, max_per_group: int = 3) -> str:
    grouped = group_by_tag_combo(articles)
    order = {"보안": 0, "규제": 1, "사고": 2, "SW": 3, "기타": 4}

    def combo_sort_key(combo):
        return (order.get(combo[0], 99), len(combo), combo)

    combos = sorted(grouped.keys(), key=combo_sort_key)

    lines = ["*상세 기사 목록(외 n건)*", ""]
    has_any = False

    for combo in combos:
        items = grouped[combo]

        # make_message와 동일한 정렬
        items = sorted(
            items,
            key=lambda a: ((a.get("published") or ""), (a.get("title") or "")),
            reverse=True
        )

        # 메인에 나온 건 제외 (메인에 보여주는 기사와 쓰레드 댓글 중복 방지)
        rest = [a for a in items if article_key(a) not in shown_keys]
        if not rest:
            continue

        has_any = True
        header = "".join([f"[{t}]" for t in combo])
        lines.append(f"*{header} 추가 {len(rest)}건*")

        for i, a in enumerate(rest, 1):
            title = (a.get("title") or "").replace("<", "＜").replace(">", "＞")
            url = (a.get("url") or "").strip()
            lines.append(f"{i}. <{url}|{title}>")

        lines.append("")

    return "\n".join(lines) if has_any else ""


# -----------------------------
# 5) main
# -----------------------------
if __name__ == "__main__":
    import sys

    keyword = '("자동차 SW" OR 자동차SW OR "차량 소프트웨어" OR SDV)'
    keyword_title = ("자동차 SW · 자동차SW · 차량 소프트웨어 · SDV")

    MAX_PER_TAG = 5

    print("SCRIPT:", __file__)
    print("PY:", sys.executable)

    # 쓰레드 토큰에 들어간 환경변수
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    print("BOT_TOKEN:", (token[:10] + "...") if token else None)
    print("CHANNEL_ID:", channel)

    raw_articles = google_news_rss(keyword, count=50)
    articles = []

for a in raw_articles:
    if is_relevant_article(a["title"], keyword):
        a["tags"] = classify_tags(a["title"])
        articles.append(a)


    print(f"raw={len(raw_articles)} filtered={len(articles)}")

    # (추가) run 내 URL 중복 제거 (canonical 기준) ->
    seen = set()
    deduped = []
    for a in articles:
        key = canonicalize_url(a.get("url", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    articles = deduped
    print(f"after_deduped={len(articles)}")

# -----------------------------
# 6) DATABASE
# -----------------------------

if not articles:
        print("not filter")
else:
        # DB 저장 (중복URL 걸러내기 위함)
        inserted, skipped, new_articles = save_articles(articles, keyword)
        print(f"DB 저장 결과: 신규 {inserted}, 중복 {skipped}")
        new_articles = dedup_near_same_title(new_articles)

        # 신규가 없으면 Slack 안 보냄 (도배 방지)
        if not new_articles:
            print("신규 기사 없음 → Slack 전송 스킵")
        else:
            # 1) 메인 메시지(신규 기준)
            main_msg, shown_keys = make_message(keyword, new_articles, max_per_group=MAX_PER_TAG)
            thread_ts = slack_post_message(main_msg)

            thread_msg = make_thread_message(new_articles, shown_keys, max_per_group=MAX_PER_TAG)
            if thread_msg:
                slack_post_thread(thread_msg, thread_ts)
                
            print(f"키워드: {keyword_title}")
            print("Slack sending SUCCESS")