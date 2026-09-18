# _archive-2026-07-23 — 归档说明

> **归档日期**:2026-07-23
> **归档原因**:整理 docs/ 时清理冗余文件,采用保守策略——不删,先归档
> **清理负责人**:Lucy(数字搭档)

## 归档清单(41 个文件)

### 来源 1:`docs/docs/` 嵌套目录(40 个文件,2026-07-17 18:58 创建)

**根因**:Windows 端工具(可能是 git clone / PowerShell 复制 / 某个同步脚本)意外产生了嵌套 `docs/docs/` 副本。

**字节对比结果**(归档前已对 40 个文件逐个做字节对比):

| 状态 | 数量 | 说明 |
|------|------|------|
| 完全相同(字节级) | 21 | 副本和源文件字节数完全相同 |
| 有字节差(CRLF vs LF) | 19 | 副本比源大几十到几百字节,差异是 Windows CRLF 换行符多 1 字节/行 |
| 源缺失(源不存在) | 0 | 所有副本都能在 `docs/` 找到对应源 |
| **有真正新内容** | **0** | 19 个有差异的全是 CRLF 换行差异,行数完全相同 |

**典型例子**:`docs/docs/notes/PromptTemplate迁移jinja2-20260717.md`
- 源 `docs/notes/PromptTemplate迁移jinja2-20260717.md`:5150 字节,LF 换行
- 副本 `docs/docs/notes/PromptTemplate迁移jinja2-20260717.md`:5231 字节,CRLF 换行
- 字节差 81 字节 = 81 行(每行多 1 字节 `\r`)

### 来源 2:`docs/AI应用架构升级路线_副本.md`(1 个文件)

**根因**:2026-07-23 整理时多生成的一个副本。

**对比结果**:`diff -q` 显示**字节级完全相同**(都是 658 行,33975 字节),是纯重复。

## 为什么归档不删

1. **保守原则**:用户(藤子)要求"先归档保留,后续按需清理"
2. **防止漏掉**:虽然字节对比显示都是 CRLF 差异,但人工可能漏看新内容
3. **git 兜底**:即使误删,git history 也能找回
4. **可逆性**:`mv` 是可逆操作,`rm` 不可逆

## 清理方法(后续)

### 阶段 A:确认无新内容(下次整理时做)

```bash
# 把所有副本用 dos2unix 转成 LF,再和源对比
find _archive-2026-07-23 -type f -name "*.md" | while read f; do
  dos2unix "$f" > /dev/null 2>&1
done

# 然后对比
for f in $(find _archive-2026-07-23 -type f -name "*.md" | sed 's|_archive-2026-07-23/||'); do
  diff -q "docs/$f" "_archive-2026-07-23/$f"
done
```

**预期结果**:所有文件 diff 无输出(完全相同)

### 阶段 B:删除归档

```bash
# 确认无新内容后
rm -rf docs/_archive-2026-07-23/
git add -A
git commit -m "docs: 删除 7-17 Windows 工具意外产生的 docs/docs/ 嵌套副本(归档 1 周后确认无新内容)"
```

### 阶段 C:发现新内容怎么办

如果对比发现某个文件有真新内容:
```bash
# 把 _archive 里那个文件覆盖回 docs/
cp "_archive-2026-07-23/path/to/file.md" "docs/path/to/file.md"
# 然后再 rm 归档
```

## 时间线

| 时间 | 事件 |
|------|------|
| 2026-07-17 18:58 | Windows 端产生 `docs/docs/` 嵌套副本(40 个文件) |
| 2026-07-17 21:11 | Mac 端 `docs/notes/PromptTemplate迁移jinja2-20260717.md` 写入(LF 换行) |
| 2026-07-22 12:17 ~ 18:22 | Mac 端多次更新 `docs/docs/` 下的文件(可能是 pull 时同步) |
| 2026-07-23 20:18 | Lucy 发现 `docs/` 下 88 个文件(47 主 + 41 副本) |
| 2026-07-23 21:18 | 41 个副本归档到 `_archive-2026-07-23/`,`docs/` 恢复 47 个文件 |

## 教训

**写给未来的自己**:
1. **Windows + Mac 混用项目,文件操作要小心**——CRLF 换行差异是常见污染源
2. **mv 优先于 rm**——任何不确定的文件先归档,1 周后没异常再删
3. **字节级 diff 是金标准**——`diff -q` 只看文本,`md5`/`wc -c` 看字节才准
4. **`.DS_Store` 等 macOS 元数据**要进 `.gitignore`,这次 `docs/docs/.DS_Store` 也一起归档了
