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
import threading
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

logger = logging.getLogger(__name__)

# Selenium 全局锁：同一时间只允许一个线程使用 Selenium，避免同时启动多个 Chrome
_selenium_lock = threading.Lock()
# API 连通性缓存：HTTP 连接失败后短时间内跳过 Selenium 降级
_api_fail_time = None
_API_FAIL_COOLDOWN = 60  # 秒：API 失败后 60 秒内不再尝试 Selenium

# 东财子域名前缀轮换：push2his.eastmoney.com / push2.eastmoney.com 支持 1..99 数字前缀
# 实测每个前缀走不同后端 IP 池，可绕过主域名层面的速率限制
_PREFIX_TARGET_HOSTS = {'push2his.eastmoney.com', 'push2.eastmoney.com'}
_PREFIX_RANGE = list(range(1, 100))  # 1..99
_PREFIX_FAIL_COOLDOWN = 60  # 秒
_prefix_fail_until = {}  # {prefix:int -> unblock_ts:float}
_prefix_lock = threading.Lock()


def _pick_prefix() -> Optional[int]:
    """选一个未在失败冷却的随机前缀，全员失败时返回 None（落到原主域名）"""
    now = time.time()
    with _prefix_lock:
        expired = [p for p, ts in _prefix_fail_until.items() if ts <= now]
        for p in expired:
            del _prefix_fail_until[p]
        available = [p for p in _PREFIX_RANGE if p not in _prefix_fail_until]
        if not available:
            return None
        return random.choice(available)


def _mark_prefix_failed(prefix: Optional[int]) -> None:
    if prefix is None:
        return
    with _prefix_lock:
        _prefix_fail_until[prefix] = time.time() + _PREFIX_FAIL_COOLDOWN


