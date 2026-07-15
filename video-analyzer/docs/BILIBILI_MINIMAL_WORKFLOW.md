# B站视频的最省 token 工作流

默认顺序：下载（yutto）→ 读取 NFO 与中文字幕 → 本地抽取少量关键帧 → MiMo 批量筛选并给出成品图 grounding 框 → 一次性组织公众号文章。

- 中文字幕覆盖合格时完全跳过 Whisper；否则才使用本地 `medium`，以兼顾中文和夹杂的英文/音标。
- 弹幕只登记为观众评论，不进入事实材料，也不发送给模型。
- 画面预算约为每 150 秒一张，最少 6 张、最多 12 张；每 6 张合并为一次 MiMo 请求。默认不做整片场景切换扫描，只做均匀候选取样和本地去重。
- 不做模型失败后的自动拆分重试；失败留痕后由人决定是否增加费用。
- 不进行第二次事实校订调用。原始字幕、场景记录和来源元数据全部保留，便于人工检查。
- MiMo 的 grounding 仅裁剪文章实际选中的配图。置信度低于 0.70 不裁；裁剪框外扩 6%，且至少保留原图 60% 的宽和高。原图永远保留。

只准备本地材料（不调用 MiMo）：

```powershell
python scripts/bilibili_workflow.py "C:\path\to\download" --prepare-only
```

从 BV 号下载并完整运行：

```powershell
python scripts/bilibili_workflow.py BV1xxxxxxxxx --part 3
```

## 固定PPT区域的完整记录模式

`--ppt-complete` 用于课件位置固定、希望尽量保留每一页PPT的课程视频。它只在固定PPT区域检测稳定变化，并保留全部有效页；文章阶段不使用一次整稿重排，而按PPT标记逐页生成结构化JSON，再统一校验和渲染。

```powershell
python scripts/bilibili_workflow.py "https://www.bilibili.com/video/BV1xxxxxxxxx?p=7" `
  --part 7 --ppt-complete --model mimo-v2.5 `
  --api-key-env XIAOMI_MIMO_API_KEY_TEM1
```

结构化文章流水线的稳定性约束：

- JSON只允许扁平的段落、三级标题、彩色重点、公式、表格和图片标记；未知字段或嵌套正文会被拒绝，避免渲染时静默丢内容。
- 局部请求负责书面化和删除口头填充，局部失败只修复该PPT，不再让整稿修复造成二次概括。
- 全局校验要求PPT编号顺序唯一、正文覆盖率不低于原稿的88%，并检查标题层级、重点标注、加粗数量和口语残留。
- 表格中的每个值必须能追溯到当前原稿；材料未明确给出的值不能根据常识补齐。
- 每页缓存同时记录原稿SHA-256和格式版本。原稿或提示词规则变化时自动失效，不会误用旧文章片段。
- `article.document.validation.json` 保存机器校验指标；只有校验通过才生成最终 `article.md`。
