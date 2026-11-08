"""
项目数据导入导出工具。

核心设计：
1. 动态模型发现 — 通过 Django 的 app registry 自动获取所有模型，新增模型无需修改此文件
2. 黑名单过滤 — 实例相关数据（用户、日志）不导出
3. 拓扑排序 — 按 FK 依赖关系排序，保证导入时不会因为引用不存在而出错
4. 字段级兼容 — 导入时只写目标数据库存在的字段，不存在的自动跳过
5. FK/M2M 重映射 — 保留旧 ID → 新 ID 的映射关系
"""

import json
from datetime import datetime

from django.apps import apps
from django.db import models, transaction, IntegrityError
from django.utils.timezone import make_aware

# =============================================================================
# 配置
# =============================================================================

# 不导出的模型（实例特有数据）
EXCLUDE_MODELS = {
    'User',          # 用户账号
    'LoginLog',      # 登录日志
    'OperationLog',  # 操作日志
    'SpiderAccessLog',  # 蜘蛛访问日志
}

# FK 字段中引用这些模型的，导入时设为 None
FK_SKIP_MODELS = {'User'}

EXPORT_VERSION = '1.0'
EXPORT_FORMAT_VERSION_KEY = '_export_format_version'

# =============================================================================
# 模型发现与排序
# =============================================================================


def get_exportable_models():
    """获取所有可导出的模型类（按依赖拓扑排序）。"""
    app_config = apps.get_app_config('XiaoYingAdmin')
    model_list = [m for m in app_config.get_models()
                  if m.__name__ not in EXCLUDE_MODELS]
    return _topological_sort(model_list)


def _topological_sort(model_list):
    """按 FK 依赖关系拓扑排序（被依赖的排前面）。"""
    model_map = {m.__name__: m for m in model_list}

    def get_fk_deps(m):
        """获取一个模型直接依赖的其他模型名。"""
        deps = set()
        for f in m._meta.get_fields():
            if isinstance(f, models.ForeignKey) and f.remote_field.model:
                fk_model = f.remote_field.model
                if fk_model.__name__ in model_map and fk_model.__name__ != m.__name__:
                    deps.add(fk_model.__name__)
            elif isinstance(f, models.ManyToManyField) and f.remote_field.model:
                m2m_model = f.remote_field.model
                if m2m_model.__name__ in model_map and m2m_model.__name__ != m.__name__:
                    deps.add(m2m_model.__name__)
        return deps

    # Kahn 算法
    deps = {m.__name__: get_fk_deps(m) for m in model_list}
    result, queue = [], [name for name, d in deps.items() if not d]
    while queue:
        name = queue.pop(0)
        result.append(model_map[name])
        for other in list(deps.keys()):
            if name in deps[other]:
                deps[other].remove(name)
                if not deps[other]:
                    queue.append(other)

    # 如果有环，剩余的追加到最后
    remaining = [model_map[n] for n in deps if deps[n]]
    return result + remaining


# =============================================================================
# 字段工具
# =============================================================================

def _get_data_fields(model):
    """
    获取模型的数据字段（排除 Django 自动管理的字段）。
    返回字段名列表。
    """
    skip = {'last_login', 'date_joined', 'password', 'is_superuser',
            'is_staff', 'groups', 'user_permissions',
            'logentry'}
    fields = []
    for f in model._meta.get_fields():
        if f.name in skip:
            continue
        # 跳过 auto_now / auto_now_add 字段（导入时由 DB 自动管理）
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            continue
        # 必须包含 id（FK 重映射需要），即使它是 auto_created
        if f.name == 'id':
            fields.append(f.name)
            continue
        # 只包含具体字段或关系字段
        if f.name in ('create_time', 'updated_time'):
            fields.append(f.name)
            continue
        # 跳过反向关系
        if hasattr(f, 'auto_created') and f.auto_created:
            continue
        if isinstance(f, (models.Field, models.ForeignKey, models.ManyToManyField)):
            fields.append(f.name)
    return fields


