import os
import pymysql
import hashlib

# MySQL 연동 TEST 완료된 환경 변수들
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
    return hashlib.sha256(url.encode("utf-8")).digest()  

# 생성한 articles Table 에 각 Column에 대한 정보 저장
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
                        url,
                        _url_hash_bytes(url),
                        published,
                        source,
                        tags
                    ))
                    inserted += 1
                    new_articles.append(a)   # 신규 URL만 따로 모음

                except pymysql.err.IntegrityError:
                    skipped += 1

        conn.commit()
    finally:
        conn.close()

    return inserted, skipped, new_articles