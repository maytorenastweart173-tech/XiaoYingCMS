"""
防火墙管理视图 — IP/页面黑名单 CRUD + 规则测试
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_GET

from XiaoYingAdmin.common.http import parse_json_body, err, get_or_404
from XiaoYingAdmin.middleware.operation_log import log_operation
from XiaoYingAdmin.models.firewall import FirewallRule


FIREWALL_TEMPLATE = 'XiaoYingAdmin/防火墙/防火墙管理.html'


@login_required
@require_http_methods(['GET'])
def firewall_view(request):
    """防火墙管理页面"""
    if not request.user.is_superuser:
        return render(request, FIREWALL_TEMPLATE, {
            'error': '权限不足，仅超级管理员可管理防火墙',
            'rules': [],
        })
    rules = FirewallRule.objects.all().order_by('rule_type', '-is_active', '-create_time')
    return render(request, FIREWALL_TEMPLATE, {
        'error': None,
        'rules': list(rules),
    })


# =============================================================================
# AJAX API
# =============================================================================

@csrf_exempt
@require_GET
def firewall_api_list(request):
    """获取所有规则列表"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})
    rules = FirewallRule.objects.all().order_by('rule_type', '-is_active', '-create_time')
    return JsonResponse({
        'ok': True,
        'rules': [{
            'id': r.id,
            'rule_type': r.rule_type,
            'rule_type_label': r.get_rule_type_display(),
            'value': r.value,
            'is_active': r.is_active,
            'response_type': r.response_type,
            'response_type_label': r.get_response_type_display(),
            'custom_content': r.custom_content,
            'redirect_url': r.redirect_url,
            'description': r.description,
            'hit_count': r.hit_count,
            'last_hit_at': r.last_hit_at.strftime('%Y-%m-%d %H:%M:%S') if r.last_hit_at else '',
            'create_time': r.create_time.strftime('%Y-%m-%d %H:%M:%S') if r.create_time else '',
        } for r in rules],
    })


@csrf_exempt
@require_POST
def firewall_api_save(request):
    """创建/更新规则"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    body, error = parse_json_body(request)
    if error is not None:
        return error

    rule_id = body.get('id')
    rule_type = (body.get('rule_type') or '').strip()
    value = (body.get('value') or '').strip()
    is_active = body.get('is_active', True)
    response_type = (body.get('response_type') or 'forbidden').strip()
    custom_content = (body.get('custom_content') or '').strip()
    redirect_url = (body.get('redirect_url') or '').strip()
    description = (body.get('description') or '').strip()

    if not rule_type:
        return err('请选择规则类型')
    if rule_type not in ('ip_block', 'page_block', 'ip_whitelist'):
        return err('规则类型无效')
    if not value:
        return err('请输入匹配值')
    if response_type not in ('forbidden', 'custom_html', 'custom_js', 'redirect'):
        return err('拦截响应类型无效')

    if rule_id:
        # 更新
        rule = FirewallRule.objects.filter(pk=rule_id).first()
        if not rule:
            return err('规则不存在', status=404)
        old_value = rule.value
        rule.rule_type = rule_type
        rule.value = value
        rule.is_active = is_active
        rule.response_type = response_type
        rule.custom_content = custom_content
        rule.redirect_url = redirect_url
        rule.description = description
        rule.save(update_fields=[
            'rule_type', 'value', 'is_active', 'response_type',
            'custom_content', 'redirect_url', 'description', 'updated_time',
        ])
        log_operation(request, 'update', 'FirewallRule', rule.id,
                      f'防火墙规则「{old_value}」→「{value}」')
        return JsonResponse({'ok': True, 'message': '规则已更新'})
    else:
        # 创建 — 先检查是否已存在相同规则（防止重复创建）
        existing = FirewallRule.objects.filter(rule_type=rule_type, value=value).first()
        if existing:
            return JsonResponse({
                'ok': False,
                'error': f'规则「{value}」已存在（{existing.get_rule_type_display()}），请勿重复添加',
            })
        # 创建
        rule = FirewallRule.objects.create(
            rule_type=rule_type,
            value=value,
            is_active=is_active,
            response_type=response_type,
            custom_content=custom_content,
            redirect_url=redirect_url,
            description=description,
        )
        log_operation(request, 'create', 'FirewallRule', rule.id,
                      f'防火墙规则「{value}」({rule.get_rule_type_display()})')
        return JsonResponse({'ok': True, 'message': '规则已创建', 'id': rule.id})


@csrf_exempt
@require_POST
def firewall_api_toggle(request, pk):
    """切换规则启用/禁用"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    rule = FirewallRule.objects.filter(pk=pk).first()
    if not rule:
        return err('规则不存在', status=404)

    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active', 'updated_time'])

    status_text = '启用' if rule.is_active else '禁用'
    log_operation(request, 'update', 'FirewallRule', rule.id,
                  f'防火墙规则「{rule.value}」→ {status_text}')
    return JsonResponse({'ok': True, 'is_active': rule.is_active, 'message': f'规则已{status_text}'})


@csrf_exempt
@require_POST
def firewall_api_delete(request, pk):
    """删除规则"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    rule = FirewallRule.objects.filter(pk=pk).first()
    if not rule:
        return err('规则不存在', status=404)

    val = rule.value
    rule.delete()
    log_operation(request, 'delete', 'FirewallRule', pk,
                  f'防火墙规则「{val}」')
    return JsonResponse({'ok': True, 'message': '规则已删除'})


@csrf_exempt
@require_POST
def firewall_api_test(request):
    """测试一条规则是否匹配当前请求"""
    body, error = parse_json_body(request)
    if error is not None:
        return error

    test_ip = (body.get('test_ip') or '').strip()
    test_path = (body.get('test_path') or '').strip()
    rule_type = (body.get('rule_type') or '').strip()
    rule_value = (body.get('value') or '').strip()

    if not rule_value:
        return err('请输入匹配值')

    if rule_type == 'ip_block' or rule_type == 'ip_whitelist':
        if not test_ip:
            return err('请输入测试 IP')
        from XiaoYingAdmin.middleware.firewall import FirewallMiddleware
        matched = FirewallMiddleware._match_ip(test_ip, rule_value)
    elif rule_type == 'page_block':
        if not test_path:
            return err('请输入测试路径')
        from XiaoYingAdmin.middleware.firewall import FirewallMiddleware
        matched = FirewallMiddleware._match_path(test_path, rule_value)
    else:
        return err('暂不支持该类型的测试')

    return JsonResponse({
        'ok': True,
        'matched': matched,
        'message': '✅ 匹配成功，该规则会拦截此请求' if matched else '❌ 不匹配',
    })
