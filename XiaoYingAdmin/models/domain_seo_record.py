"""
域名快排手段时间线记录 — 关联到 SeoDomain 实体的具体操作记录。
"""

from django.db import models
from django.utils.timezone import localtime

from XiaoYingAdmin.common.base import BaseModel


class DomainSeoRecord(BaseModel):
    """域名快排手段时间线记录 —— 具体某一天对某域名做了哪些操作。"""

    seo_domain = models.ForeignKey(
        'SeoDomain',
        on_delete=models.CASCADE,
        related_name='records',
        verbose_name='关联域名',
        help_text='该记录所属的域名实体',
    )

    # 操作时间（精确到秒）
    action_date = models.DateTimeField(
        '操作时间',
        db_index=True,
        help_text='执行该 SEO 手段的时间（精确到秒）',
    )

    # 操作描述
    description = models.TextField(
        '操作描述',
        help_text='自定义描述，如"提交了必应站长"、"使用了蜘蛛池"等',
    )

    class Meta:
        db_table = 'domain_seo_record'
        verbose_name = '域名SEO时间线记录'
        verbose_name_plural = '域名SEO时间线记录'
        ordering = ['-action_date', '-create_time']

    def __str__(self):
        return f'[{self.action_date}] {self.description[:40]}'

    def to_dict(self) -> dict:
        # 本地化时间显示（处理时区转换）
        dt = self.action_date
        if dt:
            try:
                dt = localtime(dt)
            except Exception:
                pass
        return {
            'id': self.id,
            'seo_domain_id': self.seo_domain_id,
            'domain': str(self.seo_domain) if self.seo_domain_id else '',
            'domain_type': self.seo_domain.domain_type if self.seo_domain_id else '',
            'domain_type_label': self.seo_domain.get_domain_type_display() if self.seo_domain_id else '',
            'action_date': dt.strftime('%Y-%m-%d %H:%M:%S') if dt else '',
            'description': self.description,
            'create_time': self.create_time.isoformat() if self.create_time else '',
            'updated_time': self.updated_time.isoformat() if self.updated_time else '',
        }
