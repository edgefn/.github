#!/usr/bin/env python3
"""Discord webhook 通知脚本。

复用 wechat_work_notify.py 的 markdown 构建逻辑，将其转换为
Discord embed 格式后发送。支持相同的 4 种 NOTIFY_TYPE。

用法：
  # 真实发送
  NOTIFY_TYPE=deploy_result DISCORD_WEBHOOK_URL=xxx python3 scripts/discord_notify.py

  # 仅打印 embed JSON，不发送
  NOTIFY_TYPE=merge python3 scripts/discord_notify.py --dry
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

# 同目录下的 wechat_work_notify，复用其 markdown 构建函数和辅助函数
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechat_work_notify import build_markdown, env, required  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Discord embed 颜色（十进制）
# ---------------------------------------------------------------------------

# 状态 → Discord embed 颜色
STATUS_COLORS = {
    "success": 5763719,    # 绿色
    "failure": 15548997,   # 红色
    "cancelled": 10070709, # 灰色
}
DEFAULT_COLOR = 15548997  # 默认红色

# Discord embed 单条 description 最大字符数
DISCORD_EMBED_DESC_MAX = 4096
DISCORD_EMBED_TITLE_MAX = 256

# HTTP 配置
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0


# ---------------------------------------------------------------------------
# markdown → Discord embed 解析
# ---------------------------------------------------------------------------


def _extract_title_and_description(markdown: str) -> tuple[str, str]:
    """从 markdown 中分离标题和正文。

    规则：第一行以 ### 开头则视为标题，其余行作为 description。
    如果没有标题行，则整个 markdown 作为 description，title 留空。
    """
    lines = markdown.strip().splitlines()
    if lines and lines[0].startswith("### "):
        title = lines[0][4:].strip()  # 去掉 "### "
        description = "\n".join(lines[1:]).strip()
    else:
        title = ""
        description = markdown.strip()
    return title, description


def _resolve_color() -> int:
    """根据通知类型和状态决定 embed 颜色。"""
    notify_type = env("NOTIFY_TYPE", "merge")

    if notify_type in ("deploy_result", "build"):
        build_status = env("BUILD_STATUS")
        deploy_status = env("DEPLOY_STATUS")
        # 构建失败优先显示红色
        status = deploy_status if build_status == "success" else build_status
    elif notify_type == "deploy":
        status = "success"  # 任务提交即成功
    else:  # merge
        status = "success"

    return STATUS_COLORS.get(status, DEFAULT_COLOR)


def build_embed() -> dict:
    """构建 Discord embed payload。"""
    markdown = build_markdown()
    title, description = _extract_title_and_description(markdown)

    run_url = env("RUN_URL", "")

    # RUN_URL 为空时，移除 description 中的空链接行 `**详情**：[查看工作流]()`
    if not run_url:
        description = "\n".join(
            line for line in description.splitlines()
            if not line.endswith("[查看工作流]()")
        ).strip()

    # title 超长截断（Discord 限制 256）
    if len(title) > DISCORD_EMBED_TITLE_MAX:
        title = title[:DISCORD_EMBED_TITLE_MAX - 3] + "..."

    # description 超长截断
    if len(description) > DISCORD_EMBED_DESC_MAX:
        description = description[:DISCORD_EMBED_DESC_MAX - 3] + "..."

    color = _resolve_color()

    # url 为空时不传，避免 Discord 渲染异常
    embed_fields = {
        "title": title,
        "description": description,
        "color": color,
    }
    if run_url:
        embed_fields["url"] = run_url

    return {"embeds": [embed_fields]}


# ---------------------------------------------------------------------------
# 发送
# ---------------------------------------------------------------------------


def post_discord_webhook(webhook_url: str, payload: dict) -> None:
    """发送 Discord webhook 消息。

    - 网络/HTTP 错误最多重试 MAX_RETRIES 次，指数退避
    - Discord 返回非 2xx 状态码时抛异常
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error: Optional[Exception] = None
    body = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                webhook_url,
                data=data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    # Cloudflare 会拦截无 User-Agent 的请求（error 1010）
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            # 429 是 Discord 限流，也需要重试
            last_error = RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            last_error = RuntimeError(f"Request failed: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
    else:
        raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        parser = argparse.ArgumentParser(
            description="Send Discord webhook notification"
        )
        parser.add_argument(
            "--dry", action="store_true",
            help="Do not send request; print embed JSON only"
        )
        args = parser.parse_args()

        embed = build_embed()
        if args.dry:
            print(json.dumps(embed, ensure_ascii=False, indent=2))
            return 0

        webhook_url = required("DISCORD_WEBHOOK_URL")
        post_discord_webhook(webhook_url, embed)
        return 0
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
