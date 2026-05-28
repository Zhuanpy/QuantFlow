import akshare as ak
import time

try:
    df = ak.stock_zh_a_hist(
        symbol="000001",
        period="daily",
        start_date="20240101",
        end_date="20241231",
        adjust="qfq"
    )

    print(df.head())

except Exception as e:
    print("错误:", e)

    time.sleep(3)