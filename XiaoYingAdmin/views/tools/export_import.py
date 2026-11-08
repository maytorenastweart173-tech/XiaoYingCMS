"""
导入导出页面及 API 视图。
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_GET

from XiaoYingAdmin.utils.export_import import (
    export_all,
    import_all,
    get_exportable_models,
    IMPORT_DEFAULT,
    IMPORT_OVERWRITE,
    IMPORT_SKIP,
)

PAGE_TEMPLATE = 'XiaoYingAdmin/工具/导入导出.html'


@login_required
@require_http_methods(['GET'])
def export_import_view(request):
    """导入导出页面"""
    models_meta = []
    for m in get_exportable_models():
        count = m.objects.count()
        models_meta.append({
            'name': m.__name__,
            'verbose_name': m._meta.verbose_name or m.__name__,
            'count': count,
        })
    return render(request, PAGE_TEMPLATE, {'models_meta': models_meta})


@csrf_exempt
@require_GET
def api_export(request):
    """导出全部项目数据为 JSON"""
    try:
        data = export_all()
        resp = HttpResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type='application/json; charset=utf-8',
        )
        resp['Content-Disposition'] = 'attachment; filename="xiaoying_export.json"'
        return resp
    except Exception as e:
        return JsonResponse({'code': 1, 'message': f'导出失败: {e}'})


@csrf_exempt
@require_POST
def api_import_preview(request):
    """上传 JSON 预览导入内容（不实际写入数据库）。"""
    try:
        data = _parse_upload(request)
    except Exception as e:
        return JsonResponse({'code': 1, 'message': f'解析失败: {e}'})

    from django.apps import apps
    from XiaoYingAdmin.utils.export_import import EXCLUDE_MODELS

    # 构建目标模型索引
    app_config = apps.get_app_config('XiaoYingAdmin')
    target_models = {m.__name__: m for m in app_config.get_models()
                     if m.__name__ not in EXCLUDE_MODELS}

    models_data = data.get('models', {})
    preview = []
    for model_name, rows in models_data.items():
        is_unknown = model_name not in target_models
        existing_count = 0
        if not is_unknown:
            # 统计目标库中已有的记录数（按 id 或按导出数据中的 key 判断）
            ids = [r.get('id') for r in rows if r.get('id') is not None]
            if ids:
                existing_count = target_models[model_name].objects.filter(id__in=ids).count()
        preview.append({
            'name': model_name,
            'count': len(rows),
            'existing_count': existing_count,
            'is_unknown': is_unknown,
        })

    unknown_models = _get_unknown_models(data)
    total_overwrite = sum(m['existing_count'] for m in preview if not m['is_unknown'])
    total_new = sum(m['count'] - m['existing_count'] for m in preview if not m['is_unknown'])

    return JsonResponse({
        'code': 0,
        'data': {
            'version': data.get('_export_format_version', '<未知>'),
            'export_time': data.get('export_time', ''),
            'models': preview,
            'total_records': sum(m['count'] for m in preview),
            'total_existing': total_overwrite,
            'total_new': total_new,
            'unknown_models': list(unknown_models),
        },
    })


@csrf_exempt
@require_POST
def api_import_execute(request):
    """执行导入。"""
    try:
        data = _parse_upload(request)
    except Exception as e:
        return JsonResponse({'code': 1, 'message': f'解析失败: {e}'})

    strategy = request.GET.get('conflict', IMPORT_DEFAULT)
    if strategy not in (IMPORT_SKIP, IMPORT_OVERWRITE):
        strategy = IMPORT_DEFAULT

    try:
        result = import_all(data, conflict_strategy=strategy)
        return JsonResponse({
            'code': 0,
            'data': result.to_dict(),
        })
    except Exception as e:
        return JsonResponse({'code': 1, 'message': f'导入失败: {e}'})


def _parse_upload(request):
    """从请求中解析上传的 JSON 文件。"""
    if request.content_type and 'json' in request.content_type:
        return json.loads(request.body)
    # 文件上传
    if request.FILES:
        f = request.FILES.get('file')
        if f:
            return json.loads(f.read().decode('utf-8'))
    raise ValueError('未找到有效的 JSON 数据')


def _get_unknown_models(data):
    """检测导出文件中有但当前系统不存在的模型。"""
    from django.apps import apps
    from XiaoYingAdmin.utils.export_import import EXCLUDE_MODELS
    app_config = apps.get_app_config('XiaoYingAdmin')
    target_names = {m.__name__ for m in app_config.get_models()
                    if m.__name__ not in EXCLUDE_MODELS}
    export_names = set(data.get('models', {}).keys())
    return export_names - target_names