def _serialize_value(val, model_field=None):
    """将 Python 值序列化为 JSON 可存储的格式。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if hasattr(val, 'pk'):
        # FK 对象 → 存储其 PK
        return val.pk
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if not isinstance(val, (str, int, float, bool)):
        return str(val)
    return val


def _deserialize_value(val, model_field):
    """将 JSON 值反序列化为 Python 类型。"""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            pass
    return val


# =============================================================================
# 导出
# =============================================================================


def export_all() -> dict:
    """
    导出所有可导出模型的数据。
    返回可序列化为 JSON 的字典。
    """
    models_to_export = get_exportable_models()
    result = {
        EXPORT_FORMAT_VERSION_KEY: EXPORT_VERSION,
        'export_time': datetime.now().isoformat(),
        'models': {},
    }
    for model_cls in models_to_export:
        fields = _get_data_fields(model_cls)
        m2m_fields = {f.name for f in model_cls._meta.get_fields()
                      if isinstance(f, models.ManyToManyField) and f.name in fields}

        rows = []
        qs = model_cls.objects.all().prefetch_related(*m2m_fields)
        for obj in qs:
            row = {}
            for field_name in fields:
                if field_name in m2m_fields:
                    try:
                        manager = getattr(obj, field_name)
                        row[field_name] = [o.pk for o in manager.all()]
                    except Exception:
                        row[field_name] = []
                else:
                    try:
                        val = getattr(obj, field_name)
                        row[field_name] = _serialize_value(val)
                    except Exception:
                        row[field_name] = None
            rows.append(row)

        if rows:
            result['models'][model_cls.__name__] = rows

    return result


# =============================================================================
# 导入
# =============================================================================

# 导入时的冲突处理策略
IMPORT_SKIP = 'skip'          # 跳过冲突（保留目标库原有数据）
IMPORT_OVERWRITE = 'overwrite'  # 覆盖现有数据

IMPORT_DEFAULT = IMPORT_SKIP


class ImportResult:
    """导入结果报告。"""

    def __init__(self):
        self.overwritten = {}    # model_name → 覆盖更新的数量
        self.created = {}        # model_name → 新增的数量
        self.skipped = {}        # model_name → 跳过的数量
        self.errors = []         # [(model_name, detail)]
        self.warnings = []       # [(model_name, detail)]
        self.unknown_models = []  # 导出中有但当前 DB 找不到的模型
        self.unknown_fields = {}  # model_name → [不存在的字段名]

    def add_overwritten(self, model_name, count=1):
        self.overwritten[model_name] = self.overwritten.get(model_name, 0) + count

    def add_created(self, model_name, count=1):
        self.created[model_name] = self.created.get(model_name, 0) + count

    def add_skipped(self, model_name, count=1):
        self.skipped[model_name] = self.skipped.get(model_name, 0) + count

    def add_error(self, model_name, detail):
        self.errors.append((model_name, detail))

    def add_warning(self, model_name, detail):
        self.warnings.append((model_name, detail))

    def to_dict(self):
        total_imported = sum(self.overwritten.values()) + sum(self.created.values())
        return {
            'overwritten': self.overwritten,
            'created': self.created,
            'skipped': self.skipped,
            'errors': self.errors[:30],
            'warnings': self.warnings[:30],
            'unknown_models': self.unknown_models,
            'unknown_fields': self.unknown_fields,
            'total_overwritten': sum(self.overwritten.values()),
            'total_created': sum(self.created.values()),
            'total_imported': total_imported,
            'total_skipped': sum(self.skipped.values()),
            'total_errors': len(self.errors),
            'total_warnings': len(self.warnings),
        }

    def has_issues(self):
        return bool(self.errors or self.unknown_models or self.unknown_fields)


def import_all(data: dict, conflict_strategy: str = IMPORT_DEFAULT) -> ImportResult:
    """
    导入数据。

    参数：
        data — export_all 生成的字典
        conflict_strategy — 冲突处理方式
    返回：
        ImportResult 对象
    """
    result = ImportResult()

    # 构建目标模型索引
    app_config = apps.get_app_config('XiaoYingAdmin')
    target_models = {m.__name__: m for m in app_config.get_models()
                     if m.__name__ not in EXCLUDE_MODELS}

    # 按导出文件中的模型顺序处理（已拓扑排序）
    export_models = data.get('models', {})

    # 检测未知模型（导出有的但当前 DB 没有）
    for model_name in export_models:
        if model_name not in target_models:
            result.unknown_models.append(model_name)

    # 构建模型处理顺序
    ordered_models = [m for m in get_exportable_models()
                      if m.__name__ in export_models]

    # ID 重映射：{model_name: {old_id: new_id}}
    id_map = {}

    for model_cls in ordered_models:
        model_name = model_cls.__name__
        rows = export_models.get(model_name, [])
        if not rows:
            continue

        # 获取目标模型的数据字段
        target_fields = set(_get_data_fields(model_cls))
        exported_field_names = set(rows[0].keys()) if rows else set()

        # 检测未知字段
        unknown = exported_field_names - target_fields
        if unknown:
            result.unknown_fields[model_name] = sorted(unknown)

        # 获取 FK/M2M 字段信息
        fk_fields = {}
        m2m_fields = {}
        for f in model_cls._meta.get_fields():
            if f.name in target_fields:
                if isinstance(f, models.ForeignKey) and f.remote_field.model:
                    fk_fields[f.name] = f
                elif isinstance(f, models.ManyToManyField) and f.remote_field.model:
                    m2m_fields[f.name] = f

        id_map[model_name] = {}

        for row in rows:
            old_pk = row.get('id')
            try:
                with transaction.atomic():
                    create_kwargs = {}

                    for field_name, val in row.items():
                        # 跳过目标不存在的字段
                        if field_name not in target_fields:
                            continue
                        # 跳过 id（让 DB 自增）
                        if field_name == 'id':
                            continue

                        # 处理 FK
                        if field_name in fk_fields:
                            fk = fk_fields[field_name]
                            fk_model = fk.remote_field.model
                            fk_name = fk_model.__name__
                            # 使用 attname（如 seo_domain_id）而非 name（如 seo_domain）
                            # 因为 Django FK 字段的 __set__ 不接受整数直接赋值
                            fk_key = fk.attname
                            if fk_name in FK_SKIP_MODELS:
                                create_kwargs[fk_key] = None
                                continue
                            if val is not None and fk_name in id_map:
                                create_kwargs[fk_key] = id_map[fk_name].get(val)
                            else:
                                create_kwargs[fk_key] = None
                            continue

                        # M2M 暂不处理（建完实例后设置）
                        if field_name in m2m_fields:
                            continue

                        # 普通字段
                        f = model_cls._meta.get_field(field_name)
                        create_kwargs[field_name] = _deserialize_value(val, f)

                    # 查找冲突
                    existing = None
                    if old_pk is not None:
                        try:
                            existing = model_cls.objects.get(pk=old_pk)
                        except model_cls.DoesNotExist:
                            pass

                    if existing:
                        if conflict_strategy == IMPORT_OVERWRITE:
                            for k, v in create_kwargs.items():
                                setattr(existing, k, v)
                            existing.save()
                            new_obj = existing
                            new_pk = existing.pk
                            result.add_overwritten(model_name)
                        else:
                            result.add_skipped(model_name)
                            id_map[model_name][old_pk] = existing.pk
                            continue
                    else:
                        # 先尝试用原 ID 创建
                        if old_pk is not None:
                            try:
                                new_obj = model_cls.objects.create(pk=old_pk, **create_kwargs)
                                new_pk = old_pk
                            except (IntegrityError, Exception):
                                # ID 冲突，尝试不带 PK 创建（让 DB 自增）
                                try:
                                    new_obj = model_cls.objects.create(**create_kwargs)
                                    new_pk = new_obj.pk
                                except (IntegrityError, Exception) as inner_e:
                                    # 唯一约束冲突（如 task_id 已存在），跳过
                                    result.add_skipped(model_name)
                                    if old_pk is not None:
                                        # 尝试从唯一字段查找已有记录
                                        try:
                                            existing = model_cls.objects.filter(**create_kwargs).first()
                                            if existing:
                                                id_map[model_name][old_pk] = existing.pk
                                        except Exception:
                                            pass
                                    continue
                        else:
                            try:
                                new_obj = model_cls.objects.create(**create_kwargs)
                                new_pk = new_obj.pk
                            except (IntegrityError, Exception):
                                result.add_skipped(model_name)
                                continue

                    # 处理 M2M
                    for m2m_name in m2m_fields:
                        raw_ids = row.get(m2m_name, [])
                        if raw_ids:
                            m2m_fk_model = m2m_fields[m2m_name].remote_field.model
                            m2m_fk_name = m2m_fk_model.__name__
                            resolved = []
                            for rid in raw_ids:
                                if m2m_fk_name in id_map:
                                    resolved.append(id_map[m2m_fk_name].get(rid))
                                else:
                                    resolved.append(rid)
                            resolved = [r for r in resolved if r is not None]
                            if resolved:
                                try:
                                    getattr(new_obj, m2m_name).set(resolved)
                                except Exception:
                                    pass

                    id_map[model_name][old_pk] = new_pk
                    result.add_created(model_name)

            except Exception as e:
                result.add_error(model_name, f'记录 #{old_pk}: {e}')

    return result
