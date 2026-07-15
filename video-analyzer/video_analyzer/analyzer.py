from __future__ import annotations

from typing import List, Dict, Any, Optional, TYPE_CHECKING
import json
import logging
import re

from json_repair import repair_json

from .clients.llm_client import LLMClient
from .prompt import PromptLoader
from .frame import Frame

if TYPE_CHECKING:
    from .audio_processor import AudioTranscript


logger = logging.getLogger(__name__)


GROUP_PROMPT = """You are documenting a chronological group of video frames.
The images are supplied in exactly the order listed below:
{FRAME_INDEX}

Return JSON only, with this exact top-level shape: {\"scenes\": [...]}.
Return one scene for every supplied image. Each scene must contain:
- frame_number: integer copied from the list
- timestamp: number copied from the list
- visible_facts: array of directly visible, objective statements
- screen_text: array of readable on-screen strings
- changes: array of visible changes relative to the preceding supplied image
- key_objects: array of objects with name, bbox, and importance. bbox is
  [left, top, right, bottom], normalized to 0..1. Include at most 3 objects.
- uncertainties: array of facts that cannot be confirmed due to blur/occlusion
- inferences: array; leave empty unless an inference is explicitly requested

Do not infer quality, intent, identity, brand, causality, or truth from appearance.
Do not put speech in visible_facts. Use concise statements.
{USER_PROMPT}
"""

BILIBILI_GROUP_PROMPT = """你正在为B站视频制作一份节省token的图文素材记录。图片顺序与下面列表一致：
{FRAME_INDEX}

对应时刻的字幕提示：
{SUBTITLE_HINTS}

只返回JSON，顶层必须是 {"scenes": [...]}，每张图对应一个scene。每个scene包含：
- frame_number：列表中的整数；
- timestamp：列表中的秒数；
- visible_facts：最多2条，只记录视频内容本身，不描述播放器、弹幕、系统时间和无关边框；每条不超过28个汉字；
- screen_text：最多4条与主题有关的原文短语，保持原语言；不得逐项抄写同一张幻灯片；
- content_bbox：适合文章配图的保守矩形 [left, top, right, bottom]，坐标归一化为0到1；必须完整包含相关标题、图表、公式、标签和有意义的讲解动作，只排除无关边框、播放器界面、弹幕与大片空白；拿不准时返回[0,0,1,1]；
- content_confidence：0到1；
- crop_reason：只能是 slide、slide_and_teacher、full_frame 或 uncertain 之一；
- relevance：content、transition或noise；
- uncertainties：仅记录真正影响理解的不确定项，通常为空。

不要生成物品框，不要逐项解释浏览器或画面装饰。所有说明使用简体中文，screen_text除外。
"""

LECTURE_SLIDE_GROUP_PROMPT = """你正在逐页记录一堂大学课程的PPT。图片已经裁剪到固定的PPT屏幕区域，顺序如下：
{FRAME_INDEX}

只返回JSON，顶层必须是 {"scenes": [...]}，每张图对应一个scene。不得把多页合并。每个scene包含：
- frame_number：列表中的整数；
- timestamp：列表中的秒数；
- slide_title：PPT标题原文；
- visible_facts：2至6条，忠实说明本页在讲什么，保留变量名、数值、元件和关系；
- screen_text：0至12条重要的PPT原文，不要只摘标题；
- formulas：本页全部可辨认的关键公式、等式和不等式，保持原符号；
- diagrams：电路、波形、框图或坐标图的结构说明；
- relevance：content、duplicate或noise；只有画面确实不是课程PPT时才用noise；
- uncertainties：看不清或被老师遮挡且影响理解的内容。

这是课程记录，不是概括任务。不能为了简洁而省掉PPT上的推导条件、公式、图中标注和对比关系。不要补充画面外知识。
"""

