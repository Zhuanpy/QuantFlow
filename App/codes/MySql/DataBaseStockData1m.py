# -*- coding: utf-8 -*-
"""
股票1分钟数据访问层（本地 parquet 唯一来源）

历史背景：原版"先查 MySQL，失败再读本地"，但 per-year bind（data1m2024 等）
从未配置进 SQLALCHEMY_BINDS，每次访问都会先抛一遍 bind 错再 fallback。
现在统一只走本地：`<root>/data/quarters/<year>/<quarter>/<code>.parquet|csv`。
"""
import pandas as pd
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class StockData1m:
    """
    股票1分钟数据访问类
    提供从数据库或CSV文件加载数据的功能
    """

    # 股票代码格式：6位数字（如 000001, 600000）
    STOCK_CODE_PATTERN = re.compile(r'^\d{6}$')

    @classmethod
    def _validate_stock_code(cls, stock_code: str) -> bool:
        """验证股票代码格式"""
        return bool(cls.STOCK_CODE_PATTERN.match(stock_code))

    @staticmethod
    def load_1m(stock_code: str, year: str) -> pd.DataFrame:
        """
        加载股票1分钟数据
        
        Args:
            stock_code: 股票代码
            year: 年份（字符串，如 "2024"）
            
        Returns:
            pd.DataFrame: 包含1分钟数据的DataFrame，包含列：date, open, close, high, low, volume, money
        """
        # 验证股票代码格式
        if not StockData1m._validate_stock_code(stock_code):
            logger.error(f"无效的股票代码格式: {stock_code}")
            return pd.DataFrame()

        try:
            int(year)  # 仅校验
        except ValueError:
            logger.error(f"无效的年份格式: {year}")
            return pd.DataFrame()

        # 直接从本地 parquet/csv 加载（<root>/data/quarters/<year>/<quarter>/<code>）
        try:
            from config import Config
            quarters_dir = Path(Config.get_project_root()) / 'data' / 'quarters' / year
            if not quarters_dir.exists():
                logger.warning(f"季度目录不存在: {quarters_dir}")
                return pd.DataFrame()

            all_data = []
            for quarter_dir in sorted(quarters_dir.iterdir()):
                if not quarter_dir.is_dir():
                    continue
                parquet_file = quarter_dir / f'{stock_code}.parquet'
                csv_file = quarter_dir / f'{stock_code}.csv'
                if parquet_file.exists():
                    df = pd.read_parquet(parquet_file)
                    df['date'] = pd.to_datetime(df['date'])
                    all_data.append(df)
                elif csv_file.exists():
                    df = pd.read_csv(csv_file, parse_dates=['date'])
                    all_data.append(df)

            if not all_data:
                logger.warning(f"未找到 {stock_code} {year}年的本地1分钟数据文件")
                return pd.DataFrame()

            data = (pd.concat(all_data, ignore_index=True)
                    .drop_duplicates(subset=['date'])
                    .sort_values('date').reset_index(drop=True))
            logger.info(f"成功加载股票 {stock_code} {year}年1分钟数据，共 {len(data)} 条记录")
            return data

        except Exception as e:
            logger.error(f"加载 {stock_code} {year}年 1m 数据失败: {e}")
            return pd.DataFrame()
    
    @staticmethod
    def load_1m_for_day(stock_code: str, day) -> pd.DataFrame:
        """加载某一天的 1m 数据，合并历史 quarters 与盘中 quarters_live。

        盘中：data/quarters_live/<YYYY-MM-DD>/<code>.parquet（前端定时拉的临时存储）。
        盘后：data/quarters/<year>/Q<n>/<code>.parquet（全量下载的历史归档）。

        live 优先（更新鲜）；同一分钟撞上则保留 live 那条。返回按 date 升序、
        无重复的 OHLCV DataFrame。空表说明这天没数据。
        """
        from config import Config

        if not StockData1m._validate_stock_code(stock_code):
            return pd.DataFrame()
        try:
            day_ts = pd.to_datetime(day).normalize()
        except Exception:
            logger.error(f"无效日期: {day}")
            return pd.DataFrame()

        root = Path(Config.get_project_root())
        quarter = (day_ts.month - 1) // 3 + 1
        frames = []

        # 历史归档（这一天可能已被盘后全量覆盖到这里）
        hist_candidates = [
            root / 'data' / 'quarters' / str(day_ts.year) / f'Q{quarter}' / f'{stock_code}.parquet',
            root / 'data' / 'quarters' / str(day_ts.year) / f'Q{quarter}' / f'{stock_code}.csv',
        ]
        for p in hist_candidates:
            if p.exists():
                try:
                    df = pd.read_parquet(p) if p.suffix == '.parquet' else pd.read_csv(p)
                    df['date'] = pd.to_datetime(df['date'])
                    df = df[df['date'].dt.date == day_ts.date()]
                    if not df.empty:
                        df['_src'] = 'hist'
                        frames.append(df)
                    break  # 同一只票同一季度只可能命中一处
                except Exception as e:
                    logger.warning(f'读历史 1m {p} 失败: {e}')

        # 盘中临时（前端定时拉过来的）
        live_path = root / 'data' / 'quarters_live' / day_ts.strftime('%Y-%m-%d') / f'{stock_code}.parquet'
        if live_path.exists():
            try:
                df = pd.read_parquet(live_path)
                df['date'] = pd.to_datetime(df['date'])
                df = df[df['date'].dt.date == day_ts.date()]
                if not df.empty:
                    df['_src'] = 'live'
                    frames.append(df)
            except Exception as e:
                logger.warning(f'读 live 1m {live_path} 失败: {e}')

        if not frames:
            return pd.DataFrame()

        merged = pd.concat(frames, ignore_index=True)
        # 同 date 时 live 优先：先按 _src 排让 live 排后，drop_duplicates(keep='last')
        merged['_src_order'] = (merged['_src'] == 'live').astype(int)
        merged = (merged
                  .sort_values(['date', '_src_order'])
                  .drop_duplicates(subset=['date'], keep='last')
                  .sort_values('date')
                  .drop(columns=['_src', '_src_order'])
                  .reset_index(drop=True))
        return merged

    @staticmethod
    def load_1m_by_date_range(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        按日期范围加载股票1分钟数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            pd.DataFrame: 包含1分钟数据的DataFrame
        """
        try:
            start_year = pd.to_datetime(start_date).year
            end_year = pd.to_datetime(end_date).year
            
            all_data = []
            for year in range(start_year, end_year + 1):
                year_data = StockData1m.load_1m(stock_code, str(year))
                if not year_data.empty:
                    all_data.append(year_data)
            
            if not all_data:
                return pd.DataFrame()
            
            # 合并所有年份的数据
            combined_data = pd.concat(all_data, ignore_index=True)
            
            # 过滤日期范围
            combined_data = combined_data[
                (combined_data['date'] >= pd.to_datetime(start_date)) &
                (combined_data['date'] <= pd.to_datetime(end_date))
            ]
            
            logger.info(f"成功加载股票 {stock_code} {start_date} 至 {end_date} 的1分钟数据，共 {len(combined_data)} 条记录")
            return combined_data.sort_values('date').reset_index(drop=True)
            
        except Exception as e:
            logger.error(f"按日期范围加载数据失败: {e}")
            return pd.DataFrame()


if __name__ == '__main__':
    # 测试代码
    data = StockData1m.load_1m('000001', '2024')
    print(f"加载数据: {len(data)} 条记录")
    print(data.head() if not data.empty else "数据为空")

