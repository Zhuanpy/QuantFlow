from pytdx.hq import TdxHq_API
import pandas as pd


servers = [
    # 已验证可用的服务器（2025-10-25测试）
    ('60.191.117.167', 7709),    # ✓ 可用
    ('115.238.56.198', 7709),    # ✓ 可用
    ('115.238.90.165', 7709),    # ✓ 可用
    # 备用服务器
    ('119.147.212.81', 7709),
    ('114.80.80.72', 7709),
    ('114.80.80.76', 7709),
    ('218.108.98.244', 7709),
    ('218.108.47.69', 7709),
]

def get_market(stock_code):
    """
    根据股票代码判断市场
    
    Parameters:
    stock_code: 股票代码，如 '000001' 或 '600000'
    
    Returns:
    int: 0-深圳市场，1-上海市场
    """
    if stock_code.startswith(('6', '5', '9')):  # 上海市场
        return 1
    else:  # 深圳市场（0, 3, 2开头）
        return 0

def get_1m_data(stock_code, market, days=5):
    """
    获取股票1分钟K线数据（改进版）

    Parameters:
    stock_code: 股票代码，如 '000001'
    market: 市场代码，0-深圳，1-上海
    days: 获取天数，默认5天
    
    Returns:
    DataFrame: 包含 date, open, close, high, low, volume, money 列
    """
    api = TdxHq_API()
    
    # 计算需要获取的K线数量（每天约240根1分钟线）
    count = min(days * 240, 800)  # 限制最多800根
    
    # 尝试多个服务器
    for server_ip, server_port in servers:
        try:
            print(f"尝试连接服务器: {server_ip}:{server_port}")
            if api.connect(server_ip, server_port):
                print(f"✓ 连接成功: {server_ip}:{server_port}")
                
                # 获取1分钟线数据
                data = api.get_security_bars(
                    8,  # 8表示1分钟线
                    market,
                    stock_code,
                    0,  # 从第0根开始
                    count  # 获取指定数量的K线
                )

                # 转换为DataFrame
                df = pd.DataFrame(data)

                # 重命名列，匹配东方财富的格式
                if not df.empty:
                    df = df.rename(columns={
                        'datetime': 'date',
                        'open': 'open',
                        'high': 'high',
                        'low': 'low',
                        'close': 'close',
                        'vol': 'volume',
                        'amount': 'money'  # 成交额
                    })
                    
                    # 转换日期格式
                    df['date'] = pd.to_datetime(df['date'])
                    
                    # 确保包含所有必需的列
                    if 'money' not in df.columns:
                        df['money'] = 0  # 如果没有成交额，设为0
                    
                    # 按时间排序
                    df = df.sort_values('date').reset_index(drop=True)
                    
                    print(f"✓ 成功获取 {len(df)} 条数据")
                    return df
                else:
                    print(f"✗ 服务器返回空数据")
                    continue
                    
        except Exception as e:
            print(f"✗ 服务器 {server_ip}:{server_port} 异常: {e}")
            continue
        finally:
            try:
                api.disconnect()
            except:
                pass
    
    # 所有服务器都失败
    print("❌ 所有服务器都连接失败")
    return pd.DataFrame()


def get_stock_1m_data_easy(stock_code, days=5):
    """
    简化版：自动判断市场并获取数据
    
    Parameters:
    stock_code: 股票代码，如 '000001' 或 '600000'
    days: 获取天数，默认5天
    
    Returns:
    DataFrame: 包含 date, open, close, high, low, volume, money 列
    """
    market = get_market(stock_code)
    return get_1m_data(stock_code, market, days)


# 使用示例
if __name__ == "__main__":
    print("=" * 80)
    print("pytdx 股票数据获取测试")
    print("=" * 80)
    print()
    
    # 测试深圳市场股票
    print("测试 1: 平安银行 (000001) - 深圳市场")
    print("-" * 80)
    df1 = get_stock_1m_data_easy('000001', days=3)
    if not df1.empty:
        print(f"✓ 成功获取 {len(df1)} 条数据")
        print(df1)
        print(f"数据列: {df1.columns.tolist()}")
    else:
        print("✗ 获取失败")
    
    print()
    print("=" * 80)
    
    # 测试上海市场股票
    print("测试 2: 贵州茅台 (600519) - 上海市场")
    print("-" * 80)
    df2 = get_stock_1m_data_easy('600519', days=3)
    if not df2.empty:
        print(f"✓ 成功获取 {len(df2)} 条数据")
        print(df2)
    else:
        print("✗ 获取失败")
    
    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)