LECTURE_ARTICLE_PROMPT = """你是一名严谨的课程记录编辑。请把下面的“逐页PPT记录”和“带时间的完整字幕”重组为一篇可直接发布的中文公众号文章。

这不是摘要，也不是提纲。首要目标是完整保留老师实际讲授的内容，连贯性排在完整性之后；不得为了文章简短、结构漂亮或避免重复而删掉有教学意义的信息。

必须遵守：
- 按老师的讲授顺序保留定义、问题提出、推导步骤、公式、数值例子、类比、条件限制、相互比较、设计取舍和结论。
- 老师对同一概念从不同角度的解释若提供了新的理由、例子或限定，必须保留；只删除口头语、结巴和完全同义的机械重复。
- PPT与字幕互相补充：公式和图示以PPT为准，解释过程以字幕为准。不得把字幕压缩成一句“老师介绍了……”。
- 不得加入材料之外的背景知识、应用场景、评价或学习建议。
- 允许文章较长，不设字数上限；以覆盖完整课堂内容为准。
- 使用自然的简体中文，设置标题和必要小标题；不得使用“报告”“分析”“摘要”“总结”作为标题或段落名。
- 每个relevance=content的PPT页都应在最相关段落前插入一次 `<!-- FRAME: N -->`。N只能使用所给frame_number，按顺序使用，不得重复。
- duplicate或noise页不必插入文章。

排版与语言规范：
- 使用一个一级标题，并使用足够多的二级、三级标题。每次教学主题、推导阶段或观察角度发生变化时都应分段，避免连续的大段文字。
- 每个自然段通常为2至4句。长推导可拆成“问题—推导—结论”三个短段，不要把整页讲解堆成一个段落。
- 删除“好”“那么”“其实”“我们来看”“大家需要知道”等不承载知识的口头衔接，以及重复的称呼和语气词；但口语后面的定义、理由、步骤、例子和限定条件必须保留。
- 使用Markdown加粗突出术语、变量关系和关键结论；适合比较的内容使用项目列表或表格，公式单独成行并加粗。
- 可使用以下四种HTML彩色标签，但要克制，全篇约6至12处，只标真正重要的信息：
  - `<span style="color:#2563EB"><strong>核心定义</strong></span>`：定义和概念边界；
  - `<span style="color:#D97706"><strong>推导要点</strong></span>`：关键推导步骤或假设；
  - `<span style="color:#059669"><strong>得到结论</strong></span>`：由材料直接得到的结论；
  - `<span style="color:#DC2626"><strong>注意</strong></span>`：容易混淆的条件、极性或限制。
- 彩色标签应放在Markdown引用块中，例如：`> <span ...>...</span> 正文`，以便即使颜色样式被平台过滤，仍保留醒目的引用块层次。
- 不要用“好同学们”开篇，不要模拟老师逐字说话；改写为书面课程记录，但不得变成简略概述。
- 正文建议保持3500至5500个汉字；如果完整覆盖需要更长，可以超过，不得为凑字数添加材料外内容。

逐页PPT记录：
{SCENES}

带时间的完整字幕：
{TRANSCRIPT}

只输出Markdown正文，不要解释编辑过程，不要使用代码围栏。
"""

LECTURE_FORMATTING_PROMPT = """你负责把一篇内容完整的课程记录转换为结构化文章文档。只返回JSON对象，不直接输出Markdown。

JSON必须严格符合：
{
  "title": "文章标题",
  "lead": ["导语短段1", "可选短段2"],
  "sections": [
    {
      "heading": "二级标题",
      "blocks": [
        {"type": "paragraph", "text": "可含**Markdown加粗**的2至4句正文"},
        {"type": "subheading", "text": "三级标题"},
        {"type": "callout", "kind": "definition|derivation|conclusion|warning", "text": "提示内容"},
        {"type": "equation", "text": "公式或变量关系"},
        {"type": "table", "headers": ["列1", "列2"], "rows": [["值1", "值2"]]},
        {"type": "frame", "frame_number": 1}
      ]
    }
  ]
}

内容约束：
- 这是重组，不是概括。不得删除原稿中的定义、推导步骤、公式、数值例子、类比、比较、限定条件和结论。
- 删除“好”“那么”“其实”“我们来看”“大家需要”等纯口头衔接和重复称呼；将逐字口语改为书面课程记录。
- 不得添加原稿外知识，不得使用“报告”“分析”“摘要”“总结”作为标题。

结构约束：
- sections至少7个；每个heading必须描述具体知识内容。
- 全文至少4个subheading block，段落通常2至4句，长推导拆成多个paragraph。
- 全文使用8至12个callout，四种kind都至少一次。
- 至少15处`**加粗**`，用于术语、条件、公式和结论。
- 对适合比较的课程内容使用table；不适合时不得硬造表格。
- frame block必须严格按此列表各出现一次，不得遗漏、重复或改序：{FRAME_NUMBERS}
- 结构化文本总量不得短于原稿的90%，只能缩减口头语和机械重复。

完整原稿：
{DRAFT}
"""

