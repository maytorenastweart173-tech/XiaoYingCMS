"""
蜘蛛日志 — 忽略路径管理

独立的视图文件，管理 SpiderLogConfig.ignore_paths 字段。
避免与 spider/logs.py 主视图混杂在一起。
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from XiaoYingAdmin.common.http import parse_json_body, err
from XiaoYingAdmin.models.spider_log import SpiderLogConfig


TEMPLATE = 'XiaoYingAdmin/蜘蛛管理/蜘蛛日志/路径过滤/index.html'


@login_required
@require_GET
def ignore_paths_view(request):
    """忽略路径管理页面"""
    config = SpiderLogConfig.get_singleton()
    paths = _parse_paths(config.ignore_paths)
    return render(request, TEMPLATE, {
        'paths': paths,
    })


# =============================================================================
# AJAX API
# =============================================================================

@csrf_exempt
@login_required
@require_GET
def ignore_paths_api_list(request):
    """获取当前忽略路径列表"""
    config = SpiderLogConfig.get_singleton()
    paths = _parse_paths(config.ignore_paths)
    return JsonResponse({'ok': True, 'paths': paths})


@csrf_exempt
@login_required
@require_POST
def ignore_paths_api_save(request):
    """
    保存忽略路径列表。

    请求: application/json
      {"paths": ["/favicon.ico", "/robots.txt", "/api/health/"]}

    会保留原有的注释行。
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    new_paths = body.get('paths', [])
    if not isinstance(new_paths, list):
        return err('paths 必须是一个数组')

    # 去重、去空、去无效值
    cleaned = []
    seen = set()
    for p in new_paths:
        p = p.strip()
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        cleaned.append(p)

    config = SpiderLogConfig.get_singleton()
    config.ignore_paths = '\n'.join(cleaned)
    config.save(update_fields=['ignore_paths', 'updated_time'])
    return JsonResponse({'ok': True, 'message': '已保存', 'count': len(cleaned)})


# =============================================================================
# 辅助
# =============================================================================

def _parse_paths(text: str) -> list:
    """将 ignore_paths 文本解析为路径列表（保留注释行）。"""
    if not text or not text.strip():
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]
