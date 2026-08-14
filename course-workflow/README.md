# 互动课程工坊

接受任意 B 站视频链接或 BV 号；多分集视频可附带 `?p=` 或单独填写分集。输入后依次执行：

1. 校验 BV 链接和分集；
2. 使用 yutto 下载视频、中文字幕和元数据；
3. 按选择使用固定 PPT 区域完整记录，或通用视频关键帧模式生成 `record.json` 与 `article.md`；
4. 使用一次 MiMo 文本调用生成教案、题目和“老师已经讲完”的时间戳；
5. 将 SRT 转成浏览器字幕轨，并收集所有文件名含 `article` 的 Markdown；
6. 依据 `record.json` 的 PPT 帧时间戳拆分笔记，补齐对应 PPT 图片；
7. 返回带字幕、教案、Article 笔记和时间同步题目的播放网址。

首页另有“生成严格图文文章”路线。它复用上述流程的下载、字幕、完整 PPT 抽帧、视觉理解、严格内容覆盖和 Markdown 配图验收，但不会调用课程题目构建器。完成产物写入独立的 `data/articles/` 索引，只出现在“图文文章”陈列和 `/articles/<id>` 阅读页，不会写入 `data/lessons/` 或提供播放器地址。

同一入口也可无头调用：

```powershell
python .\scripts\generate_strict_article.py "BV1xxxxxxxxx" --part 7
```

命令默认复用下载并发布到前端；stderr 输出逐阶段 JSON，stdout 最后一行输出文章元数据 JSON。恢复保留现场时同时传 `--output <record_dir> --resume`。个人 Codex skill 名称为 `$generate-strict-video-article`，其内部调用的也是这条 CLI。

运行 `start.ps1` 后，电脑访问 <http://127.0.0.1:8765/>；同一路由器下的平板访问
`http://<电脑的 WLAN IPv4 地址>:8765/`。启动器默认监听局域网，平板无需配置系统代理。

严格课程校验包默认开启：它包含 PPT 页覆盖、来源数字保留、固定文章结构与标注数量等面向课程记录的规则。关闭它会保留 JSON、时间戳、题目和文件完整性校验，但不因课程专用的保真/版式规则中止任务。只有画面中有稳定 PPT 区域时才应开启“完整 PPT 记录”。

首页“处理多个分集”支持“仅下载资源”和“下载并制作课程”两种模式。两种模式都可选择串行或最多 3 路并行，新下载固定为 yutto 质量代码 `64`（最高 720p），并默认复用已有视频与字幕。批量课程会跳过已有完成课程，防止未经确认重做。

## 音标课程 OCR 链路

选择 `phonetics_course` 时，字幕文件和 Whisper 转写都会被显式禁用，`source_manifest.json`、`transcript.json`、`record.json` 和课程 JSON 均记录 `evidence_mode: ocr_primary`。画面处理分为两条互不替代的轨道：

1. 课程插图轨道按场景变化抽取并去重；
2. 字幕 OCR 轨道每 0.25 秒扫描一次画面下部文字区，为每个不同文字状态保留一张稳定帧，逐段写入 `ocr_timeline.json`。

字幕轨道不受课程插图数量限制。检测到字幕但 OCR 为空或仍有不确定字符时，任务停在 `dense_caption_ocr_validation`，保留原始分段、OCR 结果和调用日志，不会静默生成“完成”课程。

本音标系列 P3–P47 的串行入口：

```powershell
..\scripts\run-phonetics-course-batch.ps1 -Source BV1iV411z7Nj -StartPart 3 -EndPart 47
```

## 唯一运行源

本目录是工作区唯一规范课程服务与数据源。`GET /api/series` 必须返回 `canonical: true`，且 `canonical_root` 指向本目录。工作区顶层历史 `course-workflow/` 仅作为兼容入口，不能再启动独立数据服务或承载功能改动。

## 运行约束

- 需要有效的 yutto 登录状态。
- 需要环境变量 `XIAOMI_MIMO_API_KEY_TEM1` 和 `XIAOMI_MIMO_BASE_URL`。
- 确定性内容验收失败立即停止；短暂 API 故障按照分析器配置重试，并保留调用记录。
- 原始下载和分析中间产物保留在原有 `downloads/`、`records/` 目录。
- `article.md`、`article.source.md`、历史版本、草稿和 rejected 版本都会保留并可在页面切换。
