"""
域名快排追踪 — 域名列表 + 域名时间线（LayUI 风格）双层结构。
"""
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils.timezone import make_aware
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_GET

from XiaoYingAdmin.common.http import parse_json_body, err
from XiaoYingAdmin.models.domain_seo_record import DomainSeoRecord
from XiaoYingAdmin.models.generated_page import GeneratedPage
from XiaoYingAdmin.models.multi_page_project import MultiPageProject
from XiaoYingAdmin.models.seo_domain import SeoDomain
from XiaoYingAdmin.utils.domain_utils import (
    build_domain_hierarchy,
    group_domains_by_root,
)

DOMAINS_TEMPLATE = 'XiaoYingAdmin/SEO/域名SEO记录管理.html'
TIMELINE_TEMPLATE = 'XiaoYingAdmin/SEO/域名时间线.html'

# 默认每页条数
DEFAULT_PAGE_SIZE = 20


def _parse_dt(s: str):
    """解析 '2026-07-06 14:30:00' 或 '2026-07-06T14:30:00' 为 timezone-aware datetime。"""
    if not s:
        return None
    s = s.strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s, fmt)
            return make_aware(dt)
        except ValueError:
            continue
    return None


# =============================================================================
# 页面视图
# =============================================================================

@login_required
@require_http_methods(['GET'])
def domain_seo_domains_view(request):
    """域名列表页 — 展示所有待追踪的域名实体"""
    return render(request, DOMAINS_TEMPLATE)


@login_required
@require_http_methods(['GET'])
def domain_seo_timeline_view(request, pk):
    """域名时间线页 — 展示某个域名的 LayUI 时间线"""
    domain = get_object_or_404(SeoDomain, id=pk)
    return render(request, TIMELINE_TEMPLATE, {'domain': domain.to_dict()})


# =============================================================================
# 域名实体 CRUD API
# =============================================================================

@csrf_exempt
@require_GET
def api_seo_domains_list(request):
    """获取所有域名实体（分页）"""
    q = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))

    domains = SeoDomain.objects.all()
    if q:
        domains = domains.filter(domain__icontains=q)

    paginator = Paginator(domains, page_size)
    total = paginator.count
    total_pages = paginator.num_pages

    try:
        page_obj = paginator.page(page)
    except Exception:
        return JsonResponse({'ok': False, 'error': '页码无效'})

    return JsonResponse({
        'ok': True,
        'domains': [d.to_dict() for d in page_obj.object_list],
        'page': page,
        'page_size': page_size,
        'total': total,
        'total_pages': total_pages,
    })


@csrf_exempt
@require_GET
def api_seo_domains_tree(request):
    """获取域名树形数据（按根域名分组 + 分页），同级分页。

    GET /xiaoying_admin/api/seo/domains/tree/?page=1&page_size=20&q=xxx

    返回结构：
      tree: [{ domain, domain_type, remark, record_count, children: [递归多层] }]
    """
    q = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))

    domains_qs = SeoDomain.objects.all()
    if q:
        domains_qs = domains_qs.filter(domain__icontains=q)

    # 全部取出，用于分组
    all_domains = list(domains_qs)
    if not all_domains:
        return JsonResponse({'ok': True, 'tree': [], 'total': 0, 'page': 1, 'total_pages': 0})

    # 收集所有域名字符串，按根分组
    domain_names = [d.domain for d in all_domains]
    root_groups = group_domains_by_root(domain_names)
    domain_map = {d.domain: d for d in all_domains}

    def _hierarchy_to_dict(hierarchy_list):
        """将 build_domain_hierarchy 输出的结构映射为 SeoDomain dict + 递归 children。"""
        result = []
        for h_node in hierarchy_list:
            domain_str = h_node['domain']
            obj = domain_map.get(domain_str)
            if obj:
                node = obj.to_dict()
            else:
                # 虚拟根域名（不在 SeoDomain 表中）
                node = {
                    'id': 0,
                    'domain': domain_str,
                    'domain_type': 'root',
                    'domain_type_label': '根域名',
                    'remark': '',
                    'record_count': 0,
                    'create_time': '',
                }
            # 递归处理子域名
            child_nodes = _hierarchy_to_dict(h_node['children']) if h_node['children'] else []
            node['children'] = child_nodes
            result.append(node)
        return result

    def _dedup_ids(node, seen):
        """递归标记已见过的 id，避免重复。"""
        if node.get('id') and node['id'] in seen:
            return False
        if node.get('id'):
            seen.add(node['id'])
        for child in node.get('children', []):
            _dedup_ids(child, seen)
        return True

    # 构建多层级树
    tree = []
    seen_ids = set()

    for root_domain in root_groups:
        root_obj = domain_map.get(root_domain)
        group_members = root_groups[root_domain]

        if not root_obj:
            # 虚拟根域名（由 group_domains_by_root 自动推断，不在 SeoDomain 表中）
            # 虚拟根域名本身不在 group_members 中，需加入以便 build_domain_hierarchy
            # 能正确构建多层级结构（子域名嵌套在虚拟根下）
            extended_members = group_members + [root_domain]
            hierarchy = build_domain_hierarchy(extended_members)
            for h_node in hierarchy:
                mapped_nodes = _hierarchy_to_dict([h_node])
                for node in mapped_nodes:
                    _dedup_ids(node, seen_ids)
                tree.extend(mapped_nodes)
            continue

        # 真实根域名：用 hierarchy 来组织子域名
        hierarchy = build_domain_hierarchy(group_members)
        for h_node in hierarchy:
            if h_node['domain'] == root_domain:
                node = root_obj.to_dict()
                child_nodes = _hierarchy_to_dict(h_node['children'])
                node['children'] = child_nodes
                _dedup_ids(node, seen_ids)
                tree.append(node)
            else:
                # 根域名不在 group 最前面（非常规情况），按独立节点处理
                mapped = _hierarchy_to_dict([h_node])
                for nd in mapped:
                    _dedup_ids(nd, seen_ids)
                tree.extend(mapped)

    # 分页
    paginator = Paginator(tree, page_size)
    total = paginator.count
    total_pages = paginator.num_pages
    try:
        page_obj = paginator.page(page)
    except Exception:
        return JsonResponse({'ok': False, 'error': '页码无效'})

    return JsonResponse({
        'ok': True,
        'tree': page_obj.object_list,
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': total_pages,
    })


