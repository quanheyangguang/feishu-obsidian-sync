#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书多维表格「正文」字段 vs 本地 Obsidian 七目录备份核对（严格只读）

- 凭据从 F:/wwangyp/write_to_feishu.py 导入，本文件不复制密钥
- --check : 主命令。分页拉全量 + 本地索引 + 多级匹配 + 输出 Markdown 报告
- --probe : 只拉 1 条记录，探测字段结构与类型（诊断用）
- --raw   : 打印原始响应结构（诊断用）
- --full  : --check 的兼容别名，行为完全相同
- 不写飞书、不写 Obsidian；仅写 F:/WorkBuddy 下的中间 JSON 与报告
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VAULT = Path(r"F:\wwangyp")
TARGET_DIRS = ["工作心法", "日常随笔", "技术AI", "投资理财", "运动健康", "哲学认知", "家庭生活"]
EXCLUDE_PARTS = {"_索引"}          # 非文稿目录
BACKUP_ROOT = Path(r"F:\WorkBuddy\backups")   # 迁移前备份（仅用于定位，不算七目录备份）
IGNORE_PARTS = {".workbuddy", ".git", ".trash", ".obsidian", "_索引", "__pycache__"}
RAW_JSON = Path(r"F:\WorkBuddy\04_脚本工具\_feishu_records_tmp.json")
REPORT_DIR = Path(r"F:\WorkBuddy\02_文档产出")
REPORT = REPORT_DIR / f"{datetime.now():%Y%m%d}_飞书正文备份核对报告_V1.md"   # 按当天日期，跨天自动留历史
REPORT_PTR = Path(r"F:\WorkBuddy\04_脚本工具\_latest_report_path.txt")         # 最新报告路径指针，供补写脚本读取
PREFIX_N = 200
SHINGLE_N = 7
SIM_HIGH = 0.80                    # 高相似 → 判为「正文不一致」
SIM_LOW = 0.30                     # 低相似 → 判为「疑似」


