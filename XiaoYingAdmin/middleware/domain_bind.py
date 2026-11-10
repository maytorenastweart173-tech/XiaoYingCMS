"""
域名绑定中间件 — 将绑定了域名的页面直接渲染到前台。

支持单页面（GeneratedPage）和多页面（MultiPageProject）：
  - 单页面：匹配域名 → 返回该页面 HTML
  - 多页面：匹配域名 → 按请求路径匹配项目内的页面 → 返回对应页面 HTML

一条域名只能绑定一个项目，互斥约束在启用时校验。

工作流程：
  1. 请求进来，判断路径是否以 /xiaoying_admin/ 开头
  2. 是 → 跳过，正常走 Django 路由（管理后台）
  3. 否 → 从 Host 请求头提取域名
  4. 先匹配多页面项目 → 按路径返回多页面内的页面
  5. 未匹配 → 匹配单页面
  6. 皆未匹配 → 返回"未开启页面"
"""

import re

from django.http import Http404, HttpResponse
from XiaoYingAdmin.models.generated_page import GeneratedPage


# ---------------------------------------------------------------------------
# 备选 CSS（当多页面 HTML 不含 <style> 时注入）
# ---------------------------------------------------------------------------
_FALLBACK_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;line-height:1.6;color:#333;background:#fff}
header{background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:0 20px;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.15)}
nav{max-width:1200px;margin:0 auto}
nav ul{list-style:none;display:flex;gap:4px;overflow-x:auto}
nav ul li a{display:block;padding:14px 20px;color:rgba(255,255,255,.85);text-decoration:none;font-size:15px;font-weight:500;transition:all .2s;white-space:nowrap;border-bottom:3px solid transparent}
nav ul li a:hover,nav ul li a.active{color:#fff;border-bottom-color:#ffd54f;background:rgba(255,255,255,.1)}
main{max-width:1200px;margin:0 auto;padding:20px;min-height:60vh}
section{padding:40px 0}
.hero{text-align:center;padding:80px 20px 60px;background:linear-gradient(135deg,#e3f2fd,#bbdefb);border-radius:12px;margin:20px 0}
.hero h1{font-size:2.4em;color:#0d47a1;margin-bottom:16px}
.hero p{font-size:1.15em;color:#555;max-width:600px;margin:0 auto 28px}
.cta-button,.cta-btn{display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff!important;border-radius:6px;text-decoration:none;font-size:16px;font-weight:600;transition:transform .2s,box-shadow .2s;box-shadow:0 4px 14px rgba(26,115,232,.35)}
.cta-button:hover,.cta-btn:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(26,115,232,.45)}
h1{font-size:2em;color:#0d47a1;margin-bottom:16px}
h2{font-size:1.5em;color:#1a3a5c;margin:32px 0 16px}
h3{font-size:1.15em;color:#333;margin-bottom:8px}
.features-preview h2{text-align:center;margin-bottom:32px}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:24px}
.feature-item{background:#fff;border:1px solid #e8edf2;border-radius:10px;padding:28px 24px;transition:box-shadow .3s,transform .3s}
.feature-item:hover{box-shadow:0 8px 24px rgba(0,0,0,.08);transform:translateY(-4px)}
.feature-item h3{color:#1a73e8}
.cta-banner{text-align:center;padding:60px 20px;background:linear-gradient(135deg,#1a73e8,#0d47a1);border-radius:12px;color:#fff;margin:20px 0}
.cta-banner h2{color:#fff;font-size:1.8em}
.cta-banner .cta-button{background:#fff;color:#1a73e8!important;box-shadow:0 4px 14px rgba(0,0,0,.2)}
.download-options{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin:24px 0}
.download-btn,.download-btn-win,.download-btn-mac{display:inline-block;padding:16px 32px;border-radius:8px;text-decoration:none;font-size:15px;font-weight:600;transition:transform .2s;min-width:160px;text-align:center}
.download-btn,.download-btn-win{background:#1a73e8;color:#fff!important}
.download-btn-mac{background:#2d3748;color:#fff!important}
.download-btn:hover,.download-btn-win:hover,.download-btn-mac:hover{transform:translateY(-3px)}
article{margin-bottom:24px;padding:20px 24px;border:1px solid #e8edf2;border-radius:8px;background:#fafbfc}
article h2{margin-top:0}
blockquote{border-left:4px solid #1a73e8;margin:12px 0;padding:12px 20px;background:#f5f8ff;border-radius:0 8px 8px 0;font-style:italic}
img{max-width:100%;height:auto;border-radius:8px;margin:16px 0}
footer{background:#1a3a5c;color:rgba(255,255,255,.7);text-align:center;padding:24px 20px;font-size:14px;margin-top:40px}
@media(max-width:768px){.hero h1{font-size:1.6em}.hero{padding:40px 16px}.feature-grid{grid-template-columns:1fr}nav ul li a{padding:12px 14px;font-size:14px}}
"""


def _sanitize_html(html: str) -> str:
    """清理 HTML：移除外部 CSS 引用，注入备选样式。"""
    cleaned = re.sub(
        r'<link\s[^>]*rel=["\']stylesheet["\'][^>]*/?>',
        '',
        html,
        flags=re.IGNORECASE,
    )
    if '<style' not in cleaned:
        cleaned = cleaned.replace(
            '</head>',
            f'<style>{_FALLBACK_CSS}</style></head>',
        )
    return cleaned


class DomainBindMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._page_model = None

    @property
    def page_model(self):
        if self._page_model is None:
            self._page_model = GeneratedPage
        return self._page_model

    # ------------------------------------------------------------------
    # 域名匹配
    # ------------------------------------------------------------------

    @staticmethod
    def _match_domain(host: str, domain_pattern: str) -> bool:
        """判断 host 是否匹配 domain_pattern。
        支持精确匹配和 *. 通配符匹配。
        """
        if domain_pattern == host:
            return True
        if domain_pattern.startswith('*.') and host.endswith(domain_pattern[1:]):
            return '.' in host and host != domain_pattern[1:]
        return False

    # ------------------------------------------------------------------
    # 单页面查找
    # ------------------------------------------------------------------

    def _find_single_page(self, host: str):
        """在所有已绑定域名的单页面中查找匹配 host 的页面。"""
        qs = self.page_model.objects.exclude(domains=[])
        for page in qs.iterator():
            for d in (page.domains or []):
                if self._match_domain(host, d):
                    return page

        # 兼容旧版：检查 domain 字段
        return self.page_model.objects.filter(domain=host).first()

    # ------------------------------------------------------------------
    # 多页面查找
    # ------------------------------------------------------------------

    def _find_multi_page_project(self, host: str):
        """查找启用了多页面且域名匹配 host 的项目。"""
        from XiaoYingAdmin.models.multi_page_project import MultiPageProject
        for project in MultiPageProject.objects.filter(
            is_enabled=True,
        ).exclude(enabled_domain='').iterator():
            if self._match_domain(host, project.enabled_domain):
                return project
        return None

    def _find_multi_page_by_path(self, project, path: str):
        """在多页面项目中查找匹配请求路径的页面。
        先精确匹配 url_path，再尝试 path 去尾斜杠后匹配。
        """
        from XiaoYingAdmin.models.multi_page import MultiPage
        # 标准化路径
        clean_path = path.rstrip('/') or '/'
        if not clean_path.startswith('/'):
            clean_path = '/' + clean_path

        # 精确匹配
        page = MultiPage.objects.filter(
            project=project, url_path=clean_path,
        ).first()
        if page:
            return page

        # 尝试补充 / 或去掉 /
        alt_path = clean_path + '/' if not clean_path.endswith('/') else clean_path.rstrip('/') or '/'
        if alt_path != clean_path:
            page = MultiPage.objects.filter(
                project=project, url_path=alt_path,
            ).first()
            if page:
                return page

        # 尝试匹配 index.html（根路径访问）
        if clean_path == '/':
            page = MultiPage.objects.filter(
                project=project, url_path='/index.html',
            ).first()
            return page

        return None

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def __call__(self, request):
        # 管理后台路径 → 跳过
        if request.path.startswith('/xiaoying_admin/'):
            return self.get_response(request)

        # 静态文件路径 → 跳过（开发服务器由 staticfiles 处理）
        if request.path.startswith('/static/'):
            return self.get_response(request)

        # 上传文件路径 → 跳过，交给项目路由处理
        if request.path.startswith('/media/'):
            return self.get_response(request)

        # 从请求头提取 Host
        raw_host = request.META.get('HTTP_HOST', '')
        host = raw_host.split(':')[0].strip().lower()
        if not host:
            return self.get_response(request)

        # ---- 0. robots.txt / sitemap.xml（按域名动态生成）----
        path_lower = request.path.lower()
        if request.method in ('GET', 'HEAD'):
            if path_lower in ('/robots.txt', '/sitemap.xml'):
                from XiaoYingAdmin.utils.seo_files import robots_response, sitemap_response

                try:
                    if path_lower == '/robots.txt':
                        return robots_response(host)
                    return sitemap_response(host)
                except Http404:
                    return HttpResponse('', status=404)

        # ---- 0.1 必应站点验证文件（按域名动态返回，互不影响）----
        if request.method in ('GET', 'HEAD'):
            from XiaoYingAdmin.models.domain_brand import DomainBrand

            if path_lower == DomainBrand.BING_AUTH_PATH.lower():
                brand = DomainBrand.resolve(host)
                if brand and brand.enabled and brand.bing_verify_code:
                    from XiaoYingAdmin.utils.domain_brand import bing_auth_response

                    return bing_auth_response(brand)
                # 未配置的域名一律 404，避免把其他域名的验证文件错误返回
                return HttpResponse('', status=404)

        # ---- 1. 域名品牌资源（favicon / logo / og 图）----
        if request.method in ('GET', 'HEAD'):
            from XiaoYingAdmin.models.domain_brand import DomainBrand

            if request.path in DomainBrand.ASSET_PATHS:
                brand = DomainBrand.resolve(host)
                if brand:
                    from XiaoYingAdmin.utils.domain_brand import brand_asset_response

                    try:
                        return brand_asset_response(brand, request.path)
                    except Http404:
                        pass

        # ---- 1. 尝试多页面 ----
        project = self._find_multi_page_project(host)
        if project is None and raw_host.strip().lower() != host:
            project = self._find_multi_page_project(raw_host.strip().lower())

        if project:
            page = self._find_multi_page_by_path(project, request.path)
            if page:
                from XiaoYingAdmin.utils.domain_brand import apply_brand_to_html

                html = apply_brand_to_html(_sanitize_html(page.html_content), host)
                return HttpResponse(
                    html,
                    content_type='text/html; charset=utf-8',
                )
            return HttpResponse(
                '页面不存在',
                content_type='text/plain; charset=utf-8',
            )

        # ---- 2. 尝试单页面 ----
        page = self._find_single_page(host)
        if page is None and raw_host.strip().lower() != host:
            page = self._find_single_page(raw_host.strip().lower())

        if page:
            from XiaoYingAdmin.utils.domain_brand import apply_brand_to_html

            return HttpResponse(
                apply_brand_to_html(page.html_content, host),
                content_type='text/html; charset=utf-8',
            )

        # ---- 3. 都没有 → 返回提示 ----
        return HttpResponse(
            '未开启页面',
            content_type='text/plain; charset=utf-8',
        )
