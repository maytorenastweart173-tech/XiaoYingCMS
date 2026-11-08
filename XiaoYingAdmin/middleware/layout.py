"""
菜单布局中间件 + 上下文处理器

功能：
  1. 加载菜单配置 JSON，带文件变更检测缓存
  2. 根据 request.path 递归检测当前激活菜单
  3. 通过上下文处理器将菜单数据注入模板变量
  4. 支持视图通过 request.show_sidebar 控制显隐

使用方式：
  视图函数中设置 request.show_sidebar = False 可隐藏侧边栏（默认为 True）

日志调试：
  在 .env 中设置 LOG_DEBUG=True 可开启 loguru 调试日志
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
from django.conf import settings
from loguru import logger

# 加载一次 .env（后续 os.getenv 直接读内存，无需重复读文件）
load_dotenv()

# =============================================================================
# 配置
# =============================================================================

# 菜单配置文件的绝对路径（会自动查找）
MENU_CONFIG_PATHS = [
    Path(settings.BASE_DIR) / 'XingXingWeb' / 'templates' / '左侧菜单' / '左侧菜单配置.json',
    Path(__file__).resolve().parent.parent / 'templates' / 'XiaoYingAdmin' / '左侧菜单' / '左侧菜单配置.json',
]

# =============================================================================
# 辅助函数
# =============================================================================


def _get_log_debug():
    """读取 LOG_DEBUG 环境变量，判断是否开启调试日志（.env 已在模块顶部加载一次）"""
    return os.getenv('LOG_DEBUG', 'False').lower() in ('true', '1', 'yes')


def _load_menu_config():
    """
    加载菜单配置文件。

    返回 (menu_data, mtime) 元组：
      - menu_data: list，菜单配置列表
      - mtime: float，文件修改时间戳
    如果加载失败，返回 ([], 0)
    """
    config_file = None
    for path in MENU_CONFIG_PATHS:
        if path.exists():
            config_file = path
            break

    if config_file is None:
        if _get_log_debug():
            logger.warning("菜单配置文件未找到，已搜索路径: {}", MENU_CONFIG_PATHS)
        return [], 0

    try:
        mtime = config_file.stat().st_mtime
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list):
            if _get_log_debug():
                logger.warning("菜单配置格式错误：应为 JSON 数组，实际为 {}", type(data).__name__)
            return [], mtime
        if _get_log_debug():
            logger.info("菜单配置加载成功，共 {} 个一级菜单", len(data))
        return data, mtime
    except json.JSONDecodeError as e:
        if _get_log_debug():
            logger.error("菜单配置 JSON 解析失败: {}", e)
        return [], mtime
    except Exception as e:
        if _get_log_debug():
            logger.error("菜单配置加载异常: {}", e)
        return [], 0


def _find_active_urls(menu_items, current_path):
    """
    递归查找当前路径匹配的所有激活 URL（包括祖先菜单）。

    匹配规则：
      1. 当前请求路径与菜单项的 url 完全匹配（current_path == url）
      2. 当前请求路径以菜单项的 url 开头（current_path.startswith(url)）
      3. 前缀匹配：当前请求路径与菜单项的子菜单同目录（如搜索页 /search/ 与首页 /index/ 在同一目录下）
      4. 目录级回退：如果当前路径是目录（如 /、/web/），自动尝试匹配 index/ 页面

    参数：
      menu_items: list，菜单配置列表
      current_path: str，当前请求路径

    返回：
      set，包含所有激活的菜单 url
    """
    active_urls = set()

    for item in menu_items:
        url = item.get('url', '')
        children = item.get('children', [])

        # 当前菜单项是否有激活的子菜单
        child_active = set()
        if children:
            child_active = _find_active_urls(children, current_path)

        if child_active:
            # 子菜单有激活项 → 当前菜单也是激活的（展开父级）
            if url:
                active_urls.add(url)
            active_urls.update(child_active)
        elif url and current_path.startswith(url):
            # 当前请求路径以菜单项的 url 开头 → 激活
            active_urls.add(url)
        elif not child_active and children and not url:
            # 前缀匹配回退：当前路径与某个子菜单同目录但非精确匹配
            # 例如 /web/music/line1/search/ 与 /web/music/line1/index/ 同目录
            for child in children:
                child_url = child.get('url', '')
                if child_url:
                    # 比较路径段：段数相同且前 N-1 段一致 → 同目录兄弟页面
                    child_parts = child_url.strip('/').split('/')
                    path_parts = current_path.strip('/').split('/')
                    if len(child_parts) == len(path_parts):
                        is_sibling = all(
                            child_parts[i] == path_parts[i]
                            for i in range(len(child_parts) - 1)
                        )
                        if is_sibling:
                            active_urls.update(_find_active_urls(children, current_path))
                            break

    # ===== 目录级回退：访问目录时自动匹配 index 页 =====
    # 如果当前路径是根 / 或空，尝试匹配 /web/index/
    # 如果当前路径是 /xxx/ 格式，尝试匹配 /xxx/index/
    if not active_urls:
        fallback_path = None
        if current_path in ('/', ''):
            fallback_path = '/web/index/'
        elif current_path.endswith('/'):
            fallback_path = '{}index/'.format(current_path)

        if fallback_path:
            for item in menu_items:
                url = item.get('url', '')
                if url and url == fallback_path:
                    active_urls.add(url)
                    break
                # 也递归检查子菜单
                if item.get('children'):
                    child_active = _find_active_urls(item['children'], fallback_path)
                    if child_active:
                        if url:
                            active_urls.add(url)
                        active_urls.update(child_active)
                        break

    return active_urls


def _filter_superuser_only(menu_items, is_superuser):
    """
    递归过滤菜单中仅超级管理员可见的项。

    配置项中标记了 ``"superuser_only": true`` 的菜单仅在
    ``is_superuser == True`` 时保留，否则整项（含子菜单）被移除。

    参数:
      menu_items: list，原始菜单配置
      is_superuser: bool，当前用户是否为超级管理员

    返回:
      list，过滤后的菜单配置
    """
    result = []
    for item in menu_items:
        # 标记为 superuser_only 但当前不是超级管理员 → 跳过此项及所有子项
        if item.get('superuser_only') and not is_superuser:
            continue
        # 递归处理子菜单
        children = item.get('children', [])
        if children:
            item = dict(item)  # 浅拷贝，避免影响缓存
            item['children'] = _filter_superuser_only(children, is_superuser)
        result.append(item)
    return result


# =============================================================================
# 中间件
# =============================================================================

class LayoutMiddleware:
    """
    Django 中间件 — 菜单布局数据处理。

    将菜单数据和激活信息注入到 request 对象中，
    供布局上下文处理器读取并传递到模板。
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._menu_cache = None   # 缓存的菜单数据
        self._cache_mtime = 0.0   # 缓存对应文件的修改时间
        if _get_log_debug():
            logger.info("LayoutMiddleware 初始化完成")

    def _get_menu_data(self):
        """
        获取菜单数据（带文件变更检测缓存）。

        如果配置文件未修改，直接返回缓存；否则重新加载。
        """
        data, mtime = _load_menu_config()

        # 如果文件 mtime 没变且已有缓存，返回缓存
        if self._menu_cache is not None and mtime == self._cache_mtime:
            return self._menu_cache

        self._menu_cache = data
        self._cache_mtime = mtime
        return data

    def __call__(self, request):
        """
        处理每个请求：注入菜单数据到 request 对象。
        """
        # 默认显示侧边栏
        request.show_sidebar = getattr(request, 'show_sidebar', True)

        # 登录/登出/注册/忘记密码页不显示侧边栏
        path = request.path_info
        if any(path.startswith(p) for p in [
            '/xiaoying_admin/login/',
            '/xiaoying_admin/logout/',
            '/xiaoying_admin/register/',
            '/xiaoying_admin/forgot_password/',
        ]):
            request.show_sidebar = False

        if request.show_sidebar:
            menu_data = self._get_menu_data()
            current_path = request.path_info

            # 权限过滤：仅超级管理员可见的菜单项
            is_superuser = getattr(request.user, 'is_superuser', False)
            menu_data = _filter_superuser_only(menu_data, is_superuser)

            active_urls = _find_active_urls(menu_data, current_path)

            request.sidebar_menu_data = menu_data
            request.sidebar_active_urls = active_urls

            if _get_log_debug():
                logger.debug("当前路径: {} | 激活菜单: {}", current_path, active_urls)
        else:
            request.sidebar_menu_data = []
            request.sidebar_active_urls = set()

        response = self.get_response(request)
        return response


