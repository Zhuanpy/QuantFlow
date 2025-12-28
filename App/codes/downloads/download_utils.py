# -*-coding:utf8-*-
import requests
import logging
import time
import urllib3

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options


def WebDriver():
    # 创建 ChromeDriver 服务
    service = Service(ChromeDriverManager().install())

    # 创建 Chrome 配置选项
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")  # 添加任何所需的配置选项

    # 使用服务和选项启动 WebDriver
    driver = webdriver.Chrome(service=service, options=chrome_options)

    return driver


def page_source(url, headers=None, cookies=None, max_retries=5, retry_delay=2):
    """
    获取页面源代码。

    参数:
        url: 请求的URL
        headers: 请求头
        cookies: Cookie信息
        max_retries: 最大重试次数
        retry_delay: 重试延迟时间（秒）

    返回:
        str: 页面源代码，如果失败则返回None
    """
    session = requests.Session()
    
    # 设置最大重定向次数
    session.max_redirects = 3
    
    # 东方财富专用请求头
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'http://quote.eastmoney.com/',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Sec-Fetch-Dest': 'script',
        'Sec-Fetch-Mode': 'no-cors',
        'Sec-Fetch-Site': 'same-site'
    }
    
    # 合并用户提供的headers和默认headers
    if headers:
        default_headers.update(headers)
    
    i = 0
    while i < max_retries:
        try:
            r = session.get(
                url,
                headers=default_headers,
                cookies=cookies,
                timeout=(5, 10),
                verify=False,
                allow_redirects=True
            )
            
            if r.status_code == 200:
                # 尝试检测编码
                if r.encoding == 'ISO-8859-1':
                    # 如果检测到ISO-8859-1，尝试使用utf-8
                    try:
                        return r.content.decode('utf-8')
                    except UnicodeDecodeError:
                        # 如果utf-8失败，尝试gbk
                        try:
                            return r.content.decode('gbk')
                        except UnicodeDecodeError:
                            # 最后使用默认编码
                            return r.text
                else:
                    return r.text
            else:
                logging.error(f"HTTP error {r.status_code} for URL: {url}")
                if r.text:
                    logging.error(f"Response content: {r.text[:200]}")
                
        except requests.exceptions.TooManyRedirects:
            logging.error(f"Too many redirects for URL: {url}")
            # 在遇到重定向问题时尝试使用新的会话
            session = requests.Session()
            session.max_redirects = 3
            
        except requests.exceptions.Timeout as e:
            logging.error(f"Timeout error: {e}")
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Request error: {e}")
            
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            
        i += 1
        if i < max_retries:
            logging.info(f"Retrying request ({i+1}/{max_retries})...")
            time.sleep(retry_delay)
            
    return None


def UrlCode(code: str):
    """
    将股票/板块代码转换为东方财富API格式
    
    参数:
        code (str): 原始代码
        
    返回:
        str: 转换后的代码
    """
    if code.startswith('BK'):  # 板块代码
        return f'90.{code}'
    elif code[0] == '0' or code[0] == '3':  # 深市股票
        return f'0.{code}'
    elif code[0] == '6':  # 沪市股票
        return f'1.{code}'
    else:
        print(f'东方财富代码无分类:{code};')
        return code  # 返回原代码，避免返回None


if __name__ == '__main__':
    pass