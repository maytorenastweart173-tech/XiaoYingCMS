"""
多页面生成配置模型 — AI 提示词、模型选择等集中管理。

使用方式：
  config = MultiPageConfig.get_config()
  prompt = config.system_prompt
  model = config.model_name
"""

from django.db import models
from XiaoYingAdmin.common.base import BaseModel


class MultiPageConfig(BaseModel):
    """
    多页面 AI 生成的全局配置。

    采用单行模式（全局仅一条配置），所有设置集中管理。
    通过 get_config() 获取或创建默认配置。
    """

    # ── AI 生成参数 ──
    max_tokens = models.PositiveIntegerField(
        '最大输出 Token',
        default=32768,
        help_text='AI 返回的最大 token 数，越大输出越长',
    )
    timeout = models.PositiveIntegerField(
        '请求超时(秒)',
        default=300,
        help_text='API 调用超时时间，单位秒',
    )

    # ── 生成参数 ──
    max_pages = models.PositiveIntegerField(
        '最大页面数',
        default=4,
        help_text='AI 一次生成的最大页面数',
    )
    page_content_max_chars = models.PositiveIntegerField(
        '每页最大字符数',
        default=8000,
        help_text='每个页面 HTML 内容的字符上限',
    )

    # ── 系统提示词 ──
    system_prompt = models.TextField(
        '系统提示词',
        default='',
        help_text='多页面 AI 生成的系统提示词。留空使用内置默认',
    )

    # ── 扩展配置（未来使用） ──
    extra_config = models.JSONField(
        '扩展配置',
        default=dict,
        blank=True,
        help_text='预留的扩展配置，JSON 格式',
    )

    class Meta:
        verbose_name = '多页面生成配置'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'多页面配置 (max_pages={self.max_pages})'

    def to_dict(self):
        return {
            'id': self.id,
            'max_tokens': self.max_tokens,
            'timeout': self.timeout,
            'max_pages': self.max_pages,
            'page_content_max_chars': self.page_content_max_chars,
            'system_prompt': self.system_prompt,
            'extra_config': self.extra_config,
            'create_time': self.create_time.isoformat() if self.create_time else None,
            'updated_time': self.updated_time.isoformat() if self.updated_time else None,
        }

    @classmethod
    def get_config(cls):
        """获取当前配置（单行模式），不存在则创建默认配置。"""
        config = cls.objects.first()
        if config is None:
            config = cls.objects.create()
        return config
