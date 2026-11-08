"""
登录日志模型 — 记录每次登录尝试的详细信息。

字段说明：
  - user:      关联用户（可能为 None，如用户名不存在时）
  - username:  登录时提交的用户名（冗余存储，用户被删后仍可追溯）
  - ip_address: 客户端 IP 地址
  - user_agent: 客户端 User-Agent
  - status:    结果: success / failed_disabled / failed_password / failed_not_found
  - error_msg: 错误详情
  - login_time: 登录时间
  - session_key: 登录成功后的 session key（仅 success 时有值）

使用方式：
  LoginLog.objects.create(
      user=user,
      username=username,
      ip_address=ip,
      user_agent=ua,
      status='success',
  )
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class LoginLog(BaseModel):
    """登录日志"""

    STATUS_CHOICES = [
        ('success', '登录成功'),
        ('failed_disabled', '用户已禁用'),
        ('failed_password', '密码错误'),
        ('failed_not_found', '用户不存在'),
        ('failed_inactive', '用户未激活'),
    ]

    user = models.ForeignKey(
        'User', on_delete=models.SET_NULL,
        blank=True, null=True, default=None,
        verbose_name='关联用户',
        help_text='关联的后台用户，用户被删后此字段置空',
    )
    username = models.CharField(
        '登录账号', max_length=150,
        help_text='登录时提交的用户名（冗余存储）',
    )
    ip_address = models.GenericIPAddressField(
        'IP 地址', blank=True, null=True, default=None,
    )
    user_agent = models.TextField(
        'User-Agent', blank=True, default='',
    )
    status = models.CharField(
        '登录结果', max_length=30, choices=STATUS_CHOICES,
        db_index=True,
    )
    error_msg = models.CharField(
        '错误信息', max_length=255, blank=True, default='',
    )
    login_time = models.DateTimeField(
        '登录时间', auto_now_add=True, db_index=True,
    )
    session_key = models.CharField(
        'Session Key', max_length=40, blank=True, default='',
        help_text='登录成功后的 session key',
    )

    class Meta:
        verbose_name = '登录日志'
        verbose_name_plural = '登录日志'
        db_table = 'xiaoying_admin_login_log'
        ordering = ['-login_time']

    def __str__(self):
        return f'{self.username} | {self.get_status_display()} | {self.login_time:%Y-%m-%d %H:%M}'
