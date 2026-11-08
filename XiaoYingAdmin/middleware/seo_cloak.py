"""
斗篷伪装中间件 — 根据访问者身份返回不同内容。

核心逻辑（与 规则.js 一致）：
  1. is_spider()      — User-Agent 匹配爬虫关键字
  2. is_from_search() — Referer 匹配搜索引擎域名
  3. 根据 SeoCloakRule 配置的 action 决策矩阵决定响应行为

决策矩阵：
  ┌──────────────────────┬──────────────────┬──────────────────┐
  │                      │ is_spider=True    │ is_spider=False   │
  ├──────────────────────┼──────────────────┼──────────────────┤
  │ is_from_search=True  │ spider_action     │ search_action     │
  │ is_from_search=False │ spider_action     │ direct_action     │
  └──────────────────────┴──────────────────┴──────────────────┘

典型场景：
  - 爬虫（如 Googlebot）        → 展示 SEO 优化内容 或 301 到 SEO 页
  - 用户从搜索引擎点击跳转      → 展示伪装内容 或 302 到推广页
  - 用户直接访问/从其他站跳转   → 放行（返回正常页面）

注册位置：
  应放在 DomainBindMiddleware 之后、SessionMiddleware 之前，
  避免不必要的 session/认证开销。
"""

from django.http import HttpResponse, HttpResponseRedirect, HttpResponsePermanentRedirect
from XiaoYingAdmin.models.seo_cloak import SeoCloakRule


# 状态码 → Django 响应类的映射
REDIRECT_RESPONSE_MAP = {
    301: HttpResponsePermanentRedirect,
    302: HttpResponseRedirect,
    303: lambda url: HttpResponseRedirect(url, status=303),
    307: lambda url: HttpResponseRedirect(url, status=307),
    308: lambda url: HttpResponsePermanentRedirect(url, status=308),
}


class SeoCloakMiddleware:
    """
    斗篷伪装中间件。

    在非后台路径上根据 User-Agent 和 Referer 进行访问者身份识别，
    并按规则执行对应的内容替换/拦截/重定向操作。
    支持 301/302/303/307/308 五种重定向策略。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # === 1. 只对非后台路径生效 ===
        if request.path.startswith('/xiaoying_admin/'):
            return self.get_response(request)

        # === 2. 获取当前请求域名对应的规则 ===
        host = request.get_host()  # 保留端口，让 get_for_domain 精确匹配
        rule = SeoCloakRule.get_for_domain(host)
        if not rule.is_enabled:
            return self.get_response(request)

        # === 3. 白名单路径跳过 ===
        if rule.is_whitelisted(request.path):
            return self.get_response(request)

        # === 4. 身份识别 ===
        is_spider = rule.is_spider(
            request.META.get('HTTP_USER_AGENT', '')
        )
        is_from_search = rule.is_from_search_engine(
            request.META.get('HTTP_REFERER', '')
        )

        # === 5. 确定行为 ===
        action = rule.determine_action(is_spider, is_from_search)

        # === 6. 执行行为 ===
        if action == 'block':
            return HttpResponse('Access Denied', status=403)

        if action == 'show_seo' and rule.seo_content:
            return HttpResponse(rule.seo_content, content_type='text/html; charset=utf-8')

        if action == 'show_cloak' and rule.cloak_content:
            return HttpResponse(rule.cloak_content, content_type='text/html; charset=utf-8')

        if action == 'redirect':
            redirect_url = rule.get_redirect_url(is_spider, is_from_search)
            if redirect_url:
                status_code = rule.redirect_status_code
                redirect_class = REDIRECT_RESPONSE_MAP.get(status_code, HttpResponseRedirect)
                return redirect_class(redirect_url)

        # pass_through 或内容为空 → 正常处理
        return self.get_response(request)
