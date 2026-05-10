# -*- coding: utf-8 -*-
"""
板块数据下载模块

提供从东方财富下载板块数据的功能
"""

import json
import re
import logging
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup as soup

from App.codes.downloads.eastmoney.http_client import EastMoneyHttpClient
from App.codes.downloads.eastmoney.data_parser import get_1m_data, show_download
from download_utils import page_source
from App.codes.RnnDataFile.parser import my_headers, my_url

logger = logging.getLogger(__name__)


class BoardDownloader:
    """板块数据下载器"""

    @classmethod
    def board_1m_data(cls, code: str) -> pd.DataFrame:
        """
        下载板块单日1分钟数据

        Args:
            code: 板块代码

        Returns:
            pd.DataFrame: 板块数据
        """
        headers = my_headers('board_1m_data')
        url = my_url('board_1m_data').format(code)
        source = page_source(url=url, headers=headers)
        dl = get_1m_data(source, match=True, multiple=False)

        show_download('1m', code)
        return dl

    @classmethod
    def board_1m_multiple(cls, code: str, days: int = 5) -> pd.DataFrame:
        """
        下载板块多天 1 分钟数据。

        优先级：
          1) East Money kline API（fqt=0，与日 K 同源）
          2) AKShare 兜底（stock_board_industry_hist_min_em / concept_min_em，按板块中文名拉）

        不能用 pytdx：pytdx get_security_bars 返回的板块指数被"成份股复权"过，
        数值约为真实指数的 56%–64%（与东财官网/日 K 不一致）。

        Args:
            code: 板块代码（如 BK0437）
            days: 需要下载的天数

        Returns:
            pd.DataFrame: 下载的板块数据
        """
        logger.info("*" * 80)
        logger.info(f"开始下载板块 {code} 的 {days} 天 1m 数据（East Money fqt=0）")
        logger.info("*" * 80)

        lmt = min(days * 240, 2000)  # 最多2000条记录
        logger.info(f"计算记录数量: {days}天 x 240 = {lmt} 条")

        # ---------- 主路径：East Money ----------
        try:
            url = my_url('board_1m_multiple_days').format(code, lmt)
            logger.info(f"尝试访问URL: {url}")

            source = EastMoneyHttpClient.get_source_with_rotation(url, 'board_1m_multiple_days')

            if source:
                dl = get_1m_data(source, match=False, multiple=True)
                if not dl.empty:
                    show_download('1m', code)
                    logger.info(f"East Money 成功下载板块 {code} 的 {len(dl)} 条记录")
                    return dl
                else:
                    logger.warning(f"East Money 板块 {code} 数据解析后为空")
            else:
                logger.warning(f"East Money 返回空数据")
        except Exception as e:
            logger.warning(f"East Money 获取失败: {e}")

        # ---------- 兜底：AKShare ----------
        logger.warning(f"East Money 失败，尝试 AKShare 兜底拉 {code} 的 1m")
        try:
            dl_ak = cls._ak_board_1m(code, days=days)
            if not dl_ak.empty:
                show_download('1m', code)
                logger.info(f"AKShare 成功下载板块 {code} 的 {len(dl_ak)} 条记录")
                return dl_ak
        except Exception as e:
            logger.warning(f"AKShare 兜底也失败: {e}")

        logger.error(f"East Money + AKShare 全部失败，{code} 无可用 1m 数据")
        return pd.DataFrame()

    @classmethod
    def _lookup_board_name(cls, code: str) -> Optional[str]:
        """从 data_stock_info 查板块代码对应的中文名。"""
        try:
            from App.exts import db
            from sqlalchemy import text
            eng = db.engines['quanttradingsystem']
            with eng.connect() as conn:
                row = conn.execute(
                    text("SELECT name FROM data_stock_info WHERE code = :code LIMIT 1"),
                    {'code': code}
                ).fetchone()
            return row[0] if row and row[0] else None
        except Exception as e:
            logger.warning(f"查板块名失败 {code}: {e}")
            return None

    @classmethod
    def _ak_board_1m(cls, code: str, days: int = 5) -> pd.DataFrame:
        """AKShare 兜底：通过板块中文名拉 1m，返回与 East Money 同结构的 DataFrame。

        AKShare 的 stock_board_industry_hist_min_em / stock_board_concept_hist_min_em
        都接收"板块中文名"而非 BK 代码，所以要先查表。
        返回列对齐到 ['date','open','close','high','low','volume','money']。
        """
        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare 未安装，跳过兜底")
            return pd.DataFrame()

        name = cls._lookup_board_name(code)
        if not name:
            logger.warning(f"找不到 {code} 的中文名，无法用 AKShare 兜底")
            return pd.DataFrame()

        logger.info(f"AKShare 用板块名 '{name}'（{code}）拉 1m")

        # 行业板块优先；不在行业列表则回退到概念板块
        df = pd.DataFrame()
        for fn_name in ('stock_board_industry_hist_min_em', 'stock_board_concept_hist_min_em'):
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = fn(symbol=name, period='1')
                if df is not None and not df.empty:
                    logger.info(f"AKShare {fn_name}('{name}') 返回 {len(df)} 行")
                    break
                df = pd.DataFrame()
            except Exception as e:
                logger.warning(f"AKShare {fn_name}('{name}') 失败: {e}")
                df = pd.DataFrame()

        if df.empty:
            return df

        # 列名映射（AKShare 是中文列）
        rename_map = {
            '日期时间': 'date', '日期': 'date', '时间': 'date',
            '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low',
            '成交量': 'volume', '成交额': 'money',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        keep = [c for c in ('date', 'open', 'close', 'high', 'low', 'volume', 'money') if c in df.columns]
        if 'date' not in keep:
            logger.warning(f"AKShare 返回数据缺 date 列，原列: {list(df.columns)}")
            return pd.DataFrame()
        df = df[keep].copy()

        df['date'] = pd.to_datetime(df['date'])
        for c in ('open', 'close', 'high', 'low', 'volume', 'money'):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')

        # 截到最近 days 天（AKShare 默认返回较长历史）
        if not df.empty:
            cutoff = df['date'].max().normalize() - pd.Timedelta(days=days + 2)
            df = df[df['date'] >= cutoff].sort_values('date').reset_index(drop=True)

        return df

    @classmethod
    def board_daily(cls, code: str, days: int = 365) -> pd.DataFrame:
        """下载板块日 K（klt=101，fqt=0 不复权）。

        必须从 East Money 走，因为：
          - pytdx get_index_bars 返回的板块指数被某种"成份股复权"算法处理过，
            数值约为真实指数的 56%（与东财网页/手机端不一致）
          - East Money 后端只有 fqt=0 才返回与官网一致的真实指数点位

        Returns:
            pd.DataFrame 列：date / open / close / high / low / volume / money
        """
        lmt = max(min(int(days), 3000), 30)
        try:
            url = my_url('board_daily_kline').format(code, lmt)
            logger.info(f'[board_daily] {code} URL: {url}')
            source = EastMoneyHttpClient.get_source_with_rotation(url, 'board_daily_kline')
            if not source:
                logger.warning(f'[board_daily] {code} 无返回')
                return pd.DataFrame()

            data = json.loads(source) if isinstance(source, str) else source
            d = (data or {}).get('data') or {}
            klines = d.get('klines') or []
            if not klines:
                logger.warning(f'[board_daily] {code} klines 为空 (rc={data.get("rc")})')
                return pd.DataFrame()

            rows = []
            for line in klines:
                p = line.split(',')
                # 日 K kline API 返回 11 列：date,open,close,high,low,volume,money,...
                if len(p) < 7:
                    continue
                rows.append({
                    'date': p[0],
                    'open': float(p[1]),
                    'close': float(p[2]),
                    'high': float(p[3]),
                    'low': float(p[4]),
                    'volume': int(float(p[5])),
                    'money': int(float(p[6])),
                })
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            df['date'] = pd.to_datetime(df['date']).dt.normalize()
            df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
            logger.info(f'[board_daily] {code} 成功 {len(df)} 条 (klt=101 fqt=0)')
            return df
        except Exception as e:
            logger.exception(f'[board_daily] {code} 异常: {e}')
            return pd.DataFrame()

    @classmethod
    def industry_list(cls) -> pd.DataFrame:
        """
        下载板块列表

        Returns:
            pd.DataFrame: 板块列表
        """
        from selenium import webdriver

        web = 'http://quote.eastmoney.com/center/boardlist.html#industry_board'
        driver = webdriver.Chrome()
        driver.get(web)

        source = driver.page_source
        bs_data = soup(source, 'html.parser')
        board_data = bs_data.find('li', class_='sub-items menu-industry_board-wrapper')
        board_data = board_data.find_all('li')
        data = pd.DataFrame(data=None)

        for i in range(len(board_data)):
            board_name = board_data[i].find(class_='text').text
            board_code = str(board_data[i].find('a')['href']).strip()[-6:]
            data.loc[i, 'board_name'] = board_name
            data.loc[i, 'board_code'] = board_code

        driver.close()
        data['stock_name'] = None
        data['stock_code'] = None
        return data

    @classmethod
    def industry_ind_stock(cls, name: str, code: str, num: int = 300) -> Optional[pd.DataFrame]:
        """
        下载板块成份股

        Args:
            name: 板块名称
            code: 板块代码
            num: 最大返回数量

        Returns:
            Optional[pd.DataFrame]: 板块成份股数据
        """
        url = my_url('industry_ind_stock').format(num, code)
        headers = my_headers('industry_ind_stock')
        source = page_source(url=url, headers=headers)

        dl = None
        if source:
            p1 = re.compile(r'[(](.*?)[)]', re.S)
            page_data = re.findall(p1, source)
            json_data = json.loads(page_data[0])['code_data']['diff']

            values_list = []
            for i in range(len(json_data)):
                values = list(json_data[i].values())
                values_list.append(values)

            key_list = list(json_data[0])
            dl = pd.DataFrame(data=values_list, columns=key_list)

            rename_ = {
                'f3': '涨跌幅', 'f4': '涨跌额', 'f5': '成交量', 'f6': '成交额',
                'f7': '振幅', 'f8': '换手率', 'f9': '市盈率动', 'f10': '量比',
                'f12': 'stock_code', 'f14': 'stock_name', 'f15': 'close',
                'f16': 'low', 'f17': 'open', 'f18': 'preclose', 'f20': '总市值',
                'f21': '流通市值', 'f23': '市净率', 'f115': '市盈率'
            }

            dl = dl.rename(columns=rename_)

            dl = dl.drop(columns=['f1', 'f2', 'f11', 'f13', 'f22', 'f24', 'f25', 'f45',
                                  'f62', 'f128', 'f140', 'f141', 'f136', 'f152'])

            dl['board_name'] = name
            dl['board_code'] = code
            dl['date'] = pd.Timestamp('today').date()

            dl = dl[['board_name', 'board_code', 'stock_code', 'stock_name', 'date']]

        return dl
