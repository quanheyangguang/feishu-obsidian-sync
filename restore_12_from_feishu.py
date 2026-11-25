# -*- coding: utf-8 -*-
"""飞书原始日记 → Obsidian 成品补写工具（通用三命令）

文件名沿用历史（首批补写 12 篇），脚本本身已通用化：
  --draft [--since YYYY-MM] [--out PATH]
      从飞书记录 + 核对报告「本机全无」节自动生成补写草稿 plan JSON；不写 Obsidian。
  --apply --plan PATH [--dry-run]
      读取（经人工审核的）plan，只新建不覆盖地写入 Obsidian，逐文件回读校验。
  --inspect --plan PATH
      只读检查 plan 完整性与目标文件是否已存在，不写任何文件。

口径（用户 2026-11-25 确认）：
  1) 正文原文照搬，仅首行规范化为 YYYY年M月D日，星期X。
  2) 分类/domain/目录按审核后的 plan 落盘。
  3) YAML 元数据按内容生成填充（draft 给建议值，人工审核）。
安全：只新建不覆盖；写完逐文件回读校验；UTF-8 No BOM；CRLF 兼容。
"""
import argparse
import datetime
import json
import pathlib
import random
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VAULT = pathlib.Path(r"F:\wwangyp")
SRC = pathlib.Path(r"F:\WorkBuddy\04_脚本工具\_feishu_records_tmp.json")
REPORT_PTR = pathlib.Path(r"F:\WorkBuddy\04_脚本工具\_latest_report_path.txt")
LEGACY_REPORT = pathlib.Path(r"F:\WorkBuddy\02_文档产出\20261125_飞书正文备份核对报告_V1.md")   # 旧固定路径兜底
DEFAULT_DRAFT = pathlib.Path(r"F:\WorkBuddy\04_脚本工具\_restore_plan_draft.json")
DIRS = ["工作心法", "日常随笔", "技术AI", "投资理财", "运动健康", "哲学认知", "家庭生活"]
DOMAIN_OF_DIR = {"技术AI": "AI技术"}
CAT_OF_DIR = {"技术AI": "AI技术"}

W = "一二三四五六日"
WEEK = lambda d: "星期" + W[d.weekday()]

MISS_HEAD = "## 四、飞书有、本机（全库+备份区）均无"
NEXT_HEAD = "## 五、"


# ---------------- 基础工具 ----------------

