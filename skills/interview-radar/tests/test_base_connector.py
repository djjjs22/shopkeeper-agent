from scripts.models import RawPost
from scripts.connectors.base import Connector, SearchResult


def test_searchresult_holds_status_and_posts():
    posts = [RawPost("github", "u1", "text", "Q1")]
    r = SearchResult(posts=posts, status="ok", message="")
    assert r.posts == posts
    assert r.status == "ok"


def test_searchresult_degraded_factory_has_no_posts():
    r = SearchResult.degraded("nowcoder", "needs cookie")
    assert r.posts == []
    assert r.status == "degraded"
    assert "cookie" in r.message


def test_connector_is_abstract():
    class Dummy(Connector):
        name = "dummy"

        def search(self, queries):
            return SearchResult(posts=[], status="ok", message="")

    assert Dummy().search(["x"]).status == "ok"
