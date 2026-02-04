import os
import pymysql

# 환경 변수 지정 TEST 용
conn = pymysql.connect(
    host=os.environ["MYSQL_HOST"],
    port=int(os.environ["MYSQL_PORT"]),
    user=os.environ["MYSQL_USER"],
    password=os.environ["MYSQL_PASSWORD"],
    db=os.environ["MYSQL_DB"],
    charset="utf8mb4"
)

print("MySQL 연결 성공")
conn.close()