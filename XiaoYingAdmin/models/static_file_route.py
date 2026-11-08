# -*- coding: utf-8 -*-
"""静态文件路由模型 — 白名单路径→根目录文件映射"""

from django.db import models
from django.conf import settings
from XiaoYingAdmin.common.base import BaseModel


class StaticFileRoute(BaseModel):
    """静态文件白名单路由规则

    当请求路径匹配白名单时，从网站根目录查找并返回对应文件。
    例如：请求 /aa.txt，白名单中有 aa.txt，则返回 {BASE_DIR}/aa.txt。
    """
    path = models.CharField(
        max_length=255, unique=True,
        verbose_name="文件路径",
        help_text="如: aa.txt、image/logo.png、static/*，请求路径去掉开头的 / 后与此比较",
    )
    is_active = models.BooleanField(default=True, verbose_name="启用")
    description = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="备注说明",
    )
    custom_not_found_msg = models.TextField(
        blank=True, default="",
        verbose_name="自定义找不到文件提示",
        help_text="文件存在但读取失败或找不到时返回此内容，留空则返回默认提示",
    )

    class Meta:
        db_table = 'static_file_route'
        verbose_name = '静态文件路由'
        verbose_name_plural = '静态文件路由'
        ordering = ['-create_time']

    def __str__(self):
        return self.path

    @classmethod
    def get_active_paths(cls):
        """获取所有启用的路径列表"""
        return list(cls.objects.filter(is_active=True).values_list('path', flat=True))

    @classmethod
    def match_path(cls, request_path: str):
        """判断请求路径是否匹配白名单

        返回匹配到的 StaticFileRoute 实例，无匹配返回 None。
        支持：
        - 精确匹配：请求 /aa.txt ↔ 白名单 aa.txt
        - 通配符后缀：请求 /image/logo.png ↔ 白名单 image/*
        """
        # 去掉开头的 /
        clean = request_path.lstrip('/')
        if not clean:
            return None

        rules = cls.objects.filter(is_active=True)
        for rule in rules:
            rule_path = rule.path
            # 精确匹配
            if clean == rule_path:
                return rule
            # 通配符后缀匹配: path/*
            if rule_path.endswith('/*'):
                prefix = rule_path[:-2]  # 去掉 /*
                if clean.startswith(prefix):
                    return rule
        return None

    @classmethod
    def get_file_path(cls, request_path: str):
        """根据请求路径获取对应文件在根目录的绝对路径"""
        clean = request_path.lstrip('/')
        if not clean:
            return None
        base_dir = getattr(settings, 'BASE_DIR', None)
        if not base_dir:
            return None
        import os
        return os.path.join(str(base_dir), clean)
