"""
操作日志中间件 — 自动记录后台用户的所有关键操作。

设计思路：
  不再依赖硬编码的 PATH_TARGET_MAP，而是利用 Django 的 request.resolver_match.url_name
  自动推断操作对象类型和操作类型。新加功能无需修改此文件。

功能：
  1. 中间件自动记录所有已登录用户对 /xiaoying_admin/ 的写操作（POST/PUT/DELETE）
  2. 暴露 log_operation() 函数供视图手动调用（带额外上下文）

使用方式（视图手动调用）:
  from XiaoYingAdmin.middleware.operation_log import log_operation
  log_operation(request, 'create', 'User', user.pk, f'用户 {user.username}',
                detail={'field': 'value'})
"""

import json
from django.utils.deprecation import MiddlewareMixin

from XiaoYingAdmin.common.http import get_client_ip
from XiaoYingAdmin.models.operation_log import OperationLog
from XiaoYingAdmin.utils.backup import check_and_auto_backup


# =============================================================================
# 配置
# =============================================================================

# 不记录的路径前缀（白名单：轮询、只读列表 API 等）
SKIP_PREFIXES = [
    '/xiaoying_admin/api/generate/progress/',
    '/xiaoying_admin/api/login_logs/list/',
    '/xiaoying_admin/api/operation_logs/list/',
]

# 不记录的 HTTP 方法（只读操作）
SKIP_METHODS = ('GET', 'HEAD', 'OPTIONS')

# URL name → (target_type, target_repr) 的精确映射
# 仅用于 URL name 命名不规范的路径，大部分路径会自动推断
URL_NAME_TARGET_MAP = {
    'site_settings':           ('SiteSettings', '网站设置'),
    'spider_logs_api_clear':   ('SpiderAccessLog', '清空蜘蛛日志'),
    'spider_logs_api_export':  ('SpiderAccessLog', '导出蜘蛛日志'),
    'seo_cloak_config_save':   ('SeoCloakRule', '斗篷伪装配置'),
}

# 类型别名：URL name 推断出的类型名 → 实际模型名
TYPE_ALIASES = {
    'SavedPage': 'GeneratedPage',
    'Spider': 'SpiderAccessLog',
    'Page': 'PageGeneration',
    'Seo': 'SeoCloakRule',
    'Auth': 'Auth',
    'Loginlog': 'LoginLog',
    'Operationlog': 'OperationLog',
    'Prompt': 'Prompt',
    'User': 'User',
    'Sitesetting': 'SiteSettings',
}

# 类型 → 中文描述映射（用于 target_repr）
TYPE_REPR_MAP = {
    'Prompt': '提示词',
    'GeneratedPage': '生成页面',
    'SiteSettings': '网站设置',
    'User': '用户',
    'SpiderAccessLog': '蜘蛛日志',
    'SeoCloakRule': '斗篷伪装',
    'PageGeneration': '页面生成',
    'LoginLog': '登录日志',
    'OperationLog': '操作日志',
    'Auth': '认证',
}

# action 关键词 → 标准 action 映射
ACTION_KEYWORD_MAP = {
    'save': 'create', 'create': 'create',
    'update': 'update', 'edit': 'update', 'set': 'update',
    'delete': 'delete', 'remove': 'delete', 'clear': 'delete',
    'activate': 'update', 'toggle': 'update', 'switch': 'update',
    'export': 'export',
    'start': 'create', 'stop': 'update', 'generate': 'create',
    'login': 'login', 'logout': 'logout', 'register': 'create',
}

# 从 URL name 中识别 action 的关键词集合
ACTION_KEYWORDS = set(ACTION_KEYWORD_MAP.keys())


# =============================================================================
# 辅助函数
# =============================================================================


def _try_parse_json_body(request):
    """尝试解析 JSON 请求体，失败返回 None"""
    content_type = request.META.get('CONTENT_TYPE', '')
    if 'application/json' in content_type:
        try:
            return json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None
    return None


def _infer_from_url_name(url_name):
    """
    从 URL name 自动推断 target_type 和 action。

    规则：
      1. 先查 URL_NAME_TARGET_MAP（手动映射）
      2. 去掉 api_ 前缀
      3. 按 _ 分割，最后一段若是 action 关键词则剥离
      4. 剩余部分拼成 PascalCase 作为 target_type
      5. 应用 TYPE_ALIASES 别名映射

    返回 (target_type, target_repr, action)
    """
    if not url_name:
        return ('', '', 'update')

    # 1. 精确映射
    if url_name in URL_NAME_TARGET_MAP:
        ttype, trepr = URL_NAME_TARGET_MAP[url_name]
        return (ttype, trepr, None)  # action=None 表示由中间件自动推断

    # 2. 去掉 api_ 前缀
    name = url_name
    if name.startswith('api_'):
        name = name[4:]

    # 3. 按 _ 分割，识别 action 关键词
    parts = name.split('_')
    action = None
    entity_parts = list(parts)

    for i, part in enumerate(parts):
        if part in ACTION_KEYWORDS:
            action = ACTION_KEYWORD_MAP[part]
            entity_parts = parts[:i]
            break

    # 4. 拼成 PascalCase
    if not entity_parts:
        entity_parts = parts[:1] if parts else ['Unknown']
    target_type = ''.join(p.capitalize() for p in entity_parts)

    # 5. 别名映射
    target_type = TYPE_ALIASES.get(target_type, target_type)

    # 6. target_repr
    target_repr = TYPE_REPR_MAP.get(target_type, target_type)

    return (target_type, target_repr, action)


