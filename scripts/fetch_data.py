#!/usr/bin/env python3
"""
雷军抖音 & 微博粉丝数每日追踪脚本
Lei Jun Douyin & Weibo Follower Count Daily Tracker

用法 / Usage:
    python scripts/fetch_data.py              # 正常运行，跳过已有当日数据
    python scripts/fetch_data.py --overwrite  # 强制覆盖当日数据
    python scripts/fetch_data.py --test       # 测试模式，不写入文件
"""

import json
import os
import re
import sys
import time
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ============================================================
# 配置 / Configuration
# ============================================================
WEIBO_UID = "1749127163"                       # 雷军微博 UID（注意：2397992731 是异常账号）
DOUYIN_SEC_UID = "MS4wLjABAAAAompXkPoYOGsA152dqYoytKycjIZ_aCCxHwGmLX5IsDM"  # 雷军抖音 sec_uid（抖音号：xmleijun）
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data.json")
TIMEOUT = 30  # 请求超时秒数
TZ = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_data")

HEADERS_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ============================================================
# 网络请求工具 / HTTP helpers
# ============================================================
def http_get_json(url, headers=None, extra_headers=None, timeout=TIMEOUT, retries=3):
    """发送 GET 请求并返回 JSON，失败返回 None（带重试）"""
    last_err = None
    for attempt in range(retries):
        hdrs = dict(headers or HEADERS_MOBILE)
        if extra_headers:
            hdrs.update(extra_headers)
        req = Request(url, headers=hdrs, method="GET")
        body = ""
        try:
            with urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                body = resp.read().decode(charset, errors="replace")
                return json.loads(body)
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            last_err = f"HTTP {e.code} for {url}, body: {body}"
            log.error(last_err)
        except URLError as e:
            last_err = f"URL error for {url}: {e.reason}"
            log.error(last_err)
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error for {url}: {e}, body preview: {body[:200]}"
            log.error(last_err)
        except Exception as e:
            last_err = f"Request failed for {url}: {e}"
            log.error(last_err)
        # 指数退避重试
        if attempt < retries - 1:
            wait = 2 ** attempt
            log.info(f"第 {attempt + 1} 次重试，等待 {wait}s...")
            time.sleep(wait)
    return None


def http_get_html(url, headers=None, extra_headers=None, timeout=TIMEOUT, retries=3):
    """发送 GET 请求并返回 HTML 文本，失败返回 None（带重试）"""
    last_err = None
    for attempt in range(retries):
        hdrs = dict(headers or HEADERS_MOBILE)
        if extra_headers:
            hdrs.update(extra_headers)
        req = Request(url, headers=hdrs, method="GET")
        try:
            with urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            last_err = f"HTTP {e.code} for {url}, body: {body}"
            log.error(last_err)
        except URLError as e:
            last_err = f"URL error for {url}: {e.reason}"
            log.error(last_err)
        except Exception as e:
            last_err = f"Request failed for {url}: {e}"
            log.error(last_err)
        # 指数退避重试
        if attempt < retries - 1:
            wait = 2 ** attempt
            log.info(f"第 {attempt + 1} 次重试，等待 {wait}s...")
            time.sleep(wait)
    return None


