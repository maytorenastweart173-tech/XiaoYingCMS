"""
页面生成任务模型 — 跟踪每一次 AI 页面生成的进度。

为什么需要：
  - AI 调用可能耗时数十秒甚至分钟级，前端需要轮询进度
  - 进度数据必须在服务端持久化：用户刷新页面后再次进入时仍能恢复"生成中"状态
  - 任务结果（HTML）需要长期保存，便于用户查看历史生成

字段要点：
  - task_id: UUID，对外暴露的任务标识
  - session_key: 关联的 session，跨请求查询用
  - status: pending / running / completed / failed
  - progress: 0-100 的整数
  - message: 当前阶段的文字描述（"正在请求 AI 接口..."等）
  - input_content: 用户提交的原始描述
  - prompt_snapshot: 此次任务实际使用的提示词快照（便于回溯）
  - result_html: 成功后保存的 AI 输出
  - error_message: 失败时的错误详情
"""

import uuid

from django.conf import settings
from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class PageGenerationTask(BaseModel):
    """
    页面生成任务记录。
    """

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, '等待中'),
        (STATUS_RUNNING, '生成中'),
        (STATUS_COMPLETED, '已完成'),
        (STATUS_FAILED, '失败'),
    )

    task_id = models.UUIDField(
        '任务 ID',
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        help_text='对外暴露的 UUID',
    )
    session_key = models.CharField(
        '会话标识',
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        help_text='关联的 Django session_key',
    )
    status = models.CharField(
        '状态',
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(
        '进度',
        default=0,
        help_text='0-100',
    )
    message = models.TextField(
        '状态消息',
        blank=True,
        default='',
        help_text='当前阶段描述',
    )
    input_content = models.TextField(
        '用户输入',
        blank=True,
        default='',
    )
    prompt_snapshot = models.TextField(
        '提示词快照',
        blank=True,
        default='',
        help_text='此次任务实际使用的提示词，便于回溯',
    )
    result_html = models.TextField(
        '生成结果',
        blank=True,
        default='',
    )
    error_message = models.TextField(
        '错误信息',
        blank=True,
        default='',
    )
    page_name = models.CharField(
        '页面名称',
        max_length=128,
        blank=True,
        default='',
        help_text='AI 总结出的简短页面名称，如"爱思助手"',
    )
    domain = models.CharField(
        '绑定域名',
        max_length=255,
        blank=True,
        default='',
        help_text='生成时用户指定的绑定域名，生成完成后自动绑定到 GeneratedPage',
    )
    domain_prompt_config = models.JSONField(
        '域名与提示词配置',
        default=list,
        blank=True,
        help_text='多域名生成时每个域名及其对应的提示词配置，'
                  '格式：[{"domain": "...", "prompt_id": 1 | null}, ...]',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='创建者',
        help_text='发起该任务的用户',
    )

    class Meta:
        verbose_name = '页面生成任务'
        verbose_name_plural = verbose_name
        ordering = ['-create_time']

    def __str__(self):
        return f'{self.task_id} [{self.get_status_display()}] {self.progress}%'

    def update_progress(self, progress: int, message: str = ''):
        """便捷方法：更新进度和状态消息。

        注意：不会保存 result_html/error_message，这两个字段需要显式保存。
        """
        if progress >= 100:
            self.status = self.STATUS_COMPLETED
            self.progress = 100
        else:
            self.status = self.STATUS_RUNNING
            self.progress = progress
        if message:
            self.message = message
        self.save(update_fields=['status', 'progress', 'message', 'updated_time'])

    def mark_failed(self, error_message: str):
        """便捷方法：标记任务失败。"""
        self.status = self.STATUS_FAILED
        self.error_message = error_message
        self.progress = 100
        self.save(update_fields=['status', 'error_message', 'progress', 'updated_time'])