def _extract_post_desc(request):
    """从 POST 数据或 JSON 体中提取主要描述文本"""
    # 先尝试 JSON body
    json_data = _try_parse_json_body(request)
    if json_data:
        for field in ('name', 'title', 'content', 'category', 'domain', 'description'):
            val = json_data.get(field, '')
            if val and isinstance(val, str) and len(val) < 100:
                return val

    # 再试 POST 表单字段
    for field in ('username', 'name', 'title', 'content'):
        val = request.POST.get(field, '').strip()
        if val and len(val) < 100:
            return val

    return ''


def log_operation(request, action, target_type='', target_id='',
                  target_repr='', detail=None):
    """
    记录一条操作日志（供视图手动调用）。

    参数:
      request:      HttpRequest
      action:       create/update/delete/login/logout/export/other
      target_type:  操作对象类型（如 User、SeoCloakRule）
      target_id:    操作对象 ID
      target_repr:  操作描述文字
      detail:       dict，附加详情
    """
    if not request.user.is_authenticated:
        return

    OperationLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        username=request.user.username,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else '',
        target_repr=target_repr,
        detail=detail,
        method=request.method,
        path=request.path_info,
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
    )


# =============================================================================
# 中间件
# =============================================================================

class OperationLogMiddleware(MiddlewareMixin):
    """
    自动记录后台用户的关键操作。

    工作原理：
      - 在 process_response 阶段（不阻塞请求），检查是否满足记录条件
      - 使用 request.resolver_match.url_name 推断操作对象类型和操作类型
      - 所有 /xiaoying_admin/ 下的写操作都会被自动记录
      - 新加功能无需修改此文件，只需在 urls.py 中定义合理的 name 即可
    """

    def process_response(self, request, response):
        """响应返回后记录操作日志（不阻塞请求）"""
        # 仅记录已登录用户
        if not request.user.is_authenticated:
            return response

        path = request.path_info

        # 仅记录 /xiaoying_admin/ 下的请求
        if not path.startswith('/xiaoying_admin/'):
            return response

        # 跳过白名单路径（轮询等）
        for prefix in SKIP_PREFIXES:
            if path.startswith(prefix):
                return response

        # 跳过只读方法
        if request.method in SKIP_METHODS:
            return response

        # 跳过类 GET 的只读 API（没有 resolver_match 或 name 为空）
        resolver = getattr(request, 'resolver_match', None)
        url_name = getattr(resolver, 'url_name', '') if resolver else ''

        # =====================================================================
        # 推断操作信息
        # =====================================================================
        target_type, target_repr, inferred_action = _infer_from_url_name(url_name)

        # action：优先使用推断值，否则从 HTTP 方法推断
        if inferred_action:
            action = inferred_action
        else:
            action = _infer_action_from_method(request.method, path)

        # 从 POST/JSON 数据提取描述文本
        post_desc = _extract_post_desc(request)
        if post_desc:
            target_repr = f'{target_repr}「{post_desc}」' if target_repr else post_desc

        # =====================================================================
        # 构建详情（捕获请求数据 + 变更摘要）
        # =====================================================================
        detail = {'method': request.method}
        submitted = {}
        if request.method in ('POST', 'PUT', 'PATCH'):
            json_data = _try_parse_json_body(request)
            if json_data:
                # JSON 请求：排除敏感字段
                submitted = {k: v for k, v in json_data.items()
                             if k not in ('password', 'new_password', 'old_password',
                                          'password2', 'csrfmiddlewaretoken')}
                detail['body'] = dict(submitted)
            else:
                # 表单请求
                submitted = {k: v for k, v in request.POST.items()
                             if k not in ('csrfmiddlewaretoken', 'password',
                                          'new_password', 'old_password',
                                          'password2')}
                detail['post_fields'] = dict(submitted)
            # 构建变更摘要：将提交的数据中最有意义的字段提取出来
            if submitted:
                changes = {}
                change_hints = {
                    'is_active': '启用状态', 'is_staff': '管理员权限',
                    'is_superuser': '超级管理员', 'domain': '绑定域名',
                    'name': '名称', 'content': '内容', 'description': '描述',
                    'category': '分类', 'input_content': '需求描述',
                    'html_content': 'HTML内容', 'registration_enabled': '开放注册',
                    'statistics_code': '统计代码', 'username': '用户名',
                    'email': '邮箱',
                }
                for k, v in submitted.items():
                    label = change_hints.get(k, k)
                    changes[label] = {'new': v, 'old': None}
                detail['changes'] = changes

        # =====================================================================
        # 写入数据库（复用 log_operation 统一入口）
        # =====================================================================
        log_operation(request, action, target_type, target_id='',
                      target_repr=target_repr or path, detail=detail)

        # =====================================================================
        # 自动备份阈值检查（写入一条后判断是否达到阈值）
        # =====================================================================
        check_and_auto_backup(
            OperationLog, 'op_logs', 'auto_backup_operation_threshold',
        )

        return response


def _infer_action_from_method(method, path):
    """根据 HTTP 方法和路径推断操作类型（兜底方案）"""
    if method == 'POST':
        if '/delete/' in path:
            return 'delete'
        if '/create/' in path or '/save/' in path:
            return 'create'
        return 'update'
    if method == 'PUT':
        return 'update'
    if method == 'DELETE':
        return 'delete'
    return 'other'
