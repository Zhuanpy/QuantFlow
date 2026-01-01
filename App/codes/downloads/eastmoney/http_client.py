# -*- coding: utf-8 -*-
"""
HTTP 客户端模块

提供东方财富数据下载的 HTTP 请求封装，包括：
- 请求头轮换机制
- Selenium 浏览器模拟降级
- 重试和错误处理
"""

import logging
import time
import random
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

logger = logging.getLogger(__name__)


class HeaderRotator:
    """请求头轮换器 - 管理多个请求头的轮换"""

    def __init__(self, header_type: str = 'stock_1m_multiple_days'):
        from config import Config
        self.headers_pool = Config.get_eastmoney_headers_pool(header_type)
        self.current_index = 0
        self.failed_indices = set()
        logger.info(f"初始化请求头池，共 {len(self.headers_pool)} 个请求头")

    def get_next_header(self) -> dict:
        """获取下一个请求头"""
        if len(self.failed_indices) >= len(self.headers_pool):
            logger.warning("所有请求头都已失败，重置请求头池")
            self.failed_indices.clear()
            self.current_index = 0

        attempts = 0
        while self.current_index in self.failed_indices and attempts < len(self.headers_pool):
            self.current_index = (self.current_index + 1) % len(self.headers_pool)
            attempts += 1

        header = self.headers_pool[self.current_index].copy()
        user_agent = header.get('User-Agent', 'Unknown')
        logger.info(f"使用请求头 #{self.current_index + 1}/{len(self.headers_pool)}: {user_agent[:50]}...")
        return header

    def mark_current_failed(self):
        """标记当前请求头失败"""
        self.failed_indices.add(self.current_index)
        logger.warning(f"标记请求头 #{self.current_index + 1} 为失败状态")

    def rotate(self):
        """轮换到下一个请求头"""
        self.current_index = (self.current_index + 1) % len(self.headers_pool)
        logger.info(f"轮换到下一个请求头 #{self.current_index + 1}")

    def get_current_header(self) -> dict:
        """获取当前请求头"""
        return self.headers_pool[self.current_index].copy()

    def reset(self):
        """重置轮换器状态"""
        self.current_index = 0
        self.failed_indices.clear()
        logger.info("重置请求头轮换器")


