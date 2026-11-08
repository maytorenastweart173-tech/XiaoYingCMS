"""
页面树形结构 API — 分类管理 + 树形数据 + 页面分类分配。

提供以下接口：
  - 分类 CRUD（增删改查）
  - 为页面设置分类
  - 获取完整的树形数据（分类 → 根域名 → 域名 → 页面）
"""

# =============================================================================
# 导入
# =============================================================================

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from XiaoYingAdmin.common.http import parse_json_body, err
from XiaoYingAdmin.middleware.operation_log import log_operation
from XiaoYingAdmin.models.generated_page import GeneratedPage
from XiaoYingAdmin.models.page_category import PageCategory
from XiaoYingAdmin.utils.domain_utils import group_domains_by_root, _find_parent_domain


# =============================================================================
# 分类 CRUD
# =============================================================================


def _category_to_dict(cat: PageCategory) -> dict:
    """将分类对象转为字典。"""
    return {
        'id': cat.id,
        'name': cat.name,
        'description': cat.description,
        'sort_order': cat.sort_order,
    }


@require_GET
def page_category_list(request):
    """
    GET /api/pages/categories/
    获取所有分类列表，按 sort_order → create_time 排序。
    """
    cats = PageCategory.objects.all().order_by('sort_order', 'create_time')
    return JsonResponse({
        'categories': [_category_to_dict(c) for c in cats],
    })


@csrf_exempt
@require_POST
def page_category_create(request):
    """
    POST /api/pages/categories/create/
    创建新分类。

    请求: application/json
      {"name": "分类名称"}                       // 必填
      {"name": "分类名称", "description": "描述"}  // 可选
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    name = (body.get('name') or '').strip()
    if not name:
        return err('分类名称不能为空')
    if len(name) > 64:
        return err('分类名称不能超过 64 个字符')

    if PageCategory.objects.filter(name=name).exists():
        return err(f'分类「{name}」已存在')

    cat = PageCategory(
        name=name,
        description=(body.get('description') or '').strip(),
        sort_order=body.get('sort_order', 0),
        created_by=request.user if request.user.is_authenticated else None,
    )
    cat.save()

    log_operation(request, 'create', 'PageCategory', cat.id,
                  f'创建页面分类「{cat.name}」')

    return JsonResponse({'message': '分类创建成功', 'category': _category_to_dict(cat)})


@csrf_exempt
@require_POST
def page_category_update(request):
    """
    POST /api/pages/categories/update/
    更新分类名称/描述/排序。

    请求: application/json
      {"id": 1, "name": "新名称", "description": "新描述"}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    cat_id = body.get('id')
    if not cat_id:
        return err('缺少分类 ID')

    try:
        cat = PageCategory.objects.get(id=cat_id)
    except PageCategory.DoesNotExist:
        return err('分类不存在')

    old_name = cat.name
    changed = []

    if 'name' in body:
        name = (body['name'] or '').strip()
        if not name:
            return err('分类名称不能为空')
        if name != cat.name and PageCategory.objects.filter(name=name).exists():
            return err(f'分类「{name}」已存在')
        cat.name = name
        changed.append('name')

    if 'description' in body:
        cat.description = (body['description'] or '').strip()
        changed.append('description')

    if 'sort_order' in body:
        cat.sort_order = int(body['sort_order'])
        changed.append('sort_order')

    if changed:
        cat.save(update_fields=changed)
        log_operation(request, 'update', 'PageCategory', cat.id,
                      f'更新页面分类「{cat.name}」',
                      detail={'changes': {'旧名称': old_name if 'name' in changed else None}})

    return JsonResponse({'message': '分类已更新', 'category': _category_to_dict(cat)})


