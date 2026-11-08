# -*- coding: utf-8 -*-
"""静态文件路由中间件 — 将白名单路径映射到根目录文件"""

import os
import mimetypes
import logging

from django.http import HttpResponse, HttpResponseNotFound
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings

logger = logging.getLogger(__name__)


class StaticFileServeMiddleware(MiddlewareMixin):
    """根据 StaticFileRoute 白名单从根目录提供静态文件

    处理流程：
    1. 请求路径 → 去掉开头的 /
    2. 查找匹配的白名单规则
    3. 匹配 → 在 BASE_DIR 中查找对应文件
    4. 文件存在 → 返回文件内容（自动识别 MIME 类型）
    5. 文件不存在 → 返回自定义 404
    """

    def process_request(self, request):
        # 只处理 GET/HEAD 请求
        if request.method not in ('GET', 'HEAD'):
            return None

        # 跳过后台路径
        path = request.path
        if path.startswith('/xiaoying_admin/'):
            return None

        try:
            from XiaoYingAdmin.models.static_file_route import StaticFileRoute

            # 匹配白名单
            rule = StaticFileRoute.match_path(path)
            if not rule:
                return None  # 不在白名单中，放行

            # 获取文件路径
            file_path = StaticFileRoute.get_file_path(path)
            if not file_path:
                return self._not_found(rule)

            # 检查文件是否存在
            if not os.path.isfile(file_path):
                return self._not_found(rule)

            # 读取文件并返回
            try:
                with open(file_path, 'rb') as f:
                    content = f.read()

                content_type, _ = mimetypes.guess_type(file_path)
                if not content_type:
                    content_type = 'application/octet-stream'

                return HttpResponse(content, content_type=content_type)

            except (IOError, OSError) as e:
                logger.warning("静态文件路由读取失败: %s - %s", file_path, e)
                return self._not_found(rule)

        except Exception as e:
            logger.error("静态文件路由中间件异常: %s", e, exc_info=True)
            return None  # 出错时静默放行

    def _not_found(self, rule):
        """返回自定义或默认的 404 响应"""
        msg = rule.custom_not_found_msg or '文件未找到'
        return HttpResponseNotFound(
            f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>404 文件未找到</title>'
            f'<style>body{{font-family:sans-serif;text-align:center;padding:80px 20px;background:#f5f5f5}}'
            f'h1{{color:#e74c3c;font-size:48px;margin:0 0 10px}}p{{color:#666;font-size:16px}}</style>'
            f'</head><body><h1>404</h1><p>{msg}</p></body></html>',
            content_type='text/html; charset=utf-8',
        )
