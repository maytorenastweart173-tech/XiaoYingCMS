import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 .env 配置文件
# override=True：确保 .env 中的值覆盖 uwsgi 等外部进程设置的环境变量
load_dotenv(BASE_DIR / '.env', override=True)

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-fallback-key-change-in-production')

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# 跨域设置
SECURE_CROSS_ORIGIN_OPENER_POLICY = "None"
# 跨域请求配置，允许所有源的跨域请求
CORS_ORIGIN_ALLOW_ALL = True


# 应用定义配置
# https://docs.djangoproject.com/en/5.2/ref/settings/#installed-apps

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders', # 跨域请求中间件
    'XiaoYingAdmin.apps.XiaoyingadminConfig', # 后台
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', # 全站批量设置安全相关 HTTP 响应头，防御多种浏览器层面攻击，是全站安全第一道防线
    'XiaoYingAdmin.middleware.firewall.FirewallMiddleware', # 防火墙：放在最外层（洋葱模型最外层），IP/页面黑名单拦截优先于所有中间件
    'XiaoYingAdmin.middleware.spider_log.SpiderLogMiddleware', # 蜘蛛日志：放在最外层（洋葱模型 response 阶段最后处理），即便 SeoCloak/DomainBind 短路 return response 也能被记录
    'XiaoYingAdmin.middleware.statistics_code.StatisticsCodeMiddleware', # 统计代码注入：必须放在最前面！响应阶段是反向洋葱模型，SeoCloak/DomainBind 直接 return HttpResponse 会短路后续中间件。放最前面可保证它们的 response 也能被注入
    'XiaoYingAdmin.middleware.static_file_serve.StaticFileServeMiddleware', # 静态文件路由：白名单路径→根目录文件映射，在斗篷/域名绑定之前处理
    'XiaoYingAdmin.middleware.seo_cloak.SeoCloakMiddleware', # 斗篷伪装：先按 UA/Referer 决定是否替换/重定向（必须在 DomainBind 之前，否则被截胡）
    'XiaoYingAdmin.middleware.domain_bind.DomainBindMiddleware', # 域名绑定：斗篷未处理时，按域名匹配渲染已绑定页面
    'django.contrib.sessions.middleware.SessionMiddleware', # 实现 Django 会话（Session）机制，维护用户服务端状态。
    'corsheaders.middleware.CorsMiddleware', # 跨域请求中间件
    'django.middleware.common.CommonMiddleware', # 用来处理如日志记录、请求计数等通用任务的中间件
    # 'django.middleware.csrf.CsrfViewMiddleware', # 用来处理CSRF攻击的中间件
    'django.contrib.auth.middleware.AuthenticationMiddleware', # 认证中间件,用来处理用户认证相关的请求
    'XiaoYingAdmin.middleware.auth.LoginRequiredMiddleware', # 登录认证中间件,未登录跳转登录页
    'XiaoYingAdmin.middleware.operation_log.OperationLogMiddleware', # 操作日志中间件,记录用户后台操作
    'django.contrib.messages.middleware.MessageMiddleware', # 消息中间件,用来处理消息相关的请求和响应
    'django.middleware.clickjacking.XFrameOptionsMiddleware', # 用来处理点击劫持攻击的中间件
    'XiaoYingAdmin.middleware.layout.LayoutMiddleware', # 菜单布局中间件,注入侧边栏菜单数据
]

ROOT_URLCONF = 'XiaoYingCMS.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'XiaoYingAdmin.middleware.layout.layout_context_processor', # 注入侧边栏菜单数据 & SEO 站点变量
            ],
        },
    },
]

WSGI_APPLICATION = 'XiaoYingCMS.wsgi.application'


# 数据库配置
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# 密码验证配置
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# 国际化配置
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'zh-hans' # 中文简体

TIME_ZONE = 'Asia/Shanghai' # 上海时间

USE_I18N = True # 开启国际化

USE_TZ = True # 开启时区支持


# 静态文件配置,比如CSS,JavaScript,Images等
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# 静态文件查找源目录：dev server 在 DEBUG=False 时也通过 urls.py 的 serve 路由
# 从 STATICFILES_DIRS[0] 读取文件（见 XiaoYingCMS/urls.py）。
# 必须指向 app 的 static 目录，否则 dev 模式下静态文件全 404。
STATICFILES_DIRS = [
    BASE_DIR / 'XiaoYingAdmin' / 'static',
]


# 自定义用户模型
AUTH_USER_MODEL = 'XiaoYingAdmin.User'

# 登录 URL（未登录跳转地址）
LOGIN_URL = '/xiaoying_admin/login/'

# 默认主键字段类型配置
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# 媒体文件配置
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# 站点配置（从 .env 读取，供 XiaoYingAdmin/middleware/layout.py 上下文处理器使用）
SITE_NAME = os.getenv('SITE_NAME', '小影CMS管理系统')
VERSION = os.getenv('VERSION', '1.0.0')


# API地址: 本框架采用小影API服务
XIAOYING_API_URL = os.getenv('API_URL', 'http://127.0.0.1:8000')

# 备份目录（可自定义绝对路径，如 D:/backups）
BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')
if not os.path.isabs(BACKUP_DIR):
    BACKUP_DIR = os.path.join(str(BASE_DIR), BACKUP_DIR)
