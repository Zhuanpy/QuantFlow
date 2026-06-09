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
          1) push2delay trends2（HTTPS）—— push2his:80 常被整段 TCP RST，
             浏览器能直连 push2delay https，故作为首选源
          2) East Money push2his kline API（fqt=0，与日 K 同源）
          3) AKShare 兜底（stock_board_industry_hist_min_em / concept_min_em，按板块中文名拉）

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

        # ---------- 首选：push2delay trends2（HTTPS，ndays 1–5）----------
        # push2his:80 的 kline 当前被整段 TCP RST（含所有子域名前缀、Selenium、
        # AKShare），而 push2delay https 浏览器可直连，故优先走这条。
        ndays = min(max(int(days), 1), 5)
        try:
            url = my_url('board_1m_trends_delay').format(code, ndays)
            logger.info(f"尝试 push2delay trends2 源: {url}")

            source = EastMoneyHttpClient.get_source_with_rotation(
                url, 'board_1m_trends_delay', force_selenium=True)

            if source:
                dl = get_1m_data(source, match=False, multiple=True)
                if not dl.empty:
                    show_download('1m', code)
                    logger.info(f"push2delay 成功下载板块 {code} 的 {len(dl)} 条记录")
                    return dl
                else:
                    logger.warning(f"push2delay 板块 {code} 数据解析后为空")
            else:
                logger.warning(f"push2delay 返回空数据")
        except Exception as e:
            logger.warning(f"push2delay 获取失败: {e}")

        # ---------- 次选：East Money push2his kline ----------
        try:
            url = my_url('board_1m_multiple_days').format(code, lmt)
            logger.info(f"尝试访问URL: {url}")

            source = EastMoneyHttpClient.get_source_with_rotation(
                url, 'board_1m_multiple_days', force_selenium=True)

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

    # 板块日 K 直连用的 host / UA 轮换池（https push2his 已验证可直连，
    # http:80 常被整段 TCP RST，故强制 https）
    _DAILY_HOSTS = (
        'push2his.eastmoney.com',
        '1.push2his.eastmoney.com',
        '2.push2his.eastmoney.com',
    )
    _DAILY_UAS = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
        '(KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    )

    @staticmethod
    def _parse_daily_klines(source) -> pd.DataFrame:
        """把 East Money kline 接口返回（JSON 文本或 dict）解析成日 K DataFrame。

        返回列：date / open / close / high / low / volume / money；解析失败返回空表。
        """
        if not source:
            return pd.DataFrame()
        try:
            data = json.loads(source) if isinstance(source, str) else source
        except (json.JSONDecodeError, TypeError):
            return pd.DataFrame()
        d = (data or {}).get('data') or {}
        klines = d.get('klines') or []
        if not klines:
            return pd.DataFrame()

        rows = []
        for line in klines:
            p = line.split(',')
            # 日 K kline API 返回 11 列：date,open,close,high,low,volume,money,...
            if len(p) < 7:
                continue
            try:
                rows.append({
                    'date': p[0],
                    'open': float(p[1]),
                    'close': float(p[2]),
                    'high': float(p[3]),
                    'low': float(p[4]),
                    'volume': int(float(p[5])),
                    'money': int(float(p[6])),
                })
            except (ValueError, IndexError):
                continue
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df['date'] = pd.to_datetime(df['date']).dt.normalize()
        df = df.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
        return df

    @classmethod
    def _board_daily_via_requests(cls, code: str, lmt: int, retries: int = 3) -> pd.DataFrame:
        """普通 requests 直连东财日 K（https push2his，多 host/UA 轮换 + 重试）。

        首选源：push2his 可达时 attempt0 即秒回；被 RST 时快速失败，由 15m 兜底接手。
        重试 backoff 控制在 ~1.5s 内，避免整批板块在限流窗口里空等。
        """
        import time
        import requests

        params = {
            'secid': f'90.{code}', 'klt': '101', 'fqt': '0',
            'lmt': str(lmt), 'end': '20500101',
            'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        }
        attempts = []
        for i in range(retries):
            host = cls._DAILY_HOSTS[i % len(cls._DAILY_HOSTS)]
            ua = cls._DAILY_UAS[i % len(cls._DAILY_UAS)]
            url = f'https://{host}/api/qt/stock/kline/get'
            try:
                resp = requests.get(url, params=params, timeout=15, headers={
                    'User-Agent': ua,
                    'Referer': 'https://quote.eastmoney.com/',
                    'Accept': '*/*',
                })
                if resp.status_code != 200:
                    attempts.append(f'{host} HTTP {resp.status_code}')
                    time.sleep(0.5 * (i + 1))
                    continue
                df = cls._parse_daily_klines(resp.text)
                if not df.empty:
                    return df
                attempts.append(f'{host} 空klines')
            except requests.exceptions.RequestException as e:
                attempts.append(f'{host} {type(e).__name__}')
            if i < retries - 1:
                time.sleep(0.5 * (i + 1))
        logger.warning(f'[board_daily] {code} requests 直连未取到: {" | ".join(attempts)}')
        return pd.DataFrame()

    @classmethod
    def _board_daily_from_15m(cls, code: str) -> pd.DataFrame:
        """用本地已下载的 15m 数据聚合出日 K（完全绕开 push2his）。

        日 K = 当日首根 15m 的 open、末根 close、最高 high、最低 low、成交量求和。
        单位换算（已实测校准）：
          - 15m volume 单位为"股"，日 K（与 push2his 一致）单位为"手"，故 volume/100
          - money 单位一致，直接求和
        仅覆盖 15m 文件的跨度（通常最近若干日），用于补日 K 的尾部缺口；
        历史更早的部分已在 data_stock_daily 里，靠 upsert 不受影响。
        """
        try:
            from App.routes.data.viewer_15m_route import (
                _get_15m_dir, _find_15m_file, _read_15m_file)
        except Exception as e:
            logger.warning(f'[board_daily] 15m 兜底不可用（导入失败）: {e}')
            return pd.DataFrame()
        try:
            fpath = _find_15m_file(_get_15m_dir(), code)
            if not fpath:
                logger.info(f'[board_daily] {code} 无 15m 文件，跳过 15m 兜底')
                return pd.DataFrame()
            df = _read_15m_file(fpath)
            if df is None or df.empty or 'date' not in df.columns:
                return pd.DataFrame()

            df = df.copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.dropna(subset=['open', 'close', 'high', 'low'])
            df = df.sort_values('date')
            df['d'] = df['date'].dt.normalize()
            has_money = 'money' in df.columns
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
            if has_money:
                df['money'] = pd.to_numeric(df['money'], errors='coerce').fillna(0)

            rows = []
            for d, g in df.groupby('d'):
                rows.append({
                    'date': d,
                    'open': float(g['open'].iloc[0]),
                    'close': float(g['close'].iloc[-1]),
                    'high': float(g['high'].max()),
                    'low': float(g['low'].min()),
                    'volume': int(round(g['volume'].sum() / 100.0)),   # 股 → 手
                    'money': int(round(g['money'].sum())) if has_money else 0,
                })
            out = pd.DataFrame(rows)
            if out.empty:
                return out
            out['date'] = pd.to_datetime(out['date']).dt.normalize()
            out = out.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
            return out
        except Exception as e:
            logger.warning(f'[board_daily] {code} 15m 聚合兜底失败: {e}')
            return pd.DataFrame()

    @classmethod
    def board_daily(cls, code: str, days: int = 365) -> pd.DataFrame:
        """下载板块日 K（klt=101，fqt=0 不复权）。

        数据源优先级：
          1) requests 直连 https push2his（首选，权威全历史）
          2) 本地 15m 聚合（绕开 push2his，补最近若干日缺口，最稳）
          3) Selenium 轮换兜底（原路径，应对偶发拦截）

        必须从 East Money 走，因为：
          - pytdx get_index_bars 返回的板块指数被某种"成份股复权"算法处理过，
            数值约为真实指数的 56%（与东财网页/手机端不一致）
          - East Money 后端只有 fqt=0 才返回与官网一致的真实指数点位

        Returns:
            pd.DataFrame 列：date / open / close / high / low / volume / money
        """
        lmt = max(min(int(days), 3000), 30)

        # ---------- 首选：requests 直连 ----------
        df = cls._board_daily_via_requests(code, lmt)
        if not df.empty:
            logger.info(f'[board_daily] {code} requests 成功 {len(df)} 条 (klt=101 fqt=0)')
            return df

        # ---------- 次选：本地 15m 聚合（绕开 push2his，最稳）----------
        df = cls._board_daily_from_15m(code)
        if not df.empty:
            logger.info(f'[board_daily] {code} 15m 聚合兜底成功 {len(df)} 条（覆盖最近 {len(df)} 日）')
            return df

        # ---------- 兜底：Selenium 轮换 ----------
        logger.warning(f'[board_daily] {code} requests 未取到，回退 Selenium')
        try:
            url = my_url('board_daily_kline').format(code, lmt)
            source = EastMoneyHttpClient.get_source_with_rotation(
                url, 'board_daily_kline', force_selenium=True)
            df = cls._parse_daily_klines(source)
            if not df.empty:
                logger.info(f'[board_daily] {code} Selenium 兜底成功 {len(df)} 条')
            else:
                logger.warning(f'[board_daily] {code} Selenium 兜底也无数据')
            return df
        except Exception as e:
            logger.exception(f'[board_daily] {code} Selenium 兜底异常: {e}')
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