@csrf_exempt
@require_POST
def api_seo_domains_create(request):
    """创建域名实体"""
    data, error = parse_json_body(request)
    if error:
        return error

    domain = (data.get('domain') or '').strip()
    domain_type = (data.get('domain_type') or '').strip()
    remark = (data.get('remark') or '').strip()

    if not domain:
        return err('域名不能为空')
    if domain_type not in ('root', 'multi'):
        return err('域名类型无效，必须为 root 或 multi')
    if SeoDomain.objects.filter(domain=domain).exists():
        return err(f'域名 "{domain}" 已存在')

    obj = SeoDomain(domain=domain, domain_type=domain_type, remark=remark)
    obj.save()
    return JsonResponse({'ok': True, 'message': '域名已创建', 'domain': obj.to_dict()})


@csrf_exempt
@require_POST
def api_seo_domains_update(request, pk):
    """更新域名实体"""
    try:
        obj = SeoDomain.objects.get(id=pk)
    except SeoDomain.DoesNotExist:
        return err('域名不存在', status=404)

    data, error = parse_json_body(request)
    if error:
        return error

    domain = (data.get('domain') or '').strip()
    domain_type = (data.get('domain_type') or '').strip()
    remark = (data.get('remark') or '').strip()

    if domain and domain != obj.domain:
        if SeoDomain.objects.filter(domain=domain).exclude(id=pk).exists():
            return err(f'域名 "{domain}" 已被其他记录使用')
        obj.domain = domain
    if domain_type in ('root', 'multi'):
        obj.domain_type = domain_type
    if data.get('remark') is not None:
        obj.remark = remark

    obj.save()
    return JsonResponse({'ok': True, 'message': '域名已更新', 'domain': obj.to_dict()})


@csrf_exempt
@require_POST
def api_seo_domains_delete(request, pk):
    """删除域名实体（同时删除其下的所有时间线记录）"""
    try:
        obj = SeoDomain.objects.get(id=pk)
    except SeoDomain.DoesNotExist:
        return err('域名不存在', status=404)
    obj.delete()
    return JsonResponse({'ok': True, 'message': '域名已删除'})


@csrf_exempt
@require_POST
def api_seo_domains_sync(request):
    """从已有项目/页面同步域名到 SeoDomain（去重新增）"""
    seen = set(SeoDomain.objects.values_list('domain', flat=True))
    added = []

    for proj in MultiPageProject.objects.exclude(root_domain=''):
        d = proj.root_domain.strip()
        if d and d not in seen:
            seen.add(d)
            obj = SeoDomain(domain=d, domain_type='root',
                            remark=f'来自多页面项目: {proj.name}')
            obj.save()
            added.append(obj.to_dict())
    for proj in MultiPageProject.objects.exclude(enabled_domain=''):
        d = proj.enabled_domain.strip()
        if d and d not in seen:
            seen.add(d)
            obj = SeoDomain(domain=d, domain_type='root',
                            remark=f'来自多页面项目(启用域名): {proj.name}')
            obj.save()
            added.append(obj.to_dict())
    for page in GeneratedPage.objects.exclude(domains=[]).exclude(domains__exact=[]):
        for raw in (page.domains or []):
            d = raw.strip() if isinstance(raw, str) else str(raw).strip()
            if d and d not in seen:
                seen.add(d)
                dt = 'multi' if d.startswith('*.') else 'root'
                obj = SeoDomain(domain=d, domain_type=dt,
                                remark=f'来自单页面: {page.name}')
                obj.save()
                added.append(obj.to_dict())

    return JsonResponse({'ok': True, 'message': f'同步完成，新增 {len(added)} 个域名', 'added': added})


