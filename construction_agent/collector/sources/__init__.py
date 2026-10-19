"""数据源采集器。

每个模块实现一个具体的数据源采集器，继承 BaseCollector：
- open_gov.py: 政府开放数据平台（沈阳、咸宁、菏泽等）
- safehoo.py: 安全管理网 safehoo.com
- mohurd.py: 住建部年度事故通报
- mem.py: 应急管理部事故通报
- news_aggregator.py: 新闻搜索引擎聚合
- academic_import.py: 学术数据集直接导入
"""
