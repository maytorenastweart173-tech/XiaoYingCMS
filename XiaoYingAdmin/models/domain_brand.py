"""
域名品牌配置 — 为某个域名（含多级子域名）统一配置 Logo 与站点图标。

设计要点：
  - 一条记录对应一个域名；可以是根域名（example.com），也可以是子域名（m.example.com）
  - 解析时优先精确匹配请求域名，其次逐级去掉最左侧标签向上回退，
    因此根域名的配置会自动被所有子域名继承，子域名也可以单独覆盖
  - 图标文件全部落在 media/domain_brand/ 下，通过站点自身的
    /favicon.ico、/apple-touch-icon.png 等稳定路径对外提供，便于搜索引擎抓取
"""

from django.db import models

from XiaoYingAdmin.common.base import BaseModel


def _brand_path(prefix, instance, filename):
    """按域名分目录存放，便于清理与排查。"""
    domain = (instance.domain or 'unknown').strip().lower() or 'unknown'
    return f'domain_brand/{prefix}/{domain}/{filename}'


def source_upload(instance, filename):
    return _brand_path('source', instance, filename)


def logo_upload(instance, filename):
    return _brand_path('logo', instance, filename)


def favicon_upload(instance, filename):
    return _brand_path('favicon', instance, filename)


def icon_upload(instance, filename):
    return _brand_path('icon', instance, filename)


def og_upload(instance, filename):
    return _brand_path('og', instance, filename)


class DomainBrand(BaseModel):
    """域名级品牌配置（Logo / Favicon / SEO 图片）。"""

    domain = models.CharField(
        '域名',
        max_length=255,
        unique=True,
        help_text='根域名或子域名，如 example.com、m.example.com；'
                  '根域名的配置会被所有子域名继承',
    )
    brand_name = models.CharField(
        '品牌名称',
        max_length=100,
        blank=True,
        help_text='用于 SEO 的 og:site_name 与结构化数据',
    )
    enabled = models.BooleanField('启用', default=True)

    # 生成源图（可选）：上传一张方形原图，自动生成各尺寸
    source_image = models.FileField(
        '原图', upload_to=source_upload,
        blank=True, null=True,
        help_text='建议 512×512 以上的方形 PNG/JPG，系统会自动生成各尺寸',
    )

    # 各尺寸资源
    logo = models.FileField('页面 Logo', upload_to=logo_upload, blank=True, null=True)
    favicon = models.FileField('favicon.ico', upload_to=favicon_upload, blank=True, null=True)
    icon_32 = models.FileField('32×32', upload_to=icon_upload, blank=True, null=True)
    icon_180 = models.FileField('180×180（apple-touch）', upload_to=icon_upload, blank=True, null=True)
    icon_192 = models.FileField('192×192', upload_to=icon_upload, blank=True, null=True)
    icon_512 = models.FileField('512×512', upload_to=icon_upload, blank=True, null=True)
    og_image = models.FileField('社交分享图', upload_to=og_upload, blank=True, null=True)

    # 手工上传过的尺寸，自动生成时会跳过，避免覆盖
    manual_sizes = models.JSONField('手动上传的尺寸', default=list, blank=True)

    replace_page_logo = models.BooleanField(
        '替换页面内 Logo', default=True,
        help_text='把页面头部的品牌 Logo 替换为本域名的 Logo；如页面结构特殊可关闭',
    )
    seo_enabled = models.BooleanField(
        '输出 SEO 图片标签', default=True,
        help_text='输出 og:image、twitter:image、JSON-LD logo 等标签',
    )
    logo_selector = models.CharField(
        'Logo CSS 选择器', max_length=200, blank=True, default='',
        help_text='可选：精确指定页面内 Logo 元素，留空则自动识别头部 Logo',
    )

    # 必应站点验证（Bing Webmaster Tools）
    bing_verify_code = models.CharField(
        '必应验证码', max_length=128, blank=True, default='',
        help_text='Bing Webmaster Tools 提供的 msvalidate.01 验证码；'
                  '配置后本域名及子域名会提供 /BingSiteAuth.xml 并输出 Meta 标签',
    )

    # robots.txt / sitemap.xml
    robots_txt = models.TextField(
        '自定义 robots.txt', blank=True, default='',
        help_text='留空自动生成：User-agent: * / Allow: / / Sitemap: 当前域名',
    )
    sitemap_scope = models.CharField(
        'Sitemap 主机范围', max_length=16, default='inherit',
        choices=[
            ('inherit', '继承主域名范围'),
            ('custom', '自定义主机列表'),
        ],
        help_text='inherit：自动收录主域名 + 已知子域名；custom：只收录 sitemap_hosts 中的主机',
    )
    sitemap_hosts = models.JSONField(
        'Sitemap 主机列表', default=list, blank=True,
        help_text='额外或自定义的主机名列表，如 ["aaa.example.com", "bbb.example.com"]',
    )
    sitemap_extra_urls = models.TextField(
        'Sitemap 额外 URL', blank=True, default='',
        help_text='每行一个，支持 /path 相对路径或完整 https:// 地址',
    )

    class Meta:
        verbose_name = '域名品牌'
        verbose_name_plural = '域名品牌'
        ordering = ['domain']

    def __str__(self):
        return self.domain

    # ------------------------------------------------------------------
    # 尺寸元数据（供生成、上传、前端展示共用）
    # ------------------------------------------------------------------
    SIZE_FIELDS = {
        'logo': ('页面 Logo', '任意'),
        'favicon': ('favicon.ico', '16/32/48'),
        'icon_32': ('32×32', '32'),
        'icon_180': ('180×180', '180'),
        'icon_192': ('192×192', '192'),
        'icon_512': ('512×512', '512'),
        'og_image': ('社交分享图', '1200×630'),
    }

    # 站点对外提供的稳定路径 → 模型字段
    ASSET_PATHS = {
        '/favicon.ico': 'favicon',
        '/apple-touch-icon.png': 'icon_180',
        '/icon-32.png': 'icon_32',
        '/icon-192.png': 'icon_192',
        '/icon-512.png': 'icon_512',
        '/site-logo.png': 'logo',
        '/og-image.png': 'og_image',
    }

    # 必应站点验证文件路径（按访问域名动态返回，不落物理文件）
    BING_AUTH_PATH = '/BingSiteAuth.xml'

    # ------------------------------------------------------------------
    # 域名解析：精确 → 逐级向上回退（子域名继承根域名）
    # ------------------------------------------------------------------
    @classmethod
    def normalize_domain(cls, value):
        from urllib.parse import urlparse

        raw = (value or '').strip().lower()
        if not raw:
            return ''
        if '://' in raw:
            raw = urlparse(raw).netloc or ''
        raw = raw.split('/')[0].split(':')[0].strip().strip('.')
        if raw.startswith('*.'):
            # 通配符与根域名等价：根域名配置本身就会被子域名继承
            raw = raw[2:]
        if raw.startswith('www.'):
            raw = raw[4:]
        return raw

    @classmethod
    def resolve(cls, host):
        """按请求域名找到应生效的品牌配置，找不到返回 None。"""
        domain = cls.normalize_domain(host)
        if not domain or '.' not in domain:
            return None

        labels = domain.split('.')
        candidates = []
        for i in range(len(labels) - 1):
            candidates.append('.'.join(labels[i:]))

        for candidate in candidates:
            obj = cls.objects.filter(domain=candidate, enabled=True).first()
            if obj:
                return obj
        return None

    # ------------------------------------------------------------------
    # 资源访问
    # ------------------------------------------------------------------
    def asset_version(self):
        """用更新时间做缓存版本号，改图后链接自动变化。"""
        stamp = self.updated_time.strftime('%Y%m%d%H%M') if self.updated_time else '0'
        return stamp

    def asset_url(self, field_name, *, absolute=False, host=''):
        """返回某尺寸的对外访问地址（优先站点稳定路径，其次 media 直链）。"""
        field = getattr(self, field_name, None)
        if not field:
            return ''

        path = ''
        for url_path, name in self.ASSET_PATHS.items():
            if name == field_name:
                path = url_path
                break

        if path:
            url = f'{path}?v={self.asset_version()}'
        else:
            url = field.url

        if absolute:
            host = (host or self.domain).strip().lower()
            return f'https://{host}{url}' if url.startswith('/') else url
        return url

    def preview_url(self, field_name):
        """后台预览用的 media 直链（不依赖绑定域名，后台可直接访问）。"""
        field = getattr(self, field_name, None)
        if not field:
            return ''
        url = field.url
        version = self.asset_version()
        return f'{url}?v={version}' if '?' not in url else url

    def to_dict(self, *, include_preview=False):
        data = {
            'id': self.id,
            'domain': self.domain,
            'brand_name': self.brand_name,
            'enabled': self.enabled,
            'replace_page_logo': self.replace_page_logo,
            'seo_enabled': self.seo_enabled,
            'logo_selector': self.logo_selector,
            'bing_verify_code': self.bing_verify_code or '',
            'bing_configured': bool(self.bing_verify_code),
            'robots_txt': self.robots_txt or '',
            'sitemap_scope': self.sitemap_scope or 'inherit',
            'sitemap_hosts': self.sitemap_hosts or [],
            'sitemap_extra_urls': self.sitemap_extra_urls or '',
            'manual_sizes': self.manual_sizes or [],
            'updated_at': self.updated_time.strftime('%Y-%m-%d %H:%M:%S') if self.updated_time else '',
            'assets': {
                name: bool(getattr(self, name))
                for name in self.SIZE_FIELDS
            },
        }
        if include_preview:
            data['preview'] = {
                name: self.preview_url(name) for name in self.SIZE_FIELDS
            }
        return data