# =============================================================================
# 时间线记录 CRUD API
# =============================================================================

@csrf_exempt
@require_GET
def api_seo_domain_records_list(request, pk):
    """获取某个域名下的所有时间线记录"""
    records = DomainSeoRecord.objects.filter(seo_domain_id=pk) \
        .select_related('seo_domain') \
        .order_by('-action_date', '-create_time')
    return JsonResponse({
        'ok': True,
        'records': [r.to_dict() for r in records],
    })


@csrf_exempt
@require_POST
def api_seo_domain_records_create(request, pk):
    """为某个域名新增一条时间线记录"""
    try:
        domain_obj = SeoDomain.objects.get(id=pk)
    except SeoDomain.DoesNotExist:
        return err('域名不存在', status=404)

    data, error = parse_json_body(request)
    if error:
        return error

    action_date_str = (data.get('action_date') or '').strip()
    description = (data.get('description') or '').strip()

    if not action_date_str:
        return err('操作时间不能为空')
    if not description:
        return err('操作描述不能为空')

    action_date = _parse_dt(action_date_str)
    if action_date is None:
        return err('操作时间格式无效，请使用 YYYY-MM-DD HH:MM:SS 格式')

    record = DomainSeoRecord(
        seo_domain=domain_obj,
        action_date=action_date,
        description=description,
    )
    record.save()
    return JsonResponse({'ok': True, 'message': '记录已创建', 'record': record.to_dict()})


@csrf_exempt
@require_POST
def api_seo_domain_records_batch_create(request):
    """为多个域名批量新增同一条时间线记录

    POST JSON:
      { "domain_ids": [1,2,3], "action_date": "2026-07-11 14:30:00", "description": "上传了蜘蛛池" }
    """
    data, error = parse_json_body(request)
    if error:
        return error

    domain_ids = data.get('domain_ids', [])
    action_date_str = (data.get('action_date') or '').strip()
    description = (data.get('description') or '').strip()

    if not domain_ids or not isinstance(domain_ids, list):
        return err('请选择至少一个域名')
    if not action_date_str:
        return err('操作时间不能为空')
    if not description:
        return err('操作描述不能为空')

    action_date = _parse_dt(action_date_str)
    if action_date is None:
        return err('操作时间格式无效，请使用 YYYY-MM-DD HH:MM:SS 格式')

    # 查重：过滤掉不存在的 domain_id
    existing = set(SeoDomain.objects.filter(id__in=domain_ids).values_list('id', flat=True))
    if not existing:
        return err('所选域名均不存在')

    created = []
    for did in existing:
        record = DomainSeoRecord(
            seo_domain_id=did,
            action_date=action_date,
            description=description,
        )
        record.save()
        created.append(record.to_dict())

    return JsonResponse({
        'ok': True,
        'message': f'已为 {len(created)} 个域名创建时间线记录',
        'records': created,
    })


@csrf_exempt
@require_POST
def api_seo_records_update(request, pk):
    """更新一条时间线记录"""
    try:
        record = DomainSeoRecord.objects.select_related('seo_domain').get(id=pk)
    except DomainSeoRecord.DoesNotExist:
        return err('记录不存在', status=404)

    data, error = parse_json_body(request)
    if error:
        return error

    action_date_str = (data.get('action_date') or '').strip()
    description = (data.get('description') or '').strip()

    if action_date_str:
        dt = _parse_dt(action_date_str)
        if dt is None:
            return err('操作时间格式无效，请使用 YYYY-MM-DD HH:MM:SS 格式')
        record.action_date = dt
    if description:
        record.description = description

    record.save()
    return JsonResponse({'ok': True, 'message': '记录已更新', 'record': record.to_dict()})


@csrf_exempt
@require_POST
def api_seo_records_delete(request, pk):
    """删除一条时间线记录"""
    try:
        record = DomainSeoRecord.objects.get(id=pk)
    except DomainSeoRecord.DoesNotExist:
        return err('记录不存在', status=404)
    domain_id = record.seo_domain_id
    record.delete()
    return JsonResponse({'ok': True, 'message': '记录已删除', 'seo_domain_id': domain_id})


@csrf_exempt
@require_POST
def api_seo_records_clear_all(request):
    """清空所有域名SEO时间线记录（包括关联的域名实体）"""
    data, error = parse_json_body(request)
    if error:
        return error
    if data.get('confirm') != 'yes':
        return err('请确认清空操作')

    from XiaoYingAdmin.models.seo_domain import SeoDomain

    # 统计
    record_count = DomainSeoRecord.objects.count()
    domain_count = SeoDomain.objects.count()

    # 先删记录再删域名（外键约束）
    DomainSeoRecord.objects.all().delete()
    SeoDomain.objects.all().delete()

    return JsonResponse({
        'ok': True,
        'message': f'已清空 {record_count} 条时间线记录和 {domain_count} 个域名实体',
        'deleted_records': record_count,
        'deleted_domains': domain_count,
    })
