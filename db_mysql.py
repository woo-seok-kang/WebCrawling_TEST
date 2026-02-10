import os
import pymysql
import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# (필요시 추가 가능) 추적/광고 파라미터 제거
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "ref", "ref_src", "spm"
}

def canonicalize_url(url: str) -> str:
    """동일 기사 판정을 위한 URL 정규화"""
    url = (url or "").strip()
    if not url:
        return ""

    p = urlparse(url)

    # DB query: tracking 제거 + 정렬
    q = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if k.lower() in TRACKING_PARAMS:
            continue
        q.append((k, v))
    query = urlencode(sorted(q))

    # fragment 제거, trailing slash 정리
    path = p.path or ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()

    return urlunparse((scheme, netloc, path, "", query, ""))

def get_conn():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )

def _url_hash_bytes(url: str) -> bytes:
    """원본 URL이 아니라 '정규화 URL' 기준으로 해시 생성"""
    canon = canonicalize_url(url)
    return hashlib.sha256(canon.encode("utf-8")).digest()

def save_articles(articles, keyword):
    inserted, skipped = 0, 0
    new_articles = []

    sql = """
    INSERT INTO articles
    (keyword, title, url, url_hash, published, source, tags)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for a in articles:
                try:
                    title = a["title"]
                    url = a["url"]
                    published = a.get("published", "")
                    tags = ",".join(a.get("tags", []))
                    source = title.rsplit(" - ", 1)[-1] if " - " in title else ""

                    cur.execute(sql, (
                        keyword,
                        title,
                        url,                 # 원본 URL 저장
                        _url_hash_bytes(url),# canonical 기준 중복 차단
                        published,
                        source,
                        tags
                    ))
                    inserted += 1
                    new_articles.append(a) # 신규 URL만 따로 모음

                except pymysql.err.IntegrityError:
                    skipped += 1

        conn.commit()
    finally:
        conn.close()

    return inserted, skipped, new_articles
