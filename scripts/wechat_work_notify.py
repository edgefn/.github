#!/usr/bin/env python3
"""企业微信机器人 webhook 通知脚本。

支持 4 种通知类型（通过 NOTIFY_TYPE 环境变量选择）：
  - deploy_result : 部署最终结果
  - deploy        : 部署任务提交（异步）
  - build         : 构建完成
  - merge         : PR 合并（默认）

用法：
  # 真实发送
  NOTIFY_TYPE=deploy_result WECHAT_WEBHOOK_KEY=xxx python3 scripts/wechat_work_notify.py

  # 仅打印 markdown，不发送
  NOTIFY_TYPE=merge python3 scripts/wechat_work_notify.py --dry
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# ECS 环境前缀，对外展示时去除，让通知标题更简洁
ECS_PREFIXES = ("r9s-dev-", "r9s-stag-", "r9s-prod-")

# 构建/部署英文状态 → 中文标签
STATUS_LABELS = {
    "success": "成功",
    "failure": "失败",
    "cancelled": "已取消",
}

# 微信 markdown 单条消息最大字节数（含 UTF-8 多字节字符）
WECHAT_MARKDOWN_MAX_BYTES = 4096

# HTTP 配置
REQUEST_TIMEOUT = 10  # 秒
MAX_RETRIES = 3  # 重试次数
RETRY_BACKOFF = 1.0  # 初始等待（秒），指数退避


# ---------------------------------------------------------------------------
# 环境变量工具
# ---------------------------------------------------------------------------


def env(name: str, default: str = "") -> str:
    """读取环境变量，不存在则返回 default。"""
    return os.environ.get(name, default)


def required(name: str) -> str:
    """读取必需的环境变量，缺失则抛 ValueError。"""
    value = env(name)
    if not value:
        raise ValueError(f"Missing env var: {name}")
    return value


# ---------------------------------------------------------------------------
# 公共辅助函数
# ---------------------------------------------------------------------------


def display_service_name(service_name: str) -> str:
    """去除 ECS 环境前缀，让 customer-facing 通知标题更简洁。"""
    for prefix in ECS_PREFIXES:
        if service_name.startswith(prefix):
            return service_name[len(prefix) :]
    return service_name


def _repo() -> str:
    """仓库名，兼容 REPO 和 GITHUB_REPOSITORY。"""
    return env("REPO", env("GITHUB_REPOSITORY", ""))


def _short_sha() -> str:
    """git commit SHA 前 7 位，兼容本地和 GitHub Actions。"""
    return env("SHA", env("GITHUB_SHA", ""))[:7]


def _short_actor() -> str:
    """操作人，兼容本地和 GitHub Actions。"""
    return env("ACTOR", env("GITHUB_ACTOR", ""))


def _status_label(status: str, default: str = "未完成") -> str:
    """将英文状态映射为中文标签，未知状态返回 default。"""
    return STATUS_LABELS.get(status, default)


def _with_run_url(content: str) -> str:
    """如果 RUN_URL 已设置，在 markdown 末尾追加工作流链接。"""
    run_url = env("RUN_URL", "")
    if run_url:
        content += f"**详情**：[查看工作流]({run_url})\n"
    return content


# ---------------------------------------------------------------------------
# 各通知类型的 markdown 构建
# ---------------------------------------------------------------------------


def _build_deploy_result() -> str:
    """部署最终结果通知（服务已达到稳定状态）。"""
    service_name = display_service_name(env("SERVICE_NAME"))
    build_status = env("BUILD_STATUS")
    deploy_status = env("DEPLOY_STATUS")
    # 构建失败时整体取构建状态，否则取部署状态
    status = deploy_status if build_status == "success" else build_status
    icon = "✅" if status == "success" else "❌"

    content = (
        f"### {icon} {env('PLATFORM')} {service_name} 部署{_status_label(status)}\n"
        f"**环境**：{env('ENV_NAME')}\n"
        f"**区域**：{env('REGION')}\n"
        f"**仓库**：{_repo()}\n"
        f"**版本**：{env('RELEASE_TAG')} ({env('SHA', env('GITHUB_SHA'))[:7]})\n"
        f"**操作人**：{_short_actor()}\n"
        f"**构建状态**：{build_status}\n"
        f"**部署状态**：{deploy_status}\n"
    )
    return _with_run_url(content)


def _build_deploy() -> str:
    """部署任务提交通知（异步，不代表服务已稳定）。"""
    service_name = display_service_name(env("SERVICE_NAME", ""))
    deploy_status = env("DEPLOY_STATUS", "")

    content = (
        f"### 🚀 {service_name} 部署任务已提交\n"
        f"**环境**：{env('ENV_NAME', 'test')}\n"
        f"**区域**：{env('REGION', '')}\n"
        f"**仓库**：{_repo()}\n"
        f"**版本**：{_short_sha()}\n"
        f"**操作人**：{_short_actor()}\n"
        f"**提交状态**：{deploy_status}\n"
        f"**详情**：[查看工作流]({env('RUN_URL', '')})\n"
    )
    return _with_run_url(content)


def _build_build() -> str:
    """构建完成通知。"""
    service_name = display_service_name(env("SERVICE_NAME", ""))
    deploy_status = env("DEPLOY_STATUS", "")
    icon = "✅" if deploy_status == "success" else "❌"
    status_label = _status_label(deploy_status, default=deploy_status or "未知")

    content = (
        f"### {icon} {service_name} 构建{status_label}\n"
        f"**环境**：{env('ENV_NAME', 'test')}\n"
        f"**区域**：{env('REGION', '')}\n"
        f"**仓库**：{_repo()}\n"
        f"**版本**：{_short_sha()}\n"
        f"**操作人**：{_short_actor()}\n"
        f"**构建状态**：{status_label}\n"
        f"**详情**：[查看工作流]({env('RUN_URL', '')})\n"
    )
    return _with_run_url(content)


def _build_pr_merge() -> str:
    """PR 合并通知（默认类型）。"""
    pr_author = env("PR_AUTHOR", "")
    pr_number = env("PR_NUMBER", "")
    pr_title = env("PR_TITLE", "")
    pr_url = env("PR_URL", "")
    base_ref = env("BASE_REF", "")
    head_ref = env("HEAD_REF", "")

    if not pr_number and pr_url:
        pr_number = pr_url.rstrip("/").split("/")[-1]

    title_line = "### ✅ PR 已合并"
    if pr_number and pr_url:
        title_line = f"### ✅ PR [{pr_number}]({pr_url}) 已合并"

    return (
        f"{title_line}\n"
        f"**仓库**：{_repo()}\n"
        f"**提交人**：{pr_author}\n"
        f"**分支**：{head_ref} → {base_ref}\n"
        f"**标题**：{pr_title}\n"
        f"**详情**：[查看工作流]({env('RUN_URL', '')})\n"
    )


# ---------------------------------------------------------------------------
# 入口：分发到对应构建函数
# ---------------------------------------------------------------------------

# 通知类型 → 构建函数映射，新增类型只需在此注册
_BUILDERS: dict[str, Callable[[], str]] = {
    "deploy_result": _build_deploy_result,
    "deploy": _build_deploy,
    "build": _build_build,
    "merge": _build_pr_merge,
}


def build_markdown() -> str:
    """根据 NOTIFY_TYPE 分发到对应的 markdown 构建函数。"""
    notify_type = env("NOTIFY_TYPE", "merge")
    builder = _BUILDERS.get(notify_type, _build_pr_merge)
    return builder()


# ---------------------------------------------------------------------------
# 发送
# ---------------------------------------------------------------------------


def post_wechat_work_markdown(webhook_key: str, content: str) -> None:
    """发送企业微信机器人 markdown 消息。

    - 内容超过微信单条限制时直接拒绝，避免静默截断
    - 网络/HTTP 错误最多重试 MAX_RETRIES 次，指数退避
    """
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > WECHAT_MARKDOWN_MAX_BYTES:
        raise RuntimeError(
            f"Markdown 内容过长 ({len(content_bytes)} 字节 > {WECHAT_MARKDOWN_MAX_BYTES})"
        )

    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error: Optional[Exception] = None
    body = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break  # 成功退出重试循环
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            last_error = RuntimeError(f"Request failed: {e}")

        if attempt < MAX_RETRIES:
            # 指数退避：1s → 2s → 4s
            time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
    else:
        # 所有重试均失败
        raise last_error  # type: ignore[misc]

    # 解析微信返回
    try:
        result = json.loads(body)
    except Exception:
        raise RuntimeError(f"Unexpected response: {body}")

    if result.get("errcode") != 0:
        raise RuntimeError(f"WeChat Work error: {body}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        parser = argparse.ArgumentParser(
            description="Send WeChat Work webhook notification"
        )
        parser.add_argument(
            "--dry", action="store_true", help="Do not send request; print content only"
        )
        args = parser.parse_args()

        content = build_markdown()
        if args.dry:
            print(content)
            return 0

        webhook_key = required("WECHAT_WEBHOOK_KEY")
        post_wechat_work_markdown(webhook_key, content)
        return 0
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
