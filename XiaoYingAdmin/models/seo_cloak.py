"""
斗篷伪装（Cloaking）配置模型

存储搜索引擎/爬虫识别规则和伪装行为策略。
支持运行时动态修改，修改后立即生效（中间件每次请求重新读取）。
"""

import json

from django.db import models
from XiaoYingAdmin.common.base import BaseModel


# =============================================================================
# 默认规则（从 规则.js 转换而来）
# =============================================================================

DEFAULT_SEARCH_ENGINES = [
    "google.", "bing.", "yahoo.", "yandex.", "duckduckgo.",
    "baidu.", "sogou.", "sm.", "360.",
]

DEFAULT_SPIDER_KEYWORDS = [
    "googlebot", "bingbot", "baiduspider", "yandexbot", "duckduckbot",
    "sogou", "sosospider", "360spider", "slurp", "mj12bot", "ahrefsbot",
    "semrushbot", "seznambot", "dotbot", "crawler", "spider", "bot",
]

# 默认白名单：这些路径前缀默认不参与斗篷伪装（开发服务器必备路径，
# 被斗篷拦截会导致 CSS/JS/图片 404 或被错误重定向）。
# 与用户配置的白名单合并使用：默认在前，用户追加在后。
DEFAULT_WHITELIST_PATHS = [
    "/static/",
    "/media/",
]

# =============================================================================
# 行为选项
# =============================================================================

ACTION_CHOICES = [
    ("pass_through", "放行（返回正常内容）"),
    ("show_seo", "展示 SEO 优化内容"),
    ("show_cloak", "展示伪装内容"),
    ("redirect", "重定向到指定 URL"),
    ("block", "阻止访问"),
]

# =============================================================================
# HTTP 重定向状态码说明
# =============================================================================

REDIRECT_CHOICES = [
    (301, "301 Moved Permanently",
     "永久重定向。搜索引擎会将原 URL 的权重、排名完全传递到目标 URL。"
     "适用于永久性的 URL 变更。权重传递：✅ 完全传递"),
    (302, "302 Found",
     "临时重定向。搜索引擎保留原 URL 的索引和权重，不传递到目标 URL。"
     "适用于临时性的跳转。权重传递：❌ 不传递"),
    (303, "303 See Other",
     "查看其他位置。用于 POST 请求后重定向到 GET，避免重复提交。"
     "搜索引擎通常不索引此跳转。权重传递：❌ 不传递"),
    (307, "307 Temporary Redirect",
     "临时重定向（保证方法不变）。类似 302 但保证请求方法不变（POST→POST）。"
     "适用于 API/表单提交场景。权重传递：❌ 不传递"),
    (308, "308 Permanent Redirect",
     "永久重定向（保证方法不变）。类似 301 但保证请求方法不变。"
     "适用于永久性 API 端点变更。权重传递：✅ 完全传递"),
]

# 仅状态码列表（用于字段 choices）
REDIRECT_CODE_CHOICES = [(code, f"{code} — {label}") for code, label, _ in REDIRECT_CHOICES]


