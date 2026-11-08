# -*- coding: utf-8 -*-
"""静态文件路由 — 白名单路径管理视图"""

import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q

from XiaoYingAdmin.models.static_file_route import StaticFileRoute

DEFAULT_PAGE_SIZE = 15


def static_file_route_view(request):
    """页面视图：静态文件路由管理"""
    return render(request, 'XiaoYingAdmin/安全防护/静态文件路由.html', {
        'default_page_size': DEFAULT_PAGE_SIZE,
    })


def _to_dict(rule):
    """将 StaticFileRoute 转为 dict"""
    return {
        'id': rule.id,
        'path': rule.path,
        'is_active': rule.is_active,
        'description': rule.description,
        'custom_not_found_msg': rule.custom_not_found_msg,
        'create_time': rule.create_time.strftime('%Y-%m-%d %H:%M:%S') if rule.create_time else '',
    }


@require_GET
def static_file_route_api_list(request):
    """获取规则列表（支持分页、搜索）"""
    qs = StaticFileRoute.objects.all()

    # 搜索
    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(Q(path__icontains=search) | Q(description__icontains=search))

    # 分页
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(1, min(100, int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))))
    except (ValueError, TypeError):
        page_size = DEFAULT_PAGE_SIZE

    total = qs.count()
    total_pages = max(1, -(-total // page_size))
    start = (page - 1) * page_size
    end = start + page_size

    rules_page = qs[start:end]

    return JsonResponse({
        'items': [_to_dict(r) for r in rules_page],
        'page': page,
        'page_size': page_size,
        'total': total,
        'total_pages': total_pages,
    })


@csrf_exempt
@require_POST
def static_file_route_api_save(request):
    """创建/更新规则"""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '无效的JSON'})

    rule_id = body.get('id')
    path = (body.get('path') or '').strip().lstrip('/')  # 去掉开头 /
    if not path:
        return JsonResponse({'error': '文件路径不能为空'})

    is_active = body.get('is_active', True)
    description = (body.get('description') or '').strip()
    custom_not_found_msg = (body.get('custom_not_found_msg') or '').strip()

    if rule_id:
        # 更新
        try:
            rule = StaticFileRoute.objects.get(pk=rule_id)
        except StaticFileRoute.DoesNotExist:
            return JsonResponse({'error': '规则不存在'})
        # 检查路径冲突
        if StaticFileRoute.objects.filter(path=path).exclude(pk=rule_id).exists():
            return JsonResponse({'error': f'路径 "{path}" 已存在'})
        rule.path = path
        rule.is_active = bool(is_active)
        rule.description = description
        rule.custom_not_found_msg = custom_not_found_msg
        rule.save()
        # 记录操作日志
        from XiaoYingAdmin.middleware.operation_log import log_operation
        log_operation(request, 'static_file_route_save', f'更新静态文件路由: {path}', 'StaticFileRoute')
        return JsonResponse({'message': '更新成功', 'rule': _to_dict(rule)})
    else:
        # 新增
        if StaticFileRoute.objects.filter(path=path).exists():
            return JsonResponse({'error': f'路径 "{path}" 已存在'})
        rule = StaticFileRoute.objects.create(
            path=path,
            is_active=bool(is_active),
            description=description,
            custom_not_found_msg=custom_not_found_msg,
        )
        from XiaoYingAdmin.middleware.operation_log import log_operation
        log_operation(request, 'static_file_route_create', f'新增静态文件路由: {path}', 'StaticFileRoute')
        return JsonResponse({'message': '创建成功', 'rule': _to_dict(rule)})


@csrf_exempt
@require_POST
def static_file_route_api_delete(request):
    """删除规则"""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '无效的JSON'})

    rule_id = body.get('id')
    if not rule_id:
        return JsonResponse({'error': '缺少ID'})

    try:
        rule = StaticFileRoute.objects.get(pk=rule_id)
        path = rule.path
        rule.delete()
        from XiaoYingAdmin.middleware.operation_log import log_operation
        log_operation(request, 'static_file_route_delete', f'删除静态文件路由: {path}', 'StaticFileRoute')
        return JsonResponse({'message': '删除成功'})
    except StaticFileRoute.DoesNotExist:
        return JsonResponse({'error': '规则不存在'})


@csrf_exempt
@require_POST
def static_file_route_api_toggle(request):
    """切换启用/禁用"""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '无效的JSON'})

    rule_id = body.get('id')
    if not rule_id:
        return JsonResponse({'error': '缺少ID'})

    try:
        rule = StaticFileRoute.objects.get(pk=rule_id)
        rule.is_active = not rule.is_active
        rule.save()
        status = '启用' if rule.is_active else '禁用'
        from XiaoYingAdmin.middleware.operation_log import log_operation
        log_operation(request, 'static_file_route_toggle', f'{status}静态文件路由: {rule.path}', 'StaticFileRoute')
        return JsonResponse({'message': f'已{status}', 'is_active': rule.is_active})
    except StaticFileRoute.DoesNotExist:
        return JsonResponse({'error': '规则不存在'})
