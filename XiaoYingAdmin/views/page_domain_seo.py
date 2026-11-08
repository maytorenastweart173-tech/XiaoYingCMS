"""
页面域名SEO状态 API 视图 — 收录 & 排名第一管理。
"""

import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from XiaoYingAdmin.common.http import err
from XiaoYingAdmin.models import GeneratedPage, PageDomainSeo, SiteSettings


@require_GET
def api_search_engines_list(request):
    """获取可用的搜索引擎列表（从 SiteSettings 读取）"""
    settings, _ = SiteSettings.objects.get_or_create(pk=1)
    engines = settings.search_engines or []
    return JsonResponse({'code': 0, 'data': engines})


@csrf_exempt
@require_POST
def api_page_domain_seo_save(request):
    """
    保存域名 SEO 状态（收录 + 排名第一）。

    POST JSON:
    {
        "page_id": 1,
        "domain": "example.com",
        "indexed_engines": ["百度", "谷歌"],
        "is_rank_first": true,
        "rank_first_engines": ["百度"],
        "rank_first_keywords": {"百度": "小程序开发"}
    }
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return err('JSON 解析失败')

    page_id = body.get('page_id')
    domain = body.get('domain', '').strip()

    if not page_id or not domain:
        return err('page_id 和 domain 不能为空')

    # 校验页面存在
    try:
        page = GeneratedPage.objects.get(pk=page_id)
    except GeneratedPage.DoesNotExist:
        return err('页面不存在')

    # 校验域名属于该页面
    if domain not in (page.domains or []) and domain != page.domain:
        return err(f'域名 "{domain}" 不属于该页面')

    indexed_engines = body.get('indexed_engines', [])
    is_rank_first = body.get('is_rank_first', False)
    rank_first_engines = body.get('rank_first_engines', [])
    rank_first_keywords = body.get('rank_first_keywords', {})

    # 依赖校验：排名第一必须至少有一个收录引擎
    if is_rank_first and not indexed_engines:
        return err('必须先收录至少一个搜索引擎，才能设置排名第一')

    # 排名第一的引擎必须是已收录的引擎的子集
    if is_rank_first and rank_first_engines:
        invalid = [e for e in rank_first_engines if e not in indexed_engines]
        if invalid:
            return err(f'排名第一的引擎 {invalid} 不在已收录列表中')

    seo, created = PageDomainSeo.objects.update_or_create(
        page=page,
        domain=domain,
        defaults={
            'indexed_engines': indexed_engines,
            'is_rank_first': is_rank_first,
            'rank_first_engines': rank_first_engines,
            'rank_first_keywords': rank_first_keywords,
        },
    )

    return JsonResponse({
        'code': 0,
        'message': '保存成功',
        'data': seo.to_dict(),
    })


@require_GET
def api_page_domain_seo_get(request):
    """获取指定页面域名的 SEO 状态"""
    page_id = request.GET.get('page_id')
    domain = request.GET.get('domain', '').strip()

    if not page_id or not domain:
        return err('page_id 和 domain 不能为空')

    try:
        seo = PageDomainSeo.objects.get(page_id=page_id, domain=domain)
        return JsonResponse({'code': 0, 'data': seo.to_dict()})
    except PageDomainSeo.DoesNotExist:
        return JsonResponse({
            'code': 0,
            'data': {
                'page_id': int(page_id),
                'domain': domain,
                'indexed_engines': [],
                'is_rank_first': False,
                'rank_first_engines': [],
                'rank_first_keywords': {},
            }
        })
