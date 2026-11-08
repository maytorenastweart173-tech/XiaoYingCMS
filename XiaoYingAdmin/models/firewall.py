"""
防火墙规则模型 — IP/页面黑名单 + 自定义拦截响应

包含一个 model：
  FirewallRule — 单条规则，支持 IP 黑名单、页面黑名单、IP 白名单
"""
from django.db import models
from XiaoYingAdmin.common.base import BaseModel


# =============================================================================
# 规则类型
# =============================================================================

RULE_TYPE_CHOICES = [
    ('ip_block', 'IP 黑名单'),
    ('page_block', '路径黑名单'),
    ('ip_whitelist', 'IP 白名单'),
]

RESPONSE_TYPE_CHOICES = [
    ('forbidden', '默认拒绝（403）'),
    ('custom_html', '自定义 HTML'),
    ('custom_js', '自定义 JS'),
    ('redirect', '重定向'),
]


class FirewallRule(BaseModel):
    """
    防火墙规则。

    规则匹配优先级：
      1. IP 白名单（ip_whitelist）— 命中则直接放行，不继续匹配
      2. IP 黑名单（ip_block）— 命中则拦截
      3. 路径黑名单（page_block）— 命中则拦截

    拦截响应：
      - forbidden:  返回 403 默认页面
      - custom_html: 返回 403 + 自定义 HTML 内容
      - custom_js:   返回 200 + 自定义 JS（可用于弹窗警告、跳转等）
      - redirect:    重定向到指定 URL
    """

    rule_type = models.CharField(
        max_length=20, choices=RULE_TYPE_CHOICES,
        verbose_name="规则类型",
    )
    value = models.CharField(
        max_length=500,
        verbose_name="匹配值",
        help_text="IP 地址（如 192.168.1.1）或路径（如 /admin/），路径支持前缀匹配",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="启用",
    )
    response_type = models.CharField(
        max_length=20, choices=RESPONSE_TYPE_CHOICES, default='forbidden',
        verbose_name="拦截响应类型",
    )
    custom_content = models.TextField(
        blank=True, default='',
        verbose_name="自定义内容",
        help_text="response_type 为 custom_html 时填写 HTML；为 custom_js 时填写 JS 代码",
    )
    redirect_url = models.CharField(
        max_length=500, blank=True, default='',
        verbose_name="重定向 URL",
        help_text="response_type 为 redirect 时填写目标 URL",
    )
    description = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name="备注描述",
    )
    hit_count = models.IntegerField(
        default=0,
        verbose_name="命中次数",
        help_text="该规则被触发的总次数",
    )
    last_hit_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="最后命中时间",
    )

    class Meta:
        verbose_name = "防火墙规则"
        verbose_name_plural = "防火墙规则"
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['rule_type', 'is_active'], name='idx_fw_type_active'),
        ]

    def __str__(self):
        return f'[{self.get_rule_type_display()}] {self.value}'

    def hit(self):
        """记录一次命中（原子更新）"""
        from django.utils import timezone
        FirewallRule.objects.filter(pk=self.pk).update(
            hit_count=models.F('hit_count') + 1,
            last_hit_at=timezone.now(),
        )
