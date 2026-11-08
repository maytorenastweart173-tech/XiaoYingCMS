"""
页面分类模型 — 用于对已保存页面进行分组管理。

支持多对多关系：一个页面可属于多个分类，一个分类可包含多个页面。
"""

from django.conf import settings
from django.db import models


class PageCategory(models.Model):
    """
    页面分类。

    每个分类有名称、描述和排序序号，
    通过 GeneratedPage.categories ManyToManyField 与页面关联。
    """

    name = models.CharField(
        '分类名称',
        max_length=64,
        unique=True,
        help_text='分类的唯一名称，如"电商站"、"企业站"',
    )
    description = models.CharField(
        '分类描述',
        max_length=255,
        blank=True,
        default='',
        help_text='可选，对分类用途的简短说明',
    )
    sort_order = models.IntegerField(
        '排序序号',
        default=0,
        help_text='数字越小越靠前',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='创建者',
    )
    create_time = models.DateTimeField(
        '创建时间',
        auto_now_add=True,
    )

    class Meta:
        verbose_name = '页面分类'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'create_time']

    def __str__(self):
        return self.name

    def to_dict(self) -> dict:
        """序列化为前端所需字典。"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'sort_order': self.sort_order,
        }
