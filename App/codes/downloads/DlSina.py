# -*- coding: utf-8 -*-
from selenium import webdriver
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 5000)


class DownloadData:  # 新浪财经下载数据

    @classmethod
    def Pe(cls, mk, stock_code):
        web = f'https://finance.sina.com.cn/realstock/company/{mk}{stock_code}/nc.shtml'
        driver = webdriver.Chrome()
        driver.get(web)
        pe = driver.find_element_by_xpath('/html/body/div[7]/div[3]/div[1]/div[1]/div[2]/div[2]/table/tbody/tr[4]/td[3]').text
        driver.close()
        return pe


if __name__ == '__main__':
    mk = 1
    stock_code = '002475'
    st = DownloadData()
