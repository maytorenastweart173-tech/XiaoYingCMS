"""
用户系统配置模型（单例模式，仅有一条记录）。

字段说明：
  - registration_enabled: 是否开放前台注册
  - default_group_id: 新注册用户默认所属组 ID（预留）
  - extra_config: JSON 扩展配置

使用方式：
  from XiaoYingAdmin.models.user_config import UserConfig
  config = UserConfig.get_singleton()
  if config.registration_enabled:
      # 允许注册
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class UserConfig(BaseModel):
    """用户系统全局配置"""

    registration_enabled = models.BooleanField(
        verbose_name='开放注册',
        default=False,
        help_text='开启后，用户可通过注册页面自行注册账号',
    )
    default_group_id = models.IntegerField(
        verbose_name='默认用户组',
        blank=True, null=True, default=None,
        help_text='新注册用户自动加入的用户组 ID（预留），为空则不自动加入',
    )
    extra_config = models.JSONField(
        verbose_name='扩展配置',
        blank=True, default=dict,
        help_text='JSON 格式扩展配置，用于后续扩展',
    )

    class Meta:
        verbose_name = '用户系统配置'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'用户系统配置 v{self.pk}'

    @classmethod
    def get_singleton(cls):
        """获取单例配置，不存在则自动创建"""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj
