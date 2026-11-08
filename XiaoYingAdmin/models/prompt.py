"""
AI 提示词模型 — 管理页面生成所使用的提示词。

设计要点：
  - 提示词按 category（类别）分组，例如 "page_generation"
  - 同 category 下的多个记录代表不同版本；只有 is_active=True 且 version 最大的会被使用
  - 创建时自动 version+1，更新内容后保存即递增
  - 字段可扩展：未来可加 tags / variables / example_input 等

使用示例：
  from XiaoYingAdmin.models.prompt import Prompt

  prompt_text = Prompt.get_active_content('page_generation')
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


class Prompt(BaseModel):
    """
    提示词记录。

    同一 category 下可能存在多条历史版本；当前生效的提示词由
    `Prompt.get_active_content()` 选取（is_active=True, version 最大）。
    """

    CATEGORY_CHOICES = (
        ('page_generation', '页面生成'),
        ('page_optimization', '页面优化'),
        ('page_seo_optimization', 'SEO优化'),
        ('custom', '自定义'),
    )

    category = models.CharField(
        '提示词分类',
        max_length=64,
        choices=CATEGORY_CHOICES,
        default='page_generation',
        db_index=True,
        help_text='按用途分组，调用时按分类查找',
    )
    name = models.CharField(
        '提示词名称',
        max_length=128,
        help_text='人类可读的名称，如"通用页面生成 v3"',
    )
    content = models.TextField(
        '提示词内容',
        help_text='完整的提示词文本，会作为 system_prompt 发送给 AI',
    )
    version = models.PositiveIntegerField(
        '版本号',
        default=1,
        help_text='同一 category 下递增；创建时自动 = max+1',
    )
    is_active = models.BooleanField(
        '是否启用',
        default=True,
        help_text='关闭后该提示词不会被 AI 调用使用',
    )
    description = models.TextField(
        '变更说明',
        blank=True,
        default='',
        help_text='本次更新的说明，例如"调整了输出格式要求"',
    )

    class Meta:
        verbose_name = '提示词'
        verbose_name_plural = verbose_name
        ordering = ['category', '-version']
        indexes = [
            models.Index(fields=['category', 'is_active', '-version']),
        ]

    def __str__(self):
        return f'[{self.get_category_display()}] {self.name} v{self.version}'

    def to_dict(self) -> dict:
        """序列化为前端所需的字典（列表与详情共用同一结构）。"""
        from XiaoYingAdmin.common.http import fmt_dt

        return {
            'id': self.id,
            'category': self.category,
            'category_display': self.get_category_display(),
            'name': self.name,
            'content': self.content,
            'version': self.version,
            'is_active': self.is_active,
            'description': self.description,
            'create_time': fmt_dt(self.create_time),
            'update_time': fmt_dt(self.updated_time),
        }

    def save(self, *args, **kwargs):
        """创建时自动设置 version = 同分类最大版本 + 1。"""
        if not self.pk:
            max_version = (
                Prompt.objects
                .filter(category=self.category)
                .aggregate(models.Max('version'))['version__max']
            ) or 0
            self.version = max_version + 1
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls, category: str = 'page_generation'):
        """获取指定分类下当前生效的提示词（is_active=True, version 最大）。"""
        return (
            cls.objects
            .filter(category=category, is_active=True)
            .order_by('-version')
            .first()
        )

    @classmethod
    def get_all_active(cls, category: str = 'page_generation'):
        """
        获取指定分类下所有启用的提示词（is_active=True, 按 version 降序）。
        返回 list[Prompt]。
        """
        return list(
            cls.objects
            .filter(category=category, is_active=True)
            .order_by('-version')
        )

    @classmethod
    def get_active_content(cls, category: str = 'page_generation') -> str:
        """
        获取指定分类下当前生效的提示词文本。

        若数据库中无提示词记录，返回内置默认提示词（保证 AI 调用不会因为空提示词失败）。
        """
        prompt = cls.get_active(category)
        if prompt is not None:
            return prompt.content
        return DEFAULT_PAGE_GENERATION_PROMPT

    @classmethod
    def get_all_active_contents(cls, category: str = 'page_generation') -> list[dict]:
        """
        获取指定分类下所有活跃提示词，返回 system_prompt 条目列表。

        策略（按优先级排列）：
          1. 如果指定分类下有 is_active=True 的提示词 → 全部返回
          2. 没有 → 返回内置默认提示词（兜底）

        每个条目: {"role": "system", "content": "..."}
        """
        prompts = cls.objects.filter(category=category, is_active=True).order_by('-version')
        if prompts.exists():
            return [{'role': 'system', 'content': p.content} for p in prompts]
        return [{'role': 'system', 'content': DEFAULT_PAGE_GENERATION_PROMPT}]


    @classmethod
    def get_active_for_categories(cls, categories: list[str]) -> list[dict]:
        """
        获取指定多个分类下所有启用的提示词。
        如果某分类下无活跃提示词，则使用该分类的内置默认提示词兜底。

        返回: [{"role": "system", "content": "..."}, ...]
        """
        prompts = cls.objects.filter(category__in=categories, is_active=True).order_by('-version')
        all_contents = [{'role': 'system', 'content': p.content} for p in prompts]

        # 补充各分类的兜底默认提示词
        defaults = {
            'page_generation': DEFAULT_PAGE_GENERATION_PROMPT,
            'page_seo_optimization': DEFAULT_SEO_OPTIMIZATION_PROMPT,
        }
        for cat in categories:
            if not any(p.category == cat for p in prompts):
                if cat in defaults:
                    all_contents.append({'role': 'system', 'content': defaults[cat]})

        return all_contents


# 内置默认提示词 — 仅在数据库中没有任何提示词时使用
DEFAULT_PAGE_GENERATION_PROMPT = """你是一位资深的前端工程师、UI 设计师与 SEO 专家。请根据用户需求，生成一个完整、可直接上线的现代响应式 HTML 落地页。

## 输出格式（必须严格遵守）
1. 只返回完整 HTML 文档：以 <!DOCTYPE html> 开头，以 </html> 结尾
2. 不要使用 markdown 代码块包裹，不要输出任何解释性文字
3. 所有 CSS 必须内嵌在 <head> 的 <style> 标签中，不引用任何外部 CSS / JS / 字体文件
4. 不使用 LayUI、Bootstrap、Tailwind 等外部框架
5. 不在 HTML 中写任何注释
6. 文案使用中文（用户指定其他语言时除外）
7. 不要使用“...”省略代码，必须输出完整内容

## 页面结构（至少 8 个区块，按主题合理扩展）
1. 顶部导航：品牌名/Logo + 5-7 个锚点导航 + 主 CTA 按钮，移动端提供折叠菜单
2. Hero 首屏：强有力主标题、副标题、两个 CTA 按钮、信任背书（用户量/数据/评分）
3. 关键数据条：3-6 个指标（数字 + 说明）
4. 核心功能：6 个功能卡片，每卡含内联 SVG 图标、标题、2-3 句具体说明
5. 工作流程/使用步骤：3-6 步，编号清晰
6. 使用场景：3-4 个场景卡片，说明「谁在什么场景下如何受益」
7. 用户评价/客户案例：3 条，包含姓名、身份、具体评价
8. 常见问题 FAQ：5-8 条问答，使用 <details>/<summary> 实现可展开
9. 底部 CTA + Footer：版权、备案占位、联系方式、隐私/条款等链接

## 内容要求
- 每个区块都要有充实、具体、有说服力的中文文案，全页正文不少于 1200 字
- 文案要围绕用户需求中的产品/服务展开，突出卖点、差异化与行动号召
- 不要写空泛的套话，不要重复同一句话；每个功能点都要给出具体价值
- 如果用户提供了品牌名、域名、联系方式等，必须原样使用

## 设计要求
- 现代、简洁、留白充足，使用 CSS 变量统一主色与辅助色
- 使用 Flexbox / Grid 布局，完整响应式（桌面 / 平板 / 手机）
- 使用渐变、阴影、圆角、hover 动效、入场过渡提升质感
- 图片优先使用内联 SVG 插画、CSS 渐变或几何图形，避免外链图片 404
- 交互只使用原生 CSS/JS（移动端菜单、FAQ 展开、平滑滚动），不依赖外部库
- 必须避免横向滚动：html/body 设置 overflow-x:hidden；移动端菜单/抽屉不得使用超出视口的 fixed 定位（不要用 translateX(100%) 把面板推到屏幕外），确保 scrollWidth 不超过屏幕宽度
- 所有图片、SVG、视频设置 max-width:100%; height:auto
- 标题使用 text-wrap:balance，避免中文被生硬断词
- 在 <head> 中加入内联 SVG data URI favicon（不要依赖 /favicon.ico）

## SEO 要求
- <title>：包含核心关键词，30 字以内
- <meta name="description">：80-160 字，有吸引力
- <meta name="keywords">：5-10 个相关关键词
- Open Graph：og:title / og:description / og:type / og:url
- 若用户提供了绑定域名，canonical 与 og:url 必须使用该域名
- 语义化标签：header / nav / main / section / article / footer；有且仅有一个 h1，标题层级正确
- 在 </head> 前添加合适的 JSON-LD 结构化数据（Organization / WebSite / FAQPage 等）
- 所有 <img> 必须有 alt（若使用 SVG 则加 aria-label）

请直接输出完整 HTML，不要省略任何代码。"""

# 内置默认 SEO 优化提示词 — 仅在数据库中无活跃 SEO 优化提示词时使用
DEFAULT_SEO_OPTIMIZATION_PROMPT = """你是一个专业的 SEO 工程师与前端优化专家。
我会提供一段用户原始的页面需求描述，以及一段 AI 已生成的 HTML 代码。
请对这段 HTML 进行全面 SEO 优化，**只返回优化后的完整 HTML 代码，不要加任何额外说明**。

优化方向如下：
1. **Meta 标签** — 确保 <title> 包含核心关键词（3-8 个字），<meta name="description"> 简明扼要地概括页面内容（10-30 字），<meta name="keywords"> 包含页面核心关键词。如果页面已有这些标签则优化，没有则补充。
2. **语义化 HTML5** — 使用 <header>、<nav>、<main>、<section>、<article>、<aside>、<footer> 等语义标签替换冗余的 <div> 包裹。
3. **标题层级 (H1-H6)** — 确保每个页面有且仅有一个 <h1>，标题层级严格按 h1 > h2 > h3 嵌套使用。
4. **图片优化** — 检查 <img> 标签是否都有 alt 属性，缺少则补充有意义的描述性 alt 文字。
5. **链接优化** — 确保 <a> 标签有 title 属性，外部链接使用 rel="nofollow noopener"。
6. **结构化数据 (Schema.org)** — 根据页面内容类型（如文章、产品、FAQ、本地商家等）添加合适的 JSON-LD 结构化数据，放在 </head> 前。
7. **规范 URL (Canonical)** — 在 <head> 中添加 <link rel="canonical" href="..."> 占位。
8. **性能优化提示** — 设置 loading="lazy" 属性于图片，保证 CSS/JS 引用放到合适位置。
9. **移动端适配** — 确保已有 viewport meta 标签，确保页面通过媒体查询适配手机。

**重要**：
- 保持原有的页面布局、颜色、字体和整体视觉效果不变
- 不要改变用户可见的文字内容
- 不要返回任何额外说明文字，只返回完整的 HTML 代码
- 返回的 HTML 必须可以直接运行"""