LECTURE_COVERAGE_REPAIR_PROMPT = """你负责修复一篇课程记录的PPT覆盖缺口。输出修复后的完整Markdown正文，不要解释，不要使用代码围栏。

严格要求：
- 保留现有文章的全部教学内容和书面结构，不得把文章缩写或重新概括。
- 图片标记必须严格按以下序列各出现一次，不得遗漏、重复或改变顺序：{FRAME_NUMBERS}
- 对缺失图片，依据“缺失页材料”在时间顺序对应位置补入必要的课程内容，并在该段落前插入对应 `<!-- FRAME: N -->`。
- 若只是重复或错序标记，只调整标记，不得删除其附近正文。
- 只使用所给PPT记录与字幕，不得补充外部知识；保留公式、条件、推导关系和教师解释。
- 不得使用“报告”“分析”“摘要”“总结”作为标题。

当前覆盖校验：
{VALIDATION}

缺失页材料：
{MISSING_MATERIALS}

待修复完整文章：
{DRAFT}
"""

LECTURE_FORMATTING_REPAIR_PROMPT = """下面的结构化课程文章没有通过程序校验。请只修复列出的错误，同时保持原稿全部教学内容。只返回修复后的完整JSON对象。

校验错误：
{ERRORS}

必须保留的完整原稿：
{DRAFT}

待修复JSON：
{DOCUMENT}
"""

LECTURE_GROUP_FORMATTING_REPAIR_PROMPT = """下面的课程文章局部没有通过结构或保真校验。只返回修复后的完整JSON对象，顶层只能是 `{"sections": [...]}`。

每个section只能包含heading和blocks。blocks必须是扁平数组，元素只能采用以下六种形状，禁止在任何block里再次放blocks：
- `{"type":"paragraph","text":"..."}`
- `{"type":"subheading","text":"..."}`
- `{"type":"callout","kind":"definition|derivation|conclusion|warning","text":"..."}`
- `{"type":"equation","text":"..."}`
- `{"type":"table","headers":["...","..."],"rows":[["...","..."]]}`
- `{"type":"frame","frame_number":1}`

若校验错误指出某个block含有content、blocks或其他未知字段，必须把该字段承载的教学正文原样迁移为紧随其后的独立paragraph block，然后删除未知字段；不得直接丢弃该正文。不得原样返回未修复JSON。

只修复列出的错误。保留原稿中的定义、解释、推导、例子、比较、条件和结论，不得添加原稿没有给出的知识。PPT编号由程序绑定，禁止输出frame block。删除“那么、其实、我们来看、大家要、啊”等纯口头衔接；禁止复制逐字稿后再追加重复的整理版，但书面化后的教学文本不能明显短于原稿。
若错误涉及标题命名，必须改成该段实际讲授的具体知识主题，禁止使用“报告、摘要、总结、小结”等泛化栏目名。
局部原稿含带时间字幕时，必须按讲授顺序保留每个不同的数值例子、初始条件、周期阶段、状态变化和因果解释；禁止只复述PPT结论而省略老师的推演过程。
若错误名包含group_missing_source_numbers，必须把错误名列出的原稿数值及其原有含义补回正文，禁止另行推导新数值。

校验错误：
{ERRORS}

必须保留的局部原稿：
{DRAFT}

待修复JSON：
{DOCUMENT}
"""

LECTURE_GROUP_FORMATTING_PROMPT = """你负责把一小段课程原稿转换为结构化文章局部。只返回JSON对象：
{"sections": [{"heading": "具体二级标题", "blocks": [
  {"type": "paragraph", "text": "含**必要加粗**的2至4句正文"},
  {"type": "subheading", "text": "具体三级标题"},
  {"type": "callout", "kind": "definition|derivation|conclusion|warning", "text": "提示内容"},
  {"type": "equation", "text": "公式"},
  {"type": "table", "headers": ["列1", "列2"], "rows": [["值1", "值2"]]},
  {"type": "frame", "frame_number": 1}
]}]}

必须遵守：
- 只整理所给局部原稿，不得概括掉定义、解释、推导、例子、比较、条件和结论，也不得添加原稿外知识。
- 必须重写逐字口语，删除“那么、其实、我们来看、大家要、啊”等不承载知识的填充语；禁止先复制逐字稿、再追加一段内容重复的整理版。同一知识点只写一次，文本量不得低于局部原稿的80%。
- 至少生成{MIN_SECTIONS}个具体section，至少{MIN_SUBHEADINGS}个subheading，至少{MIN_BOLD}处`**加粗**`。
- 有明确教学重点时最多生成1个callout，kind优先使用：{CALLOUT_KINDS}；纯目录或过渡页可以不生成callout，禁止为同一页生成第二个callout。
- PPT由程序绑定到本单元，禁止输出frame block。适合比较时使用table，不适合时不要硬造。
- table的每一个单元格都必须能直接追溯到局部原稿；原稿没有明确给出的数值或关系必须写“原稿未给出”或不设该列，禁止根据公式、常识或外部知识补齐。
- blocks必须是扁平数组；subheading只是标题，禁止在任何block里嵌套blocks或增加示例之外的字段。
- heading和subheading必须直接写具体知识主题，禁止使用“报告、摘要、总结、小结”等泛化栏目名；“稳态分析”等课程术语不受此限制。
- 局部原稿含带时间字幕时，必须按讲授顺序保留每个不同的数值例子、初始条件、周期阶段、状态变化和因果解释；禁止只复述PPT结论而省略老师的推演过程。

局部原稿：
{DRAFT}
"""

