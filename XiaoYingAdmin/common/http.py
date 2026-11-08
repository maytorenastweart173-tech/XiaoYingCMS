"""
通用 HTTP / JSON 视图辅助工具。

为什么需要：
  views 层中存在大量重复的样板代码——解析请求体 JSON、统一错误响应、
  按主键取对象（不存在则返回 404）、批量序列化时间字段等。本模块把这些
  逻辑收敛到一处，使视图函数只关注业务本身，减少冗余、统一行为。

设计原则：
  - 不改变任何现有接口的返回结构（仍是裸 JSON），仅抽取重复逻辑。
  - 全部为纯函数 / 轻量包装，无副作用，便于在任意视图中复用。

使用示例：
    from XiaoYingAdmin.common.http import parse_json_body, err, get_or_404

    @require_POST
    def my_view(request):
        body, error = parse_json_body(request)
        if error is not None:
            return error                       # 已是 JsonResponse(400)
        obj, error = get_or_404(MyModel, id=body.get('id'))
        if error is not None:
            return error                       # 已是 JsonResponse(404)
        ...
"""

import json

from django.http import JsonResponse, HttpRequest

# 统一的日期时间格式 —— 全项目共用，避免在各视图里硬编码格式字符串
DATETIME_FMT = '%Y-%m-%d %H:%M:%S'
DATETIME_FMT_SHORT = '%Y-%m-%d %H:%M'


def err(message: str, status: int = 400) -> JsonResponse:
    """返回统一的错误响应：{"error": message}（保持现有裸结构）。"""
    return JsonResponse({'error': message}, status=status)


def parse_json_body(request):
    """
    解析请求体为 JSON 字典。

    返回 (data, error_response)：
      - 成功：(dict, None)
      - 失败：(None, JsonResponse) —— 调用方直接 return 该响应即可

    用法见模块 docstring。
    """
    try:
        return json.loads(request.body.decode('utf-8')), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, err('请求体必须为 JSON', status=400)


def get_or_404(model, *, not_found_msg: str = None, **lookup):
    """
    按条件获取单个对象，不存在 / 主键非法时返回 404 错误响应。

    参数：
      model: Django 模型类
      not_found_msg: 自定义“不存在”提示，默认根据 verbose_name 生成
      **lookup: 传给 .get() 的查询条件，如 id=1 / task_id='...'

    返回 (obj, error_response)：
      - 成功：(实例, None)
      - 失败：(None, JsonResponse(404))
    """
    if not_found_msg is None:
        not_found_msg = f'{model._meta.verbose_name}不存在'
    try:
        return model.objects.get(**lookup), None
    except (model.DoesNotExist, ValueError):
        return None, err(not_found_msg, status=404)


def fmt_dt(value, fmt: str = DATETIME_FMT) -> str:
    """安全格式化 datetime 字段；为空时返回空串。"""
    return value.strftime(fmt) if value else ''


def get_client_ip(request: HttpRequest) -> str:
    """获取客户端真实 IP（支持代理透传）。"""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def get_request_host(request: HttpRequest) -> str:
    """获取请求的 Host 域名（不含端口）。"""
    raw_host = request.META.get('HTTP_HOST', '')
    return raw_host.split(':')[0].strip().lower()


def is_host_allowed(host: str, domain_list_text: str) -> bool:
    """
    检查 Host 是否在域名白名单中。
    支持：
      - 精确域名: xiaoyingclub.com
      - 通配符: *.example.com
      - 多行文本，每行一个域名
    """
    if not domain_list_text or not domain_list_text.strip():
        return True  # 未配置白名单 = 全部允许
    host = host.lower()
    for line in domain_list_text.strip().split('\n'):
        pattern = line.strip().lower()
        if not pattern:
            continue
        if pattern == host:
            return True
        if pattern.startswith('*.') and host.endswith(pattern[1:]):
            return True
    return False
