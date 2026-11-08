"""
认证中间件 — 未登录用户自动跳转到登录页。

白名单路径（无需登录即可访问）:
  - /xiaoying_admin/login/
  - /xiaoying_admin/logout/  （已登录时主动退出用）
  - 静态资源路径

IP 白名单（在网站设置中配置）:
  - 只允许指定的 IP 访问登录页和后台
  - 非白名单 IP 返回 403

注意: 此中间件必须放在 AuthenticationMiddleware 之后,
      确保 request.user 已可用。
"""

import re

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.deprecation import MiddlewareMixin

from XiaoYingAdmin.models.user import User


# 无需登录的白名单路径（支持正则）
LOGIN_WHITELIST = [
    r'^/xiaoying_admin/login/.*$',
    r'^/xiaoying_admin/logout/.*$',
    r'^/xiaoying_admin/register/.*$',
    r'^/xiaoying_admin/forgot_password/.*$',
    r'^/static/.*$',
    r'^/media/.*$',
]


class LoginRequiredMiddleware(MiddlewareMixin):
    """确保后台页面必须登录才能访问"""

    def process_request(self, request):
        # =====================================================================
        # 首次运行：系统中无超级管理员时自动创建默认账号
        # =====================================================================
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser('xiaoying', password='xiaoyingadmin')

        path = request.path_info

        # 非后台路径不处理
        if not path.startswith('/xiaoying_admin/'):
            return None

        from XiaoYingAdmin.models.site_settings import SiteSettings
        from XiaoYingAdmin.common.http import get_client_ip, get_request_host, is_host_allowed

        site_settings = SiteSettings.objects.first()

        # =====================================================================
        # IP 白名单检查：如果配置了白名单，非白名单 IP 禁止访问后台
        # =====================================================================
        if site_settings and site_settings.login_ip_whitelist.strip():
            whitelist_ips = [
                ip.strip() for ip in site_settings.login_ip_whitelist.strip().split('\n')
                if ip.strip()
            ]
            if whitelist_ips:
                client_ip = get_client_ip(request)
                if client_ip not in whitelist_ips:
                    return HttpResponse(
                        '<h1 style="text-align:center;margin-top:15%;color:#999;">'
                        '访问被拒绝<br><span style="font-size:14px;">您的 IP 不在访问白名单中</span></h1>',
                        status=403, content_type='text/html; charset=utf-8',
                    )

        # =====================================================================
        # 域名白名单检查：如果配置了允许的域名，非白名单域名禁止访问后台
        # =====================================================================
        if site_settings and site_settings.allowed_admin_domains.strip():
            current_host = get_request_host(request)
            if not is_host_allowed(current_host, site_settings.allowed_admin_domains):
                return HttpResponse(
                    '<h1 style="text-align:center;margin-top:15%;color:#999;">'
                    '访问被拒绝<br><span style="font-size:14px;">'
                    '请通过指定的域名访问后台管理</span></h1>',
                    status=403, content_type='text/html; charset=utf-8',
                )

        # 如果用户已认证,通行
        if request.user.is_authenticated:
            return None

        # 白名单路径通行
        for pattern in LOGIN_WHITELIST:
            if re.match(pattern, path):
                return None

        # 未登录 → 跳转到登录页
        login_url = getattr(settings, 'LOGIN_URL', '/xiaoying_admin/login/')
        return HttpResponseRedirect(login_url)