EDITORIAL_PROMPT = """你是一名严谨的中文编辑。请仅依据下面提供的“场景记录”和“语音转写”，写出一篇可直接发布在公众号的图文文章。

编辑目标：
- 删除与内容无关的浏览器标签、系统时间、音量浮层、播放控件和重复画面描述。
- 把重复场景合并为连贯段落，按教学内容而不是逐帧顺序组织。
- 保留可由材料支持的具体内容、术语、音标和示例；不要补充材料没有说明的知识。
- 当画面文字和语音可能不一致时，只写能确认的部分，或明确标注不确定。
- 用自然、克制的简体中文。文章应有标题、引言和小标题，可直接发布；不要使用“报告”“分析”“摘要”“总结”作为标题或段落名。
- 文章长度控制在 900 至 1,600 个汉字，避免逐帧复述和宣传腔。
- 不得加入原料中没有出现的语言学知识、例词、定义、历史背景或学习建议；不要使用“经典”“更自信”“希望”“降低难度”等泛化表述。
- 对来自转写的说法，使用“讲解中提到”或等价表述；不要把转写内容扩展为外部事实。
- 在最适合插图的段落前插入 4 至 8 个标记，格式必须是 `<!-- FRAME: N -->`，其中 N 是所给场景的 frame_number。只能使用已有的 N。

场景记录：
{SCENES}

语音转写：
{TRANSCRIPT}

只输出 Markdown 正文，不要使用代码围栏，也不要解释编辑过程。
"""

FACT_CHECK_PROMPT = """你是一名严格的事实校订编辑。请依据“可核验原料”改写“待校订文章”，输出可直接发布的 Markdown 正文。

要求：
- 删除所有无法由原料直接支持的句子，即使它们在常识上看似正确。
- 特别删除外部例词、音标释义、历史趋势、学习效果承诺、比喻性开场和结尾感言，除非原料明确出现。
- 允许合并重复内容、调整段落顺序和润色语言，但不能增加事实。
- 对语音转写中的技术说法，写成“讲解中提到……”，不要擅自校正或展开其含义。
- 保留文章标题、小标题和已有的 `<!-- FRAME: N -->` 图片标记；只能保留或使用原料已有的 N。
- 不使用“报告”“分析”“摘要”“总结”作标题或段落名。

可核验原料：
{SOURCES}

待校订文章：
{DRAFT}

只输出校订后的 Markdown 正文，不要解释改动。
"""