def load_writer():
    """从已验证的写入脚本导入凭据与正文提取逻辑，保证判据逐字一致。"""
    path = VAULT / "write_to_feishu.py"
    spec = importlib.util.spec_from_file_location("_feishu_writer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = load_writer()


# ---------------- 飞书只读 API ----------------

def get_token():
    req = urllib.request.Request(
        W.FEISHU_TOKEN_URL,
        data=json.dumps({"app_id": W.APP_ID, "app_secret": W.APP_SECRET}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败：code={data.get('code')} msg={data.get('msg')}")
    return data["tenant_access_token"]


def api_get(url, token):
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 API 错误：code={data.get('code')} msg={data.get('msg')}")
    return data.get("data") or {}


def list_records(token, page_size=500, max_pages=100):
    base = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{W.APP_TOKEN}"
        f"/tables/{W.TABLE_ID}/records?page_size={page_size}"
    )
    out, page_token, pages = [], None, 0
    while True:
        url = base + (f"&page_token={page_token}" if page_token else "")
        data = api_get(url, token)
        out.extend(data.get("items") or [])   # 飞书 list records 返回键是 items，不是 records
        pages += 1
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
        if pages >= max_pages:
            raise RuntimeError(f"超过最大页数 {max_pages}，已中止以防无限循环")
    return out, pages


def flatten(value):
    """把飞书字段值（str / 富文本数组 / 对象）展平为纯文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or json.dumps(item, ensure_ascii=False)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("value") or json.dumps(value, ensure_ascii=False))
    return str(value)


# ---------------- 归一化与指纹 ----------------

def norm(s):
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def compact(s):
    return re.sub(r"\s+", "", norm(s))


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def shingles(s, n=SHINGLE_N):
    c = compact(s)[:12000]
    if len(c) < n:
        return {c} if c else set()
    return {c[i:i + n] for i in range(len(c) - n + 1)}


def strip_yaml(text):
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:])
    return text


def meta(text, key):
    m = re.search(rf"(?mi)^{re.escape(key)}\s*:\s*(.+)$", text)
    return m.group(1).strip().strip('"\'') if m else ""


def brief(s, n=80):
    s = re.sub(r"\s+", " ", norm(s)).replace("|", "\\|").replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


# ---------------- 本地索引 ----------------

def build_local_index(scope="six"):
    """scope: six=七目录; vault=全库; backups=迁移前备份区"""
    entries, no_body, errors = [], [], []
    if scope == "backups":
        roots = [(BACKUP_ROOT, "backups")]
    elif scope == "vault":
        roots = [(VAULT, "库内")]
    else:
        roots = [(VAULT / d, d) for d in TARGET_DIRS]
    for root, label in roots:
        if not root.exists():
            errors.append(f"目录不存在：{root}")
            continue
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(root)
            if IGNORE_PARTS.intersection(rel.parts):
                continue
            if scope == "vault" and rel.parts and rel.parts[0] not in TARGET_DIRS:
                pass   # 全库模式保留其他目录（如 家庭生活），用于定位
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                errors.append(f"{p}: 读取失败 {e}")
                continue
            body = W.extract_body_segment(raw)
            full = strip_yaml(raw)
            body_n, full_n = norm(body or ""), norm(full)
            if not body_n and scope == "six":
                no_body.append({"path": str(p), "title": meta(raw, "title") or p.stem})
            c = compact(full or raw)
            if not c:
                continue
            entries.append({
                "path": str(p),
                "dir": label,
                "title": meta(raw, "title") or p.stem,
                "uid": meta(raw, "UID") or meta(raw, "uid"),
                "date_line": next((ln.strip() for ln in body_n.splitlines() if ln.strip()), ""),
                "compact": c,
                "sh": shingles(c),
                "body_len": len(body_n),
                "used": False,
            })
    return entries, no_body, errors


# ---------------- 比对 ----------------

def probe_list(c, k=10, n=40):
    """从正文紧凑串均匀取 n 个 10 字探针（长片段不易跨文件误配）。"""
    if len(c) <= k:
        return [c] if c and len(re.sub(r"\d", "", c)) >= 4 else []
    step = max(1, (len(c) - k) // (n - 1))
    out = []
    for i in range(0, len(c) - k, step):
        seg = c[i:i + k]
        if len(re.sub(r"\d", "", seg)) < 5:   # 跳过几乎纯数字的日期片段
            continue
        out.append(seg)
        if len(out) >= n:
            break
    return out


def match_index(records, index_entries):
    """逐条飞书记录用多探针在本索引中找候选，返回 (results, used_ids)"""
    results = []
    for rec in records:
        fields = rec.get("fields") or {}
        key = next((k for k in fields if str(k).strip() == "正文"), None)
        text = flatten(fields.get(key)) if key else ""
        sum_key = next((k for k in fields if str(k).strip() == "摘要"), None)
        summary = flatten(fields.get(sum_key)) if sum_key else ""
        date_key = next((k for k in fields if str(k).strip() in {"日期", "创建时间"}), None)
        rec_date = fields.get(date_key) if date_key else ""
        c = compact(text)
        pr = probe_list(c)
        found = []
        for i, L in enumerate(index_entries):
            n_hit = sum(1 for x in pr if x in L["compact"])
            if n_hit >= 1:
                found.append((i, n_hit))
        results.append({
            "record_id": rec.get("record_id", ""),
            "text": text, "summary": summary, "rec_date": rec_date,
            "field_key": key, "compact": c,
            "found": found,   # [(idx, 命中探针数), ...]
        })
    return results


def classify(primary, vault_res, backup_res):
    """三层判定：七目录 → 库内其他目录 → 迁移前备份区。"""
    for p, v, b in zip(primary, vault_res, backup_res):
        if p["found"]:
            p["status"] = "七目录有备份"
            p["location"] = "七目录"
            p["which_index"] = "six"
        elif v["found"]:
            p["status"] = "库内其他目录有"
            p["location"] = "库内其他"
            p["found"] = v["found"]
            p["which_index"] = "vault"
        elif b["found"]:
            p["status"] = "仅备份区有"
            p["location"] = "backups(迁移前)"
            p["found"] = b["found"]
            p["which_index"] = "backups"
        else:
            p["status"] = "飞书有本地无"
            p["location"] = "无"
            p["which_index"] = ""
    return primary


# ---------------- 报告 ----------------

def write_report(res, locals_, vault_entries, backups, no_body, errors, pages, field_types, fetch_time):
    """res 为 classify 后的结果，found 存本地索引下标。"""
    counts = Counter(r["status"] for r in res)
    matched_paths = set()
    for r in res:
        if r["status"] == "七目录有备份" and r["found"]:
            matched_paths.add(locals_[r["found"][0][0]]["path"])
    unused = [L for L in locals_ if L["path"] not in matched_paths]

    def table(rows, headers, maker):
        out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
        for row in rows:
            out.append("| " + " | ".join(maker(row)) + " |")
        return out

    def path_of(r):
        p = r.get("matched_path", "")
        return f"`{p}`" if p else "—"

    lines = [
        "# 飞书「正文」字段与本地备份核对报告",
        "",
        f"- 生成时间：{fetch_time}",
        f"- 飞书表格：`{W.APP_TOKEN}` / `{W.TABLE_ID}`（按 table 全量拉取，不受视图筛选影响）",
        f"- 拉取页数：{pages}；记录数：**{len(res)}**",
        f"- 本地范围：`F:\\wwangyp` 下{'、'.join(TARGET_DIRS)}（排除 `_索引`），共 **{len(locals_)}** 篇；迁移前备份区 `F:\\WorkBuddy\\backups` **{len(backups)}** 篇（仅用于定位）",
        f"- 飞书字段类型：{field_types}",
        "",
        "## 一、汇总",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
        f"| 七目录有备份 | {counts.get('七目录有备份', 0)} |",
        f"| 库内其他目录有（不在七目录内） | {counts.get('库内其他目录有', 0)} |",
        f"| 仅迁移前备份区有（七目录缺失） | {counts.get('仅备份区有', 0)} |",
        f"| 飞书有、本机全无 | {counts.get('飞书有本地无', 0)} |",
        f"| 本地七目录有、飞书无 | {len(unused)} |",
        "",
        "> 判定口径：飞书正文是原始日记，本地多为加工/扩写版，逐字全文匹配会大量误判。改用**正文 40 个 10 字探针**：任一探针在本地文件全文中出现即判为有备份（内容相同、允许文字有差别）。依次查七目录 → 库内其他目录 → 迁移前备份区。",
        "",
        f"> 证据强度：命中项中 **{sum(1 for r in res if r.get('probe_hits',0)==1)} 条仅靠 1 个探针命中（弱证据）**，建议人工抽查；≥2 个探针为强证据。",
        "",
        "## 二、七目录缺失、但库内其他目录有（位置不符）",
        "",
    ]
    otherdir = [r for r in res if r["status"] == "库内其他目录有"]
    if otherdir:
        lines += table(otherdir, ["record_id", "探针数", "表格摘要", "实际位置", "正文摘要"], lambda r: [
            f"`{r['record_id']}`", str(r.get("probe_hits", "")), brief(r["summary"], 40), path_of(r), brief(r["text"], 45)
        ])
    else:
        lines.append("无。")

    lines += ["", "## 三、七目录缺失、但迁移前备份区有（建议补回七目录）", ""]
    onlybak = [r for r in res if r["status"] == "仅备份区有"]
    if onlybak:
        lines += table(onlybak, ["record_id", "探针数", "表格摘要", "备份文件", "正文摘要"], lambda r: [
            f"`{r['record_id']}`", str(r.get("probe_hits", "")), brief(r["summary"], 42), path_of(r), brief(r["text"], 55)
        ])
    else:
        lines.append("无。")

    lines += ["", "## 四、飞书有、本机（全库+备份区）均无", ""]
    miss = [r for r in res if r["status"] == "飞书有本地无"]
    if miss:
        lines += table(miss, ["record_id", "表格摘要", "正文摘要（前70字）"], lambda r: [
            f"`{r['record_id']}`", brief(r["summary"], 45), brief(r["text"], 70)
        ])
    else:
        lines.append("无。")

    lines += ["", "## 五、本地七目录有、飞书无", ""]
    if unused:
        lines += table(unused, ["所属目录", "文件", "标题"], lambda L: [
            L["dir"], f"`{Path(L['path']).name}`", brief(L["title"], 50)
        ])
    else:
        lines.append("无。")

    lines += ["", "## 六、数据质量与限制", ""]
    lines += [
        "- 早期本地文件用 `## 鹏鸣漱玉` 等旧标记或无正文段标记，故**不依赖** `## 📝 正文` 提取；直接对文件全文做探针检索。",
        f"- 飞书正文为原始日记，本地文件常为加工/扩写版，文字不完全一致；本工具判「有备份」不判「逐字相同」。",
        "- 探针法对大面积改写存在假阴性可能；第四节（本机全无）清单建议人工抽查后再定是否补录。",
        f"- 本地正文段标记统计（仅供参考）：无 `## 📝 正文` 标记的七目录文件 {len(no_body)} 篇。",
    ]
    if errors:
        lines.append("- 扫描异常：" + "；".join(errors[:10]))
    lines += ["- 本报告只做比对，不修改飞书表格，也不修改 Obsidian 任何文件。"]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    REPORT_PTR.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PTR.write_text(str(REPORT), encoding="utf-8")   # 指针：供 restore 脚本定位最新报告
    return counts, len(unused), len(miss), len(onlybak)


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="主命令：全量核对并生成报告")
    ap.add_argument("--probe", action="store_true", help="只拉 1 条记录探测字段结构（诊断用）")
    ap.add_argument("--raw", action="store_true", help="打印原始响应结构（不打印凭据，诊断用）")
    ap.add_argument("--full", action="store_true", help="--check 的兼容别名，行为相同")
    args = ap.parse_args()
    if not (args.check or args.probe or args.raw or args.full):
        ap.error("请指定 --check / --probe / --raw（--full 为 --check 别名）")

    token = get_token()
    print("[OK] 已取得 tenant_access_token（不打印）")

    if args.raw:
        for ps in (1, 100, 500):
            url = (
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{W.APP_TOKEN}"
                f"/tables/{W.TABLE_ID}/records?page_size={ps}"
            )
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="GET"
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
            obj = json.loads(raw)
            d = obj.get("data") or {}
            print(f"[RAW] page_size={ps} code={obj.get('code')} msg={obj.get('msg')} top_keys={list(obj.keys())}")
            print(f"[RAW]   data_keys={list(d.keys())} total={d.get('total')} has_more={d.get('has_more')} page_token={str(d.get('page_token'))[:30]}")
            print(f"[RAW]   raw_head={raw[:400]}")
        return

    if args.probe:
        data = api_get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{W.APP_TOKEN}"
            f"/tables/{W.TABLE_ID}/records?page_size=1", token,
        )
        recs = data.get("items") or []
        print(f"[PROBE] 首页记录数={len(recs)} has_more={data.get('has_more')}")
        for rec in recs:
            print(f"[PROBE] record_id={rec.get('record_id')}")
            for k, v in (rec.get("fields") or {}).items():
                t = flatten(v)
                print(f"[PROBE]   field={k!r} type={type(v).__name__} len={len(t)} preview={brief(t, 60)}")
        return

    if not (args.check or args.full):
        return   # 防御：仅 --check / --full 进入全量核对

    records, pages = list_records(token)
    print(f"[OK] 飞书记录拉取完成：{len(records)} 条 / {pages} 页")
    RAW_JSON.parent.mkdir(parents=True, exist_ok=True)
    RAW_JSON.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] 原始记录暂存：{RAW_JSON}")

    field_types = Counter()
    for rec in records:
        for k, v in (rec.get("fields") or {}).items():
            if str(k).strip() == "正文":
                field_types[f"{k}:{type(v).__name__}"] += 1
    ft = ", ".join(f"{k}×{v}" for k, v in field_types.items()) or "未见「正文」字段"

    locals_, no_body, errors = build_local_index("six")
    vault_entries, _, v_errors = build_local_index("vault")
    backups, _, b_errors = build_local_index("backups")
    print(f"[OK] 索引：七目录 {len(locals_)} / 全库 {len(vault_entries)} / 备份区 {len(backups)} 篇")
    primary = match_index(records, locals_)
    vault_res = match_index(records, vault_entries)
    backup_res = match_index(records, backups)
    res = classify(primary, vault_res, backup_res)
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    index_map = {"six": locals_, "vault": vault_entries, "backups": backups}
    for r in res:
        arr = index_map.get(r.get("which_index", ""), [])
        if r["found"] and arr:
            best_idx, best_hits = max(r["found"], key=lambda x: x[1])
            r["matched_path"] = arr[best_idx]["path"]
            r["probe_hits"] = best_hits
        else:
            r["matched_path"] = ""
            r["probe_hits"] = 0
    counts, n_unused, n_miss, n_bak = write_report(
        res, locals_, vault_entries, backups, no_body, errors + v_errors + b_errors, pages, ft, fetch_time
    )
    print("[OK] 比对完成：" + "; ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"[OK] 七目录有飞书无={n_unused}；飞书有本机全无={n_miss}；仅备份区有={n_bak}")
    print(f"[OK] 报告：{REPORT}")


if __name__ == "__main__":
    main()
