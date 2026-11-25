# feishu-obsidian-sync

飞书多维表格「正文」字段 ↔ 本机 Obsidian 库备份的**核对与补写工具链**。

- **核对**：只读拉取飞书表格全部记录，按正文内容（不是逐字）判断本地是否有备份，输出分类报告。
- **补写**：从未备份的飞书记录生成草稿，人工审核元数据后，按 Obsidian 成品规范**只新建不覆盖**地写回库。
- **安全底线**：核对全程不写飞书、不写 Obsidian；补写只新建不覆盖；未经审核的草稿默认拒绝写盘；全程不发大模型推理请求、不消耗额度。

本仓库是**私有仓库**，仅存放工具代码与文档，**不含任何真实日记正文、凭据、二维码或核对报告**。

## 一、这个仓库解决什么问题

飞书表格里存的是**原始日记**，本地 Obsidian 里多为**加工/扩写版**，两者文字不同但记的是同一件事。因此：

- 不能用「逐字全文匹配」判断备份是否存在（会大量误判为缺失）；
- 不能用飞书「日期」字段配对（它是录入日，与内容日期可能差 ±1 天）。

本工具用「正文内容探针」做判定：从每条飞书正文均匀取 40 个 10 字片段，任一在候选本地文件全文中出现，即判为同内容（并排除几乎纯数字的日期片段，避免跨文件误配）。三层查找：指定目录 → 库内其他目录 → 迁移前备份区。报告对仅靠 1 个探针命中的弱证据单列探针数，提示人工抽查。

判断「差距不该这么大」时，用独立方法（4 字 shingle 覆盖率、不同探针长度）交叉验证命中下限；多种方法命中数应集中，若仍差距大则结论客观成立，不为迎合而放宽到误配。

## 二、文件清单

| 文件 | 作用 |
|---|---|
| `feishu_backup_audit.py` | 核对脚本。`--check` 拉全量 + 比对 + 出报告；`--probe` / `--raw` 用于探测字段结构 |
| `restore_12_from_feishu.py` | 补写脚本。`--draft` 生成草稿、`--inspect` 只读检查、`--apply` 写盘 + 回读校验 |
| `SKILL.md` | WorkBuddy 用户级 Skill 源文件，含触发词、完整流程、元数据规范、已知坑、安全回滚规则 |
| `plan_template.json` | 审核后 plan 的**脱敏模板**（结构示例，无真实日记），供填写参考 |

脚本名里的 `12` 是历史遗留（首批补写 12 篇），脚本本身已通用化，不限于 12 篇。

## 三、环境依赖与适配（重要）

脚本在本机验证通过，但**路径为写死的 Windows 绝对路径**，换机器/换库需改脚本顶部常量。Python 用 3.14 正式版验证过，仅用标准库（`urllib` / `json` / `re` / `importlib` 等），无需 `pip install`。

### 3.1 需要修改的路径常量

`feishu_backup_audit.py` 顶部：

```python
VAULT       = Path(r"F:\wwangyp")                   # Obsidian 库根目录
TARGET_DIRS = [...七个领域目录...]                   # 参与核对的目录
BACKUP_ROOT = Path(r"F:\WorkBuddy\backups")          # 迁移前备份（仅用于定位）
RAW_JSON    = Path(r"F:\WorkBuddy\04_脚本工具\_feishu_records_tmp.json")  # 飞书记录缓存
REPORT_DIR  = Path(r"F:\WorkBuddy\02_文档产出")      # 报告输出目录
REPORT_PTR  = Path(r"F:\WorkBuddy\04_脚本工具\_latest_report_path.txt")   # 最新报告指针
```

`restore_12_from_feishu.py` 顶部：

```python
VAULT         = pathlib.Path(r"F:\wwangyp")
SRC           = pathlib.Path(r"F:\WorkBuddy\04_脚本工具\_feishu_records_tmp.json")
REPORT_PTR    = pathlib.Path(r"F:\WorkBuddy\04_脚本工具\_latest_report_path.txt")
DEFAULT_DRAFT = pathlib.Path(r"F:\WorkBuddy\04_脚本工具\_restore_plan_draft.json")
DIRS          = [...七个领域目录...]
```

报告文件名按当天日期动态生成（`YYYYMMDD_飞书正文备份核对报告_V1.md`），同一天重跑覆盖当天文件，跨天自动留历史。核对脚本写指针文件，补写脚本经指针定位最新报告（指针缺失时回退旧固定路径）。

### 3.2 飞书凭据准备

脚本**不内嵌密钥**，运行时从 Obsidian 库根目录的 `write_to_feishu.py` 导入 `APP_ID` / `APP_SECRET`（以及表格 token 等）。该文件不在本仓库，请在本机自行准备，例如：

```python
# <VAULT>/write_to_feishu.py（本地文件，切勿提交）
APP_ID = "cli_..."
APP_SECRET = "..."
APP_TOKEN = "..."   # 多维表格 app_token
TABLE_ID = "tbl..."
```