def _rewrite_host_with_prefix(url: str) -> Tuple[str, Optional[int]]:
    """若 URL 主机是 push2his/push2.eastmoney.com，随机加 1..99 前缀
    返回 (新 URL, 使用的前缀整数 或 None 表示未改写)"""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if host not in _PREFIX_TARGET_HOSTS:
            return url, None
        prefix = _pick_prefix()
        if prefix is None:
            return url, None
        new_host = f"{prefix}.{host}"
        new_netloc = f"{new_host}:{parsed.port}" if parsed.port else new_host
        return urlunparse(parsed._replace(netloc=new_netloc)), prefix
    except Exception as e:
        logger.warning(f"URL 主机改写失败: {e}")
        return url, None


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
        # 子域名前缀轮换：仅对 push2his / push2.eastmoney.com 生效
        request_url, prefix_used = _rewrite_host_with_prefix(url)
        if prefix_used is not None:
            logger.info(f"使用子域名前缀 {prefix_used}.（绕过主域名限流）")

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

            delay = random.uniform(1, 2)
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

            logger.info(f"正在请求: {request_url[:80]}...")

            response = session.get(
                request_url,
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
            _mark_prefix_failed(prefix_used)
            logger.error(f"连接错误 {request_url[:80]}...: {str(e)}")
            return ""
        except requests.exceptions.Timeout as e:
            _mark_prefix_failed(prefix_used)
            logger.error(f"请求超时 {request_url[:80]}...: {str(e)}")
            return ""
        except requests.exceptions.RequestException as e:
            _mark_prefix_failed(prefix_used)
            logger.error(f"请求异常 {request_url[:80]}...: {str(e)}")
            return ""
        except Exception as e:
            _mark_prefix_failed(prefix_used)
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

            # 先打开东财同域页面（拿 cookie / 过反爬），再用页面内 fetch 取 API 原始文本。
            # 直接 driver.get(api_url) 会被 Chrome 的 JSON 查看器包成 HTML，难以解析；
            # 同源 fetch 拿到的就是浏览器里看到的那段原始 JSON（用户已验证浏览器可访问）。
            try:
                parsed = urlparse(url)
                warmup = f"{parsed.scheme}://{parsed.netloc}/"
            except Exception:
                warmup = "https://quote.eastmoney.com/"

            logger.info(f"Selenium 预热访问同域: {warmup}")
            try:
                driver.get(warmup)
                time.sleep(random.uniform(1.5, 3))
            except Exception as e:
                logger.warning(f"预热页面加载失败（继续尝试 fetch）: {e}")

            logger.info(f"Selenium 页面内 fetch API: {url}")
            fetch_script = """
                const url = arguments[0];
                const done = arguments[arguments.length - 1];
                fetch(url, {credentials: 'include'})
                    .then(r => r.text())
                    .then(t => done({ok: true, text: t}))
                    .catch(e => done({ok: false, text: String(e)}));
            """
            content = ""
            try:
                result = driver.execute_async_script(fetch_script, url)
                if isinstance(result, dict) and result.get('ok'):
                    content = result.get('text') or ""
                    logger.info(f"Selenium fetch 成功，内容长度: {len(content)}")
                else:
                    err = result.get('text') if isinstance(result, dict) else result
                    logger.warning(f"Selenium fetch 失败: {err}")
            except Exception as e:
                logger.warning(f"Selenium execute_async_script 异常: {e}")

            # fetch 不行时，退回直接访问 URL，再从 <pre>/body 抠出 JSON 文本
            if not content or len(content) < 50:
                logger.info("fetch 无结果，回退到直接访问 URL 解析页面")
                try:
                    driver.get(url)
                    time.sleep(random.uniform(2, 4))
                    content = driver.execute_script(
                        "const p=document.querySelector('pre');"
                        "return p?p.innerText:(document.body?document.body.innerText:'');"
                    ) or ""
                except Exception as e:
                    logger.warning(f"回退解析页面失败: {e}")
                    content = ""

            content = (content or "").strip()

            if not content or len(content) < 50:
                logger.error("Selenium 获取的内容为空或过短")
                return ""

            if content.startswith('<'):
                logger.error("Selenium 返回的是 HTML 页面，而不是 JSON 数据")
                return ""

            if '{' not in content:
                logger.error("Selenium 内容不含 JSON，格式不正确")
                return ""

            logger.info(f"Selenium 成功获取数据，长度: {len(content)}")
            return content

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
                                  use_selenium_fallback: bool = None,
                                  force_selenium: bool = False) -> str:
        """
        使用请求头轮换获取页面源代码，失败时降级到 Selenium

        Args:
            url: 请求的URL
            header_type: 请求头类型
            use_selenium_fallback: 是否启用 Selenium 降级
            force_selenium: True 时无视"API 连续失败冷却"，HTTP 失败必尝试一次
                Selenium（板块用：数量少、浏览器可直连，值得花这点开销）

        Returns:
            str: 页面源代码
        """
        global _api_fail_time

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
                _api_fail_time = None  # 清除失败标记
                return content
            else:
                logger.warning(f"请求头 #{rotator.current_index + 1} 返回空内容")
        except Exception as e:
            logger.error(f"请求头 #{rotator.current_index + 1} 请求失败: {e}")
            rotator.mark_current_failed()

        logger.warning("HTTP 请求失败")
        rotator.rotate()

        # 记录 API 失败时间
        if _api_fail_time is None:
            _api_fail_time = time.time()

        # 判断是否应该尝试 Selenium 降级。
        # force_selenium=True 时即便配置里 use_selenium_fallback=False 也强制走一次
        # （板块专用：用户已验证浏览器能直连该 API）。
        if use_selenium_fallback or force_selenium:
            # 如果 API 在冷却期内连续失败，跳过 Selenium（避免反复启动 Chrome）。
            # force_selenium=True 时不跳过——板块数量少且浏览器能直连，值得一试。
            if (not force_selenium and _api_fail_time
                    and (time.time() - _api_fail_time) < _API_FAIL_COOLDOWN):
                elapsed = int(time.time() - _api_fail_time)
                if elapsed > 5:  # 首次失败仍尝试一次 Selenium
                    logger.warning(f"API 连续失败中（{elapsed}秒），跳过 Selenium 降级以避免资源浪费")
                    return ""

            # 使用锁确保同一时间只有一个 Selenium 实例
            acquired = _selenium_lock.acquire(timeout=30)
            if not acquired:
                logger.warning("其他线程正在使用 Selenium，跳过降级")
                return ""

            try:
                logger.warning("降级使用 Selenium 获取数据")
                content = cls.get_source_with_selenium(url)

                if content and len(content) > 100:
                    logger.info("Selenium 成功获取数据")
                    _api_fail_time = None  # Selenium 成功，清除失败标记
                    return content
                else:
                    logger.error("Selenium 获取失败或数据为空")
            except Exception as e:
                logger.error(f"Selenium 执行异常: {e}")
            finally:
                _selenium_lock.release()
        else:
            logger.warning("Selenium 降级已禁用")

        logger.error("所有数据源都失败")
        return ""
