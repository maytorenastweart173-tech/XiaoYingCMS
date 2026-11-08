import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from XiaoYingAdmin.common.http import err, parse_json_body
from XiaoYingAdmin.models.seo_cloak import SeoCloakRule, ACTION_CHOICES, REDIRECT_CHOICES, REDIRECT_CODE_CHOICES

# 默认每页条数
DEFAULT_PAGE_SIZE = 15


# =============================================================================
# 页面视图
# =============================================================================

def seo_cloak_view(request):
    """
    斗篷伪装（Cloaking）配置页面。

    核心原理：根据访问者身份（搜索引擎爬虫 vs 真实用户）返回不同的内容。
    对爬虫展示精心优化的 SEO 内容，对真实用户展示正常页面，
    从而在搜索引擎中获得更高排名。
    """
    all_rules = SeoCloakRule.objects.all()
    action_choices_list = [{'value': v, 'label': l} for v, l in ACTION_CHOICES]
    redirect_choices_list = [
        {'code': code, 'label': label, 'desc': desc}
        for code, label, desc in REDIRECT_CHOICES
    ]
    return render(request, 'XiaoYingAdmin/黑帽SEO/斗篷伪装/index.html', {
        'rules': [r.to_dict() for r in all_rules],
        'config': SeoCloakRule.get_singleton().to_dict(),
        'action_choices': action_choices_list,
        'redirect_choices': redirect_choices_list,
        'action_choices_json': json.dumps(action_choices_list, ensure_ascii=False),
        'redirect_choices_json': json.dumps(redirect_choices_list, ensure_ascii=False),
        'default_page_size': DEFAULT_PAGE_SIZE,
    })



# =============================================================================
# AJAX API: 规则 CRUD
# =============================================================================

