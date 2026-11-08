"""
已保存的生成页面 — 每次 AI 生成完成后自动保存。

每次 AI 生成完 HTML 后，系统会再次调用 AI 总结出一个简短页面名称
（如"爱思助手"），然后以该名称保存到本表。
"""

from django.conf import settings
from django.db import models

from XiaoYingAdmin.common.base import BaseModel
from XiaoYingAdmin.models.page_domain_seo import PageDomainSeo


class GeneratedPage(BaseModel):
    """
    已保存的 AI 生成页面。
    """

    name = models.CharField(
        '页面名称',
        max_length=128,
        help_text='AI 总结出的简短名称，如"爱思助手"',
    )
    html_content = models.TextField(
        'HTML 内容',
        help_text='完整的生成 HTML',
    )
    task_id = models.UUIDField(
        '关联任务 ID',
        db_index=True,
        help_text='生成该页面的 PageGenerationTask.task_id',
    )
    input_content = models.TextField(
        '用户输入',
        blank=True,
        default='',
        help_text='用户提交的原始需求描述',
    )
    domain = models.CharField(
        '使用域名（旧）',
        max_length=255,
        null=True,
        blank=True,
        default=None,
        help_text='已废弃，请使用 domains 字段。仅用于兼容旧数据。',
    )
    domains = models.JSONField(
        '绑定域名列表',
        default=list,
        blank=True,
        help_text='支持多个域名及 *. 通配符，如 ["example.com", "*.example.com"]',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='创建者',
        help_text='生成该页面的用户；超级管理员可查看全部，普通用户只能看自己的',
    )
    crosslink_excluded = models.BooleanField(
        '排除智能互链',
        default=False,
        help_text='勾选后，该页面不参与智能互链，其他页面也不会链接到它',
    )
    categories = models.ManyToManyField(
        'PageCategory',
        blank=True,
        verbose_name='所属分类',
        help_text='页面可属于多个自定义分类',
    )

    class Meta:
        verbose_name = '已保存页面'
        verbose_name_plural = verbose_name
        ordering = ['-create_time']

    def __str__(self):
        return self.name

    def to_dict(self, *, with_html: bool = False) -> dict:
        """
        序列化为前端所需的字典。

        参数：
          with_html: 是否包含完整 HTML 内容（列表页不需要，详情页需要）
        """
        from XiaoYingAdmin.common.http import fmt_dt, DATETIME_FMT_SHORT

        data = {
            'id': self.id,
            'name': self.name,
            'input_content': self.input_content,
            'domain': self.domain,
            'domains': self.domains or [],
            'create_time': fmt_dt(self.create_time, DATETIME_FMT_SHORT),
            'created_by': self.created_by.username if self.created_by else None,
            'created_by_id': self.created_by_id,
            'crosslink_excluded': self.crosslink_excluded,
            'category_ids': list(self.categories.values_list('id', flat=True)) if self.pk else [],
            'domain_seo': {s.domain: s.to_dict() for s in self.domain_seo_set.all()},
        }
        if with_html:
            data['html_content'] = self.html_content
        return data
