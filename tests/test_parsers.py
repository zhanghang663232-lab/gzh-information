import json

from gzh_reader.comments import extract_comment_context, parse_comment_page
from gzh_reader.content import ContentFetcher, classify_page
from gzh_reader.metrics import MetricsFetcher, parse_metrics
from gzh_reader.models import ArticleSeed, CaptureSession, Status


def test_metrics_preserve_zero_and_cap():
    payload = {"appmsgstat": {"readNum": 100001, "likeNum": 0, "oldLikeNum": 3,
                               "shareNum": 4, "commentNum": 0}}
    assert parse_metrics(json.dumps(payload)) == {
        "readNum": 100001, "likeNum": 0, "oldLikeNum": 3, "shareNum": 4, "commentNum": 0,
    }


def test_comment_context_and_replies():
    html = "var comment_id='c'; var appmsgid='m'; var idx='1'; var appmsg_token='t';"
    assert extract_comment_context(html)["comment_id"] == "c"
    comments, _cursor, done = parse_comment_page({"data": {"list": [{
        "id": "1", "content": "好", "like_num": 0,
        "reply": [{"id": "r1", "content": "谢谢", "like_num": 0}],
    }], "is_end": 1}}, "url:x")
    assert done and comments[0].replies[0].reply_id == "r1"
    assert comments[0].like_num == 0


def test_page_terminal_states():
    assert classify_page("该内容已被发布者删除")[0] == Status.DELETED
    assert classify_page("内容违规无法查看")[0] == Status.RESTRICTED
    assert classify_page("操作频繁", 429)[0] == Status.RATE_LIMITED


def test_content_parser_extracts_body_and_assets():
    article = ArticleSeed(stable_key="url:x", url="https://x", biz="b")
    html = """<title>标题</title><div id="js_content"><p>正文</p><img data-src="https://img/x.jpg"></div>"""
    item = ContentFetcher().parse_content(article, html)
    assert item.status == Status.OK and "正文" in item.markdown
    assert item.assets == ["https://img/x.jpg"]


def test_cross_account_credential_rejected_without_request():
    article = ArticleSeed(stable_key="url:x", url="https://x", biz="b")
    result = MetricsFetcher().fetch_metrics(article, CaptureSession(biz="other", fresh_url="", cookie="x"))
    assert result.status == Status.FAILED
    assert "跨公众号" in result.reason
