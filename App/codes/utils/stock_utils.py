# -*- coding: utf-8 -*-
"""
股票工具模块

提供股票代码处理、市场判断等功能
"""

from typing import Optional, Literal


class StockCode:
    """股票代码处理工具类"""

    # 市场常量
    MARKET_SH = 'SH'  # 上海
    MARKET_SZ = 'SZ'  # 深圳

    # 分类常量
    CLASS_MAIN = '主板'
    CLASS_SME = '中小板'
    CLASS_GEM = '创业板'
    CLASS_STAR = '科创板'
    CLASS_BSHARE = 'B股'
    CLASS_INDEX = '指数'
    CLASS_BOND = '转债'
    CLASS_FUND = '基金'

    @classmethod
    def stand_code(cls, code: str) -> str:
        """
        标准化股票代码为6位数字

        Args:
            code: 原始股票代码

        Returns:
            str: 6位标准化代码
        """
        code = str(code)
        len_ = len(code)
        if len_ < 6:
            _code = '000000'
            code = f'{_code[:(6 - len_)]}{code}'
        else:
            code = code[:6]
        return code

    @classmethod
    def code2market(cls, code: str) -> str:
        """
        根据股票代码判断市场

        Args:
            code: 股票代码

        Returns:
            str: 市场代码 ('SH', 'SZ', 'None')
        """
        code = str(code)
        if code[0] == '6':
            return cls.MARKET_SH
        elif code[0] in ('0', '3'):
            return cls.MARKET_SZ
        else:
            print(f'股票: {code}未区分市场类；')
            return 'None'

    @classmethod
    def code_with_market(cls, code: str) -> str:
        """
        为股票代码添加市场后缀

        Args:
            code: 股票代码

        Returns:
            str: 带市场后缀的代码 (如 '600000.SH')
        """
        code = str(code)
        if code[0] == '6':
            return f'{code}.SH'
        elif code[0] in ('0', '3'):
            return f'{code}.SZ'
        else:
            print(f'股票: {code}无市场分类;')
            return code

    @classmethod
    def code2classification(cls, code: str) -> Optional[str]:
        """
        根据股票代码判断分类

        Args:
            code: 股票代码

        Returns:
            str: 分类名称，无法识别返回 None
        """
        code = str(code)
        classification = None

        prefix3 = code[:3]
        prefix2 = code[:2]

        # 主板
        if prefix3 in ('600', '601', '602', '603', '605', '000'):
            classification = cls.CLASS_MAIN
        # 中小板
        elif prefix3 == '002':
            classification = cls.CLASS_SME
        # 深股通
        elif prefix3 == '003':
            classification = '深股通'
        # 科创板
        elif prefix3 in ('688', '689'):
            classification = cls.CLASS_STAR
        # 创业板
        elif prefix3 == '300':
            classification = cls.CLASS_GEM
        # B股
        elif prefix3 in ('900', '200'):
            classification = cls.CLASS_BSHARE
        # 指数
        elif prefix3 == '880':
            classification = cls.CLASS_INDEX
        # 转债
        elif prefix2 in ('12', '13', '11'):
            classification = cls.CLASS_BOND
        # 债券
        elif prefix2 == '20':
            classification = '债券'
        # 基金
        elif prefix2 in ('15', '16', '50', '51', '56', '58'):
            classification = cls.CLASS_FUND

        return classification

    @classmethod
    def is_a_share(cls, code: str) -> bool:
        """
        判断是否为A股

        Args:
            code: 股票代码

        Returns:
            bool: 是否为A股
        """
        classification = cls.code2classification(code)
        return classification in (cls.CLASS_MAIN, cls.CLASS_SME, cls.CLASS_GEM, cls.CLASS_STAR)

    @classmethod
    def is_index(cls, code: str) -> bool:
        """
        判断是否为指数

        Args:
            code: 股票代码

        Returns:
            bool: 是否为指数
        """
        return cls.code2classification(code) == cls.CLASS_INDEX

    @classmethod
    def get_secid(cls, code: str) -> str:
        """
        获取东方财富 secid 格式

        Args:
            code: 股票代码

        Returns:
            str: secid (如 '1.600000' 或 '0.000001')
        """
        code = cls.stand_code(code)
        market = cls.code2market(code)
        if market == cls.MARKET_SH:
            return f'1.{code}'
        else:
            return f'0.{code}'


# 便捷函数
standardize_code = StockCode.stand_code
get_market = StockCode.code2market
add_market_suffix = StockCode.code_with_market
get_classification = StockCode.code2classification
is_a_share = StockCode.is_a_share
