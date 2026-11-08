"""
友情链接模型 — 自定义友情链接配置。

在单页面管理列表中维护，用于为生成的页面添加友情链接。
支持配置自动加载到指定分类下的全部页面，或所有页面（类似智能互链，但链接可自由配置）。
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class FriendLink(BaseModel):
    """自定义友情链接"""

    # 应用范围
    SCOPE_ALL = 'all'          # 全部页面
    SCOPE_CATEGORY = 'category'  # 指定分类下的全部页面
    SCOPE_CHOICES = [
        (SCOPE_ALL, '全部页面'),
        (SCOPE_CATEGORY, '指定分类'),
    ]

    title = models.CharField(
        '链接标题',
        max_length=128,
        help_text='友情链接的显示标题',
    )
    url = models.CharField(
        '链接 URL',
        max_length=512,
        help_text='友情链接的跳转地址，如 https://example.com',
    )
    scope = models.CharField(
        '应用范围',
        max_length=16,
        choices=SCOPE_CHOICES,
        default=SCOPE_ALL,
        help_text='该友情链接自动加载到哪些页面',
    )
    category = models.ForeignKey(
        'PageCategory',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='应用分类',
        help_text='scope 为「指定分类」时，加载到该分类下的全部页面',
    )

    class Meta:
        verbose_name = '友情链接'
        verbose_name_plural = verbose_name
        ordering = ['-create_time']

    def __str__(self):
        return self.title

    def to_dict(self) -> dict:
        """序列化为前端所需字典。"""
        return {
            'id': self.id,
            'title': self.title,
            'url': self.url,
            'scope': self.scope,
            'scope_label': dict(self.SCOPE_CHOICES).get(self.scope, self.scope),
            'category_id': self.category_id,
            'category_name': self.category.name if self.category else '',
            'create_time': self.create_time.strftime('%Y-%m-%d %H:%M:%S'),
        }
