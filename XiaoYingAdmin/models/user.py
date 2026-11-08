"""
用户模型 — 继承 Django 的 AbstractUser,保留完整 auth 系统功能。

扩展字段（功能底座）:
  - phone:     手机号（预留,后续可用于短信登录/绑定）
  - avatar:    头像 URL（预留,后续可用于用户中心）
  - bio:       个人简介（预留）
  - is_verified: 是否已验证（预留,后续邮箱/手机验证）
  - extra_data: JSON 扩展字段（预留,任意扩展）

使用方法:
  from django.contrib.auth import authenticate, login, logout
  user = authenticate(username=..., password=...)
  login(request, user)
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """后台管理用户"""
    phone = models.CharField('手机号', max_length=20, blank=True, default='')
    avatar = models.URLField('头像', blank=True, default='')
    bio = models.TextField('个人简介', blank=True, default='')
    is_verified = models.BooleanField('是否已验证', default=False)
    extra_data = models.JSONField('扩展数据', blank=True, default=dict)

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = '用户'
        db_table = 'xiaoying_admin_user'

    def __str__(self):
        return self.username or self.email or str(self.pk)
