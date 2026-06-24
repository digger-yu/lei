# lei - 雷军粉丝追踪

每日自动追踪雷军微博 & 抖音粉丝数，GitHub Pages 展示曲线。

## 部署

### 1. 克隆仓库

```bash
git clone https://github.com/<用户名>/lei.git
cd lei
git remote set-url origin https://github.com/<用户名>/lei.git
git push -u origin main
```

### 2. 启用 GitHub Pages

1. 进入仓库页面，点击 **Settings**
2. 左侧菜单找到 **Pages**
3. **Build and deployment** → **Source** 选择 **GitHub Actions**
   （会自动使用仓库中的 `.github/workflows/pages.yml` 部署）

### 3. 配置 Secrets

1. **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加：
   - Name: `DOUYIN_COOKIE`
   - Value: 浏览器登录 www.douyin.com → F12 → Network → 任意请求 → 复制 Cookie 头
   - Name: `SMTP_USERNAME`
   - Value: 你的 Outlook 邮箱地址（用于 Cookie 过期提醒）
   - Name: `SMTP_PASSWORD`
   - Value: Outlook 应用专用密码（非登录密码）
3. 微博无需配置，已改用访客系统自动获取

### 4. 首次运行

1. 进入 **Actions** 标签页
2. 左侧选择 **每日获取雷军粉丝数据**
3. 点击 **Run workflow** → **Run workflow** 手动触发
4. 等待完成后访问 `https://<用户名>.github.io/lei/` 查看仪表盘

## Workflows

| Workflow | 文件 | 触发方式 | 作用 |
|----------|------|----------|------|
| 每日数据采集 | `daily-fetch.yml` | 每天北京时间 10:00 定时触发 / 手动触发 | 调用微博、抖音 API 获取粉丝数，直接写入 `docs/data.json` |
| Pages 部署 | `pages.yml` | `docs/` 目录有变更时自动触发 / 手动触发 | 将 `docs/` 目录部署到 GitHub Pages |

数据流：`daily-fetch` 采集数据 → 提交 `docs/data.json` → 触发 `pages` 重新部署 → 仪表盘更新

## 本地运行

```bash
python scripts/fetch_data.py --test       # 测试，不写文件
python scripts/fetch_data.py              # 正常运行
python scripts/fetch_data.py --overwrite  # 覆盖今日数据
```

本地运行微博同样免 Cookie。抖音需设置环境变量：

```bash
# PowerShell
$env:DOUYIN_COOKIE="你的抖音Cookie"
python scripts/fetch_data.py
```

## 备注

- 每天北京时间 10:00 自动采集
- 微博通过访客系统免登录获取，无需手动维护 Cookie
- 抖音需手动提供 Cookie，过期后会自动发送邮件提醒到配置的邮箱
- 微博 UID: `1749127163`，抖音 sec_uid 见 `scripts/fetch_data.py`
- 数据仅供学习研究

MIT
