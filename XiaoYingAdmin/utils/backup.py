# -*- coding: utf-8 -*-
"""数据备份工具 — 导出/导入模型数据到 JSONL 格式文件"""

import json
import os
import re
import logging
from datetime import datetime

from django.conf import settings

logger = logging.getLogger(__name__)


def get_backup_dir() -> str:
    """获取备份根目录（自动创建）"""
    backup_dir = getattr(settings, 'BACKUP_DIR', None)
    if not backup_dir:
        backup_dir = os.path.join(str(settings.BASE_DIR), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def _serialize_val(val):
    """将模型字段值序列化为 JSON 可用的值"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d %H:%M:%S')
    if hasattr(val, 'pk'):  # ForeignKey 对象
        return val.pk
    return val


def backup_model(model_class, filename_prefix: str, filters: dict = None) -> dict:
    """备份模型数据到 JSONL 文件

    Args:
        model_class: Django Model 类
        filename_prefix: 文件名前缀，如 spider_logs
        filters: 可选的过滤条件 dict，如 {'spider_name__gt': ''}

    Returns:
        dict: {filepath, filename, count, size_bytes}
    """
    backup_dir = get_backup_dir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{filename_prefix}_{timestamp}.jsonl'
    filepath = os.path.join(backup_dir, filename)

    qs = model_class.objects.all()
    if filters:
        qs = qs.filter(**filters)

    count = 0
    with open(filepath, 'w', encoding='utf-8') as f:
        for obj in qs.iterator(chunk_size=500):
            row = {}
            for field in model_class._meta.fields:
                try:
                    val = getattr(obj, field.attname)
                    row[field.name] = _serialize_val(val)
                except Exception as e:
                    row[field.name] = None
                    logger.warning("备份序列化异常 %s.%s: %s", filename_prefix, field.name, e)
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            count += 1

    size_bytes = os.path.getsize(filepath)
    logger.info("备份完成: %s | %d 条 | %.2f KB", filepath, count, size_bytes / 1024)

    return {
        'filepath': filepath,
        'filename': filename,
        'count': count,
        'size_bytes': size_bytes,
        'size_str': f'{size_bytes / 1024:.1f} KB' if size_bytes < 1024 * 1024 else f'{size_bytes / 1024 / 1024:.2f} MB',
    }


def check_and_auto_backup(model_class, filename_prefix: str, threshold_field: str):
    """检查日志数量是否达到自动备份阈值，达到则备份并清空。

    由中间件在写入每条日志后调用，实现"达到阈值自动备份"功能。

    Args:
        model_class: Django Model 类（SpiderAccessLog / OperationLog）
        filename_prefix: 备份文件名前缀，如 spider_logs、op_logs
        threshold_field: SiteSettings 上的阈值字段名
            auto_backup_spider_threshold / auto_backup_operation_threshold
    """
    # 懒导入避免循环依赖
    from XiaoYingAdmin.models.site_settings import SiteSettings

    try:
        settings = SiteSettings.objects.get(pk=1)
    except SiteSettings.DoesNotExist:
        return

    threshold = getattr(settings, threshold_field, 0)
    if threshold <= 0:
        return  # 自动备份已关闭

    count = model_class.objects.count()
    if count >= threshold:
        logger.info("自动备份触发: %s 当前 %d 条 >= 阈值 %d", filename_prefix, count, threshold)
        try:
            result = backup_model(model_class, filename_prefix)
            model_class.objects.all().delete()
            logger.info(
                "自动备份完成: %s 共 %d 条 | 文件: %s",
                filename_prefix, result['count'], result['filename'],
            )
        except Exception as e:
            logger.error("自动备份失败 %s: %s", filename_prefix, e)


# ========== 恢复 / 导入功能 ==========


def list_backup_files(prefix: str) -> list:
    """列出备份目录下指定前缀的 JSONL 文件

    Args:
        prefix: 文件名前缀，如 spider_logs、op_logs

    Returns:
        list[dict]: [{filename, filepath, size_bytes, size_str, modified}]
    """
    backup_dir = get_backup_dir()
    if not os.path.isdir(backup_dir):
        return []

    pattern = re.compile(r'^' + re.escape(prefix) + r'_\d{8}_\d{6}\.jsonl$')
    result = []
    for fname in os.listdir(backup_dir):
        if pattern.match(fname):
            fpath = os.path.join(backup_dir, fname)
            if os.path.isfile(fpath):
                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
                result.append({
                    'filename': fname,
                    'filepath': fpath,
                    'size_bytes': size,
                    'size_str': f'{size / 1024:.1f} KB' if size < 1024 * 1024 else f'{size / 1024 / 1024:.2f} MB',
                    'modified': datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S'),
                })
    # 按修改时间倒序（最新的在前）
    result.sort(key=lambda x: x['modified'], reverse=True)
    return result


def restore_model_from_file(filepath: str, model_class, batch_size: int = 500) -> dict:
    """从 JSONL 备份文件恢复数据到数据库

    流程：
    1. 逐行读取 JSONL 文件
    2. 过滤有效字段，排除 auto_now 字段让 DB 自动填充
    3. 尝试保留原始 ID 批量写入
    4. 冲突时降级为自动分配 ID

    Args:
        filepath: JSONL 文件绝对路径
        model_class: Django Model 类
        batch_size: 批量写入每批条数

    Returns:
        dict: {success, total, imported, skipped, errors(list)}
            success=True 也不代表全部成功，需要看 imported/skipped
    """
    if not os.path.isfile(filepath):
        return {'success': False, 'error': f'文件不存在: {filepath}'}

    field_names = [f.name for f in model_class._meta.fields]
    # 外键字段映射: name -> attname，如 'user' -> 'user_id'
    fk_name_to_attname = {}
    for f in model_class._meta.fields:
        if f.is_relation and f.many_to_one:
            fk_name_to_attname[f.name] = f.attname
    auto_now_fields = set()
    for f in model_class._meta.fields:
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            auto_now_fields.add(f.name)

    result = {
        'success': True,
        'total': 0,
        'imported': 0,
        'skipped': 0,
        'errors': [],
    }

    batch = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            result['total'] += 1

            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                result['errors'].append(f'第 {line_no} 行 JSON 解析失败: {e}')
                result['skipped'] += 1
                continue

            # 过滤有效字段，去掉 auto_now 字段（让 DB 自动填充）
            clean = {}
            for k, v in data.items():
                if k in field_names and k not in auto_now_fields:
                    # 外键字段：用 attname（user_id）代替 name（user），避免 Django 需要模型实例
                    effective_k = fk_name_to_attname.get(k, k)
                    # 尝试还原 datetime 字符串
                    if isinstance(v, str) and len(v) == 19 and v[4] == '-' and v[7] == '-':
                        try:
                            from django.utils.dateparse import parse_datetime
                            parsed = parse_datetime(v)
                            if parsed:
                                clean[effective_k] = parsed
                                continue
                        except Exception:
                            pass
                    clean[effective_k] = v

            if not clean:
                result['skipped'] += 1
                continue

            batch.append(model_class(**clean))
            if len(batch) >= batch_size:
                _do_bulk_create(batch, model_class, result)
                batch = []

        if batch:
            _do_bulk_create(batch, model_class, result)

    return result


def _do_bulk_create(instances: list, model_class, result: dict):
    """批量创建，冲突时降级为自动分配 ID"""
    try:
        objs = model_class.objects.bulk_create(instances, ignore_conflicts=False)
        result['imported'] += len(objs)
    except Exception as e:
        # ID 冲突 → 降级为自动分配 ID
        logger.warning("恢复冲突 (使用原始ID): %s，降级为自动分配ID", e)
        try:
            for obj in instances:
                obj.pk = None
            objs = model_class.objects.bulk_create(instances, ignore_conflicts=True)
            result['imported'] += len(objs)
            if len(objs) < len(instances):
                result['skipped'] += len(instances) - len(objs)
        except Exception as e2:
            logger.error("批量恢复失败: %s", e2)
            result['errors'].append(f'批量写入失败: {e2}')
            result['skipped'] += len(instances)
