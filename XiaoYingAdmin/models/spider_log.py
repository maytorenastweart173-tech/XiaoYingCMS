"""
蜘蛛访问日志（Spider Access Log）模型

包含两个 model：
  1. SpiderAccessLog  — 每次访问生成一条记录
  2. SpiderLogConfig  — 单例配置（log_mode 控制是否记录）
"""
from django.db import models
from XiaoYingAdmin.common.base import BaseModel


# =============================================================================
# 记录模式选项
# =============================================================================

LOG_MODE_CHOICES = [
    ("all", "全部访问 — 记录所有请求（爬虫 + 真人）"),
    ("spider_only", "仅爬虫 — 只记录被识别为爬虫的访问"),
    ("disabled", "关闭 — 不记录任何访问"),
]


# =============================================================================
# 访问日志
# =============================================================================

class SpiderAccessLog(BaseModel):
    """
    蜘蛛 / 真人访问日志（一条记录 = 一次 HTTP 请求）。

    由 SpiderLogMiddleware 在每个请求结束时写入。
    写入失败（DB 异常）被 try/except 隔离，不影响主响应。
    """

    ip = models.GenericIPAddressField(
        verbose_name="访问 IP",
        help_text="支持 IPv4 / IPv6",
    )
    user_agent = models.TextField(
        verbose_name="User-Agent",
        help_text="完整 UA 字符串",
    )
    spider_name = models.CharField(
        max_length=64, blank=True, default="",
        verbose_name="爬虫名",
        help_text="识别出的爬虫名（如 Googlebot / Baiduspider）；非爬虫留空",
    )
    path = models.CharField(
        max_length=500,
        verbose_name="访问路径",
        help_text="请求路径（不含 query string）",
    )
    method = models.CharField(
        max_length=10, default="GET",
        verbose_name="HTTP 方法",
    )
    referer = models.CharField(
        max_length=500, blank=True, default="",
        verbose_name="来源 URL",
    )
    status_code = models.IntegerField(
        null=True, blank=True,
        verbose_name="响应状态码",
        help_text="由中间件在响应阶段回填",
    )
    response_size = models.IntegerField(
        null=True, blank=True,
        verbose_name="响应字节数",
        help_text="由中间件在响应阶段回填（len(response.content)）",
    )
    page_id = models.IntegerField(
        null=True, blank=True,
        verbose_name="关联页面 ID",
        help_text="匹配到的 GeneratedPage.id",
    )
    page_name = models.CharField(
        max_length=128, blank=True, default="",
        verbose_name="关联页面名称",
        help_text="匹配到的页面名称，方便快速查看",
    )
    matched_domain = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="匹配域名",
        help_text="请求 Host 匹配到的域名（如 127.0.0.1:8000）",
    )

    class Meta:
        verbose_name = "蜘蛛访问日志"
        verbose_name_plural = verbose_name
        ordering = ["-create_time"]
        indexes = [
            models.Index(fields=["-create_time"], name="idx_log_time"),
            models.Index(fields=["ip"], name="idx_log_ip"),
            models.Index(fields=["spider_name"], name="idx_log_spider"),
        ]

    def __str__(self):
        who = self.spider_name or f"真人({self.ip})"
        return f"[{self.create_time:%Y-%m-%d %H:%M:%S}] {who} {self.method} {self.path}"


# =============================================================================
# 全局配置（单例）
# =============================================================================

class SpiderLogConfig(BaseModel):
    """
    蜘蛛日志全局配置（单例模式，始终只有一条记录，主键固定为 1）。

    log_mode 控制中间件行为：
      - "all"          → 记录所有访问（爬虫 + 真人），spider_name 标记
      - "spider_only"  → 只记录被识别为爬虫的访问
      - "disabled"     → 不记录（中间件直接放行，无 DB 写入）
    """

    log_mode = models.CharField(
        max_length=20, choices=LOG_MODE_CHOICES, default="all",
        verbose_name="记录模式",
        help_text="控制 SpiderLogMiddleware 记录哪些访问",
    )
    ignore_paths = models.TextField(
        blank=True, default='',
        verbose_name="忽略路径",
        help_text="一行一个路径前缀，匹配的请求不会被记录到蜘蛛日志中",
    )

    class Meta:
        verbose_name = "蜘蛛日志配置"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"蜘蛛日志配置 — {self.get_log_mode_display()}"

    # ------------------------------------------------------------------
    # 单例模式
    # ------------------------------------------------------------------

    @classmethod
    def get_singleton(cls):
        """获取全局唯一的配置记录，不存在则自动创建。"""
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"log_mode": "all"})
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # 强制主键为 1 实现单例
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # 禁止删除，只允许禁用