@csrf_exempt
@require_POST
def page_category_delete(request):
    """
    POST /api/pages/categories/delete/
    删除分类（不会删除关联的页面，仅解除关联关系）。

    请求: application/json
      {"id": 1}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    cat_id = body.get('id')
    if not cat_id:
        return err('缺少分类 ID')

    try:
        cat = PageCategory.objects.get(id=cat_id)
    except PageCategory.DoesNotExist:
        return err('分类不存在')

    cat_name = cat.name
    cat.delete()

    log_operation(request, 'delete', 'PageCategory', cat_id,
                  f'删除页面分类「{cat_name}」')

    return JsonResponse({'message': f'分类「{cat_name}」已删除'})


# =============================================================================
# 清空分类/未分类/未绑定域名下的所有页面
# =============================================================================


@csrf_exempt
@require_POST
def api_page_category_clear(request):
    """
    POST /api/pages/categories/clear/
    清空指定分类下的所有页面（删除页面本身）。

    请求: application/json
      {"id": 1}               // 分类 ID
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    cat_id = body.get('id')
    if not cat_id:
        return err('缺少分类 ID')

    try:
        cat = PageCategory.objects.get(id=cat_id)
    except PageCategory.DoesNotExist:
        return err('分类不存在')

    # 获取该分类下的所有页面
    pages = GeneratedPage.objects.filter(categories=cat)
    if not request.user.is_superuser:
        pages = pages.filter(created_by=request.user)

    count = pages.count()
    if count == 0:
        return JsonResponse({'message': f'分类「{cat.name}」下没有页面'})

    # 删除页面（会级联删除分类关联）
    page_ids = list(pages.values_list('id', flat=True))
    with transaction.atomic():
        pages.delete()

    log_operation(request, 'delete', 'GeneratedPage', None,
                  f'清空分类「{cat.name}」，删除 {count} 个页面',
                  detail={'page_ids': page_ids, 'category_id': cat_id})

    return JsonResponse({
        'message': f'已清空分类「{cat.name}」，删除 {count} 个页面',
        'count': count,
    })


@csrf_exempt
@require_POST
def api_page_uncategorized_clear(request):
    """
    POST /api/pages/uncategorized/clear/
    删除所有未分类页面（没有任何分类的页面）。
    """
    all_pages = GeneratedPage.objects.all()
    if not request.user.is_superuser:
        all_pages = all_pages.filter(created_by=request.user)

    all_pages = all_pages.prefetch_related('categories')
    uncategorized = []
    for page in all_pages:
        cats = list(page.categories.all())
        if not cats:
            uncategorized.append(page)

    count = len(uncategorized)
    if count == 0:
        return JsonResponse({'message': '没有未分类的页面'})

    page_ids = [p.id for p in uncategorized]
    with transaction.atomic():
        GeneratedPage.objects.filter(id__in=page_ids).delete()

    log_operation(request, 'delete', 'GeneratedPage', None,
                  f'清空未分类页面，删除 {count} 个页面',
                  detail={'page_ids': page_ids})

    return JsonResponse({
        'message': f'已清空未分类页面，删除 {count} 个页面',
        'count': count,
    })


@csrf_exempt
@require_POST
def api_page_unbound_clear(request):
    """
    POST /api/pages/unbound/clear/
    删除所有未绑定域名的页面。
    """
    all_pages = GeneratedPage.objects.all()
    if not request.user.is_superuser:
        all_pages = all_pages.filter(created_by=request.user)

    # 没有域名的页面
    unbound = []
    for page in all_pages:
        if not page.domains or not any(d.strip() for d in page.domains):
            unbound.append(page)

    count = len(unbound)
    if count == 0:
        return JsonResponse({'message': '没有未绑定域名的页面'})

    page_ids = [p.id for p in unbound]
    with transaction.atomic():
        GeneratedPage.objects.filter(id__in=page_ids).delete()

    log_operation(request, 'delete', 'GeneratedPage', None,
                  f'清空未绑定域名页面，删除 {count} 个页面',
                  detail={'page_ids': page_ids})

    return JsonResponse({
        'message': f'已清空未绑定域名页面，删除 {count} 个页面',
        'count': count,
    })


# =============================================================================
# 为页面设置分类
# =============================================================================


