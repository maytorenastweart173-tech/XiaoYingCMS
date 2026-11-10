# 小影CMS · AI 驱动的全能建站系统

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.x%20%2F%206.x-green)](https://djangoproject.com)
[![Version](https://img.shields.io/badge/version-2.0.0-orange)](https://xiaoyingapi.com)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)

集 **AI 页面生成、SEO 优化、蜘蛛监控、安全防护**于一体的现代化 CMS 管理平台。直连 DeepSeek 大模型，一句话生成可直接上线的落地页或整站，并提供域名绑定、斗篷伪装、访问日志与防火墙等建站全流程能力。

---

## 目录

- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [部署上线](#部署上线)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [许可证](#许可证)
- [联系方式](#联系方式)

---

## 核心功能

### AI 智能生成
- **直连 DeepSeek 官方 API**（`https://api.deepseek.com/chat/completions`），不依赖第三方中转服务
- **单页生成** — 一句话描述生成完整落地页：8+ 区块结构、完整文案、响应式布局、全套 SEO 标签与 JSON-LD 结构化数据
- **整站生成** — 自动规划导航结构，一次生成多页面站点，支持输出截断后的自动追加续写
- **SEO 自动优化** — 生成完成后自动补全 title / description / keywords / canonical / Open Graph / 语义标签
- **思考模式可配** — 后台开关控制是否启用 DeepSeek 深度思考，兼顾质量与速度
- **提示词管理** — 多版本提示词库，支持启用/停用、按分类（页面生成 / SEO 优化）切换
- **参考网址生成描述** — 输入 1-5 个参考站地址，多个子 Agent 并行分析其 SEO、区块结构、组件与视觉风格，主 Agent 汇总成可直接使用的需求描述（仅提示友链形式，不含对方链接）
- **输出兜底处理** — 自动剥离 markdown 包裹、注入移动端安全样式、补齐 favicon，避免横向滚动与资源 404

### SEO 优化工具
- **域名快排追踪** — 树形结构组织根域名与子域名，时间线视图批量管理收录与排名
- **爬虫斗篷伪装** — 按 User-Agent / Referer 区分搜索引擎与真人，支持 301/302/303/307/308 与内容分发
- **智能互链生成** — 自动在站内页面间建立交叉链接，提升权重传递
- **域名绑定** — 域名与页面绑定，支持多域名与 `*.` 泛解析
- **友情链接** — 全局 / 分类级友链自动注入
- **页面树与分类** — 多级分类、批量归类、批量导入

### 蜘蛛监控分析
- 实时记录爬虫与真人访问（IP / UA / 路径 / 来源 / 状态码 / 响应大小）
- 按小时 / 天维度可视化蜘蛛活跃时段，识别搜索引擎爬虫占比
- 忽略路径过滤、日志导出、阈值自动备份与历史恢复

### 安全防护体系
- **防火墙** — IP 黑名单、路径黑名单、IP 白名单；支持 403 / 自定义 HTML / JS / 跳转响应
- **登录安全** — 最大登录失败次数自动封禁 IP、登录 IP 白名单、后台域名白名单
- **静态文件白名单路由** — 仅放行指定静态资源
- **登录日志 / 操作审计** — 全量登录记录、字段级操作记录，支持自动备份与恢复

### 操作体验
顶部多标签页导航（右键关闭当前 / 其他 / 左右侧），仪表盘实时数据概览，整体 LayUI 风格，响应式适配移动端。

---

## 技术栈

| 类别 | 说明 |
|------|------|
| 后端框架 | Django 5.x / 6.x |
| 数据库 | SQLite（默认）/ MySQL 8.0（推荐生产） |
| 前端 | LayUI + FontAwesome 6 + ECharts（本地化静态资源，无 CDN 依赖） |
| AI 推理 | DeepSeek 官方 API（`deepseek-v4-pro`） |
| 运行环境 | Python 3.10+ |

---

## 快速开始

### 环境要求
- Python 3.10+
- pip
- （可选）MySQL 8.0

### 本地运行

```bash
# 1. 克隆项目
git clone https://github.com/maytorenastweart173-tech/XiaoYingCMS.git
cd XiaoYingCMS

# 2. 创建并激活虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，至少修改 SECRET_KEY

# 5. 初始化数据库
python manage.py migrate

# 6. 启动开发服务器
python manage.py runserver 8003
```

访问 <http://127.0.0.1:8003/xiaoying_admin/login/> 进入后台。

> **首次启动自动建号**：系统检测到无超级管理员时，会自动创建默认账号 `xiaoying` / `xiaoyingadmin`，登录后请立即在「用户管理 → 修改密码」中更换。

### 配置 AI 能力

登录后台 → **核心设置 → 网站设置 → DeepSeek AI 配置**：

1. 填写 `API Key`（在 <https://platform.deepseek.com> 获取）
2. 按需开启「思考模式」（质量更高，但更慢、更耗 token）
3. 保存后即可在「页面管理 → 页面生成」中生成页面

> API Key 仅存于数据库，不会写入代码或前端。

---

## 配置说明

### 环境变量（`.env`）

| 配置项 | 用途说明 | 默认值 |
|--------|----------|--------|
| `SECRET_KEY` | Django 安全密钥，**生产环境必须更换** | 内置 fallback |
| `DEBUG` | 是否开启调试模式 | `False` |
| `ALLOWED_HOSTS` | 允许访问的域名，逗号分隔 | `*` |
| `SITE_NAME` | 后台站点显示名称 | `小影CMS管理系统` |
| `VERSION` | 系统版本号 | `2.0.0` |
| `API_URL` | 小影 API 地址（仅用于蜘蛛 IP 验证等辅助能力，与 AI 无关） | `http://127.0.0.1:8000` |
| `BACKUP_DIR` | 日志备份目录（相对项目根或绝对路径） | `backups` |

### 后台可配置项

| 模块 | 位置 | 说明 |
|------|------|------|
| DeepSeek API Key | 核心设置 → 网站设置 | AI 生成使用的密钥 |
| 思考模式 | 核心设置 → 网站设置 | 开启后启用 `thinking` + `reasoning_effort=high` |
| 全局统计代码 | 核心设置 → 网站设置 | 全站页面底部注入 |
| 日志备份阈值 | 核心设置 → 网站设置 | 蜘蛛 / 操作日志达阈值自动备份并清空 |
| 登录安全 | 核心设置 → 网站设置 | 失败封禁次数、IP 白名单、后台域名白名单 |
| 多页面生成参数 | 页面管理 → 多页面配置 | 最大 token、超时、页面数、每页字符上限、系统提示词 |
| 斗篷伪装 | SEO手段 → 斗篷伪装 | 按域名配置爬虫 / 搜索 / 直访策略 |
| 防火墙 | 安全防护 → 防火墙 | IP / 路径规则与响应方式 |

### 中间件处理链路

请求依次经过以下中间件：

1. **FirewallMiddleware** — 拦截黑名单 IP 与路径
2. **SpiderLogMiddleware** — 记录爬虫与真人访问日志
3. **StatisticsCodeMiddleware** — 注入全局统计代码
4. **StaticFileServeMiddleware** — 白名单静态文件路由
5. **SeoCloakMiddleware** — 爬虫斗篷伪装分发
6. **DomainBindMiddleware** — 域名与页面绑定解析
7. **LoginRequiredMiddleware** — 后台登录鉴权（IP / 域名白名单）
8. **OperationLogMiddleware** — 操作审计记录
9. **LayoutMiddleware** — 侧边栏菜单与布局渲染

---

## 部署上线

以下以 Linux + Nginx + Gunicorn 为例（Ubuntu / Debian 系）。

### 1. 拉取代码并安装依赖

```bash
cd /www/wwwroot
git clone https://github.com/maytorenastweart173-tech/XiaoYingCMS.git
cd XiaoYingCMS

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn          # 生产 WSGI 服务器
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
SECRET_KEY=请替换为随机长字符串
DEBUG=False
ALLOWED_HOSTS=your-domain.com,www.your-domain.com
SITE_NAME=小影CMS管理系统
VERSION=2.0.0
BACKUP_DIR=/www/wwwroot/XiaoYingCMS/backups
```

> 生成密钥：`python -c "import secrets; print(secrets.token_urlsafe(50))"`

### 3. 初始化数据库

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput   # 如使用 Nginx 托管静态文件
```

如需 MySQL，在 `XiaoYingCMS/settings.py` 中把 `DATABASES` 改为 MySQL 配置，并安装 `mysqlclient`。

### 4. 启动服务（Gunicorn + systemd）

创建 `/etc/systemd/system/xiaoyingcms.service`：

```ini
[Unit]
Description=XiaoYingCMS
After=network.target

[Service]
User=www
Group=www
WorkingDirectory=/www/wwwroot/XiaoYingCMS
Environment="PATH=/www/wwwroot/XiaoYingCMS/.venv/bin"
ExecStart=/www/wwwroot/XiaoYingCMS/.venv/bin/gunicorn XiaoYingCMS.wsgi:application \
          --bind 127.0.0.1:8003 --workers 3 --timeout 300
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now xiaoyingcms
systemctl status xiaoyingcms
```

> 超时必须 ≥ 300 秒：AI 生成单次请求可能持续 1-3 分钟，超时过短会导致 504。

### 5. Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 50m;

    location /static/ {
        alias /www/wwwroot/XiaoYingCMS/XiaoYingAdmin/static/;
        expires 7d;
    }

    location /media/ {
        alias /www/wwwroot/XiaoYingCMS/media/;
        expires 7d;
    }

    location / {
        proxy_pass http://127.0.0.1:8003;
        # 多域名共用同一个后端时，缓存 key 必须包含域名，
        # 否则不同域名的 favicon / Logo / BingSiteAuth.xml 会互相串
        proxy_cache_key "$scheme$host$request_uri";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

> 使用宝塔面板时，建议把 `proxy_cache_key` 放进 `vhost/nginx/extension/<站点名>/` 下的独立配置里，避免面板重写站点配置时丢失。否则同一个后端承载多个域名时，`/favicon.ico`、`/site-logo.png`、`/BingSiteAuth.xml` 等按域名返回的内容会被错误缓存到其他域名。

启用 HTTPS 建议使用 Certbot：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 6. 上线后检查

- 访问 `https://your-domain.com/xiaoying_admin/login/`，用 `xiaoying` / `xiaoyingadmin` 登录并立即改密
- 若迁移了数据库请确认 `db.sqlite3` 已就位；使用 MySQL 时先执行 `migrate`
- 在「网站设置」填写 DeepSeek API Key，提交一次页面生成验证 AI 链路
- 开启「防火墙」与「登录 IP 白名单」限制后台入口

### 升级流程

```bash
cd /www/wwwroot/XiaoYingCMS
git pull
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
systemctl restart xiaoyingcms
```

---

## 项目结构

```
XiaoYingCMS/
├── XiaoYingAdmin/                 # 核心业务代码
│   ├── common/                    # 通用工具
│   │   ├── base.py                # BaseModel（创建/更新时间）
│   │   ├── http.py                # JSON 解析、统一错误、IP 获取等
│   │   └── status_code.py         # 状态码常量
│   ├── middleware/                # 中间件
│   │   ├── auth.py                # 登录鉴权 + IP/域名白名单
│   │   ├── firewall.py            # 防火墙
│   │   ├── spider_log.py          # 蜘蛛访问日志
│   │   ├── seo_cloak.py           # 斗篷伪装
│   │   ├── domain_bind.py         # 域名绑定渲染
│   │   ├── static_file_serve.py   # 静态文件白名单路由
│   │   ├── statistics_code.py     # 统计代码注入
│   │   ├── operation_log.py       # 操作审计
│   │   └── layout.py              # 菜单与布局
│   ├── models/                    # 数据模型
│   ├── views/                     # 视图层
│   │   ├── page_generator.py      # 单页 AI 生成 + DeepSeek 调用
│   │   ├── multi_page_generator.py# 整站 AI 生成
│   │   ├── page_tree.py           # 页面树与分类
│   │   ├── batch_import.py        # 批量导入
│   │   ├── seo/                   # 域名记录、斗篷配置
│   │   ├── spider/                # 蜘蛛日志、分析、路径过滤
│   │   └── tools/                 # 导入导出
│   ├── migrations/                # 数据库迁移
│   ├── templates/                 # 前端模板（LayUI 风格）
│   ├── static/                    # 本地化静态资源
│   ├── utils/                     # 备份、域名、导入导出工具
│   ├── tests.py                   # 单元测试
│   └── admin.py
├── XiaoYingCMS/                   # Django 项目配置
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── media/                         # 上传文件与站点素材
├── .env.example                   # 环境变量模板
├── manage.py
└── requirements.txt
```

---

## 常见问题

**Q：生成页面时提示「未配置 DeepSeek API Key」？**
A：前往「核心设置 → 网站设置 → DeepSeek AI 配置」填写你自己的 Key 并保存。项目不含任何内置密钥，Key 只保存在数据库中。

**Q：生成页面时提示 DeepSeek 401 / 余额不足？**
A：检查 API Key 是否正确、账户是否有余额。

**Q：生成时间很长或报 504？**
A：AI 生成单次可能持续 1-3 分钟。请将 Nginx `proxy_read_timeout` 与 Gunicorn `--timeout` 都设置为 ≥300 秒。

**Q：整站生成页面数量不够？**
A：在「页面管理 → 多页面配置」中提高「最大页面数」与「每页最大字符」，并适当提高「最大 Token」。系统会在输出截断时自动追加续写。

**Q：静态资源 404？**
A：`DEBUG=False` 时由 Nginx `location /static/` 托管；确认 `alias` 指向 `XiaoYingAdmin/static/`，或执行 `collectstatic` 后指向 `STATIC_ROOT`。

**Q：忘记后台密码？**
A：删除数据库中的用户表或直接用 Django shell 重置：
```bash
python manage.py shell -c "from XiaoYingAdmin.models import User; u=User.objects.get(username='xiaoying'); u.set_password('新密码'); u.save()"
```

**Q：如何恢复默认提示词？**
A：在「页面生成 → 提示词管理」中停用所有自定义提示词，系统会自动回退到内置默认提示词。

---

## 许可证

本项目采用 [MIT](LICENSE) 许可证。

---

## 联系方式

- 微信：duyanbz
- Telegram：[@xiaoying1216](https://t.me/xiaoying1216)

---

*用心做好每一个站点 · 小影CMS v2.0*