class EastMoneyHttpClient:
    """东方财富 HTTP 客户端"""

    MAX_RETRIES = 3
    RETRY_DELAY = 2

    _header_rotators = {}

    @classmethod
    def get_header_rotator(cls, header_type: str = 'stock_1m_multiple_days') -> HeaderRotator:
        """获取或创建请求头轮换器"""
        if header_type not in cls._header_rotators:
            cls._header_rotators[header_type] = HeaderRotator(header_type)
        return cls._header_rotators[header_type]

    @classmethod
    def get_source(cls, url: str, headers: dict) -> str:
        """
        获取页面源代码

        Args:
            url: 请求的URL
            headers: 请求头信息

        Returns:
            str: 页面源代码
        """
        try:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            session = requests.Session()

            retry_strategy = Retry(
                total=4,
                backoff_factor=2,
                status_forcelist=[500, 502, 503, 504, 429, 408],
                allowed_methods=["GET", "HEAD"],
                raise_on_status=False,
                respect_retry_after_header=True
            )

            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=10,
                pool_maxsize=20,
                pool_block=False
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)

            delay = random.uniform(3, 5)
            logger.info(f"请求延迟 {delay:.2f} 秒（避免限流）")
            time.sleep(delay)

            headers_copy = headers.copy()
            headers_copy.update({
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Cache-Control': 'no-cache',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Pragma': 'no-cache',
            })

            logger.info(f"正在请求: {url[:80]}...")

            response = session.get(
                url,
                headers=headers_copy,
                timeout=(10, 20),
                verify=False,
                allow_redirects=True,
                stream=False
            )

            response.raise_for_status()

            logger.info(f"成功获取响应，状态码: {response.status_code}")

            content_encoding = response.headers.get('Content-Encoding', '')

            if content_encoding == 'br':
                try:
                    import brotli
                    content = brotli.decompress(response.content).decode('utf-8')
                    logger.info(f"Brotli解压缩成功，内容长度: {len(content)}")
                except Exception as e:
                    logger.warning(f"Brotli解压缩失败，使用response.text: {e}")
                    content = response.text
            else:
                content = response.text

            session.close()

            logger.info(f"最终内容长度: {len(content)}")
            return content

        except requests.exceptions.ConnectionError as e:
            logger.error(f"连接错误 {url[:80]}...: {str(e)}")
            return ""
        except requests.exceptions.Timeout as e:
            logger.error(f"请求超时 {url[:80]}...: {str(e)}")
            return ""
        except requests.exceptions.RequestException as e:
            logger.error(f"请求异常 {url[:80]}...: {str(e)}")
            return ""
        except Exception as e:
            logger.error(f"获取源数据时发生异常: {str(e)}")
            return ""

    @classmethod
    def get_source_with_selenium(cls, url: str) -> str:
        """
        使用 Selenium 获取页面源代码（降级方案）

        Args:
            url: 请求的URL

        Returns:
            str: 页面源代码
        """
        driver = None
        try:
            logger.info("开始初始化 Selenium...")

            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options

            from config import Config
            use_headless = Config.DOWNLOAD_CONFIG.get('selenium_headless', True)

            chrome_options = Options()
            if use_headless:
                chrome_options.add_argument('--headless')
                chrome_options.add_argument('--headless=new')
                logger.info("Selenium 使用无头模式")
            else:
                logger.info("Selenium 使用有头模式")

            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--ignore-certificate-errors')
            chrome_options.add_argument('--disable-extensions')

            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            ]
            chrome_options.add_argument(f'user-agent={random.choice(user_agents)}')

            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)

            try:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager

                logger.info("使用 webdriver-manager 自动下载 ChromeDriver...")
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=chrome_options)
                logger.info("Chrome 浏览器启动成功（自动版本管理）")

            except ImportError:
                logger.warning("webdriver-manager 未安装，使用系统 ChromeDriver")
                driver = webdriver.Chrome(options=chrome_options)
                logger.info("Chrome 浏览器启动成功（系统 ChromeDriver）")

            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)

            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                '''
            })

            logger.info(f"Selenium 访问 URL: {url}")
            driver.get(url)
            logger.info("页面加载完成")

            wait_time = random.uniform(3, 6)
            logger.info(f"等待 {wait_time:.1f} 秒加载页面...")
            time.sleep(wait_time)

            page_source = driver.page_source

            if not page_source or len(page_source) < 100:
                logger.error("Selenium 获取的页面源码为空或过短")
                return ""

            if page_source.strip().startswith('<'):
                logger.error("Selenium 返回的是 HTML 页面，而不是 JSON 数据")
                return ""

            if 'jQuery' in page_source or '{' in page_source:
                logger.info(f"Selenium 成功获取页面源码，长度: {len(page_source)}")
                return page_source
            else:
                logger.error("页面内容格式不正确")
                return ""

        except ImportError as ie:
            logger.error(f"Selenium 模块导入失败: {str(ie)}")
            return ""
        except Exception as e:
            logger.error(f"Selenium 获取数据失败: {str(e)}")
            return ""
        finally:
            if driver:
                try:
                    driver.quit()
                    logger.info("Selenium 浏览器已关闭")
                except:
                    pass

    @classmethod
    def get_source_with_rotation(cls, url: str, header_type: str = 'stock_1m_multiple_days',
                                  use_selenium_fallback: bool = None) -> str:
        """
        使用请求头轮换获取页面源代码，失败时降级到 Selenium

        Args:
            url: 请求的URL
            header_type: 请求头类型
            use_selenium_fallback: 是否启用 Selenium 降级

        Returns:
            str: 页面源代码
        """
        if use_selenium_fallback is None:
            from config import Config
            use_selenium_fallback = Config.DOWNLOAD_CONFIG.get('use_selenium_fallback', True)

        rotator = cls.get_header_rotator(header_type)

        logger.info(f"尝试使用 HTTP 请求（请求头类型: {header_type}）")

        headers = rotator.get_next_header()

        try:
            content = cls.get_source(url, headers)
            if content and len(content) > 100:
                logger.info(f"使用请求头 #{rotator.current_index + 1} 成功获取数据")
                rotator.rotate()
                return content
            else:
                logger.warning(f"请求头 #{rotator.current_index + 1} 返回空内容")
        except Exception as e:
            logger.error(f"请求头 #{rotator.current_index + 1} 请求失败: {e}")
            rotator.mark_current_failed()

        logger.warning("HTTP 请求失败")
        rotator.rotate()

        if use_selenium_fallback:
            logger.warning("降级使用 Selenium 获取数据")

            try:
                content = cls.get_source_with_selenium(url)

                if content and len(content) > 100:
                    logger.info("Selenium 成功获取数据")
                    return content
                else:
                    logger.error("Selenium 获取失败或数据为空")
            except Exception as e:
                logger.error(f"Selenium 执行异常: {e}")
        else:
            logger.warning("Selenium 降级已禁用")

        logger.error("所有数据源都失败")
        return ""