# =============================================================================
# 上下文处理器
# =============================================================================

def layout_context_processor(request):
    """
    Django 上下文处理器 — 将布局菜单数据 + 基础 SEO 配置注入模板上下文。

    在 TEMPLATES 配置中注册后，所有模板均可使用以下变量：

    【布局相关】
      - sidebar_menu_data: list，菜单配置数据
      - sidebar_active_urls: set，当前激活的菜单 URL 集合
      - show_sidebar: bool，是否显示侧边栏

    【SEO 站点级（动态部分）】
      - site_origin: str，站点根地址（如 https://xiaoyingapi.com），用于 SEO og:url
      - canonical_url: str，当前页面的绝对地址
      - site_name: str，站点名称（用于 WebSite / Organization schema）
      - org_logo_url: str，品牌 Logo 绝对 URL（用于 Organization.logo）

    【SEO 站点级（硬编码部分）】
      品牌英文别名、站内搜索 URL 模板、社交账号 (sameAs) 等在 2026-06 改为
      直接硬编码到 template.html，不再走上下文。改的时候改 HTML 模板。

    站点域名取自 .env 的 domain（settings.SITE_DOMAIN），支持动态修改；
    若未配置 domain，则回退使用当前请求的 host。

    示例（模板中）：
      {% if show_sidebar %}
        {% include '左侧菜单/左侧菜单.html' %}
      {% endif %}
    """
    # ===== 站点信息配置（从 settings 读取，.env 配置） =====
    site_name = getattr(settings, 'SITE_NAME', '星星云')
    site_domain = getattr(settings, 'SITE_DOMAIN', None) or request.get_host()
    site_protocol = getattr(settings, 'SITE_PROTOCOL', 'https')
    site_alternate_name = getattr(settings, 'SITE_ALTERNATE_NAME', 'XingXing Cloud')

    # 域名：优先取 .env 的 domain，否则回退当前请求 host
    domain = site_domain.replace('https://', '').replace('http://', '').strip('/ ')

    site_origin = '{}://{}'.format(site_protocol, domain) if domain else ''
    canonical_url = '{}{}'.format(site_origin, request.path) if site_origin else request.path

    # Organization Logo：使用站点 Logo
    org_logo_url = '{}{}'.format(
        site_origin,
        '/static/images/logo.png',
    ) if site_origin else '/static/images/logo.png'

    if _get_log_debug():
        logger.debug(
            "SEO 上下文: site_origin={} | site_name={} | org_logo_url={}",
            site_origin, site_name, org_logo_url,
        )

    return {
        # 布局相关
        'sidebar_menu_data': getattr(request, 'sidebar_menu_data', []),
        'sidebar_active_urls': getattr(request, 'sidebar_active_urls', set()),
        'show_sidebar': getattr(request, 'show_sidebar', True),
        # 站点信息
        'site_name': site_name,
        'site_domain': domain,
        'site_origin': site_origin,
        'site_alternate_name': site_alternate_name,
        'org_logo_url': org_logo_url,
        'version': getattr(settings, 'VERSION', '1.0.0'),
        # SEO
        'canonical_url': canonical_url,
    }
