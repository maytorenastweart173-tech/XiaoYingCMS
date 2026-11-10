# -*- coding: utf-8 -*-
"""按访问域名动态生成 robots.txt 与 sitemap.xml。

设计要点：
  - 完全按请求 Host 生成，不落物理文件，多个域名/子域名互不影响
  - sitemap 默认收录主域名 + 系统已知的子域名页面
  - 域名品牌中可切换为“自定义主机列表”，实现类似 aaa 只收录自己和 bbb 的效果
  - 支持额外手动 URL（相对路径或完整地址）
"""

from urllib.parse import urlsplit

from django.http import Http404, HttpResponse

from XiaoYingAdmin.models.domain_brand import DomainBrand
from XiaoYingAdmin.models.generated_page import GeneratedPage
from XiaoYingAdmin.models.multi_page import MultiPage
from XiaoYingAdmin.models.multi_page_project import MultiPageProject

# 常见二级后缀，用于正确识别「主域名」
TWO_LEVEL_SUFFIXES = {
    'com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'ac.cn',
    'co.uk', 'org.uk', 'me.uk',
    'com.au', 'net.au', 'org.au',
    'co.jp', 'ne.jp', 'or.jp',
    'co.kr', 'or.kr',
    'com.hk', 'com.tw', 'com.sg', 'com.my',
    'com.br', 'com.mx',
}


def clean_host(value):
    """规范化主机名：去协议、端口、路径、www，保留通配符。"""
    raw = (value or '').strip().lower()
    if not raw:
        return ''
    if '://' in raw:
        raw = urlsplit(raw).netloc or ''
    raw = raw.split('/')[0].split(':')[0].strip().strip('.')
    if raw.startswith('www.'):
        raw = raw[4:]
    return raw


def root_domain(host):
    """取主机对应的主域名，如 aaa.quickqidc.com.cn -> quickqidc.com.cn。"""
    host = clean_host(host)
    if not host or '.' not in host:
        return host
    labels = host.split('.')
    if len(labels) <= 2:
        return host
    last_two = '.'.join(labels[-2:])
    if last_two in TWO_LEVEL_SUFFIXES:
        return '.'.join(labels[-3:])
    return last_two


def host_matches(host, pattern):
    """判断 host 是否匹配绑定模式（支持 *.example.com 通配符）。"""
    host = clean_host(host)
    pattern = clean_host(pattern)
    if not host or not pattern:
        return False
    if pattern.startswith('*.'):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return host == pattern


# =============================================================================
# 页面收集
# =============================================================================

def _pages_for_host(host):
    """返回该主机上实际会被访问到的页面路径：[(path, updated_time), ...]。"""
    host = clean_host(host)

    # 与 DomainBindMiddleware 一致：多页面项目优先
    for project in MultiPageProject.objects.filter(
        is_enabled=True,
    ).exclude(enabled_domain='').only('id', 'enabled_domain'):
        if host_matches(host, project.enabled_domain):
            pages = []
            for page in MultiPage.objects.filter(project_id=project.id).order_by('sort_order', 'id'):
                path = page.url_path or '/'
                if not path.startswith('/'):
                    path = '/' + path
                pages.append((path, page.updated_time))
            return pages

    for page in GeneratedPage.objects.exclude(domains=[]).only('domains', 'updated_time'):
        if any(host_matches(host, d) for d in (page.domains or [])):
            return [('/', page.updated_time)]

    legacy = GeneratedPage.objects.filter(domain=host).only('updated_time').first()
    if legacy:
        return [('/', legacy.updated_time)]

    return []


def _known_hosts(root):
    """收集该主域名下系统已知的具体主机名（无法展开通配符）。"""
    root = clean_host(root)
    hosts = {root}

    def add(value):
        value = clean_host(value)
        if value and not value.startswith('*') and root_domain(value) == root:
            hosts.add(value)

    for value in DomainBrand.objects.values_list('domain', flat=True):
        add(value)

    for domains in GeneratedPage.objects.exclude(domains=[]).values_list('domains', flat=True):
        for value in (domains or []):
            add(value)

    for value in GeneratedPage.objects.exclude(
        domain__isnull=True,
    ).exclude(domain='').values_list('domain', flat=True):
        add(value)

    for value in MultiPageProject.objects.filter(
        is_enabled=True,
    ).exclude(enabled_domain='').values_list('enabled_domain', flat=True):
        add(value)

    return sorted(hosts)


