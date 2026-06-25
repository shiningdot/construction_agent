"""搜索模块测试。"""

from construction_agent.search.bing_search import BingSearchEngine, SearchResult


class TestBingSearchEngine:
    """Bing 搜索引擎测试。"""

    def test_search_no_api_key(self):
        """测试无 API Key 时返回空列表。"""
        engine = BingSearchEngine(api_key="")
        results = engine.search("test")
        assert results == []

    def test_search_result_dataclass(self):
        """测试 SearchResult 数据类。"""
        result = SearchResult(
            title="测试事故",
            url="https://example.com/accident",
            snippet="这是一起测试事故",
        )
        assert result.title == "测试事故"
        assert result.url == "https://example.com/accident"
        assert result.snippet == "这是一起测试事故"
        assert result.content is None  # 可选字段默认值

    def test_search_accident_query_building(self, mocker):
        """测试事故搜索查询构建。"""
        engine = BingSearchEngine(api_key="fake-key")

        # Mock search 方法
        mock_search = mocker.patch.object(engine, "search", return_value=[
            SearchResult(title="r1", url="https://example.com/1", snippet="s1"),
            SearchResult(title="r2", url="https://example.com/2", snippet="s2"),
        ])

        results = engine.search_accident("深圳工地坍塌")

        # 验证调用了 search 方法（3次不同查询）
        assert mock_search.call_count == 3

        # 验证查询内容
        calls = [args[0] for args in mock_search.call_args_list]
        queries = [call.get("query") for call in calls if isinstance(call, dict)]
        # 实际上 call_args_list 是 (args, kwargs) 对
        search_queries = []
        for args, kwargs in mock_search.call_args_list:
            search_queries.append(kwargs.get("query") or args[0])

        assert any("深圳工地坍塌" in q for q in search_queries)
        assert any("建筑施工事故" in q for q in search_queries)