@csrf_exempt
@require_POST
def page_batch_categorize(request):
    """
    POST /api/pages/saved/batch-categorize/
    批量将未分类页面分配到指定分类。

    请求: application/json
      {"category_id": 1}               // 将所有未分类页面分配到此分类
      {"category_id": 1, "domain": "example.com"}  // 仅将包含此域名的未分类页面分配

    返回: {"ok": true, "count": 5, "message": "已将 5 个页面分类到「电商站」"}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    category_id = body.get('category_id')
    if not category_id:
        return err('缺少分类 ID')

    try:
        category = PageCategory.objects.get(id=category_id)
    except PageCategory.DoesNotExist:
        return err('分类不存在')

    # 找到所有未分类页面
    all_pages = GeneratedPage.objects.all()
    if not request.user.is_superuser:
        all_pages = all_pages.filter(created_by=request.user)

    # 预取分类关系以筛选未分类页面
    all_pages = all_pages.prefetch_related('categories')
    uncategorized = []
    for page in all_pages:
        cats = list(page.categories.all())
        if not cats:
            uncategorized.append(page)

    if not uncategorized:
        return JsonResponse({'ok': True, 'count': 0, 'message': '没有未分类的页面'})

    # 如果指定了域名过滤
    domain_filter = (body.get('domain') or '').strip().lower()
    if domain_filter:
        filtered = []
        for page in uncategorized:
            page_domains = [d.lower() for d in (page.domains or []) if d.strip()]
            if any(domain_filter in d for d in page_domains):
                filtered.append(page)
        uncategorized = filtered

    if not uncategorized:
        return JsonResponse({'ok': True, 'count': 0, 'message': '没有匹配的未分类页面'})

    # 批量分配分类
    page_ids = [p.id for p in uncategorized]
    with transaction.atomic():
        for page in uncategorized:
            page.categories.add(category)

    log_operation(request, 'batch_update', 'GeneratedPage', None,
                  f'批量将 {len(page_ids)} 个未分类页面分配到分类「{category.name}」')

    return JsonResponse({
        'ok': True,
        'count': len(page_ids),
        'message': f'已将 {len(page_ids)} 个未分类页面分配到分类「{category.name}」',
    })


@csrf_exempt
@require_POST
def page_set_categories(request):
    """
    POST /api/pages/saved/set-categories/
    为页面设置所属分类（覆盖式）。

    请求: application/json
      {"id": 1, "category_ids": [1, 2, 3]}   // 设置分类
      {"id": 1, "category_ids": []}            // 清空分类
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    page_id = body.get('id')
    if not page_id:
        return err('缺少页面 ID')

    try:
        page = GeneratedPage.objects.get(id=page_id)
    except GeneratedPage.DoesNotExist:
        return err('页面不存在')

    category_ids = body.get('category_ids', [])
    if not isinstance(category_ids, list):
        return err('category_ids 必须为数组')

    # 校验所有分类 ID 都存在
    if category_ids:
        existing = set(PageCategory.objects.filter(id__in=category_ids).values_list('id', flat=True))
        missing = set(category_ids) - existing
        if missing:
            return err(f'分类不存在: {sorted(missing)}')

    with transaction.atomic():
        page.categories.set(category_ids)

    log_operation(request, 'update', 'GeneratedPage', page_id,
                  f'更新页面「{page.name}」分类')

    return JsonResponse({'message': '分类已更新', 'category_ids': list(page.categories.values_list('id', flat=True))})


# =============================================================================
# 树形数据构建
# =============================================================================


def _build_page_dict(page: GeneratedPage) -> dict:
    """构建页面节点的字典（不含 HTML 内容）。"""
    return {
        'id': page.id,
        'name': page.name,
        'input_content': page.input_content,
        'domains': page.domains or [],
        'create_time': page.create_time.strftime('%Y-%m-%d %H:%M') if page.create_time else '',
        'crosslink_excluded': page.crosslink_excluded,
        'domain_seo': {s.domain: s.to_dict() for s in page.domain_seo_set.all()},
    }