def parse_render_data(html):
    """
    从抖音用户主页 HTML 中解析 RENDER_DATA 字段
    返回解码后的 dict，失败返回 None
    """
    m = re.search(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        from urllib.parse import unquote
        return json.loads(unquote(m.group(1)))
    except Exception as e:
        log.error(f"RENDER_DATA 解析失败: {e}")
        return None


def safe_int(value):
    """将各种格式的数值安全转为 int，失败返回 None"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace(",", "")
    try:
        if s.endswith(("万", "w")):
            return int(float(s[:-1]) * 10000)
        if s.endswith(("亿", "e")):
            return int(float(s[:-1]) * 100000000)
        return int(float(s))
    except (ValueError, TypeError):
        return None


# ============================================================
# 微博访客 Cookie / Weibo Visitor Cookie
# ============================================================
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def get_visitor_cookie():
    """
    通过微博访客系统自动获取临时 Cookie（SUB / SUBP）
    无需登录，有效期有限，适合定时任务场景
    """
    # Step 1: 获取访客 tid
    url1 = "https://passport.weibo.com/visitor/genvisitor"
    data1 = b"cb=gen_callback&fp=%7B%7D"
    req1 = Request(url1, data=data1, method="POST", headers={
        "User-Agent": UA_DESKTOP,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urlopen(req1, timeout=TIMEOUT) as resp1:
        body1 = resp1.read().decode("utf-8")
    tid_m = re.search(r'"tid"\s*:\s*"([^"]+)"', body1)
    if not tid_m:
        log.error(f"Weibo visitor: 无法获取 tid, response: {body1[:200]}")
        return None
    tid = tid_m.group(1)

    # Step 2: 用 tid 换取 SUB / SUBP
    url2 = (
        f"https://passport.weibo.com/visitor/visitor"
        f"?a=incarnate&t={tid}&w=2&c=095&cb=cross_domain&from=weibo"
    )
    req2 = Request(url2, headers={"User-Agent": UA_DESKTOP})
    with urlopen(req2, timeout=TIMEOUT) as resp2:
        body2 = resp2.read().decode("utf-8")
    sub_m = re.search(r'"sub"\s*:\s*"([^"]+)"', body2)
    subp_m = re.search(r'"subp"\s*:\s*"([^"]+)"', body2)
    if not sub_m or not subp_m:
        log.error(f"Weibo visitor: 无法获取 Cookie, response: {body2[:200]}")
        return None
    return f"SUB={sub_m.group(1)}; SUBP={subp_m.group(1)}"


# ============================================================
# 微博粉丝数获取 / Weibo
# ============================================================
def fetch_weibo_followers(uid=WEIBO_UID):
    """
    通过微博 AJAX API + 访客 Cookie 获取粉丝数（免登录）
    """
    try:
        cookie = get_visitor_cookie()
        if not cookie:
            log.warning("Weibo: 访客 Cookie 获取失败")
            return None
    except Exception as e:
        log.error(f"Weibo: 访客 Cookie 异常: {e}")
        return None

    url = f"https://weibo.com/ajax/profile/info?uid={uid}"
    req = Request(url, headers={
        "User-Agent": UA_DESKTOP,
        "Cookie": cookie,
        "Referer": f"https://weibo.com/u/{uid}",
    })
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        log.error(f"Weibo: HTTP {e.code}")
        return None
    except Exception as e:
        log.error(f"Weibo: 请求异常: {e}")
        return None

    if data.get("ok") != 1:
        log.warning(f"Weibo: API 返回异常 ok={data.get('ok')}, response: {json.dumps(data, ensure_ascii=False)[:300]}")
        return None

    user = data.get("data", {}).get("user", {})
    if not user:
        log.warning("Weibo: 未找到 user 字段")
        return None

    count = safe_int(user.get("followers_count"))
    screen = user.get("screen_name", uid)

    if count is not None:
        log.info(f"Weibo [{screen}]: {count:,} 粉丝")
        return count

    log.warning(f"Weibo: 无法解析粉丝数, keys={list(user.keys())}")
    return None


# ============================================================
# 抖音粉丝数获取 / Douyin
# ============================================================
def _normalize_douyin_count(raw, nickname=None):
    """
    抖音后台存储的 follower_count 是「实际粉丝数 × 100」，
    渲染时再除以 100 显示。当我们直接拿到原始值时需要做缩放。
    """
    count = safe_int(raw)
    if count is None:
        return None
    # 经验值：原生 API 字段就是精确数；SSR 抓到的也是精确数。
    # 仅有少数情况下拿到的是放大值（×100），通过数量级判断做归一化。
    if count > 10**11:  # 不可能超过 1000 亿，做回退
        count = count // 100
    return count


def fetch_douyin_followers_api(sec_uid=DOUYIN_SEC_UID):
    """
    方案一：通过抖音 Web API（aweme/v1/web/user/profile/other/）获取粉丝数
    优点：结构化数据稳定
    缺点：无有效 Cookie 时会被风控拦截
    """
    url = (
        "https://www.douyin.com/aweme/v1/web/user/profile/other/"
        f"?sec_user_id={sec_uid}&aid=6383&device_platform=webapp"
    )
    cookie = os.environ.get("DOUYIN_COOKIE", "")
    extra = {
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json",
    }
    if cookie:
        extra["Cookie"] = cookie

    data = http_get_json(url, extra_headers=extra)
    if not data:
        log.warning("Douyin[API]: 请求失败（可能需要有效 Cookie）")
        return None, None

    user = data.get("user", {})
    if not user:
        log.warning(
            f"Douyin[API]: 未找到 user 字段, "
            f"response keys={list(data.keys())}, status={data.get('status_code')}"
        )
        return None, None

    count = _normalize_douyin_count(
        user.get("follower_count") or user.get("followerCount"),
        user.get("nickname"),
    )
    nickname = user.get("nickname", sec_uid[:16])
    if count is not None:
        log.info(f"Douyin[API] [{nickname}]: {count:,} 粉丝")
        return count, nickname

    log.warning(f"Douyin[API]: 无法解析粉丝数, keys={list(user.keys())}")
    return None, None


def _save_debug_html(html, label="douyin"):
    """失败时保存 HTML 到 .uploads/ 供排查"""
    try:
        today = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
        debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".uploads")
        os.makedirs(debug_dir, exist_ok=True)
        path = os.path.join(debug_dir, f"{label}_{today}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html[:10000])  # 只保存前 10KB
        log.info(f"调试 HTML 已保存到 {path}")
    except Exception as e:
        log.warning(f"保存调试 HTML 失败: {e}")


def fetch_douyin_followers_ssr(sec_uid=DOUYIN_SEC_UID):
    """
    方案二：直接抓取用户主页 HTML，从多种数据源中解析粉丝数
    优点：不需要 Cookie，不依赖 _signature / X-Bogus 签名
    缺点：依赖前端 SSR 注入结构；IP 触发风控时拿到的是滑块挑战页
    """
    url = f"https://www.douyin.com/user/{sec_uid}"
    extra = {
        "Referer": "https://www.douyin.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    html = http_get_html(url, extra_headers=extra)
    if not html:
        log.warning("Douyin[SSR]: 用户主页 HTML 获取失败")
        return None, None

    # 0) 反爬挑战页检测：拿到的是 byted_acrawler 滑块页时直接判定为失败
    if "byted_acrawler" in html or "__ac_signature" in html or "_$jsvmprt" in html:
        log.warning("Douyin[SSR]: 命中反爬挑战页（byted_acrawler），本方案不可用")
        _save_debug_html(html, "douyin_challenge")
        return None, None

    # 1) 优先尝试 RENDER_DATA 结构化解析
    data = parse_render_data(html)
    if data:
        blob = json.dumps(data, ensure_ascii=False)
        # follower_count 在 userInfo / app.userInfo / user 等多个位置都可能存在
        for key in ("follower_count", "followerCount"):
            m = re.search(rf'"{key}"\s*:\s*(\d+)', blob)
            if m:
                count = _normalize_douyin_count(m.group(1))
                # 尝试同时拿到昵称
                nick_m = re.search(r'"nickname"\s*:\s*"([^"]+)"', blob)
                nickname = nick_m.group(1) if nick_m else sec_uid[:16]
                if count is not None:
                    log.info(f"Douyin[SSR-RENDER] [{nickname}]: {count:,} 粉丝")
                    return count, nickname

    # 1.5) 尝试 _ROUTER_DATA 或 __NEXT_DATA__ 等其他注入数据
    for data_id in ("_ROUTER_DATA", "__NEXT_DATA__"):
        m = re.search(rf'<script id="{data_id}"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            try:
                from urllib.parse import unquote
                raw = unquote(m.group(1))
                blob = raw
                for key in ("follower_count", "followerCount"):
                    km = re.search(rf'"{key}"\s*:\s*(\d+)', blob)
                    if km:
                        count = _normalize_douyin_count(km.group(1))
                        nick_m = re.search(r'"nickname"\s*:\s*"([^"]+)"', blob)
                        nickname = nick_m.group(1) if nick_m else sec_uid[:16]
                        if count is not None:
                            log.info(f"Douyin[SSR-{data_id}] [{nickname}]: {count:,} 粉丝")
                            return count, nickname
            except Exception as e:
                log.warning(f"Douyin[SSR-{data_id}] 解析失败: {e}")

    # 2) Fallback: HTML 正则匹配「粉丝 xxx 万」
    m = re.search(r"粉丝\s*([\d.]+)\s*万", html)
    if m:
        count = safe_int(m.group(1) + "万")
        if count is not None:
            log.info(f"Douyin[SSR-Regex] [{sec_uid[:16]}]: {count:,} 粉丝")
            return count, None

    # 2.5) Fallback: 从 <title> 标签提取粉丝数（格式如 "雷军 - 抖音" 或 "雷军的抖音主页,粉丝:4248.0万"）
    title_m = re.search(r"<title>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if title_m:
        title_text = title_m.group(1).strip()
        # 匹配 "粉丝:4248.0万" 或 "粉丝：4248万" 等格式
        fan_m = re.search(r"粉丝[：:]\s*([\d.]+)\s*万?", title_text)
        if fan_m:
            raw_fan = fan_m.group(1)
            if "万" in title_text[fan_m.start():fan_m.end() + 1]:
                raw_fan += "万"
            count = safe_int(raw_fan)
            if count is not None:
                log.info(f"Douyin[SSR-Title] [{sec_uid[:16]}]: {count:,} 粉丝")
                return count, None

    # 全部解析方式均未命中，保存 HTML 供排查
    log.warning("Douyin[SSR]: 所有解析方式均未命中粉丝数")
    _save_debug_html(html, "douyin_no_match")
    return None, None


def fetch_douyin_followers(sec_uid=DOUYIN_SEC_UID):
    """
    抖音粉丝数获取入口：先尝试方案一（API + Cookie），失败时自动 fallback 到方案二（SSR）
    最多重试 3 轮，每轮间隔 60 秒（指数退避）
    返回粉丝数（int）或 None
    """
    max_rounds = 3
    for round_idx in range(max_rounds):
        # 方案一：API + Cookie
        count, nickname = fetch_douyin_followers_api(sec_uid)
        if count is not None:
            return count

        log.info("Douyin: 方案一（API）失败，自动切换到方案二（SSR 抓取用户主页）")
        count, nickname = fetch_douyin_followers_ssr(sec_uid)
        if count is not None:
            return count

        # 两套方案本轮均失败
        if round_idx < max_rounds - 1:
            wait = 60 * (round_idx + 1)
            log.warning(f"Douyin: 第 {round_idx + 1} 轮两套方案均失败，{wait} 秒后重试（共 {max_rounds} 轮）...")
            time.sleep(wait)

    log.error("Douyin: 3 轮重试均失败，两套获取方案均无法获取数据")
    return None


# ============================================================
# 邮件通知 / Email Notification
# ============================================================
def send_cookie_expired_email():
    """
    发送 Douyin Cookie 过期提醒邮件
    需要环境变量: SMTP_USERNAME, SMTP_PASSWORD
    """
    smtp_user = os.environ.get("SMTP_USERNAME", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_pass:
        log.warning("邮件通知: 未配置 SMTP_USERNAME 或 SMTP_PASSWORD，跳过")
        return False

    to_email = "digger-yu@outlook.com"
    subject = "[雷军粉丝追踪] 抖音 Cookie 已过期，请更新"

    body = f"""你好，

抖音粉丝数据获取失败，可能是 Cookie 已过期。

请登录以下步骤更新 Cookie：
1. 浏览器访问 https://www.douyin.com 并登录
2. 按 F12 打开开发者工具
3. 切换到 Network 标签
4. 刷新页面，找到任意请求
5. 复制 Request Headers 中的 Cookie 值
6. 更新到 GitHub Secrets: Settings → Secrets → DOUYIN_COOKIE

仓库地址: https://github.com/digger-yu/lei/settings/secrets/actions

时间: {datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")}

---
此邮件由 GitHub Actions 自动发送
"""

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP("smtp-mail.outlook.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        log.info(f"邮件通知: 已发送到 {to_email}")
        return True
    except Exception as e:
        log.error(f"邮件通知: 发送失败: {e}")
        return False


# ============================================================
# 主逻辑 / Main
# ============================================================
def load_data():
    """加载已有数据文件"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"weibo_uid": WEIBO_UID, "douyin_sec_uid": DOUYIN_SEC_UID, "records": []}


def save_data(data):
    """保存数据到文件"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"数据已保存到 {DATA_FILE}")


def main():
    overwrite = "--overwrite" in sys.argv
    test_mode = "--test" in sys.argv

    today = datetime.now(TZ).strftime("%Y-%m-%d")

    # 加载已有数据
    data = load_data()

    # 检查今日是否已有记录
    existing = [r for r in data["records"] if r["date"] == today]
    if existing and not overwrite:
        log.info(f"今日 ({today}) 数据已存在，跳过。使用 --overwrite 可强制更新")
        return

    log.info(f"开始获取雷军粉丝数据 ({today})...")

    weibo = fetch_weibo_followers()
    douyin = fetch_douyin_followers()

    # 抖音失败时发送邮件通知（两套方案都失败才发，避免 Cookie 临时失效误报）
    if douyin is None:
        log.error("Douyin: 数据获取失败（两套方案均失败），发送 Cookie 过期提醒邮件")
        send_cookie_expired_email()

    if weibo is None and douyin is None:
        log.error("所有平台数据获取均失败，本次不写入")
        sys.exit(1)

    record = {
        "date": today,
        "weibo": weibo,
        "douyin": douyin,
    }

    if test_mode:
        log.info(f"[测试模式] 获取结果: {json.dumps(record, ensure_ascii=False)}")
        return

    # 更新或追加记录
    if existing:
        data["records"] = [r for r in data["records"] if r["date"] != today]
    data["records"].append(record)
    data["records"].sort(key=lambda r: r["date"])

    save_data(data)
    log.info("完成!")


if __name__ == "__main__":
    main()