def seo_cloak_api_list(request):
    """
    返回规则列表（支持分页、搜索、筛选）。

    GET 参数：
      page      — 页码（从 1 开始，默认 1）
      page_size — 每页条数（默认 15）
      search    — 域名搜索关键字
      enabled   — 状态筛选：'all'（全部）, 'enabled'（启用）, 'disabled'（禁用）
    """
    qs = SeoCloakRule.objects.all()

    # 搜索
    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(domain__icontains=search)

    # 筛选
    enabled_filter = request.GET.get('enabled', 'all')
    if enabled_filter == 'enabled':
        qs = qs.filter(is_enabled=True)
    elif enabled_filter == 'disabled':
        qs = qs.filter(is_enabled=False)

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
    total_pages = max(1, -(-total // page_size))  # 向上取整
    start = (page - 1) * page_size
    end = start + page_size

    rules_page = qs[start:end]

    return JsonResponse({
        'ok': True,
        'rules': [r.to_dict() for r in rules_page],
        'page': page,
        'page_size': page_size,
        'total': total,
        'total_pages': total_pages,
    })


def seo_cloak_api_get(request, pk):
    """获取单条规则详情。"""
    try:
        rule = SeoCloakRule.objects.get(pk=pk)
    except SeoCloakRule.DoesNotExist:
        return err('规则不存在')
    return JsonResponse({'ok': True, 'config': rule.to_dict()})


@csrf_exempt
@require_POST
def seo_cloak_config_save(request):
    """
    保存斗篷伪装配置。

    请求: application/json
      {
        "id": null | int,          // 不传或 null 则新增，传则更新
        "domain": "example.com",  // 空字符串 = 全局默认规则
        "is_enabled": true,
        "search_engines": "[\"google.\", \"bing.\"]",
        "spider_keywords": "[\"googlebot\", \"bingbot\"]",
        ...
      }
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    rule_id = body.get('id')

    # ---- 解析绑定域名列表 ----
    # 一条规则可绑定多个域名（domains 数组）；兼容旧的单 domain 字段。
    if 'domains' in body:
        raw_domains = body['domains']
        if not isinstance(raw_domains, list):
            return err('domains 必须为数组')
        domains = []
        seen = set()
        for d in raw_domains:
            d = (str(d) or '').strip().lower()
            if d and d not in seen:
                seen.add(d)
                domains.append(d)
    else:
        d = (body.get('domain') or '').strip().lower()
        domains = [d] if d else []

    # 全局唯一性校验：每个域名（含空域名=全局默认）都不能与其他规则冲突。
    # domains 为空数组表示全局默认规则，同样需要校验。
    check_domains = domains if domains else ['']
    for d in check_domains:
        if SeoCloakRule._domain_occupied(d, exclude_pk=rule_id):
            msg = '全局默认规则已存在' if not d else f'域名 "{d}" 已被其他规则绑定'
            return err(msg)

    if rule_id:
        try:
            rule = SeoCloakRule.objects.get(pk=rule_id)
        except SeoCloakRule.DoesNotExist:
            return err('规则不存在')
    else:
        rule = SeoCloakRule()

    # 开关
    if 'is_enabled' in body:
        rule.is_enabled = bool(body['is_enabled'])

    # 搜索引擎列表
    if 'search_engines' in body:
        try:
            eng = json.loads(body['search_engines']) if isinstance(body['search_engines'], str) else body['search_engines']
            if not isinstance(eng, list):
                return err('search_engines 必须为数组')
            rule.search_engines = json.dumps(eng, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return err('search_engines JSON 格式错误')

    # 爬虫关键字列表
    if 'spider_keywords' in body:
        try:
            kw = json.loads(body['spider_keywords']) if isinstance(body['spider_keywords'], str) else body['spider_keywords']
            if not isinstance(kw, list):
                return err('spider_keywords 必须为数组')
            rule.spider_keywords = json.dumps(kw, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return err('spider_keywords JSON 格式错误')

    # 行为策略
    valid_actions = set(v for v, _ in ACTION_CHOICES)
    for field in ('spider_action', 'search_action', 'direct_action'):
        if field in body:
            val = str(body[field]).strip()
            if val not in valid_actions:
                return err(f'{field} 值非法，可选：{", ".join(sorted(valid_actions))}')
            setattr(rule, field, val)

    # 重定向状态码
    if 'redirect_status_code' in body:
        code = int(body['redirect_status_code'])
        valid_codes = set(c for c, _ in REDIRECT_CODE_CHOICES)
        if code not in valid_codes:
            return err(f'redirect_status_code 非法，可选：{sorted(valid_codes)}')
        rule.redirect_status_code = code

    # 重定向 URL（自动清除多余的反引号、引号、空格）
    for field in ('spider_redirect_url', 'search_redirect_url', 'direct_redirect_url'):
        if field in body:
            val = (body[field] or '').strip().strip('`"\' ')
            setattr(rule, field, val)

    # 其他文本字段
    for field in ('whitelist_paths', 'seo_content', 'cloak_content', 'remark'):
        if field in body:
            val = (body[field] or '')
            # remark 限制最大 1000 字符
            if field == 'remark' and len(val) > 1000:
                return err('备注长度不能超过 1000 字符')
            setattr(rule, field, val)

    # 绑定域名：第一个为主域名（domain 字段），全部域名存入 domains 列表
    rule.domain = domains[0] if domains else ''
    rule.domains = json.dumps(domains, ensure_ascii=False)

    rule.save()

    return JsonResponse({
        'message': '配置已保存',
        'config': rule.to_dict(),
    })


@csrf_exempt
@require_POST
def seo_cloak_api_delete(request, pk):
    """删除指定规则（不允许删除默认规则）。"""
    try:
        rule = SeoCloakRule.objects.get(pk=pk)
    except SeoCloakRule.DoesNotExist:
        return err('规则不存在')

    if not rule.domain:
        return err('不能删除全局默认规则')

    rule.delete()
    return JsonResponse({'message': '规则已删除'})
