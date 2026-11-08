"""
页面域名SEO状态 — 跟踪每个域名在搜索引擎中的收录与排名情况。

每个 GeneratedPage 可能绑定多个域名，每个域名独立跟踪：
  - 收录状态：该域名已被哪些搜索引擎索引
  - 排名第一：该域名在哪些搜索引擎中排名第一，以及对应的关键词
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class PageDomainSeo(BaseModel):
    """页面域名 SEO 状态"""

    page = models.ForeignKey(
        'GeneratedPage',
        on_delete=models.CASCADE,
        related_name='domain_seo_set',
        verbose_name='所属页面',
    )
    domain = models.CharField(
        '域名',
        max_length=255,
        help_text='完整的域名，如 example.com',
    )

    # ---- Feature 1: 收录状态 ----
    indexed_engines = models.JSONField(
        '已收录搜索引擎',
        default=list,
        blank=True,
        help_text='该域名已被哪些搜索引擎收录，如 ["百度", "谷歌"]',
    )

    # ---- Feature 2: 首页排名第一 ----
    is_rank_first = models.BooleanField(
        '是否排名第一',
        default=False,
        help_text='该域名是否在某个搜索引擎中排名第一',
    )
    rank_first_engines = models.JSONField(
        '排名第一的搜索引擎',
        default=list,
        blank=True,
        help_text='在哪些搜索引擎中排名第一，如 ["百度"]',
    )
    rank_first_keywords = models.JSONField(
        '排名第一的关键词',
        default=dict,
        blank=True,
        help_text='按引擎记录排名第一的关键词，如 {"百度": "小程序开发", "谷歌": "mini program"}',
    )

    class Meta:
        verbose_name = '页面域名SEO状态'
        verbose_name_plural = verbose_name
        unique_together = [('page', 'domain')]
        indexes = [
            models.Index(fields=['page', 'domain']),
        ]

    def __str__(self):
        return f'{self.domain} (页面 {self.page_id})'

    def to_dict(self):
        """序列化为前端所需字典"""
        return {
            'id': self.id,
            'page_id': self.page_id,
            'domain': self.domain,
            'indexed_engines': self.indexed_engines or [],
            'is_rank_first': self.is_rank_first,
            'rank_first_engines': self.rank_first_engines or [],
            'rank_first_keywords': self.rank_first_keywords or {},
        }
