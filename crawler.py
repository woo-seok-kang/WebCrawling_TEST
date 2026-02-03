import os
import requests
import feedparser
from urllib.parse import quote
from datetime import datetime
from collections import defaultdict

from rules import (
    INCLUDE_SW,
    INCLUDE_SEC_REG_INCIDENT,
    EXCLUDE
)

# -----------------------------
# 1) Article grouping / tagging
# -----------------------------
def group_by_tag(articles: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for a in articles:
        tags = a.get("tags", [])
        if not tags:
            grouped["기타"].append(a)
        else:
            for t in tags:
                grouped[t].append(a)
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


def is_relevant_article(title: str) -> bool:
    t = (title or "").lower()
    if any(x.lower() in t for x in EXCLUDE):
        return False

    has_sw = any(x.lower() in t for x in INCLUDE_SW)
    has_sec_reg_inc = any(x.lower() in t for x in INCLUDE_SEC_REG_INCIDENT)
    return has_sw or has_sec_reg_inc


# -----------------------------
# 2) RSS crawling
# -----------------------------
def google_news_rss(keyword: str, count: int = 20):
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
def make_message(keyword: str, articles: list[dict], max_per_tag: int = 3) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grouped = group_by_tag(articles)

    lines = [
        "📌 *Daily Auto SW News*",
        f"- 키워드: *{keyword}*",
        f"- 시간: {now}",
        ""
    ]

    tag_order = ["보안", "규제", "사고", "SW", "기타"]

    for tag in tag_order:
        if tag not in grouped:
            continue

        items = grouped[tag]
        lines.append(f"*[{tag}]* ({len(items)})")

        # 메인에는 TOP N만 (TAG 설정 3개까지)
        for i, a in enumerate(items[:max_per_tag], 1):
            title = (a.get("title") or "").replace("<", "＜").replace(">", "＞")
            url = (a.get("url") or "").strip()
            lines.append(f"{i}. <{url}|{title}>") 

        # 나머지는 스레드로
        if len(items) > max_per_tag:
            lines.append(f"   … 외 {len(items) - max_per_tag}건 (스레드 참고)")

        lines.append("")

    return "\n".join(lines)


def make_thread_message(articles: list[dict], max_per_tag: int = 3) -> str:
    grouped = group_by_tag(articles)
    tag_order = ["보안", "규제", "사고", "SW", "기타"]

    lines = ["*상세 기사 목록(외 n건)*", ""]

    has_any = False
    for tag in tag_order:
        if tag not in grouped:
            continue

        items = grouped[tag]
        rest = items[max_per_tag:]
        if not rest:
            continue

        has_any = True
        lines.append(f"*[{tag}] 추가 {len(rest)}건*")

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

    keyword = "자동차SW"
    MAX_PER_TAG = 3

    print("SCRIPT:", __file__)
    print("PY:", sys.executable)

    # 쓰레드 토큰에 들어간 환경변수
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    print("BOT_TOKEN:", (token[:10] + "...") if token else None)
    print("CHANNEL_ID:", channel)

    raw_articles = google_news_rss(keyword, count=20)
    articles = []

    for a in raw_articles:
        if is_relevant_article(a["title"]):
            a["tags"] = classify_tags(a["title"])
            articles.append(a)

    print(f"raw={len(raw_articles)} filtered={len(articles)}")

    if not articles:
        print("not filter")
    else:
        # 1) 메인 메시지
        main_msg = make_message(keyword, articles, max_per_tag=MAX_PER_TAG)
        thread_ts = slack_post_message(main_msg)

        # 2) 스레드 메시지(나머지)
        thread_msg = make_thread_message(articles, max_per_tag=MAX_PER_TAG)
        if thread_msg:
            slack_post_thread(thread_msg, thread_ts)

        print("Slack sending SUCCESS")