def infer_date(text, rec):
    """按正文内容日期判定 (date_str, 依据)，含 2027 笔误按星期修正。"""
    t = str(text or "")
    m = re.match(r"\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    if m:
        y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
        wm = re.search(r"星期([一二三四五六日])", t[:50])
        if y == 2027 and wm and WEEK(datetime.date(2026, mo, da)) == "星期" + wm.group(1):
            return f"{2026:04d}-{mo:02d}-{da:02d}", "正文2027笔误按星期修正为2026"
        try:
            datetime.date(y, mo, da)
            return f"{y:04d}-{mo:02d}-{da:02d}", "正文首行日期"
        except ValueError:
            pass
    v = (rec.get("fields") or {}).get("日期")
    if isinstance(v, int):
        d = datetime.datetime.fromtimestamp(v / 1000).date()
        return d.isoformat(), "飞书日期字段（正文无年份）"
    return "", "无法判定"


def latest_report():
    """优先读指针文件（由 --check 落盘），回退旧固定路径。"""
    if REPORT_PTR.exists():
        p = pathlib.Path(REPORT_PTR.read_text(encoding="utf-8").strip())
        if p.exists():
            return p
    return LEGACY_REPORT


def load_missing_ids():
    """从核对报告第四节取 record_id 列表（本机全无者）。"""
    rep_path = latest_report()
    if not rep_path.exists():
        raise RuntimeError(f"核对报告不存在：{rep_path}；请先跑 feishu_backup_audit.py --check")
    rep = rep_path.read_text(encoding="utf-8")
    if MISS_HEAD not in rep:
        raise RuntimeError(f"核对报告缺少章节「{MISS_HEAD}」，版本不匹配")
    sec = rep.split(MISS_HEAD)[1].split(NEXT_HEAD)[0]
    return re.findall(r"^\|\s*\x60(rec\w+)\x60", sec, re.M)


def suggest_dir_domain(text, summary):
    """关键词规则给建议目录/domain/cat；仅建议，人工审核为准。"""
    t = (str(text or "") + " " + str(summary or ""))[:2500]
    rules = [
        ("运动健康", ["血糖", "血压", "体检", "BMI", "复查", "医院", "跑步", "走路", "健身"]),
        ("投资理财", ["基金", "股票", "指数", "加仓", "持仓", "A股", "净值", "证券", "涨跌"]),
        ("技术AI", ["WorkBuddy", "Obsidian", "Calibre", "飞书多维表格", "脚本", "自动化", "Token", "AI 帮我", "与AI"]),
        ("哲学认知", ["王阳明", "中庸", "慎独", "良知", "哲学", "实修", "一念"]),
        ("家庭生活", ["妻子", "女儿", "父亲", "母亲", "菜园", "团聚", "种蒜", "剥榛子", "家里"]),
        ("工作心法", ["单位", "社区", "工单", "接诉即办", "领导", "同事", "立案", "法院", "授权"]),
    ]
    scores = []
    for d, kws in rules:
        scores.append((sum(1 for k in kws if k in t), d))
    scores.sort(reverse=True)
    best_n, best = scores[0] if scores else (0, "日常随笔")
    if best_n == 0:
        best = "日常随笔"
    domain = DOMAIN_OF_DIR.get(best, best)
    cat = CAT_OF_DIR.get(best, best)
    return best, domain, cat, best_n


def suggest_title(summary, date_str, text):
    """从飞书摘要生成描述型标题建议（非文艺型），人工审核时改写。"""
    s = re.sub(r"\s+", "", str(summary or ""))
    s = re.sub(r"^\d{1,2}月\d{1,2}日[，,。]?", "", s)
    s = re.sub(r"^20\d{2}年\d{1,2}月\d{1,2}日[，,。]?", "", s)
    s = re.sub(r"[，,。；;：:！!？?、\"'（）()《》\[\]]", "", s)
    s = re.sub(r"\s+", "", s)
    if len(s) < 4:
        s = re.sub(r"\s+", "", str(text or ""))[:18]
    return s[:20] or f"{date_str}日记"


def suggest_tags(directory, domain, text):
    tags = [domain]
    kws = {
        "基金": "基金", "股票": "股票", "指数": "指数", "血糖": "血糖", "医院": "复查",
        "AI": "AI技术", "飞书": "飞书", "Obsidian": "Obsidian", "王阳明": "王阳明",
        "菜园": "菜园", "妻子": "家人", "女儿": "家人", "社区": "基层", "法院": "法律",
        "运动": "运动", "禁食": "禁食", "阅读": "阅读",
    }
    t = str(text or "")[:2500]
    for k, tag in kws.items():
        if k in t and tag not in tags and len(tags) < 6:
            tags.append(tag)
    return tags


def suggest_creative_fields(text):
    """创作性字段给显式占位；draft 不伪造成品级文案，审核时必须替换。"""
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
    lead = lines[0][:60] if lines else "（正文为空）"
    tail = lines[-1][:60] if len(lines) > 1 else lead
    return {
        "aliases": ["【待审核：文艺型别名】"],
        "summary": "【待审核：一句话摘要，可参考飞书摘要字段】",
        "线索": ["【待审核：锚点名】", "【待审核：未来使用场景】", "【待审核：细节锚点】"],
        "keyb": ["【待审核：锚点名】", "【待审核：未来使用场景】", "【待审核：细节锚点】"],
        "note": [
            f"【待审核：核心事件，正文首段参考：{lead}】",
            "【待审核：关键人物/要素】",
            f"【待审核：底层认知，正文末段参考：{tail}】",
        ],
    }


def collect_vault_titles():
    """库内非隐藏 md 的标题/文件名集合，供 related 建议只填真实存在者。"""
    out = []
    for p in VAULT.rglob("*.md"):
        if any(x in p.parts for x in (".workbuddy", ".git", ".trash", ".obsidian", ".feishu-sync")):
            continue
        out.append((p.stem, str(p)))
    return out


def suggest_related(date_str, text, vault_titles, limit=3):
    """仅建议库内同日期或高重合关键词的真实文件 wikilink。"""
    if not date_str:
        return []
    t = re.sub(r"\s+", "", str(text or ""))
    scored = []
    for stem, _ in vault_titles:
        if stem.startswith(date_str):
            scored.append((10, stem))
            continue
        core = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
        core = re.sub(r"-(Trae|QClaw)$", "", core)
        hits = sum(1 for i in range(0, max(0, len(core) - 3), 4) if core[i:i + 4] in t)
        if hits >= 2:
            scored.append((hits, stem))
    scored.sort(reverse=True)
    return [f"[[{s}]]" for _, s in scored[:limit]]


def norm_first_line(text, date_str):
    """首行规范化为 YYYY年M月D日，星期X。其余正文原样保留。"""
    y, m, d = map(int, date_str.split("-"))
    want = f"{y}年{m}月{d}日，{WEEK(datetime.date(y, m, d))}。"
    lines = str(text or "").strip().split("\n")
    first = lines[0].strip() if lines else ""
    if re.match(r"^20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", first) or re.match(r"^\d{1,2}\s*月\s*\d{1,2}\s*日", first):
        lines[0] = want
    else:
        lines.insert(0, want)
    return "\n".join(lines), want


def uid_for(date_str):
    core = date_str.replace("-", "") + "120000000"
    return f"{core}-" + "".join(random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(4))


def build(item, body):
    """由审核后的 item 构建 (目标路径, 全文, 规范首行, 标题)。"""
    cfg_date = item["date"]
    y, m, d = map(int, cfg_date.split("-"))
    body, first = norm_first_line(body, cfg_date)
    title = item["title"]
    fname = f"{cfg_date}-{item['cat']}-{title}.md"
    path = VAULT / item["dir"] / fname
    a, b = item["线索"], item["keyb"]
    note = item["note"]
    related_links = item.get("related") or []
    related = "\n".join(f"- {x}" for x in related_links) if related_links else "- "
    tags_yaml = "\n".join(f"  - {t}" for t in item["tags"])
    tags_line = " ".join(f"#{t}" for t in item["tags"])
    related_yaml = (
        "related: [" + ", ".join(f'"{x[2:-2]}"' for x in related_links) + "]"
        if related_links else "related: []"
    )
    uid = item.get("uid") or uid_for(cfg_date)
    content = f"""---
uid: {uid}
title: {title}
author: Wyp
aliases: ["{item['aliases'][0]}"]
tags:
{tags_yaml}
domain: {item['domain']}
summary: {item['summary']}
type: essay
{related_yaml}
createdTime: {cfg_date} 12:00:00
extracted: false
---
# {title}

- 📌 标签：{tags_line}
- 💡 一句话摘要：{item['summary']}
- 🔑 关键线索：
  - 方案 A（{a[0]}）：
    - 【未来使用场景】：{a[1]}
    - 【细节锚点】：{a[2]}
  - 方案 B（{b[0]}）：
    - 【未来使用场景】：{b[1]}
    - 【细节锚点】：{b[2]}

---

## 📝 正文

{body}

## ✍️ 备注
- 核心事件：{note[0]}
- 关键人物/要素：{note[1]}
- 底层认知：{note[2]}

## 🔗 相关链接
{related}
"""
    return path, content, first, title


def verify_file(path, content, first, title):
    """只读回读校验。兼容 Windows write_text 把 \n 转 \r\n。"""
    raw = path.read_bytes()
    back = raw.decode("utf-8")
    body_lf = back.replace("\r\n", "\n")
    return {
        "no_BOM": not raw.startswith(b"\xef\xbb\xbf"),
        "yaml_start": body_lf.startswith("---\n"),
        "title_yaml": f"title: {title}" in body_lf,
        "h1": f"# {title}" in body_lf,
        "first_line": first in body_lf,
        "body": "## 📝 正文" in body_lf,
        "note": "## ✍️ 备注" in body_lf,
        "links": "## 🔗 相关链接" in body_lf,
        "no_placeholder": "undefined" not in body_lf and "{{" not in body_lf,
    }


REQUIRED = ["record_id", "date", "dir", "domain", "cat", "title", "aliases", "tags",
            "summary", "线索", "keyb", "note", "related", "body"]
PLACEHOLDER = "【待审核"


def check_plan(plan, require_review=True):
    """校验 plan 结构与审核状态，返回 (errors, warnings)。
    默认（require_review=True）时，未标记 _review=human-reviewed 或含【待审核】占位符
    视为硬错误，--apply 直接拒绝写盘；--force（require_review=False）时降级为警告放行。"""
    errors, warnings = [], []
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        return ["plan 顶层需为含 items 数组的 JSON"], []
    if not plan["items"]:
        errors.append("items 为空")
    for i, it in enumerate(plan["items"]):
        miss = [k for k in REQUIRED if k not in it]
        if miss:
            errors.append(f"items[{i}] 缺字段 {miss}")
            continue
        if it["dir"] not in DIRS:
            errors.append(f"items[{i}] dir={it['dir']} 不在 {DIRS}")
        if not re.match(r"^20\d{2}-\d{2}-\d{2}$", it["date"]):
            errors.append(f"items[{i}] date 格式错：{it['date']}")
        if any(len(it[k]) != 3 for k in ("线索", "keyb", "note")):
            errors.append(f"items[{i}] 线索/keyb/note 需各 3 项")
        if not it.get("aliases") or not it.get("tags"):
            errors.append(f"items[{i}] aliases/tags 不可为空")
        blob = json.dumps({k: it[k] for k in ("aliases", "summary", "线索", "keyb", "note")}, ensure_ascii=False)
        bucket = errors if require_review else warnings
        if PLACEHOLDER in blob:
            bucket.append(f"items[{i}] {it['date']} 含待审核占位符（需替换创作字段）")
        if it.get("_review") != "human-reviewed":
            bucket.append(f"items[{i}] {it['date']} 未标记 _review=human-reviewed（审核后置 human-reviewed，或显式 --force）")
    return errors, warnings


# ---------------- 三命令 ----------------

def cmd_draft(args):
    if not SRC.exists():
        raise RuntimeError(f"飞书记录缓存不存在：{SRC}；请先跑 feishu_backup_audit.py --check")
    rs = json.loads(SRC.read_text(encoding="utf-8"))
    idx = {r["record_id"]: r for r in rs}
    ids = load_missing_ids()
    vault_titles = collect_vault_titles()
    items, skipped = [], []
    for rid in ids:
        rec = idx.get(rid)
        if not rec:
            skipped.append((rid, "飞书缓存中无此记录"))
            continue
        text = str((rec.get("fields") or {}).get("正文", ""))
        summary = str((rec.get("fields") or {}).get("摘要", ""))
        date_str, basis = infer_date(text, rec)
        if not date_str:
            skipped.append((rid, "无法判定日期"))
            continue
        if args.since and date_str < args.since:
            continue
        d, domain, cat, score = suggest_dir_domain(text, summary)
        cre = suggest_creative_fields(text)
        item = {
            "record_id": rid,
            "date": date_str,
            "_date_basis": basis,
            "dir": d, "domain": domain, "cat": cat,
            "_dir_basis": f"关键词命中{score}（建议值，需审核）",
            "title": suggest_title(summary, date_str, text),
            "aliases": cre["aliases"],
            "tags": suggest_tags(d, domain, text),
            "summary": summary or cre["summary"],
            "线索": cre["线索"], "keyb": cre["keyb"], "note": cre["note"],
            "related": suggest_related(date_str, text, vault_titles),
            "body": text,
            "_review": "draft-unreviewed",
        }
        items.append(item)
    items.sort(key=lambda x: x["date"])
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "meta": {
            "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": str(SRC), "report": str(latest_report()),
            "since": args.since or "（不限）",
            "note": "draft 仅给确定性字段与创作占位；审核后把 _review 改为 human-reviewed 再 --apply",
        },
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 草稿生成 {len(items)} 篇 -> {out}（跳过 {len(skipped)} 条）")
    for it in items:
        print(f"  {it['date']} [{it['cat']}] {it['title']} | 摘要源:{it['summary'][:30]}")
    for rid, why in skipped[:10]:
        print(f"  [SKIP] {rid} {why}")
    print("[NEXT] 审核草稿：补创作字段、把每项 _review 改为 human-reviewed，再跑 --apply --plan <path>")


def cmd_inspect(args):
    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    errors, warnings = check_plan(plan, require_review=not args.force)
    exists, new = [], []
    for it in plan.get("items", []):
        p, _, _, _ = build(it, it.get("body", ""))
        (exists if p.exists() else new).append(str(p.relative_to(VAULT)))
    print(f"[INSPECT] items={len(plan.get('items', []))} 已存在={len(exists)} 可新建={len(new)}")
    for e in errors:
        print("  [ERROR]", e)
    for w in warnings:
        print("  [WARN]", w)
    for p in exists:
        print("  [EXISTS]", p)
    for p in new:
        print("  [NEW]", p)
    print(f"[RESULT] errors={len(errors)} warnings={len(warnings)}")


def cmd_apply(args):
    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    errors, warnings = check_plan(plan, require_review=not args.force)
    if errors:
        print("[ABORT] plan 校验失败：")
        for e in errors:
            print("  ", e)
        return 2
    for w in warnings:
        print("[WARN]", w)
    if args.dry_run:
        print("[DRY-RUN] 不写盘。计划如下：")
        for it in plan["items"]:
            p, _, _, title = build(it, it["body"])
            print(f"  {'SKIP(存在)' if p.exists() else 'NEW'} {p.relative_to(VAULT)} | {title}")
        return 0
    ok, skipped, failed = 0, 0, 0
    for it in plan["items"]:
        p, content, first, title = build(it, it["body"])
        if p.exists():
            print(f"[SKIP] 已存在不覆盖：{p.relative_to(VAULT)}")
            skipped += 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")   # UTF-8 No BOM
        bad = [k for k, v in verify_file(p, content, first, title).items() if not v]
        if bad:
            print(f"[FAIL] {p.name}: 校验失败 {bad}")
            failed += 1
        else:
            ok += 1
            print(f"[OK] {p.relative_to(VAULT)} ({len(content)}字)")
    print(f"[DONE] 新建成功 {ok}；跳过已存在 {skipped}；失败 {failed}")
    return 0 if failed == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="飞书原始日记 → Obsidian 成品补写（draft / apply / inspect）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--draft", action="store_true", help="生成补写草稿 plan JSON，不写 Obsidian")
    g.add_argument("--apply", action="store_true", help="按审核后 plan 写入 Obsidian（只新建不覆盖）")
    g.add_argument("--inspect", action="store_true", help="只读检查 plan 与文件状态")
    ap.add_argument("--plan", default="", help="--apply/--inspect 的 plan JSON 路径")
    ap.add_argument("--out", default=str(DEFAULT_DRAFT), help="--draft 输出路径")
    ap.add_argument("--since", default="", help="--draft 只取该日期及以后，如 2026-07-01")
    ap.add_argument("--dry-run", action="store_true", help="--apply 只预览不写盘")
    ap.add_argument("--force", action="store_true", help="明知未审核/含占位符仍写盘（不推荐，默认拒绝）")
    args = ap.parse_args()
    if args.since and not re.match(r"^20\d{2}-\d{2}-\d{2}$", args.since):
        ap.error("--since 需为 YYYY-MM-DD")
    if args.apply or args.inspect:
        if not args.plan:
            ap.error("--apply/--inspect 必须指定 --plan")
        if not pathlib.Path(args.plan).exists():
            ap.error(f"plan 文件不存在：{args.plan}")
    if args.draft:
        cmd_draft(args)
    elif args.inspect:
        cmd_inspect(args)
    else:
        rc = cmd_apply(args)
        if rc:
            sys.exit(rc)


if __name__ == "__main__":
    main()