def _build_domain_tree(pages):
    """
    从页面查询集中构建域名树。

    返回: [
        {
            'root': 'example.com',
            'domains': [
                {'domain': 'example.com', 'pages': [...]},
                {'domain': 'www.example.com', 'pages': [...]},
            ]
        }
    ]
    """
    # 第一步：收集所有域名 → 页面映射
    domain_to_pages = {}  # {domain_name: [page_dict, ...]}
    for page in pages:
        page_dict = _build_page_dict(page)
        for d in (page.domains or []):
            key = d.lower()
            if key not in domain_to_pages:
                domain_to_pages[key] = []
            domain_to_pages[key].append(page_dict)

    if not domain_to_pages:
        return []

    # 第二步：按根域名分组
    all_domains = list(domain_to_pages.keys())
    root_groups = group_domains_by_root(all_domains)

    # 第三步：展开虚假根域名
    # group_domains_by_root 可能推断出 2 段的虚拟根域名（如 com.cn、hl.cn），
    # 这些实际上是公共后缀而非真实根域名。对此类虚拟根域名，用 _find_parent_domain
    # 在其成员中重新查找正确的父域名，实现子域名正确归属。
    expanded_groups = {}
    for root, domain_list in root_groups.items():
        is_virtual = root not in domain_to_pages
        if is_virtual and len(root.split('.')) <= 2:
            members = sorted(domain_list)
            # 找出每个成员的正确父域名（在其同级域名中查找）
            groups = {}
            for d in members:
                p = _find_parent_domain(d, members)
                groups.setdefault(p, []).append(d)
            for new_root, new_domains in groups.items():
                expanded_groups.setdefault(new_root, []).extend(new_domains)
        else:
            expanded_groups.setdefault(root, []).extend(domain_list)

    # 第四步：构建树形结构
    result = []
    for root in sorted(expanded_groups.keys()):
        domain_list = expanded_groups[root]
        domains_node = []
        for d in sorted(domain_list):
            pages_for_domain = domain_to_pages.get(d, [])
            # 去重（同一页面可能有多域名匹配，但这里以 domain 维度展示）
            seen_ids = set()
            unique_pages = []
            for p in pages_for_domain:
                if p['id'] not in seen_ids:
                    seen_ids.add(p['id'])
                    unique_pages.append(p)
            domains_node.append({
                'domain': d,
                'pages': unique_pages,
                'page_count': len(unique_pages),
            })
        result.append({
            'root': root,
            'domains': domains_node,
            'page_count': sum(dn['page_count'] for dn in domains_node),
        })

    return result


@require_GET
def page_tree_api(request):
    """
    GET /api/pages/tree/
    获取完整的页面树形数据。

    返回结构:
    {
        "categories": [
            {
                "id": 1, "name": "电商站",
                "root_domains": [
                    {"root": "example.com", "domains": [...], "page_count": 3}
                ],
                "page_count": 5
            }
        ],
        "uncategorized": {
            "root_domains": [...],
            "page_count": 2
        },
        "unbound": {
            "pages": [...],
            "page_count": 1
        }
    }
    """
    # 获取全部页面（超级管理员看全部，普通用户只看自己的）
    all_pages = GeneratedPage.objects.all()
    if not request.user.is_superuser:
        all_pages = all_pages.filter(created_by=request.user)

    # 预取分类关系
    all_pages = all_pages.prefetch_related('categories')

    # 按分类分组页面
    cat_page_map = {}  # {category_id: [pages]}
    uncategorized_pages = []  # 没有分类的页面
    unbound_pages = []  # 没有域名的页面

    for page in all_pages:
        cats = list(page.categories.all())
        if cats:
            for cat in cats:
                cat_page_map.setdefault(cat.id, []).append(page)
        else:
            uncategorized_pages.append(page)

    # 没有域名的页面（不论是否有分类）
    has_domain_ids = set()
    for page in all_pages:
        if page.domains and any(d.strip() for d in page.domains):
            has_domain_ids.add(page.id)

    unbound_pages = [p for p in all_pages if p.id not in has_domain_ids]

    # 构建分类树
    categories = PageCategory.objects.all().order_by('sort_order', 'create_time')
    category_tree = []
    for cat in categories:
        pages_in_cat = cat_page_map.get(cat.id, [])
        root_domains = _build_domain_tree(pages_in_cat)
        category_tree.append({
            'id': cat.id,
            'name': cat.name,
            'description': cat.description,
            'root_domains': root_domains,
            'page_count': len(pages_in_cat),
        })

    # 构建"未分类"树
    uncat_root_domains = _build_domain_tree(uncategorized_pages)

    # 构建"未绑定域名"列表（只放无域名的页面）
    unbound_list = [_build_page_dict(p) for p in unbound_pages]

    return JsonResponse({
        'categories': category_tree,
        'uncategorized': {
            'root_domains': uncat_root_domains,
            'page_count': len(uncategorized_pages),
        },
        'unbound': {
            'pages': unbound_list,
            'page_count': len(unbound_pages),
        },
    })
