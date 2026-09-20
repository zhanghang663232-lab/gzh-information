import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("archive_account.py")
SPEC = importlib.util.spec_from_file_location("archive_account", SCRIPT)
archive_account = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive_account)


class ArchiveAccountTests(unittest.TestCase):
    def test_normalizes_and_drops_sensitive_values(self):
        url = archive_account.public_url("http://mp.weixin.qq.com/s?__biz=abc&pass_ticket=nope&mid=2")
        self.assertEqual(url, "https://mp.weixin.qq.com/s?__biz=abc&mid=2")
        self.assertEqual(archive_account.biz_from_url(url), "abc")

    def test_extracts_nested_article_links_once(self):
        payload = {"data": {"list": [
            {"link": "https://mp.weixin.qq.com/s?__biz=a&mid=1"},
            {"url": "https://mp.weixin.qq.com/s?mid=1&__biz=a"},
        ], "_accountName": "测试号"}}
        urls, account_name = archive_account.extract_articles(payload)
        self.assertEqual(account_name, "测试号")
        self.assertEqual(urls, ["https://mp.weixin.qq.com/s?__biz=a&mid=1"])


if __name__ == "__main__":
    unittest.main()
