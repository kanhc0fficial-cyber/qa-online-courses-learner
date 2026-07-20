# QA Online Courses Learner

面向 Windows 的本地互动课程制作与学习工具：输入 B 站 BV 号或分集链接，下载最高 720p 的视频资源，生成可审计的课程记录，并在浏览器中提供字幕、笔记、检查点题目和课程书架。

当前机器上的运行数据已经推进至 P40。课程 JSON、视频、字幕、抽帧、模型调用记录和失败现场均保留在本地运行目录中，不进入 Git 仓库。

## 核心能力

- 支持任意 B 站 BV 视频和多分集范围。
- 新下载固定使用 yutto `--video-quality 64`，并以 `ffprobe` 验证不高于 720p。
- 优先复用已有且合格的视频、字幕与分析候选，避免不必要的下载和模型调用。
- 保留 `record.json`、`article.md`、草稿、rejected 版本、任务 JSON 与错误日志，便于复查和安全续跑。
- 已有完成课程时不自动重做，也不再在首页展示该课次的历史失败任务。
- 首页按视频系列组织课程书架，支持自定义名称和排序。
- 视频检查点支持自动退出全屏答题，并在继续播放时恢复全屏。

## 三条课程链路

| 链路 | 适用内容 | 证据来源 | 题目 |
| --- | --- | --- | --- |
| `strict_course` | 有稳定 PPT 和字幕的正式课程 | PPT、字幕、音频 | 生成 |
| `general_video` | 普通讲解或无固定 PPT 的视频 | 视频、字幕或音频 | 不生成 |
| `phonetics_course` | 中英混合、IPA 与内嵌字幕课程 | 画面密集 OCR | 生成 |

`phonetics_course` 不使用下载字幕或 Whisper。它每 0.25 秒扫描画面下部的内嵌字幕区，并把 `ocr_timeline.json` 作为可审计时间线；只要存在未确认字幕段，任务就会停止并保留现场。

## 项目结构

```text
.
├─ course-workflow/          # 唯一规范 FastAPI 服务、前端、课程构建与测试
├─ video-analyzer/           # 下载、抽帧、字幕/OCR、record 与 article 主链路
├─ scripts/                  # 单课、批量、恢复与音标课程协调器
├─ downloads/                # 本地下载资源（忽略）
├─ records/                  # 可审计分析记录（忽略）
├─ setup.ps1
└─ start-course.ps1          # 唯一课程服务启动入口
```

在多项目工作区中，`course-workflow/` 是唯一规范课程实现。工作区顶层的同名历史目录只能作为兼容转发入口，不能启动第二套数据服务。

## 环境要求

- Windows 10/11
- Python 3.11+
- `ffmpeg` 与 `ffprobe` 可从 `PATH` 调用
- 有效的 B 站 yutto 登录状态
- MiMo API 环境变量

首次安装：

```powershell
.\setup.ps1
Copy-Item .env.example .env
.\.venv\Scripts\yutto.exe auth login
```

在 `.env` 中配置：

```dotenv
XIAOMI_MIMO_API_KEY_TEM1=
XIAOMI_MIMO_BASE_URL=
```

密钥、B 站登录态和 cookies 不得提交。

## 启动课程网站

```powershell
.\start-course.ps1
```

打开 <http://127.0.0.1:8765/>。启动后应同时验证规范标记，而不只是 HTTP 200：

```powershell
$series = Invoke-RestMethod http://127.0.0.1:8765/api/series
$series.canonical
$series.canonical_root
$series.lessons.Count
($series.lessons | Measure-Object part -Maximum).Maximum
```

`canonical` 必须为 `true`，`canonical_root` 必须指向本仓库的 `course-workflow`。

## 制作单节课程

严格 PPT 课程：

```powershell
.\scripts\download-and-build.ps1 -Source "BV1xxxxxxxxx" -Part 7
```

通用视频：

```powershell
.\scripts\download-and-build.ps1 -Source "BV1xxxxxxxxx" -Part 7 -GeneralVideo
```

也可以在首页使用“处理多个分集”，选择仅下载或下载并制作课程。服务会跳过已有完成课次，并按照可用槽位协调批量任务。

## 音标课程 OCR 批处理

```powershell
.\scripts\run-phonetics-course-batch.ps1 `
  -Source BV1iV411z7Nj `
  -StartPart 3 `
  -EndPart 47
```

批处理报告与事件流保存在 `course-workflow/data/batches/`；它们属于本地运行记录，不提交。

## 测试

```powershell
cd course-workflow
pytest -q

cd ..\video-analyzer
pytest -q tests
```

课程服务启动后的运行时验证还应检查：

- `/api/series` 返回 `canonical: true`；
- `canonical_root` 指向规范目录；
- 课程数量和最高 P 与本地 `data/lessons/` 一致；
- UI 改动通过对应的静态契约测试和人工浏览器检查。

## 数据与提交策略

以下内容始终保留在本地，但不会上传：

- `downloads/`
- `records/`
- `course-workflow/data/`
- `.env`、登录态、cookies 和 session
- 模型缓存、测试缓存和日志

仓库只同步源码、提示词、依赖声明、测试、可复用脚本和文档。不要为了让工作区“干净”而删除运行产物。
