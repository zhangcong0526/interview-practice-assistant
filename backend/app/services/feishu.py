"""飞书在线文档抓取。

飞书 docx 页面首屏只在 HTML 里内联约 239 个块，其余正文由前端带着
动态生成的 ccm-meta 令牌，按 cursor 逐页调用
/space/api/docx/pages/client_vars 拉取。令牌在页面 JS 里签名，
服务端无法离线复现，所以这里用无头浏览器加载页面，
顺带截获每一页分页响应，把 block_map 合并成完整文档。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, field

PAGE_API_RE = re.compile(r"/space/api/docx/pages/client_vars")

BLOCK_PREFIX = {
    "heading1": "# ",
    "heading2": "## ",
    "heading3": "### ",
    "heading4": "#### ",
    "heading5": "##### ",
    "heading6": "###### ",
    "bullet": "- ",
    "ordered": "- ",
    "todo": "- [ ] ",
    "quote": "> ",
}
SKIP_TYPES = {"page", "table_cell"}


class FeishuError(RuntimeError):
    pass


def _discover_chromium() -> str | None:
    """Playwright 的 Python 包与 Node 包各自绑定构建号，
    这里复用机器上任意一个已下载的 chromium，避免重复下载几百 MB。"""
    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit

    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not root.is_dir():
        return None

    candidates: list[tuple[int, str]] = []
    for entry in root.iterdir():
        name = entry.name
        if not name.startswith(("chromium-", "chromium_headless_shell-")):
            continue
        try:
            build = int(name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        for relative in (
            "chrome-headless-shell-win64/chrome-headless-shell.exe",
            "chrome-win64/chrome.exe",
            "chrome-linux/chrome",
            "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        ):
            executable = entry / relative
            if executable.exists():
                candidates.append((build, str(executable)))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


@dataclass
class FeishuDocument:
    title: str
    text: str
    block_count: int = 0
    page_count: int = 0
    complete: bool = False
    partial_reason: str = ""
    sequence: list[str] = field(default_factory=list)


def _block_text(block: dict) -> str:
    payload = block.get("data", {}).get("text")
    if not isinstance(payload, dict):
        return ""
    initial = payload.get("initialAttributedTexts")
    if not isinstance(initial, dict):
        return ""
    fragments = initial.get("text")
    if isinstance(fragments, str):
        return fragments
    if not isinstance(fragments, dict):
        return ""
    keys = sorted(fragments, key=lambda item: int(item) if str(item).isdigit() else 0)
    return "".join(str(fragments[key] or "") for key in keys)


def _collect_text(block_map: dict, block_id: str, depth: int = 0) -> str:
    if depth > 6:
        return ""
    block = block_map.get(block_id)
    if not isinstance(block, dict):
        return ""
    parts = [_block_text(block).strip()]
    for child in block.get("data", {}).get("children") or []:
        if isinstance(child, str):
            parts.append(_collect_text(block_map, child, depth + 1))
    return " ".join(part for part in parts if part).strip()


def _render_table(block_map: dict, data: dict, consumed: set[str]) -> list[str]:
    rows = [item for item in data.get("rows_id") or [] if isinstance(item, str)]
    columns = [item for item in data.get("columns_id") or [] if isinstance(item, str)]
    cell_set = data.get("cell_set")
    if not rows or not columns or not isinstance(cell_set, dict):
        return []
    lines: list[str] = []
    for row_index, row_id in enumerate(rows):
        cells: list[str] = []
        for column_id in columns:
            cell = cell_set.get(f"{row_id}{column_id}")
            block_id = cell.get("block_id") if isinstance(cell, dict) else None
            if isinstance(block_id, str):
                consumed.add(block_id)
                for child in block_map.get(block_id, {}).get("data", {}).get("children") or []:
                    if isinstance(child, str):
                        consumed.add(child)
                cells.append(_collect_text(block_map, block_id).replace("|", "/"))
            else:
                cells.append("")
        lines.append("| " + " | ".join(cells) + " |")
        if row_index == 0:
            lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
    return lines


def render_blocks(block_map: dict, sequence: list[str]) -> str:
    """按文档顺序把块渲染成带 Markdown 记号的纯文本。"""
    consumed: set[str] = set()
    lines: list[str] = []
    for block_id in sequence:
        if block_id in consumed:
            continue
        block = block_map.get(block_id)
        if not isinstance(block, dict):
            continue
        data = block.get("data", {})
        block_type = data.get("type", "")
        if block_type in SKIP_TYPES:
            continue
        if block_type == "divider":
            lines.append("---")
            continue
        content = _block_text(block).strip()
        if content:
            lines.append(BLOCK_PREFIX.get(block_type, "") + content)
        if block_type == "table":
            lines.extend(_render_table(block_map, data, consumed))
    return "\n".join(lines)


def is_feishu_doc_url(url: str) -> bool:
    return bool(re.search(r"https://[^/]*(feishu\.cn|larksuite\.com|larkoffice\.com)/(docx|docs|wiki)/", url))


def fetch_document(url: str, timeout_ms: int = 90_000, settle_ms: int = 2_500) -> FeishuDocument:
    """用无头浏览器加载飞书文档，截获全部分页响应后合并正文。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可执行的提示
        raise FeishuError(
            "缺少 playwright 依赖，无法抓取飞书长文档。请在 backend 目录执行："
            "pip install playwright 且 python -m playwright install chromium。"
        ) from exc

    block_map: dict = {}
    sequence: list[str] = []
    seen_ids: set[str] = set()
    pages = 0
    saw_final_page = False
    snapshot: dict | None = None

    def on_response(response) -> None:
        nonlocal pages, saw_final_page
        if not PAGE_API_RE.search(response.url):
            return
        try:
            payload = json.loads(response.text())
        except Exception:
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        chunk = data.get("block_map")
        if isinstance(chunk, dict):
            block_map.update(chunk)
        for block_id in data.get("block_sequence") or []:
            if isinstance(block_id, str) and block_id not in seen_ids:
                seen_ids.add(block_id)
                sequence.append(block_id)
        pages += 1
        if data.get("has_more") is False:
            saw_final_page = True

    with sync_playwright() as runtime:
        executable = _discover_chromium()
        launch_options: dict = {"args": ["--disable-dev-shm-usage"]}
        if executable:
            launch_options["executable_path"] = executable
        try:
            browser = runtime.chromium.launch(**launch_options)
        except Exception as exc:
            raise FeishuError(
                "无法启动无头浏览器抓取飞书文档，请执行 "
                "python -m playwright install chromium 安装浏览器内核。"
                f"（原始错误：{exc}）"
            ) from exc
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("response", on_response)
            # 连续抓取时飞书会短暂断连，退避重试即可恢复。
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        page.wait_for_timeout(4000 * (attempt + 1))
            if last_error is not None:
                raise FeishuError(
                    f"打开飞书文档失败：{last_error}。若频繁导入被限流，请稍等片刻再试。"
                ) from last_error

            # 分页是滚动/空闲驱动的，这里轮询到 has_more=false 或块数不再增长为止。
            deadline = timeout_ms
            waited = 0
            stable_rounds = 0
            last_count = -1
            while waited < deadline:
                page.wait_for_timeout(settle_ms)
                waited += settle_ms
                if saw_final_page:
                    break
                if len(block_map) == last_count:
                    stable_rounds += 1
                    if stable_rounds >= 4:
                        break
                else:
                    stable_rounds = 0
                    last_count = len(block_map)
                try:
                    page.mouse.wheel(0, 6000)
                except Exception:
                    pass
            # 页面内存里的 block_map 才有权威的根块与顶层顺序，
            # 分页响应只保证内容齐全，不保证顺序。
            try:
                snapshot = page.evaluate(
                    """() => {
                      const data = window.DATA?.clientVars?.data;
                      const map = data?.block_map || {};
                      const rootId = Object.keys(map).find(
                        key => map[key]?.data?.type === 'page' && !map[key]?.data?.parent_id
                      );
                      if (!rootId) return null;
                      const raw = map[rootId]?.data?.text?.initialAttributedTexts?.text || {};
                      const title = Object.keys(raw)
                        .sort((a, b) => Number(a) - Number(b))
                        .map(key => raw[key] || '')
                        .join('');
                      return { rootId, title, children: map[rootId]?.data?.children || [] };
                    }"""
                )
            except Exception:
                snapshot = None

            # 首屏那批块只内联在页面 HTML 里，不会再经过分页接口，
            # 缺了它们文档开头会整段丢失。
            try:
                inline_map = page.evaluate(
                    """() => window.DATA?.clientVars?.data?.block_map || {}"""
                )
                if isinstance(inline_map, dict):
                    for block_id, block in inline_map.items():
                        block_map.setdefault(block_id, block)
            except Exception:
                pass
        finally:
            browser.close()

    if not block_map:
        raise FeishuError(
            "未能取到飞书文档正文。请确认该文档已开启「互联网上获得链接的人可阅读」，"
            "或改用导出文件 / 粘贴正文导入。"
        )

    title = ""
    top_level: list[str] = []
    if isinstance(snapshot, dict):
        title = (snapshot.get("title") or "").strip()
        top_level = [item for item in snapshot.get("children") or [] if isinstance(item, str)]

    if top_level:
        # 顶层顺序只列出一级块，嵌套内容要顺着 children 展开，
        # 否则渲染顺序会退化成分页到达顺序。
        ordered: list[str] = []
        placed: set[str] = set()

        def expand(block_id: str, depth: int = 0) -> None:
            if depth > 8 or block_id in placed or block_id not in block_map:
                return
            placed.add(block_id)
            ordered.append(block_id)
            for child in block_map[block_id].get("data", {}).get("children") or []:
                if isinstance(child, str):
                    expand(child, depth + 1)

        for block_id in top_level:
            expand(block_id)
        ordered.extend(item for item in sequence if item not in placed)
        sequence = ordered

    if not sequence:
        root_id = next(
            (
                bid
                for bid, blk in block_map.items()
                if blk.get("data", {}).get("type") == "page" and not blk.get("data", {}).get("parent_id")
            ),
            "",
        )
        children = block_map.get(root_id, {}).get("data", {}).get("children") or []
        sequence = [item for item in children if isinstance(item, str)]

    text = render_blocks(block_map, sequence)
    missing = [bid for bid in sequence if bid not in block_map]
    complete = saw_final_page and not missing
    reason = ""
    if not complete:
        reason = (
            f"仅取到 {len(sequence) - len(missing)} / {len(sequence)} 个内容块"
            if missing
            else "未收到分页结束标记，正文可能不完整"
        )

    if not title and text:
        title = text.splitlines()[0].lstrip("# ").strip()

    return FeishuDocument(
        title=title,
        text=text,
        block_count=len(block_map),
        page_count=pages,
        complete=complete,
        partial_reason=reason,
        sequence=sequence,
    )