def _brand_for(host):
    """获取该主机应使用的域名品牌配置（支持子域名继承）。"""
    return DomainBrand.resolve(clean_host(host))


def _scope_hosts(host, brand):
    """计算当前 sitemap 要收录哪些主机的页面。"""
    host = clean_host(host)
    hosts = []

    def add(value):
        value = clean_host(value)
        if value and not value.startswith('*') and value not in hosts:
            hosts.append(value)

    # 当前主机始终包含
    add(host)

    if brand and brand.sitemap_scope == 'custom':
        for value in (brand.sitemap_hosts or []):
            add(value)
        return hosts

    for value in _known_hosts(root_domain(host)):
        add(value)

    if brand:
        for value in (brand.sitemap_hosts or []):
            add(value)

    return hosts


def _extra_urls(host, brand):
    """解析手动追加的 URL：支持相对路径与完整地址。"""
    if not brand:
        return []
    host = clean_host(host)
    urls = []
    for line in (brand.sitemap_extra_urls or '').splitlines():
        value = line.strip()
        if not value or value.startswith('#'):
            continue
        if value.startswith(('http://', 'https://')):
            urls.append(value)
        elif value.startswith('/'):
            urls.append('https://{}{}'.format(host, value))
        else:
            urls.append('https://{}/{}'.format(host, value))
    return urls


# =============================================================================
# 生成 XML / TXT
# =============================================================================

def build_sitemap_entries(host, brand=None):
    """返回 [(绝对 URL, 更新时间或 None), ...]，按当前域名范围去重。"""
    if brand is None:
        brand = _brand_for(host)

    entries = []
    seen = set()

    for current in _scope_hosts(host, brand):
        for path, updated in _pages_for_host(current):
            loc = 'https://{}{}'.format(current, path)
            if loc in seen:
                continue
            seen.add(loc)
            entries.append((loc, updated))

    for loc in _extra_urls(host, brand):
        if loc in seen:
            continue
        seen.add(loc)
        entries.append((loc, None))

    return entries


def _xml_escape(value):
    return (
        (value or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )


def build_sitemap_xml(entries):
    """生成标准 urlset 格式的 sitemap.xml。"""
    if not entries:
        raise Http404('没有可收录的页面')

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, updated in entries:
        lines.append('  <url>')
        lines.append('    <loc>{}</loc>'.format(_xml_escape(loc)))
        if updated:
            lines.append('    <lastmod>{}</lastmod>'.format(updated.strftime('%Y-%m-%d')))
        lines.append('  </url>')
    lines.append('</urlset>')
    return '\n'.join(lines) + '\n'


def build_robots_txt(host, brand):
    """生成 robots.txt；自定义内容会自动补上当前域名的 Sitemap 行。"""
    host = clean_host(host)
    content = (brand.robots_txt or '').strip() if brand else ''

    if not content:
        content = 'User-agent: *\nAllow: /\n'

    content = content.replace('\r\n', '\n').replace('\r', '\n')
    sitemap_line = 'Sitemap: https://{}/sitemap.xml'.format(host)
    if sitemap_line not in content:
        content = content.rstrip() + '\n\n' + sitemap_line

    return content.rstrip() + '\n'


# =============================================================================
# HTTP 响应
# =============================================================================

def sitemap_response(host):
    """返回当前域名的 sitemap.xml；没有页面时 404。"""
    brand = _brand_for(host)
    entries = build_sitemap_entries(host, brand)
    if not entries:
        raise Http404('没有可收录的页面')

    response = HttpResponse(
        build_sitemap_xml(entries),
        content_type='application/xml; charset=utf-8',
    )
    response['Cache-Control'] = 'public, max-age=600'
    return response


def robots_response(host):
    """返回当前域名的 robots.txt；没有页面时 404。"""
    brand = _brand_for(host)
    entries = build_sitemap_entries(host, brand)
    if not entries:
        raise Http404('没有可收录的页面')

    response = HttpResponse(
        build_robots_txt(host, brand),
        content_type='text/plain; charset=utf-8',
    )
    response['Cache-Control'] = 'public, max-age=600'
    return response
