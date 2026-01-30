import os
import requests
import feedparser
from urllib.parse import quote
from datetime import datetime

#SLACK WEBHOOK URL에 대한 환경변수는 SLACK webhook을 통해 만듬. (앱 생성)


def google_news_rss(keyword: str, count: int = 5):
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

def send_to_slack(message: str):
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not slack_webhook_url:
        raise RuntimeError("SLACK_WEBHOOK_URL 환경변수 미 존재")

    r = requests.post(
        slack_webhook_url.strip(),
        json={"text": message},
        timeout=10
    )
    r.raise_for_status()

def make_message(keyword: str, articles: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"*크롤링 TEST* | 키워드: *{keyword}* | {now}\n"]

    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. {a['title']}\n{a['url']}")

    return "\n\n".join(lines)

if __name__ == "__main__":
    import sys, os
    print("SCRIPT:", __file__)
    print("PY:", sys.executable)
    print("ENV:", os.environ.get("SLACK_WEBHOOK_URL"))

    keyword = "자동차SW"
    articles = google_news_rss(keyword)

    if not articles:
        print("기사 없음")
    else:
        msg = make_message(keyword, articles)
        send_to_slack(msg)

