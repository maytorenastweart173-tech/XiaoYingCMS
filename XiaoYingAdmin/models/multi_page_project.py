from django.db import models
from XiaoYingAdmin.common.base import BaseModel
from XiaoYingAdmin.models.user import User


class MultiPageProject(BaseModel):
    """多页面项目 —— 代表一次 AI 批量生成的完整站点。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        GENERATING = 'generating', '生成中'
        COMPLETED = 'completed', '已完成'
        FAILED = 'failed', '失败'

    name = models.CharField(max_length=255, verbose_name='项目名称')
    root_domain = models.CharField(max_length=255, blank=True, verbose_name='根域名')
    theme = models.TextField(verbose_name='主题描述')
    style = models.CharField(max_length=100, default='modern', verbose_name='风格偏好')
    status = models.CharField(
        max_length=50, choices=Status.choices, default=Status.DRAFT, verbose_name='状态'
    )
    nav_config = models.JSONField(default=list, blank=True, verbose_name='导航栏配置')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, verbose_name='创建者'
    )
    task_id = models.CharField(max_length=64, blank=True, verbose_name='生成任务ID')
    is_enabled = models.BooleanField(default=False, verbose_name='是否启用')
    enabled_domain = models.CharField(max_length=255, blank=True, default='', verbose_name='启用域名')
    crosslink_excluded = models.BooleanField(default=False, verbose_name='是否排除智能互链')

    class Meta:
        db_table = 'multi_page_project'
        verbose_name = '多页面项目'
        verbose_name_plural = '多页面项目'
        ordering = ['-create_time']

    def __str__(self):
        return self.name

    def total_pages(self):
        return self.pages.count()

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'root_domain': self.root_domain,
            'theme': self.theme,
            'style': self.style,
            'status': self.status,
            'status_display': self.get_status_display(),
            'nav_config': self.nav_config,
            'total_pages': self.total_pages(),
            'task_id': self.task_id,
            'is_enabled': self.is_enabled,
            'enabled_domain': self.enabled_domain,
            'crosslink_excluded': self.crosslink_excluded,
            'created_by': self.created_by_id,
            'create_time': self.create_time.isoformat() if self.create_time else None,
            'updated_time': self.updated_time.isoformat() if self.updated_time else None,
        }