class VideoAnalyzer:
    def __init__(self, client: LLMClient, model: str, prompt_loader: PromptLoader,
                 temperature: float, user_prompt: str = ""):
        self.client = client
        self.model = model
        self.prompt_loader = prompt_loader
        self.temperature = temperature
        self.user_prompt = user_prompt
        self.call_log: List[Dict[str, Any]] = []
        self._load_prompts()

    def _format_user_prompt(self) -> str:
        return f"Additional user focus: {self.user_prompt}" if self.user_prompt else ""

    def _load_prompts(self):
        # The reconstruction prompt stays user-overridable for compatibility.
        self.frame_prompt = self.prompt_loader.get_by_index(0)
        self.video_prompt = self.prompt_loader.get_by_index(1)

    @staticmethod
    def _parse_json_response(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text = response.get("response", "")
        if isinstance(text, dict):
            return text
        if not isinstance(text, str):
            return None
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        try:
            value = json.loads(stripped)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
            if not match:
                return None
            try:
                value = json.loads(match.group(0))
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                try:
                    repaired = repair_json(stripped, return_objects=True)
                    return repaired if isinstance(repaired, dict) else None
                except Exception:
                    return None

    @staticmethod
    def _speech_at(timestamp: float, transcript: Optional[AudioTranscript]) -> str:
        if not transcript:
            return ""
        texts = []
        for segment in transcript.segments or []:
            if not isinstance(segment, dict):
                continue
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            if start <= timestamp <= end:
                text = str(segment.get("text", "")).strip()
                if text:
                    texts.append(text)
        return " ".join(texts)

    @staticmethod
    def _normalize_scene(scene: Dict[str, Any], frame: Frame,
                         transcript: Optional[AudioTranscript]) -> Dict[str, Any]:
        normalized = {
            "frame_number": frame.number,
            "timestamp": frame.timestamp,
            "frame_path": str(frame.path),
            "visible_facts": scene.get("visible_facts", []),
            "speech": VideoAnalyzer._speech_at(frame.timestamp, transcript),
            "screen_text": scene.get("screen_text", []),
            "changes": scene.get("changes", []),
            "key_objects": scene.get("key_objects", []),
            "uncertainties": scene.get("uncertainties", []),
            "inferences": scene.get("inferences", []),
        }
        for key in ("visible_facts", "screen_text", "changes", "key_objects",
                    "uncertainties", "inferences"):
            if not isinstance(normalized[key], list):
                normalized[key] = []
        return normalized

    def analyze_frame_group(self, frames: List[Frame],
                            transcript: Optional[AudioTranscript] = None) -> List[Dict[str, Any]]:
        frame_index = "\n".join(
            f"- image {index + 1}: frame_number={frame.number}, timestamp={frame.timestamp:.3f}"
            for index, frame in enumerate(frames)
        )
        prompt = GROUP_PROMPT.replace("{FRAME_INDEX}", frame_index)
        prompt = prompt.replace("{USER_PROMPT}", self._format_user_prompt())
        try:
            response = self.client.generate(
                prompt=prompt,
                image_paths=[str(frame.path) for frame in frames],
                response_format={"type": "json_object"},
                model=self.model,
                temperature=self.temperature,
                num_predict=max(800, len(frames) * 650),
            )
            self.call_log.append({
                "purpose": "scene_record",
                "frame_numbers": [frame.number for frame in frames],
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            parsed = self._parse_json_response(response)
            scenes = parsed.get("scenes", []) if parsed else []
            if not isinstance(scenes, list) or len(scenes) != len(frames):
                raise ValueError(
                    f"Expected {len(frames)} scenes, received {len(scenes) if isinstance(scenes, list) else 0}"
                )
            return [
                self._normalize_scene(scene if isinstance(scene, dict) else {}, frame, transcript)
                for scene, frame in zip(scenes, frames)
            ]
        except Exception as exc:
            logger.error("Error analyzing frame group: %s", exc)
            return [self._normalize_scene({
                "uncertainties": [f"Frame group analysis failed: {exc}"]
            }, frame, transcript) for frame in frames]

    def analyze_bilibili_frame_group(
        self,
        frames: List[Frame],
        transcript: Optional[AudioTranscript] = None,
    ) -> List[Dict[str, Any]]:
        frame_index = "\n".join(
            f"- image {index + 1}: frame_number={frame.number}, timestamp={frame.timestamp:.3f}"
            for index, frame in enumerate(frames)
        )
        hints = "\n".join(
            f"- frame {frame.number}: {self._speech_at(frame.timestamp, transcript) or '该时刻无字幕'}"
            for frame in frames
        )
        prompt = BILIBILI_GROUP_PROMPT.replace("{FRAME_INDEX}", frame_index)
        prompt = prompt.replace("{SUBTITLE_HINTS}", hints)
        try:
            response = self.client.generate(
                prompt=prompt,
                image_paths=[str(frame.path) for frame in frames],
                response_format={"type": "json_object"},
                model=self.model,
                temperature=0.0,
                num_predict=max(900, len(frames) * 220),
            )
            self.call_log.append({
                "purpose": "bilibili_scene_record",
                "frame_numbers": [frame.number for frame in frames],
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
                "response_excerpt": str(response.get("response", ""))[:4000],
            })
            parsed = self._parse_json_response(response)
            scenes = parsed.get("scenes", []) if parsed else []
            if not isinstance(scenes, list) or len(scenes) != len(frames):
                raise ValueError(
                    f"Expected {len(frames)} scenes, received {len(scenes) if isinstance(scenes, list) else 0}"
                )
            normalized = []
            for scene, frame in zip(scenes, frames):
                item = self._normalize_scene(scene if isinstance(scene, dict) else {}, frame, transcript)
                item["content_bbox"] = scene.get("content_bbox", [0, 0, 1, 1])
                item["content_confidence"] = scene.get("content_confidence", 0.0)
                item["crop_reason"] = scene.get("crop_reason", "")
                item["relevance"] = scene.get("relevance", "content")
                normalized.append(item)
            return normalized
        except Exception as exc:
            logger.error("Error analyzing Bilibili frame group: %s", exc)
            return [self._normalize_scene({
                "uncertainties": [f"Bilibili frame group failed: {exc}"]
            }, frame, transcript) for frame in frames]

    def analyze_lecture_slide_group(
        self,
        frames: List[Frame],
        transcript: Optional[AudioTranscript] = None,
    ) -> List[Dict[str, Any]]:
        frame_index = "\n".join(
            f"- image {index + 1}: frame_number={frame.number}, timestamp={frame.timestamp:.3f}"
            for index, frame in enumerate(frames)
        )
        prompt = LECTURE_SLIDE_GROUP_PROMPT.replace("{FRAME_INDEX}", frame_index)
        try:
            response = self.client.generate(
                prompt=prompt,
                image_paths=[str(frame.path) for frame in frames],
                response_format={"type": "json_object"},
                model=self.model,
                temperature=0.0,
                num_predict=max(1400, len(frames) * 520),
            )
            self.call_log.append({
                "purpose": "lecture_slide_record",
                "frame_numbers": [frame.number for frame in frames],
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
                "response_excerpt": str(response.get("response", ""))[:4000],
            })
            parsed = self._parse_json_response(response)
            scenes = parsed.get("scenes", []) if parsed else []
            if not isinstance(scenes, list) or len(scenes) != len(frames):
                raise ValueError(
                    f"Expected {len(frames)} slide scenes, received "
                    f"{len(scenes) if isinstance(scenes, list) else 0}"
                )
            normalized = []
            for scene, frame in zip(scenes, frames):
                source = scene if isinstance(scene, dict) else {}
                item = self._normalize_scene(source, frame, transcript)
                item["slide_title"] = str(source.get("slide_title", "")).strip()
                formulas = source.get("formulas", [])
                diagrams = source.get("diagrams", [])
                item["formulas"] = formulas if isinstance(formulas, list) else ([str(formulas)] if formulas else [])
                item["diagrams"] = diagrams if isinstance(diagrams, list) else ([str(diagrams)] if diagrams else [])
                item["relevance"] = source.get("relevance", "content")
                normalized.append(item)
            return normalized
        except Exception as exc:
            logger.error("Error analyzing lecture slide group: %s", exc)
            return [self._normalize_scene({
                "uncertainties": [f"Lecture slide group failed: {exc}"]
            }, frame, transcript) for frame in frames]

    def analyze_frames(self, frames: List[Frame], transcript: Optional[AudioTranscript] = None,
                       group_size: int = 6) -> List[Dict[str, Any]]:
        analyses: List[Dict[str, Any]] = []
        for start in range(0, len(frames), max(1, group_size)):
            analyses.extend(self.analyze_frame_group(frames[start:start + group_size], transcript))
        return analyses

    def analyze_frame(self, frame: Frame) -> Dict[str, Any]:
        return self.analyze_frame_group([frame])[0]

    def compose_record(self, scenes: List[Dict[str, Any]], frames: List[Frame],
                       transcript: Optional[AudioTranscript] = None) -> str:
        frame_notes = []
        for frame, scene in zip(frames, scenes):
            frame_notes.append(
                f"Frame {frame.number} ({frame.timestamp:.2f}s):\n"
                + json.dumps(scene, ensure_ascii=False)
            )
        transcript_text = transcript.text if transcript and transcript.text.strip() else ""
        prompt = self.video_prompt.replace("{prompt}", self._format_user_prompt())
        prompt = prompt.replace("{FRAME_NOTES}", "\n\n".join(frame_notes))
        prompt = prompt.replace("{FIRST_FRAME}", json.dumps(scenes[0], ensure_ascii=False) if scenes else "")
        prompt = prompt.replace("{TRANSCRIPT}", transcript_text)
        try:
            response = self.client.generate(
                prompt=prompt, model=self.model, temperature=self.temperature, num_predict=1000
            )
            self.call_log.append({
                "purpose": "continuous_record",
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return str(response.get("response", "")).strip()
        except Exception as exc:
            logger.error("Error composing chronological record: %s", exc)
            return f"连续记录生成失败：{exc}"

    def compose_article(self, scenes: List[Dict[str, Any]],
                        transcript: Optional[AudioTranscript] = None,
                        source_context: Optional[Dict[str, Any]] = None) -> str:
        """Turn auditable scene records into a publishable, source-bounded article."""
        scene_source = self._article_source(scenes)
        prompt = EDITORIAL_PROMPT.replace(
            "{SCENES}", json.dumps(scene_source, ensure_ascii=False)
        ).replace(
            "{TRANSCRIPT}", transcript.text if transcript and transcript.text.strip() else "未提供"
        )
        if source_context:
            prompt = (
                "以下是发布者提供的来源元数据，只能用于标题、作者、发布日期和背景，不得替代视频事实：\n"
                + json.dumps(source_context, ensure_ascii=False)
                + "\n\n"
                + prompt
            )
        try:
            response = self.client.generate(
                prompt=prompt,
                model=self.model,
                temperature=0.15,
                num_predict=6000,
            )
            self.call_log.append({
                "purpose": "editorial_article",
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return str(response.get("response", "")).strip()
        except Exception as exc:
            logger.error("Error composing article: %s", exc)
            return f"# 文章生成失败\n\n{exc}"

    @staticmethod
    def _timestamped_transcript(transcript: Optional[AudioTranscript]) -> str:
        if not transcript:
            return "未提供"
        lines = []
        for segment in transcript.segments or []:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            text = str(segment.get("text", "")).strip()
            if text:
                lines.append(f"[{start:.1f}-{end:.1f}] {text}")
        return "\n".join(lines) or transcript.text

    def compose_lecture_article(
        self,
        scenes: List[Dict[str, Any]],
        transcript: Optional[AudioTranscript] = None,
        source_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        source_scenes = [{
            "frame_number": scene.get("frame_number"),
            "timestamp": scene.get("timestamp"),
            "slide_title": scene.get("slide_title", ""),
            "visible_facts": scene.get("visible_facts", []),
            "screen_text": scene.get("screen_text", []),
            "formulas": scene.get("formulas", []),
            "diagrams": scene.get("diagrams", []),
            "relevance": scene.get("relevance", "content"),
            "uncertainties": scene.get("uncertainties", []),
        } for scene in scenes]
        prompt = LECTURE_ARTICLE_PROMPT.replace(
            "{SCENES}", json.dumps(source_scenes, ensure_ascii=False)
        ).replace("{TRANSCRIPT}", self._timestamped_transcript(transcript))
        if source_context:
            prompt = "来源元数据：\n" + json.dumps(source_context, ensure_ascii=False) + "\n\n" + prompt
        try:
            response = self.client.generate(
                prompt=prompt,
                model=self.model,
                temperature=0.1,
                num_predict=10000,
            )
            self.call_log.append({
                "purpose": "complete_lecture_article",
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return str(response.get("response", "")).strip()
        except Exception as exc:
            logger.error("Error composing complete lecture article: %s", exc)
            return f"# 课程记录生成失败\n\n{exc}"

    def repair_lecture_article_coverage(
        self,
        article_text: str,
        frame_numbers: List[int],
        validation: Dict[str, Any],
        missing_materials: List[Dict[str, Any]],
    ) -> str:
        prompt = LECTURE_COVERAGE_REPAIR_PROMPT.replace(
            "{FRAME_NUMBERS}", json.dumps(frame_numbers)
        ).replace(
            "{VALIDATION}", json.dumps(validation, ensure_ascii=False)
        ).replace(
            "{MISSING_MATERIALS}", json.dumps(missing_materials, ensure_ascii=False)
        ).replace(
            "{DRAFT}", article_text
        )
        try:
            response = self.client.generate(
                prompt=prompt,
                model=self.model,
                temperature=0.0,
                num_predict=10000,
            )
            self.call_log.append({
                "purpose": "lecture_article_coverage_repair",
                "missing_frames": validation.get("missing", []),
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return str(response.get("response", "")).strip()
        except Exception as exc:
            logger.error("Error repairing lecture article coverage: %s", exc)
            return article_text

    def format_lecture_article(self, article_text: str,
                               frame_numbers: List[int]) -> Optional[Dict[str, Any]]:
        prompt = LECTURE_FORMATTING_PROMPT.replace("{DRAFT}", article_text)
        prompt = prompt.replace("{FRAME_NUMBERS}", json.dumps(frame_numbers))
        try:
            response = self.client.generate(
                prompt=prompt,
                response_format={"type": "json_object"},
                model=self.model,
                temperature=0.0,
                num_predict=10000,
            )
            self.call_log.append({
                "purpose": "lecture_structured_formatting",
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
                "response_excerpt": str(response.get("response", ""))[:4000],
                "response_characters": len(str(response.get("response", ""))),
            })
            return self._parse_json_response(response)
        except Exception as exc:
            logger.error("Error creating structured lecture article: %s", exc)
            return None

    def repair_lecture_article_document(
        self,
        article_text: str,
        document: Dict[str, Any],
        errors: List[str],
    ) -> Optional[Dict[str, Any]]:
        prompt = LECTURE_FORMATTING_REPAIR_PROMPT.replace(
            "{ERRORS}", json.dumps(errors, ensure_ascii=False)
        ).replace(
            "{DRAFT}", article_text
        ).replace(
            "{DOCUMENT}", json.dumps(document, ensure_ascii=False)
        )
        try:
            response = self.client.generate(
                prompt=prompt,
                response_format={"type": "json_object"},
                model=self.model,
                temperature=0.0,
                num_predict=10000,
            )
            self.call_log.append({
                "purpose": "lecture_structured_repair",
                "errors": errors,
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return self._parse_json_response(response)
        except Exception as exc:
            logger.error("Error repairing structured lecture article: %s", exc)
            return None

    def format_lecture_article_group(
        self,
        article_excerpt: str,
        frame_numbers: List[int],
        callout_kinds: List[str],
    ) -> Optional[Dict[str, Any]]:
        prompt = LECTURE_GROUP_FORMATTING_PROMPT.replace(
            "{DRAFT}", article_excerpt
        ).replace(
            "{FRAME_NUMBERS}", json.dumps(frame_numbers)
        ).replace(
            "{CALLOUT_KINDS}", json.dumps(callout_kinds, ensure_ascii=False)
        ).replace(
            "{MIN_SECTIONS}", str(max(1, len(frame_numbers)))
        ).replace(
            "{MIN_SUBHEADINGS}", str(max(1, len(frame_numbers)))
        ).replace(
            "{MIN_BOLD}", str(max(2, len(frame_numbers) * 2))
        )
        try:
            response = self.client.generate(
                prompt=prompt,
                response_format={"type": "json_object"},
                model=self.model,
                temperature=0.0,
                num_predict=5000,
            )
            self.call_log.append({
                "purpose": "lecture_group_structured_formatting",
                "frame_numbers": frame_numbers,
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
                "response_excerpt": str(response.get("response", ""))[:2000],
            })
            return self._parse_json_response(response)
        except Exception as exc:
            logger.error("Error formatting lecture group: %s", exc)
            return None

    def repair_lecture_article_group(
        self,
        article_excerpt: str,
        group: Dict[str, Any],
        errors: List[str],
    ) -> Optional[Dict[str, Any]]:
        prompt = LECTURE_GROUP_FORMATTING_REPAIR_PROMPT.replace(
            "{ERRORS}", json.dumps(errors, ensure_ascii=False)
        ).replace(
            "{DRAFT}", article_excerpt
        ).replace(
            "{DOCUMENT}", json.dumps(group, ensure_ascii=False)
        )
        try:
            response = self.client.generate(
                prompt=prompt,
                response_format={"type": "json_object"},
                model=self.model,
                temperature=0.0,
                num_predict=5000,
            )
            self.call_log.append({
                "purpose": "lecture_group_structured_repair",
                "errors": errors,
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return self._parse_json_response(response)
        except Exception as exc:
            logger.error("Error repairing structured lecture group: %s", exc)
            return None

    @staticmethod
    def _article_source(scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{
            "frame_number": scene.get("frame_number"),
            "timestamp": scene.get("timestamp"),
            "visible_facts": scene.get("visible_facts", []),
            "speech": scene.get("speech", ""),
            "screen_text": scene.get("screen_text", []),
            "uncertainties": scene.get("uncertainties", []),
        } for scene in scenes]

    def fact_check_article(self, article_text: str, scenes: List[Dict[str, Any]],
                           source_context: Optional[Dict[str, Any]] = None) -> str:
        """Remove editorial claims that cannot be traced to the source records."""
        prompt = FACT_CHECK_PROMPT.replace(
            "{SOURCES}", json.dumps({
                "metadata": source_context or {},
                "scenes": self._article_source(scenes),
            }, ensure_ascii=False)
        ).replace("{DRAFT}", article_text)
        try:
            response = self.client.generate(
                prompt=prompt,
                model=self.model,
                temperature=0.0,
                num_predict=6000,
            )
            self.call_log.append({
                "purpose": "editorial_fact_check",
                "model": response.get("model", self.model),
                "usage": response.get("usage"),
                "finish_reason": response.get("finish_reason"),
            })
            return str(response.get("response", "")).strip()
        except Exception as exc:
            logger.error("Error fact-checking article: %s", exc)
            return article_text
