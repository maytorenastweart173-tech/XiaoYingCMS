"""
操作日志模型 — 记录用户在后台的所有重要操作。

字段说明：
  - user:         操作用户（外键，用户被删置空）
  - username:     用户名（冗余存储）
  - action:       操作类型: create/update/delete/login/logout/other
  - target_type:  操作对象类型（如 User、SeoCloakRule、SiteSettings 等）
  - target_id:    操作对象 ID
  - target_repr:  操作对象文字描述（如"用户 admin"）
  - detail:       JSON 详情（可存变更字段、旧值新值等）
  - method:       HTTP 方法
  - path:         请求路径
  - ip_address:   客户端 IP
  - user_agent:   User-Agent
  - created_at:   操作时间

使用方式（视图里手动调用）:
  from XiaoYingAdmin.middleware.operation_log import log_operation
  log_operation(request, 'create', 'User', user.pk, f'用户 {user.username}')

或依赖中间件自动捕获 POST 请求（自动记录基本操作）。
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class OperationLog(BaseModel):
    """操作日志"""

    ACTION_CHOICES = [
        ('create', '创建'),
        ('update', '修改'),
        ('delete', '删除'),
        ('login', '登录'),
        ('logout', '登出'),
        ('export', '导出'),
        ('other', '其他'),
    ]

    user = models.ForeignKey(
        'User', on_delete=models.SET_NULL,
        blank=True, null=True, default=None,
        verbose_name='操作用户',
    )
    username = models.CharField(
        '用户名', max_length=150, db_index=True,
        help_text='操作用户名（冗余存储）',
    )
    action = models.CharField(
        '操作类型', max_length=20, choices=ACTION_CHOICES,
        db_index=True,
    )
    target_type = models.CharField(
        '操作对象类型', max_length=100, blank=True, default='',
        db_index=True,
        help_text='如 User、SeoCloakRule、SiteSettings 等',
    )
    target_id = models.CharField(
        '操作对象 ID', max_length=50, blank=True, default='',
        help_text='操作对象的 primary key',
    )
    target_repr = models.CharField(
        '操作描述', max_length=255, blank=True, default='',
        help_text='操作对象的文字描述，如 "用户 admin"',
    )
    detail = models.JSONField(
        '详细数据', blank=True, null=True, default=None,
        help_text='JSON 格式，可存变更字段、旧值/新值等',
    )
    method = models.CharField(
        'HTTP 方法', max_length=10, blank=True, default='',
    )
    path = models.CharField(
        '请求路径', max_length=500, blank=True, default='',
    )
    ip_address = models.GenericIPAddressField(
        'IP 地址', blank=True, null=True, default=None,
    )
    user_agent = models.TextField(
        'User-Agent', blank=True, default='',
    )
    created_at = models.DateTimeField(
        '操作时间', auto_now_add=True, db_index=True,
    )

    class Meta:
        verbose_name = '操作日志'
        verbose_name_plural = '操作日志'
        db_table = 'xiaoying_admin_operation_log'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.username} | {self.get_action_display()} {self.target_repr} | {self.created_at:%Y-%m-%d %H:%M}'
