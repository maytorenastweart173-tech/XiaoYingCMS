from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class SiteSettings(BaseModel):
    """
    全局网站设置模型（单例模式，仅有一条记录）。
    """

    statistics_code = models.TextField(
        verbose_name='全局代码统计',
        blank=True,
        null=False,
        default='',
        help_text='用于存放全局统计代码、第三方分析脚本等内容，会在全站页面底部引入',
    )
    is_active = models.BooleanField(
        verbose_name='是否启用',
        default=True,
        help_text='关闭后全局统计代码将不会在页面中输出',
    )
    auto_backup_spider_threshold = models.IntegerField(
        verbose_name='蜘蛛日志自动备份阈值',
        default=0,
        help_text='蜘蛛日志记录数达到此值时自动备份并清空。0=关闭自动备份',
    )
    auto_backup_operation_threshold = models.IntegerField(
        verbose_name='操作日志自动备份阈值',
        default=0,
        help_text='操作日志记录数达到此值时自动备份并清空。0=关闭自动备份',
    )
    search_engines = models.JSONField(
        '搜索引擎列表',
        default=list,
        blank=True,
        help_text='可用的搜索引擎列表，如 ["百度", "谷歌", "必应", "搜狗", "360搜索"]',
    )
    max_login_attempts = models.IntegerField(
        verbose_name='最大登录失败次数',
        default=0,
        help_text='同一 IP 连续登录失败达到此次数后自动封禁。0=不限制',
    )
    login_ip_whitelist = models.TextField(
        verbose_name='登录 IP 白名单',
        blank=True, default='',
        help_text='只允许这些 IP 访问登录页和后台，每行一个 IP。为空则不限制',
    )
    allowed_admin_domains = models.TextField(
        verbose_name='允许访问后台的域名白名单',
        blank=True, default='',
        help_text='只允许通过指定域名访问后台，每行一个域名（支持 *.example.com 通配符）。为空则不限制',
    )
    deepseek_api_key = models.CharField(
        verbose_name='DeepSeek API Key',
        max_length=255,
        blank=True,
        default='',
        help_text='用于直接调用 DeepSeek 官方 API，请填写自己的 Key（不会写入代码仓库）',
    )
    deepseek_thinking_enabled = models.BooleanField(
        verbose_name='启用 DeepSeek 思考模式',
        default=False,
        help_text='开启后模型先进行深度思考再生成正文，质量更高但速度更慢、消耗更多 token',
    )

    class Meta:
        verbose_name = '网站设置'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'网站设置 v{self.pk}'
