# -*- coding: utf-8 -*-
"""域名品牌管理 — 为域名（含子域名）配置 Logo 与站点图标。

页面：/xiaoying_admin/pages/domain-brands/
接口：
  GET  api/domain-brands/            列表
  POST api/domain-brands/save/       新增或更新基础配置
  POST api/domain-brands/upload/     上传原图 / Logo / 各尺寸图标（multipart）
  POST api/domain-brands/regenerate/ 按原图重新生成全部尺寸
  POST api/domain-brands/delete/     删除配置
"""

import os

from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from XiaoYingAdmin.common.http import err, get_or_404, parse_json_body
from XiaoYingAdmin.models.domain_brand import DomainBrand
from XiaoYingAdmin.utils.domain_brand import (
    generate_icon_assets,
    parse_bing_auth_xml,
    validate_bing_code,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico'}

UPLOAD_FIELDS = set(DomainBrand.SIZE_FIELDS) | {'source_image'}


def _is_superuser(request):
    user = getattr(request, 'user', None)
    return bool(user and getattr(user, 'is_authenticated', False) and user.is_superuser)


def page_domain_brands(request):
    """域名品牌管理页面。"""
    if not _is_superuser(request):
        return HttpResponseForbidden('仅超级管理员可管理域名品牌')
    return render(request, 'XiaoYingAdmin/页面管理/域名品牌.html')


def api_domain_brand_list(request):
    """GET 列表。"""
    if not _is_superuser(request):
        return err('仅超级管理员可管理域名品牌', status=403)

    brands = DomainBrand.objects.all()
    return JsonResponse({
        'brands': [b.to_dict(include_preview=True) for b in brands],
        'count': brands.count(),
    })


@csrf_exempt
@require_POST
def api_domain_brand_save(request):
    """新增 / 更新基础配置。"""
    if not _is_superuser(request):
        return err('仅超级管理员可管理域名品牌', status=403)

    body, body_error = parse_json_body(request)
    if body_error:
        return body_error

    domain = DomainBrand.normalize_domain(body.get('domain'))
    if not domain or '.' not in domain:
        return err('请填写有效域名，如 example.com 或 m.example.com')
    if len(domain) > 255:
        return err('域名过长')

    # 先校验验证码，避免无效输入时留下空白记录
    try:
        bing_code = validate_bing_code(body.get('bing_verify_code'))
    except ValueError as e:
        return err(str(e))

    brand, _created = DomainBrand.objects.get_or_create(domain=domain)
    brand.brand_name = (body.get('brand_name') or '').strip()[:100]
    brand.enabled = bool(body.get('enabled', True))
    brand.replace_page_logo = bool(body.get('replace_page_logo', True))
    brand.seo_enabled = bool(body.get('seo_enabled', True))
    brand.logo_selector = (body.get('logo_selector') or '').strip()[:200]
    brand.bing_verify_code = bing_code

    # robots.txt / sitemap.xml 配置
    brand.robots_txt = (body.get('robots_txt') or '').strip()[:20000]

    scope = body.get('sitemap_scope') or 'inherit'
    brand.sitemap_scope = scope if scope in ('inherit', 'custom') else 'inherit'

    hosts_raw = body.get('sitemap_hosts') or []
    if isinstance(hosts_raw, str):
        hosts_raw = hosts_raw.replace(',', '\n').splitlines()
    sitemap_hosts = []
    for value in hosts_raw:
        host = DomainBrand.normalize_domain(value)
        if host and '.' in host and not host.startswith('*') and host not in sitemap_hosts:
            sitemap_hosts.append(host)
    brand.sitemap_hosts = sitemap_hosts[:200]

    extra_urls = []
    for line in (body.get('sitemap_extra_urls') or '').splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith(('http://', 'https://')):
            extra_urls.append(value)
        elif value.startswith('/'):
            extra_urls.append(value)
        else:
            extra_urls.append('/' + value)
    brand.sitemap_extra_urls = '\n'.join(extra_urls[:500])

    brand.save()

    return JsonResponse({'ok': True, 'brand': brand.to_dict(include_preview=True)})


@csrf_exempt
@require_POST
def api_domain_brand_upload(request):
    """上传原图 / Logo / 单个尺寸图标。"""
    if not _is_superuser(request):
        return err('仅超级管理员可管理域名品牌', status=403)

    brand, error = get_or_404(DomainBrand, id=request.POST.get('id'))
    if error:
        return error

    kind = (request.POST.get('kind') or '').strip()
    upload = request.FILES.get('file')
    if not upload:
        return err('请选择要上传的文件')

    # 必应验证文件：只解析验证码，不保存文件本身
    if kind == 'bing_file':
        if upload.size > 1024 * 1024:
            return err('验证文件不能超过 1MB')
        try:
            code = parse_bing_auth_xml(upload.read())
        except ValueError as e:
            return err(str(e))
        if not code:
            return err('验证文件中没有有效的验证码')
        brand.bing_verify_code = code
        brand.save(update_fields=['bing_verify_code', 'updated_time'])
        return JsonResponse({
            'ok': True,
            'message': '已识别并保存必应验证码',
            'bing_verify_code': code,
            'brand': brand.to_dict(include_preview=True),
        })

    if kind not in UPLOAD_FIELDS:
        return err('未知的图片类型')
    if upload.size > MAX_UPLOAD_BYTES:
        return err('文件不能超过 10MB')

    ext = os.path.splitext(upload.name or '')[1].lower()
    if ext not in ALLOWED_EXT:
        return err('仅支持 png / jpg / webp / gif / svg / ico 格式')

    setattr(brand, kind, upload)

    manual = list(brand.manual_sizes or [])
    # 除原图外，手动上传的尺寸都记录下来；自动生成时会跳过，避免覆盖
    if kind != 'source_image' and kind not in manual:
        manual.append(kind)
    brand.manual_sizes = manual
    brand.save()

    message = '上传成功'
    if kind in ('source_image', 'logo'):
        ok, msg, _generated = generate_icon_assets(brand)
        brand.refresh_from_db()
        message = msg if ok else f'上传成功，但自动生成失败：{msg}'

    return JsonResponse({
        'ok': True,
        'message': message,
        'brand': brand.to_dict(include_preview=True),
    })


@csrf_exempt
@require_POST
def api_domain_brand_regenerate(request):
    """按原图重新生成所有尺寸（手工上传的尺寸会跳过）。"""
    if not _is_superuser(request):
        return err('仅超级管理员可管理域名品牌', status=403)

    body, body_error = parse_json_body(request)
    if body_error:
        return body_error

    brand, error = get_or_404(DomainBrand, id=body.get('id'))
    if error:
        return error

    ok, message, generated = generate_icon_assets(brand)
    brand.refresh_from_db()
    if not ok:
        return err(message)

    return JsonResponse({
        'ok': True,
        'message': message,
        'generated': generated,
        'brand': brand.to_dict(include_preview=True),
    })


@csrf_exempt
@require_POST
def api_domain_brand_delete(request):
    """删除配置及其图片文件。"""
    if not _is_superuser(request):
        return err('仅超级管理员可管理域名品牌', status=403)

    body, body_error = parse_json_body(request)
    if body_error:
        return body_error

    brand, error = get_or_404(DomainBrand, id=body.get('id'))
    if error:
        return error

    for field_name in list(DomainBrand.SIZE_FIELDS) + ['source_image']:
        field = getattr(brand, field_name, None)
        if field:
            try:
                field.delete(save=False)
            except Exception:  # noqa: BLE001
                pass

    brand.delete()
    return JsonResponse({'ok': True})
