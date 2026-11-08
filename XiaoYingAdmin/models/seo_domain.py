"""
域名快排追踪实体 — 每条域名作为独立个体，关联其 SEO 时间线记录。
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class SeoDomain(BaseModel):
    """要追踪 SEO 快排手段的域名实体。"""

    class DomainType(models.TextChoices):
        ROOT = 'root', '根域名'
        MULTI = 'multi', '多域名'

    domain = models.CharField(
        '域名',
        max_length=255,
        unique=True,
        help_text='域名字符串，如 example.com 或 *.example.com',
    )
    domain_type = models.CharField(
        '域名类型',
        max_length=50,
        choices=DomainType.choices,
        default=DomainType.ROOT,
        help_text='根域名 / 多域名，由用户手动选择',
    )
    remark = models.TextField(
        '备注',
        blank=True,
        default='',
        help_text='关于该域名的备注信息（可选）',
    )

    class Meta:
        db_table = 'seo_domain'
        verbose_name = 'SEO域名'
        verbose_name_plural = 'SEO域名'
        ordering = ['domain']

    def __str__(self):
        return self.domain

    def to_dict(self) -> dict:
        from XiaoYingAdmin.models.domain_seo_record import DomainSeoRecord
        record_count = DomainSeoRecord.objects.filter(seo_domain=self).count()
        return {
            'id': self.id,
            'domain': self.domain,
            'domain_type': self.domain_type,
            'domain_type_label': self.get_domain_type_display(),
            'remark': self.remark,
            'record_count': record_count,
            'create_time': self.create_time.isoformat() if self.create_time else '',
        }
