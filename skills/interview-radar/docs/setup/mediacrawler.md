# 小红书面经采集 — MediaCrawler 设置指引

启用 `interview-radar` skill 的小红书源是**可选**步骤。skill 本身不抓数据;
真正的采集由开源工具 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 完成,
本文档教你怎么把它和本 skill 串起来。

> 仅供个人、非商业用途。请遵守目标平台的服务条款。

## 推荐:driver 模式(一次设置,之后全自动)

只需要做**两件事**(总共约 5 分钟):

### 1. 装 MediaCrawler 到默认路径

```bash
# 默认路径是 ~/.mediacrawler/。也可以装到任意位置,然后 export MEDIACRAWLER_HOME=<path>
if [ ! -d "$HOME/.mediacrawler" ]; then
  git clone https://github.com/NanmiCoder/MediaCrawler.git ~/.mediacrawler
fi
cd ~/.mediacrawler

# 用 Python 3.11(它 requirements.txt 锁的版本)。如果本机没有 python3.11,先试 python3
python3.11 -m venv venv || python3 -m venv venv
venv/bin/python -m pip install -U pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m playwright install chromium
```

如果 `~/.mediacrawler` 已经存在,说明你之前 clone 过,不用重新 clone:

```bash
cd ~/.mediacrawler
python3.11 -m venv venv || python3 -m venv venv
venv/bin/python -m pip install -U pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m playwright install chromium
```

如果系统提示 `pip: command not found`,不要装全局 pip,始终用 `venv/bin/python -m pip ...`。

### 1.5. 关掉默认的 CDP 模式(重要)

MediaCrawler 默认 `ENABLE_CDP_MODE = True`,会尝试连接你**已经开着**的 Chrome(端口 9222),不开就报 "CDP port 9222 is not accessible" 死循环。改成 False 让它用自带的 Playwright Chromium:

```bash
venv/bin/python -c "from pathlib import Path; p=Path('config/base_config.py'); s=p.read_text(encoding='utf-8'); p.write_text(s.replace('ENABLE_CDP_MODE = True', 'ENABLE_CDP_MODE = False'), encoding='utf-8')"
```

这条命令用 Python 改配置,macOS 和 Linux 都能直接复制;不依赖系统 `sed` 语法。

### 2. 登录小红书

优先用 Cookie 登录。二维码登录现在更容易被小红书风控挡住;Cookie 登录用你正常浏览器里已经登录的小红书会话,成功率更高。

#### 方式 A: Cookie 登录(推荐)

1. 用你平时的 Chrome / Safari / Edge 打开 `https://www.xiaohongshu.com`,正常用手机扫码登录。
2. 登录成功后打开开发者工具:
   - Chrome / Edge: `F12` 或 `Option + Command + I`
   - Safari:先在设置里打开“开发”菜单,再进入 Web Inspector
3. 进入 `Application` / `Storage` 面板。
4. 左侧选择 `Cookies` -> `https://www.xiaohongshu.com`。
5. 找到名字为 `web_session` 的 cookie,复制它的 `Value`。
6. 写入 MediaCrawler 配置:

```bash
cd ~/.mediacrawler
python3 - <<'PY'
from pathlib import Path

cookie = input("paste web_session: ").strip()
path = Path("config/base_config.py")
text = path.read_text(encoding="utf-8")
lines = []
for line in text.splitlines():
    if line.startswith("LOGIN_TYPE ="):
        lines.append('LOGIN_TYPE = "cookie"  # qrcode or phone or cookie')
    elif line.startswith("COOKIES ="):
        lines.append(f'COOKIES = "web_session={cookie}"')
    elif line.startswith("ENABLE_GET_COMMENTS ="):
        lines.append("ENABLE_GET_COMMENTS = False")
    else:
        lines.append(line)
text = "\n".join(lines) + "\n"
path.write_text(text, encoding="utf-8")
PY
```

然后跑一次验证:

```bash
cd ~/.mediacrawler
venv/bin/python main.py --platform xhs --lt cookie --type search --keywords "AI 应用开发 面经" --save_data_option json --get_comment no
```

如果成功,会在 `~/.mediacrawler/data/xhs/json/` 下生成 `search_contents_*.json`。

### 3. 选择读取深度:fast / deep

小红书和牛客不一样:很多面经笔记因为文字区有字数限制,正文 caption 只写摘要、岗位名或少量题目,完整问题放在图片里。

因此小红书源必须区分两档:

| 模式 | 读取内容 | 什么时候用 |
|---|---|---|
| `fast` | 只读 MediaCrawler JSON 里的标题、正文 caption、标签、发布时间和链接 | 快速验证登录、关键词和召回质量 |
| `deep` | 在 `fast` 基础上,按 `image_list` 下载图片并做 OCR,把图片文字作为主正文 | 正式产出备考包,尤其是面经/题库类小红书笔记 |

实现上对应 `XiaohongshuConnector(..., enable_image_ocr=False)` 和 `XiaohongshuConnector(..., enable_image_ocr=True)`。默认应优先用 `deep`;如果为了速度临时用 `fast`,最终报告必须写明“未读取图片 OCR,可能漏掉图片里的完整题目”。

#### 方式 B: 二维码登录(备选)

```bash
cd ~/.mediacrawler
venv/bin/python main.py --platform xhs --lt qrcode --type search --keywords "测试" --save_data_option json --get_comment no
```

屏幕上会弹出二维码,用手机小红书 App 扫码登录。**之后登录态会被 MediaCrawler 缓存**,在过期之前都不需要再扫。

之后就别再手动跑 MediaCrawler 了 ——

**skill 会在需要小红书数据时自动调用它**(通过 `XiaohongshuConnector(driver=MediaCrawlerDriver())`),把关键词喂进去 → 拿 JSON → 进管道,全自动。

### 登录态过期了怎么办?

skill 会返回 `status="degraded"` 并提示"登录过期"。重新执行第 2 步扫码即可,代码不用动。

---

## 备用:手动模式(不想让 skill 自动跑 MediaCrawler)

如果你想自己控制爬虫节奏,或者觉得让 skill shell out 跑外部工具不放心:

```bash
# 自己跑 MediaCrawler
cd ~/.mediacrawler
venv/bin/python main.py --platform xhs --lt cookie --type search --keywords "AI 应用开发 面经" --save_data_option json --get_comment no

# 用本 skill 的适配器归一化
cd ~/.claude/skills/interview-radar
.venv/bin/python -m scripts.scrape.normalize_xhs \
    ~/.mediacrawler/data/xhs/json/search_contents_*.json \
    -o corpus_cache/xhs_export.json
```

然后让 skill 用 `XiaohongshuConnector(export_path="corpus_cache/xhs_export.json")`,不传 driver。

---

## 排错

| 现象 | 可能原因 |
|---|---|
| `MediaCrawlerNotInstalledError` | 没装在 `~/.mediacrawler/`,且没设 `$MEDIACRAWLER_HOME` |
| `MediaCrawlerScrapeError: login expired` | 登录态过期。Cookie 模式重新复制 `web_session`;二维码模式重扫码 |
| `MediaCrawlerScrapeError: ... schema may have changed` | MediaCrawler 升级了 CLI 或输出路径,改 `scripts/scrape/mediacrawler_driver.py` 里的假设 |
| 适配器(`normalize_xhs.py`)报错 | MediaCrawler 升级了 JSON schema,改 `scripts/scrape/normalize_xhs.py` 里的字段假设 |
