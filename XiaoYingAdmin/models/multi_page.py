from django.db import models
from XiaoYingAdmin.common.base import BaseModel
from XiaoYingAdmin.models.multi_page_project import MultiPageProject


class MultiPage(BaseModel):
    """多页面项目下的单个页面 —— AI 生成的完整独立 HTML 页面。"""

    project = models.ForeignKey(
        MultiPageProject, on_delete=models.CASCADE, related_name='pages', verbose_name='所属项目'
    )
    name = models.CharField(max_length=255, verbose_name='页面名称')
    url_path = models.CharField(max_length=255, verbose_name='URL路径', help_text='例如 /index.html')
    full_url = models.CharField(max_length=500, blank=True, verbose_name='完整URL')
    title = models.CharField(max_length=500, verbose_name='SEO标题')
    description = models.TextField(blank=True, verbose_name='SEO描述')
    keywords = models.TextField(blank=True, verbose_name='SEO关键词')
    html_content = models.TextField(verbose_name='HTML内容')
    nav_title = models.CharField(max_length=100, blank=True, verbose_name='导航栏显示名称')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'multi_page'
        verbose_name = '多页面'
        verbose_name_plural = '多页面'
        ordering = ['sort_order', 'id']
        unique_together = [['project', 'url_path']]

    def __str__(self):
        return f'[{self.project.name}] {self.name}'

    def save(self, *args, **kwargs):
        """自动计算 full_url。"""
        if self.url_path and self.project_id and hasattr(self, 'project'):
            try:
                if self.project.root_domain:
                    domain = self.project.root_domain.rstrip('/')
                    path = self.url_path
                    if not path.startswith('/'):
                        path = '/' + path
                    self.full_url = f'https://{domain}{path}'
            except Exception:
                pass
        super().save(*args, **kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'name': self.name,
            'url_path': self.url_path,
            'full_url': self.full_url,
            'title': self.title,
            'description': self.description,
            'keywords': self.keywords,
            'nav_title': self.nav_title or self.name,
            'sort_order': self.sort_order,
            'create_time': self.create_time.isoformat() if self.create_time else None,
            'updated_time': self.updated_time.isoformat() if self.updated_time else None,
        }
