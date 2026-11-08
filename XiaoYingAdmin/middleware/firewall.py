"""
防火墙中间件 — IP/页面黑名单拦截。

放置位置：必须放在 MIDDLEWARE 第一层（最外层），位于 SpiderLogMiddleware 之前，
这样在白名单 IP 命中时也能被蜘蛛日志记录，被拦截的请求在 SpiderLogMiddleware
的外层 response 阶段仍能被捕获。
"""
import logging
from django.db.utils import OperationalError
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.utils.deprecation import MiddlewareMixin

from XiaoYingAdmin.models.firewall import FirewallRule


logger = logging.getLogger(__name__)


class FirewallMiddleware(MiddlewareMixin):
    """根据 FirewallRule 规则拦截请求"""

    def process_request(self, request):
        # 跳过带 fireflykey 内部健康检查
        if request.META.get('HTTP_X_FIREWALL_CHECK') == '1':
            return None

        # 若数据库表尚未创建（首次部署未 migrate），静默放行
        rules = self._get_active_rules()
        if rules is None:
            return None

        client_ip = self._get_ip(request)
        path = request.path_info

        # 1. 检查 IP 白名单（优先级最高）
        whitelist_ips = [r for r in rules if r.rule_type == 'ip_whitelist']
        for rule in whitelist_ips:
            if self._match_ip(client_ip, rule.value):
                return None  # 白名单放行

        # 2. 检查 IP 黑名单
        for rule in rules:
            if rule.rule_type == 'ip_block' and self._match_ip(client_ip, rule.value):
                rule.hit()
                return self._build_block_response(rule)

        # 3. 检查路径黑名单
        for rule in rules:
            if rule.rule_type == 'page_block' and self._match_path(path, rule.value):
                rule.hit()
                return self._build_block_response(rule)

        return None

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _get_ip(request) -> str:
        """获取客户端 IP"""
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    @staticmethod
    def _match_ip(client_ip: str, rule_value: str) -> bool:
        """IP 匹配（支持精确匹配、localhost 别名、CIDR 前缀匹配如 192.168.1.）"""
        if not client_ip:
            return False
        rule_value = rule_value.strip()
        if not rule_value:
            return False
        # localhost 别名 → 匹配 127.0.0.1 / ::1
        if rule_value.lower() == 'localhost':
            return client_ip in ('127.0.0.1', '::1')
        if '/' in rule_value:
            # CIDR 精确匹配（简化版，仅匹配 /24）
            try:
                ip_prefix = rule_value.rsplit('/', 1)[0]
                return client_ip.startswith(ip_prefix.rstrip('.'))
            except (ValueError, IndexError):
                return False
        if '.' in rule_value and rule_value.endswith('.'):
            # 前缀匹配：192.168.1.
            return client_ip.startswith(rule_value)
        if '.*' in rule_value:
            # 通配符：192.168.*
            return client_ip.startswith(rule_value.replace('.*', ''))
        # 精确匹配
        return client_ip == rule_value

    @staticmethod
    def _match_path(path: str, rule_value: str) -> bool:
        """路径匹配（支持前缀匹配）"""
        rule_value = rule_value.strip()
        if not rule_value:
            return False
        # 精确匹配
        if path == rule_value:
            return True
        # 前缀匹配（如果规则以 / 开头且不以 * 结尾，做前缀匹配）
        if rule_value.startswith('/') and not rule_value.endswith('*'):
            return path.startswith(rule_value)
        # 通配符：/admin/*
        if rule_value.endswith('*'):
            return path.startswith(rule_value[:-1])
        return False

    @staticmethod
    def _build_block_response(rule):
        """根据规则的 response_type 构建拦截响应"""
        if rule.response_type == 'custom_html':
            return HttpResponseForbidden(rule.custom_content or '<h1>403 Forbidden</h1>')

        if rule.response_type == 'custom_js':
            html = f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><script>{rule.custom_content}</script></body></html>'
            return HttpResponse(html)

        if rule.response_type == 'redirect' and rule.redirect_url:
            return HttpResponseRedirect(rule.redirect_url)

        # 默认：403
        return HttpResponseForbidden(
            '<h1 style="text-align:center;margin-top:15%;color:#666;">'
            '403 Forbidden — 访问被防火墙拦截</h1>'
        )

    @staticmethod
    def _get_active_rules():
        """获取所有启用规则；若表不存在则返回 None（静默降级）"""
        try:
            return list(FirewallRule.objects.filter(is_active=True).only(
                'id', 'rule_type', 'value', 'response_type',
                'custom_content', 'redirect_url',
            ))
        except OperationalError:
            return None
