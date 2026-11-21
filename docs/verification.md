# 验证记录

这份文件只记录**可复跑**的检查，不写主观评价。所有命令都在仓库根目录执行。

## 1. 干净环境（模拟第一次 clone 的人）

```bash
git clone <repo> deck && cd deck
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
./.venv/Scripts/python -c "import pptx, matplotlib, fastapi, pymupdf, webview"
./.venv/Scripts/python -m unittest discover -s tests
./.venv/Scripts/python -m deck.cli build samples/data --offline --theme editorial --out out/deck.pptx
./.venv/Scripts/python tools/qa.py out/deck.pptx --png out/png
```

最近一次结果（commit `ca6a352`）：

| 检查 | 结果 |
| --- | --- |
| 依赖安装 | `PIP=0`，五个关键包 import 通过 |
| 离线测试 | `Ran 239 tests … OK`（29.6s） |
| 样例出稿 | 16 页 |
| 质量分 | 100，`数字问题 0 处` |

## 2. 国产/通用模型预设

`app/settings.py:PRESETS` 共 10 家：deepseek、dashscope（通义）、zhipu（智谱）、moonshot、doubao（豆包）、baichuan（百川）、minimax、siliconflow、openai、ollama（本机）。

```bash
python -c "from app.settings import PRESETS; print(len(PRESETS), sorted(PRESETS))"
```

- 每家都有 `http(s)` 开头的 `base_url`、非空 `model`、一句 `note`；
- `_clean()` 只在缺少版本段时补 `/v1`，豆包的 `/api/v3`、DashScope 的 `/compatible-mode/v1` 保持原样（`tests/test_settings.py:PresetCoverageTest`）；
- 界面「模型」弹窗里点预设即填端点，点「测试连接」会真发一次 `max_tokens=1` 的请求，401/404 原样报回。

真实端点回归（`mimo-v2.6-flash`，OpenAI 兼容）：

| 调用 | 用时 | 结果 |
| --- | --- | --- |
| `/api/ideas` | 21–68s | 3 条角度 |
| `/api/plan`（6 页） | 76s | 6 页，数字校验通过 |
| `/api/plan`（8 页预算） | 约 20 分钟 | 8 页，裁页保住章节与收尾 |
| `/api/notes` | 26s | 8 页讲稿，句子不带数字 |
| `/api/look`（16 页） | < 900s | 两批看完，结论写清看了几页 |

## 3. 多模态

一次上传 `samples/data` 全部 7 个文件（xlsx／xlsx 图表／docx／pdf／png×2／jpg）：

| 检查 | 结果 |
| --- | --- |
| 收下的文件 | 7 / 7 |
| 事实 | 24 条 |
| 资料体检 | 6 / 6 |
| 识别出的图 | 4 张（图表 / 文档页 / 照片） |
| 成稿 | 16 页 |
| 质量分 | 100 |
| 数字校验 | 0 处不符 |
| 导出 PDF | 约 1.19 MB |

单独验证过的极端输入：只有 3 张照片（4 页，质量分 100）、只有一份 Markdown（4 页）、只有一份带线表格的 PDF（6 页，表格数值被读成事实）。

## 4. 安装包

```bash
.venv/Scripts/python -m PyInstaller deck_studio.spec --noconfirm --clean --distpath dist-vNN --workpath build-vNN
python tools/accept.py          # 起打包版、走真实 HTTP、9 项断言
```

- 包体：去掉 PyMuPDF 用不到的版面模型与开发头文件、改用压缩打包，**237 MB → 85 MB**；
- `tools/accept.py` 全项 PASS（文件数、事实、资料体检、识别图、页数、质量分、预览、数字校验、PDF）；
- GitHub Release 资产的 `Digest` 与本地压缩包 `sha256` 一致（`ac7586cd…`），说明上传件与构建件逐字节相同。

## 5. 已知边界

- 逐页预览要本机有 LibreOffice；没有时 PPTX 照常导出，只是不出预览图（README 已如实写明，不再写「内置转换」）。
- 视觉读图要显式配一个支持图片输入的模型；DeepSeek 只有文本，填了会在第一次读图时 404。
- 模板只学配色、字体、标题字号，不复制别人的版式几何。
- 模型慢的时候（30 字/秒级别）一份 8 页稿要十几分钟，界面会一直显示当前步骤，不会假装完成。
