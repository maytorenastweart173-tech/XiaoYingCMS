"""
蜘蛛访问日志中间件 — 记录每个 HTTP 请求到 SpiderAccessLog 表。

工作流程：
  1. 读取 SpiderLogConfig.log_mode：
       - "disabled"     → 直接放行，不记录
       - "all"          → 记录所有（爬虫 + 真人）
       - "spider_only"  → 只记录被识别为爬虫的
  2. 跳过 /xiaoying_admin/ 后台路径
  3. 跳过 SeoCloakRule 白名单路径（与斗篷共享）
  4. 收集请求元数据：IP / UA / path / method / referer
  5. 复用 SeoCloakRule.is_spider() 识别爬虫 + 提取爬虫名
  6. 匹配请求域名是否在页面列表（GeneratedPage）中
  7. 调用下游 get_response(request) 拿到 response
  8. 收集 status_code / response_size 后 try/except 写 DB
  9. 写 DB 异常被捕获，不影响主响应返回

放置位置：MIDDLEWARE 列表靠前（在 SeoCloakMiddleware / DomainBindMiddleware 之前）。
这样即便斗篷/域名中间件直接 return HttpResponse 短路 view，本中间件的
response 阶段仍会执行（洋葱外层最后处理 response），确保所有访问被记录。
"""

import logging

from django.db import DatabaseError

from XiaoYingAdmin.models.generated_page import GeneratedPage
from XiaoYingAdmin.models.seo_cloak import SeoCloakRule
from XiaoYingAdmin.models.spider_log import SpiderAccessLog, SpiderLogConfig
from XiaoYingAdmin.utils.backup import check_and_auto_backup


logger = logging.getLogger(__name__)

# 缓存：{host: (page_id, page_name, matched_domain), ...}
# 为了避免每次请求都查 DB，只在模块首次加载时查询一次。
# 如果页面列表有变更，需要重启进程。这对开发/生产都合理。
_cache_pages = None


def _build_page_cache():
    """构建 {host_pattern: (page_id, page_name, matched_domain)} 缓存。

    支持格式：
      - 精确匹配：127.0.0.1:8000 → 精确匹配
      - 通配符：*.example.com → 后缀匹配
    """
    cache = {}
    pages = GeneratedPage.objects.only('id', 'name', 'domain', 'domains')
    for p in pages:
        # 旧字段 domain
        if p.domain:
            d = p.domain.strip().lower()
            if d:
                cache[d] = (p.id, p.name, d)
        # JSON 字段 domains
        for d in (p.domains or []):
            d = d.strip().lower()
            if not d:
                continue
            cache[d] = (p.id, p.name, d)
    return cache


def _find_matching_page(host: str):
    """从缓存中查找匹配 host 的页面。

    返回 (page_id, page_name, matched_domain) 或 (None, '', '')。
    """
    global _cache_pages
    if _cache_pages is None:
        _cache_pages = _build_page_cache()

    if not host:
        return None, '', ''

    host_lower = host.strip().lower()

    # 1. 精确匹配
    if host_lower in _cache_pages:
        return _cache_pages[host_lower]

    # 2. 通配符后缀匹配：*.example.com → 检查 host 是否以 .example.com 结尾
    for pattern, (pid, pname, pd) in _cache_pages.items():
        if pattern.startswith('*.'):
            suffix = pattern[1:]  # .example.com
            if host_lower.endswith(suffix):
                return pid, pname, pd

    return None, '', ''


def _get_client_ip(meta) -> str:
    """从 request.META 提取客户端真实 IP（优先 X-Forwarded-For）。"""
    xff = meta.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        # 多层代理时取第一个
        return xff.split(',')[0].strip()
    return meta.get('REMOTE_ADDR', '0.0.0.0')


def _extract_spider_name(user_agent: str, keywords: list) -> str:
    """从 UA 中找第一个匹配的爬虫关键字（小写匹配），返回原始大小写的 keyword。

    如果没匹配到返回空串。SpiderAccessLog.spider_name 留空表示"真人"。
    """
    if not user_agent or not keywords:
        return ''
    ua_lower = user_agent.lower()
    for kw in keywords:
        if kw and kw in ua_lower:
            return kw
    return ''


class SpiderLogMiddleware:
    """蜘蛛 / 真人访问日志中间件。"""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_ignored_path(path: str, ignore_text: str) -> bool:
        """检查路径是否在忽略列表中（按行分割，前缀匹配）。"""
        if not ignore_text or not ignore_text.strip():
            return False
        for line in ignore_text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if path.startswith(line):
                return True
        return False

    def __call__(self, request):
        # === 1. 读取配置 ===
        config = SpiderLogConfig.get_singleton()
        log_mode = config.log_mode

        # === 2. 关闭模式 → 直接放行 ===
        if log_mode == 'disabled':
            return self.get_response(request)

        # === 4. 跳过后台路径 ===
        if request.path.startswith('/xiaoying_admin/'):
            return self.get_response(request)

        # === 4b. 跳过用户配置的忽略路径 ===
        if self._is_ignored_path(request.path, config.ignore_paths):
            return self.get_response(request)

        # === 5. 跳过白名单（与 SeoCloak 共享） ===
        cloak_rule = SeoCloakRule.get_singleton()
        if cloak_rule.is_whitelisted(request.path):
            return self.get_response(request)

        # === 6. 收集请求元数据 ===
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        is_spider = cloak_rule.is_spider(user_agent)
        spider_name = _extract_spider_name(user_agent, cloak_rule.get_spider_keywords()) if is_spider else ''

        # === 7. 匹配域名 → 页面 ===
        host = request.get_host()
        page_id, page_name, matched_domain = _find_matching_page(host)

        # === 8. 根据 log_mode 决定是否记录 ===
        if log_mode == 'spider_only' and not is_spider:
            return self.get_response(request)

        # === 9. 调用下游，拿到 response ===
        response = self.get_response(request)

        # === 10. 收集响应数据后写 DB（异常隔离） ===
        try:
            status_code = response.status_code
            response_size = len(response.content) if hasattr(response, 'content') else None
            SpiderAccessLog.objects.create(
                ip=_get_client_ip(request.META)[:45],  # GenericIPAddressField 上限 45 字符
                user_agent=user_agent[:5000],  # 避免极端长 UA 撑爆 DB
                spider_name=spider_name[:64],
                path=request.path[:500],
                method=request.method[:10],
                referer=request.META.get('HTTP_REFERER', '')[:500],
                status_code=status_code,
                response_size=response_size,
                page_id=page_id,
                page_name=page_name[:128] if page_name else '',
                matched_domain=matched_domain[:255] if matched_domain else '',
            )
        except (DatabaseError, ValueError, TypeError, Exception) as e:  # noqa: BLE001 — 故意捕获所有异常，避免日志写入影响主响应
            logger.warning('SpiderLogMiddleware 写日志失败: %s', e)

        # === 11. 自动备份阈值检查 ===
        check_and_auto_backup(
            SpiderAccessLog, 'spider_logs', 'auto_backup_spider_threshold',
        )

        return response