由于凭据是运行时导入，本仓库任何文件都不含密钥。飞书应用需具备该多维表格的**记录读取**权限；核对只用 GET，补写不写飞书。

## 四、使用流程

### 流程 A：核对（只读）

```bash
# 首次或更换表格：探测「正文」字段实际类型
python feishu_backup_audit.py --probe
# 解析异常时看原始响应（不打印凭据）
python feishu_backup_audit.py --raw
# 全量核对，产出报告
python feishu_backup_audit.py --check
```

报告分五类：指定目录有备份 / 库内其他目录有 / 仅迁移前备份区有 / 飞书有本机全无 / 本地有飞书无。第四节「本机全无」是补写的输入。

### 流程 B：补写（只新建不覆盖）

**动手前先确认三件事**：① 正文口径（原文照搬 + 规范首行 / 完全照搬 / 加工润色）；② 分类目录；③ 元数据如何填。默认口径 = 原文照搬 + 规范首行、按建议分类、元数据按内容生成。

```bash
# 1) 生成草稿（不写 Obsidian）
python restore_12_from_feishu.py --draft --since 2026-07-01 --out <草稿路径>.json

# 2) 人工/AI 审核草稿：逐篇填好 aliases/summary/线索/备注，把 _review 改为 human-reviewed
#    格式参考 plan_template.json

# 3) 只读检查 + 预览（不写盘）
python restore_12_from_feishu.py --inspect --plan <审核后plan>
python restore_12_from_feishu.py --apply --plan <审核后plan> --dry-run

# 4) 正式写盘
python restore_12_from_feishu.py --apply --plan <审核后plan>
```

**审核闸门是硬的**：plan 中任一记录未标记 `_review: human-reviewed`，或含 `【待审核】` 占位符，`--apply` 会逐条报错并拒绝写盘（exit 2）；只有显式 `--force` 才放行（不推荐）。已补写的记录不会再进入新一轮草稿（幂等）。

## 五、元数据规范（审核草稿时遵循）

- `uid`：17 位核心 + 4 位小写，形如 `20260717120000000-msh4`；脚本自动生成，plan 里已有则沿用。
- 四处一致：`title` = 文件名中间段 = H1 = 摘要行标题。文件名 `YYYY-MM-DD-分类-标题.md`。
- `aliases`：文艺型短句（非描述型）。`tags`：真实标签，domain 打头，3–6 个。
- `summary`：一句话概括事件 + 结论。
- 关键线索方案 A/B 各含「锚点名 / 未来使用场景 / 细节锚点」，细节锚点必须是正文原话或精确数字。
- `note` 三行：核心事件 / 关键人物要素 / 底层认知。
- `related`：只填库内确证存在的 wikilink，查不到用 `[]`。draft 的关联命中率低是正常的，需人工核对。
- 首行必须 `YYYY年M月D日，星期X。`（带「星期」二字 + 句号），星期按日历实算，不照抄飞书笔误。

`.gitignore` 已排除真实缓存、报告、审核 plan、备份文件和凭据状态文件，避免误提交隐私数据。

## 六、WorkBuddy Skill

`SKILL.md` 是用户级 Skill 源文件。放到 `~/.workbuddy/skills/feishu-obsidian-sync/SKILL.md` 后，下次说「核对飞书备份」「补写飞书日记」即可自动加载完整流程。注意 Windows 上 `~/.workbuddy/skills` 可能是 junction，以真实落点为准。Skill 文件里的路径按本机实际修改即可。

## 七、已知坑（踩过，勿重犯）

1. 飞书 list records 返回键是 `items` 不是 `records`；`page_size=500` 一次拉全；响应含 `摘要/日期/分类` 可辅助。
2. 飞书「日期」字段是录入日，非内容日，可能与正文日期差 ±1 天；按正文内容判定年月。
3. 正文明示 2027 年但星期与 2026 吻合的是笔误，按星期修正；真的旧年份按正文保留。
4. 探针必须排除几乎纯数字的日期片段，否则会把不同日记误配到同一文件。
5. 核对目录必须含全所有实际存在的领域目录（曾漏「家庭生活」导致缺失数虚高）。
6. Windows `write_text()` 把 `\n` 转成 `\r\n`，回读校验必须先 `replace("\r\n", "\n")`，否则假失败。
7. 校验期望值不要复用被测函数本身（曾因此漏掉星期写法错误）。
8. 禁止擅自调用大模型 API 做测试；本工具全程只用飞书 REST API（GET）+ 本地文件。

## 八、安全声明

本仓库为私有，仅用于个人工具沉淀。核对不写飞书、不写 Obsidian；补写只新建不覆盖、逐文件回读校验（无 BOM、YAML 起始、四处标题一致、首行、三段齐全、无占位符）。任何真实日记正文、凭据、二维码、核对报告都不在本仓库内。如需公开，请先再次审查 `SKILL.md` 里的本机路径说明。