class SeoCloakRule(models.Model):
    """
    斗篷伪装规则配置。

    新增 domain 字段后，支持为不同域名配置不同的斗篷规则。
    domain='' 表示全局默认规则（兜底）。

    核心逻辑（与 规则.js 一致）：
      1. is_spider()      — User-Agent 匹配爬虫关键字
      2. is_from_search() — Referer 匹配搜索引擎域名
      3. 最终行为由 action 字段决定：

         ┌──────────────────────┬──────────────────┬──────────────────┐
         │                      │ is_spider=True    │ is_spider=False   │
         ├──────────────────────┼──────────────────┼──────────────────┤
         │ is_from_search=True  │ spider_action     │ search_action     │
         │ is_from_search=False │ spider_action     │ direct_action     │
         └──────────────────────┴──────────────────┴──────────────────┘

      4. 当 action=redirect 时，结合 redirect_status_code 和各场景
         的 redirect_url 执行对应 HTTP 状态码跳转。
    """

    # ===== 绑定域名（空字符串 = 全局默认规则） =====
    domain = models.CharField(
        max_length=255, blank=True, default='', unique=True,
        verbose_name='绑定域名',
        help_text='主域名 = 绑定域名列表中的第一个。空字符串表示全局默认规则。'
                  '中间件匹配顺序：精确域名 → 通配符 → 全局默认规则。',
    )

    # ===== 绑定域名列表（一条规则可绑定多个域名，共用同一套配置） =====
    # domain 始终等于 domains 列表中的第一个（主域名），保留 unique 约束与
    # 向后兼容；domains 存 JSON 数组，为空表示全局默认规则。
    domains = models.TextField(
        blank=True, default='',
        verbose_name='绑定域名列表',
        help_text='JSON 数组，该规则绑定的全部域名（第一个为主域名）。空数组表示全局默认规则。',
    )

    # ===== 开关 =====
    is_enabled = models.BooleanField(default=False, verbose_name='启用斗篷伪装')

    # ===== 搜索引擎识别规则 =====
    search_engines = models.TextField(
        default=json.dumps(DEFAULT_SEARCH_ENGINES, ensure_ascii=False),
        verbose_name='搜索引擎域名列表',
        help_text='JSON 数组，用于匹配 Referer 判断是否来自搜索引擎。',
    )

    # ===== 爬虫识别规则 =====
    spider_keywords = models.TextField(
        default=json.dumps(DEFAULT_SPIDER_KEYWORDS, ensure_ascii=False),
        verbose_name='爬虫标识关键字',
        help_text='JSON 数组，用于匹配 User-Agent 判断是否为爬虫。',
    )

    # ===== 各场景行为策略 =====
    spider_action = models.CharField(
        max_length=20, choices=ACTION_CHOICES, default='show_seo',
        verbose_name='爬虫行为',
        help_text='检测到是爬虫/蜘蛛时执行的操作',
    )
    search_action = models.CharField(
        max_length=20, choices=ACTION_CHOICES, default='show_cloak',
        verbose_name='搜索引擎用户行为',
        help_text='真实用户从搜索引擎跳转来时执行的操作',
    )
    direct_action = models.CharField(
        max_length=20, choices=ACTION_CHOICES, default='pass_through',
        verbose_name='直接访问行为',
        help_text='真实用户直接访问（非搜索引擎来源）时执行的操作',
    )

    # ===== 重定向配置 =====
    redirect_status_code = models.IntegerField(
        choices=REDIRECT_CODE_CHOICES, default=302,
        verbose_name='重定向状态码',
        help_text='执行 redirect 行为时使用的 HTTP 状态码',
    )
    spider_redirect_url = models.CharField(
        max_length=500, blank=True, default='',
        verbose_name='爬虫重定向 URL',
        help_text='爬虫命中 redirect 时的目标 URL（需完整 URL，如 https://example.com）',
    )
    search_redirect_url = models.CharField(
        max_length=500, blank=True, default='',
        verbose_name='搜索引擎用户重定向 URL',
        help_text='搜索引擎用户命中 redirect 时的目标 URL',
    )
    direct_redirect_url = models.CharField(
        max_length=500, blank=True, default='',
        verbose_name='直接访问重定向 URL',
        help_text='直接访问命中 redirect 时的目标 URL',
    )

    # ===== 内容 =====
    seo_content = models.TextField(
        blank=True, default='',
        verbose_name='SEO 优化内容',
        help_text='展示给爬虫的 HTML 内容。留空则返回原始页面。',
    )
    cloak_content = models.TextField(
        blank=True, default='',
        verbose_name='伪装内容',
        help_text='展示给搜索引擎用户的 HTML 内容。留空则返回原始页面。',
    )

    # ===== 白名单/黑名单路径 =====
    whitelist_paths = models.TextField(
        blank=True, default='',
        verbose_name='白名单路径',
        help_text='不受斗篷伪装影响的路径，每行一条（支持前缀匹配，如 /api/）',
    )

    # ===== 备注（可选，供管理员标识规则用途） =====
    remark = models.TextField(
        blank=True, default='',
        verbose_name='备注',
        help_text='可选。用于管理员标识本条规则的用途或备注说明，最长 1000 字符。',
        max_length=1000,
    )

    # ===== 元信息 =====
    created_time = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_time = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        verbose_name = '斗篷伪装规则'
        verbose_name_plural = verbose_name
        ordering = ['domain']

    def __str__(self):
        label = self.domain or '(全局默认规则)'
        status = '✅ 已启用' if self.is_enabled else '❌ 未启用'
        return f'{label} — {status}'

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_domain_list(self) -> list:
        """返回该规则绑定的全部域名列表（主域名始终在最前，自动去重）。"""
        result = []
        if self.domain:
            result.append(self.domain)
        try:
            ds = json.loads(self.domains) if self.domains else []
        except (json.JSONDecodeError, TypeError):
            ds = []
        if isinstance(ds, list):
            for d in ds:
                if d and d not in result:
                    result.append(d)
        return result

    @staticmethod
    def _domain_occupied(domain: str, exclude_pk=None) -> bool:
        """判断域名是否已被其他规则绑定（扫描主域名与绑定列表，含空域名=全局默认）。"""
        qs = SeoCloakRule.objects.all()
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        for rule in qs.iterator():
            if domain == rule.domain:
                return True
            try:
                ds = json.loads(rule.domains) if rule.domains else []
            except (json.JSONDecodeError, TypeError):
                ds = []
            if isinstance(ds, list) and domain in ds:
                return True
        return False

    def get_search_engines(self) -> list:
        """解析搜索引擎域名列表。"""
        try:
            return json.loads(self.search_engines) if self.search_engines else []
        except (json.JSONDecodeError, TypeError):
            return list(DEFAULT_SEARCH_ENGINES)

    def get_spider_keywords(self) -> list:
        """解析爬虫关键字列表。"""
        try:
            return json.loads(self.spider_keywords) if self.spider_keywords else []
        except (json.JSONDecodeError, TypeError):
            return list(DEFAULT_SPIDER_KEYWORDS)

    def get_whitelist_paths(self) -> list:
        """解析白名单路径列表：默认白名单 + 用户配置的白名单。"""
        user_paths = [p.strip() for p in self.whitelist_paths.split('\n') if p.strip()] if self.whitelist_paths else []
        return list(DEFAULT_WHITELIST_PATHS) + user_paths

    def is_whitelisted(self, path: str) -> bool:
        """判断路径是否在白名单中（前缀匹配）。"""
        return any(path.startswith(wl) for wl in self.get_whitelist_paths())

    def is_from_search_engine(self, referer: str) -> bool:
        """判断来源是否为搜索引擎（与规则.js 逻辑一致）。"""
        if not referer:
            return False
        ref = referer.lower()
        return any(engine in ref for engine in self.get_search_engines())

    def is_spider(self, user_agent: str) -> bool:
        """判断是否为爬虫（与规则.js 逻辑一致）。"""
        if not user_agent:
            return True  # 无 UA 视为爬虫
        ua = user_agent.lower()
        return any(keyword in ua for keyword in self.get_spider_keywords())

    def determine_action(self, is_spider: bool, is_from_search: bool) -> str:
        """
        根据是否爬虫 + 是否搜索引擎来源 确定最终行为。

        决策矩阵：
          spider=T, search=T → spider_action（爬虫优先）
          spider=T, search=F → spider_action
          spider=F, search=T → search_action（真实用户来自搜索引擎 → 经典斗篷场景）
          spider=F, search=F → direct_action
        """
        if is_spider:
            return self.spider_action
        if is_from_search:
            return self.search_action
        return self.direct_action

    def get_redirect_url(self, is_spider: bool, is_from_search: bool) -> str:
        """根据身份场景获取对应的重定向目标 URL。"""
        if is_spider:
            return self.spider_redirect_url or ''
        if is_from_search:
            return self.search_redirect_url or ''
        return self.direct_redirect_url or ''

    # ------------------------------------------------------------------
    # 单例模式
    # ------------------------------------------------------------------

    @classmethod
    def get_singleton(cls):
        """获取全局默认规则（domain=''）。如果不存在则自动创建。"""
        obj, _ = cls.objects.get_or_create(domain='', defaults={
            'search_engines': json.dumps(DEFAULT_SEARCH_ENGINES, ensure_ascii=False),
            'spider_keywords': json.dumps(DEFAULT_SPIDER_KEYWORDS, ensure_ascii=False),
        })
        return obj

    @classmethod
    def get_for_domain(cls, domain: str):
        """
        获取指定域名对应的斗篷规则。

        匹配顺序（对主域名与绑定列表中的所有域名生效）：
          1. 精确匹配（保留端口）
          2. 通配符匹配（*.example.com → sub.example.com）
          3. 去除端口再次精确匹配
          4. 去除端口再次通配符匹配
          5. 降级到全局默认规则（domain=''）
        """
        if not domain:
            return cls.get_singleton()

        clean = domain.split(':')[0]
        for rule in cls.objects.exclude(domain='').iterator():
            dl = rule.get_domain_list()
            if domain in dl:
                return rule
            for pattern in dl:
                if pattern.startswith('*.') and domain.endswith(pattern[2:]):
                    return rule
        if clean != domain:
            for rule in cls.objects.exclude(domain='').iterator():
                dl = rule.get_domain_list()
                if clean in dl:
                    return rule
                for pattern in dl:
                    if pattern.startswith('*.') and clean.endswith(pattern[2:]):
                        return rule
        return cls.get_singleton()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.domain:
            return  # 禁止删除默认规则
        super().delete(*args, **kwargs)

    def to_dict(self):
        """序列化为字典（供 API 返回）。"""
        return {
            'id': self.pk,
            'domain': self.domain,
            'domains': self.get_domain_list(),
            'is_enabled': self.is_enabled,
            'search_engines': self.get_search_engines(),
            'spider_keywords': self.get_spider_keywords(),
            'spider_action': self.spider_action,
            'search_action': self.search_action,
            'direct_action': self.direct_action,
            'redirect_status_code': self.redirect_status_code,
            'spider_redirect_url': self.spider_redirect_url,
            'search_redirect_url': self.search_redirect_url,
            'direct_redirect_url': self.direct_redirect_url,
            'seo_content': self.seo_content,
            'cloak_content': self.cloak_content,
            'whitelist_paths': self.get_whitelist_paths(),
            'whitelist_paths_raw': self.whitelist_paths,
            'remark': self.remark,
            'redirect_choices': [
                {'code': code, 'label': label, 'desc': desc}
                for code, label, desc in REDIRECT_CHOICES
            ],
            'action_choices': [{'value': v, 'label': l} for v, l in ACTION_CHOICES],
            'updated_time': self.updated_time.strftime('%Y-%m-%d %H:%M:%S') if self.updated_time else '',
        }
