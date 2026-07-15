"""Register already-audited P07 and P08 as immediately playable lessons."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
LESSONS = ROOT / "data" / "lessons"
LESSONS.mkdir(parents=True, exist_ok=True)
SERIES = "BV1pS4y1g7D9"


def q(identifier, prompt, options, answer, explanation=None):
    return {
        "id": identifier,
        "type": "choice",
        "prompt": prompt,
        "options": options,
        "answers": [answer],
        "explanation": explanation or answer,
    }


def checkpoint(identifier, time, title, summary, questions):
    return {"id": identifier, "time": time, "title": title, "summary": summary, "questions": questions}


def write_lesson(value):
    path = LESSONS / f"{value['id']}.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


p07_record = PROJECT / "records" / f"{SERIES}-p07_ppt_complete_mimo-v2.5_tem1"
p07_video = PROJECT / "downloads" / f"{SERIES}-p07_20260713" / "电力电子.mp4"
p07 = {
    "id": "p07-introduction",
    "series_id": SERIES,
    "part": 7,
    "source_url": f"https://www.bilibili.com/video/{SERIES}?p=7",
    "title": "2.1 介绍",
    "overview": "从稳态与暂态出发，介绍 Buck 降压电路、占空比、PWM、方波频谱、低通滤波以及三种基本 DC-DC 变换器。",
    "teaching_plan": """# 2.1 稳态分析导论

## 学习目标
- 区分稳态与暂态。
- 理解 Buck 降压、电路开关状态、占空比与 PWM。
- 理解方波直流分量、频谱和低通滤波的关系。
- 识别 Buck、Boost、Buck-Boost 的基本功能。

## 教学结构
1. 稳态和暂态的物理含义。
2. Buck 开关网络与占空比控制。
3. 方波的平均值和频率分量。
4. LC 低通滤波提取直流量。
5. 三种基本 DC-DC 变换器。
6. 本章后续将使用的分析方法。

## 重点与易错点
- 占空比是导通时间与周期的比值，互补值为 1-D。
- 直流分量就是周期平均值。
- Buck-Boost 不仅可升可降，而且输出极性反转。
""",
    "duration": 651.36,
    "video_path": str(p07_video),
    "subtitle_path": str(PROJECT / "downloads" / f"{SERIES}-p07_20260713" / "电力电子.中文.srt"),
    "record_dir": str(p07_record),
    "article_path": str(p07_record / "article.md"),
    "generation": {"source": "existing_audited_materials"},
    "checkpoints": [
        checkpoint("steady-transient", 68, "稳态与暂态", "老师完成了稳态最终状态和从零过渡到目标输出的暂态概念。", [
            q("p07q01", "电力电子系统希望达到的、稳定的最终运行状态叫作什么？", ["稳态", "暂态", "开关态"], "稳态"),
            q("p07q02", "输出电压从 0 V 逐渐过渡到稳定的 50 V，这一过程叫什么？", ["稳态", "暂态", "静态"], "暂态"),
        ]),
        checkpoint("buck-pwm", 219, "Buck、占空比与 PWM", "老师讲完了 Buck 的降压目标、占空比、互补占空比和脉冲宽度调制。", [
            q("p07q03", "Buck 变换器的输出直流电压相对输入电压如何？", ["高于输入", "低于输入", "始终相等"], "低于输入"),
            q("p07q04", "占空比 D 是什么时间与开关周期 T 的比值？", ["导通时间", "关断时间", "上升时间"], "导通时间", "D = T_on / T"),
            q("p07q05", "占空比的互补值 D′ 是什么？", ["D+1", "1-D", "D-1"], "1-D"),
            q("p07q06", "调节脉冲宽度控制输出的方法叫什么？", ["PWM 调制", "调频", "线性稳压"], "PWM 调制"),
        ]),
        checkpoint("dc-spectrum", 394, "方波的直流分量与频谱", "老师完成了平均值、视觉面积法和 D=0.5 方波奇次谐波的说明。", [
            q("p07q08", "方波的直流分量就是波形的什么值？", ["平均值", "峰值", "有效值"], "平均值"),
            q("p07q09", "课程用哪两种方式求直流分量？", ["数学积分法和视觉面积法", "微分法和相量法", "测频法和比较法"], "数学积分法和视觉面积法"),
            q("p07q10", "D=0.5 的方波还包含 3、5、7 倍频等什么次谐波？", ["奇次", "偶次", "分数次"], "奇次"),
        ]),
        checkpoint("filter-output", 499, "低通滤波与 Buck 输出", "老师讲完了截止频率、LC 滤波以及输出平均值受占空比控制。", [
            q("p07q11", "低通滤波器截止频率应放在 0 和什么之间？", ["基频 fs", "3fs", "无穷大"], "基频 fs"),
            q("p07q12", "课程使用什么低通滤波器得到平滑直流输出？", ["RC", "LC", "RL"], "LC"),
            q("p07q07", "Vin=100 V、D=0.5 时，理想 Buck 的 Vout 是多少？", ["25 V", "50 V", "200 V"], "50 V", "Vout = D×Vin = 50 V"),
        ]),
        checkpoint("converter-types", 581, "DC-DC 变换器类型", "老师讲完了 Buck、Boost、Buck-Boost 的功能和增益含义。", [
            q("p07q13", "电压增益 M(D) 表示什么比值？", ["输出电压/输入电压", "输入电压/输出电压", "纹波/输出"], "输出电压/输入电压"),
            q("p07q14", "哪一组对应关系正确？", ["Buck降压；Boost升压；Buck-Boost可升可降且反相", "Buck升压；Boost降压；Buck-Boost仅降压", "三者都只降压"], "Buck降压；Boost升压；Buck-Boost可升可降且反相"),
            q("p07q15", "Buck-Boost 输入为正时，输出极性是什么？", ["正", "负", "不确定"], "负"),
        ]),
        checkpoint("chapter-methods", 636, "本章分析方法", "老师概述了后续的伏秒平衡、电荷平衡和小纹波近似。", [
            q("p07q16", "伏秒平衡和电荷平衡分别应用于什么元件？", ["电感；电容", "电容；电感", "电阻；开关"], "电感；电容"),
        ]),
    ],
}

p08_record = PROJECT / "records" / f"{SERIES}-p08_ppt_complete_mimo-v2.5_tem1"
p08_video = PROJECT / "downloads" / f"{SERIES}-p08" / "电力电子.mp4"
p08 = {
    "id": "p08-steady-state-methods",
    "series_id": SERIES,
    "part": 8,
    "source_url": f"https://www.bilibili.com/video/{SERIES}?p=8",
    "title": "2.2 稳态分析的基本方法",
    "overview": "通过 Buck 变换器推导小纹波近似、分区间状态方程、电感电流纹波、周期性稳态、伏秒平衡与电容电荷平衡。",
    "teaching_plan": """# 2.2 稳态分析的基本方法

## 学习目标
- 使用小纹波近似简化输出电压。
- 按开关状态分区间建立电感方程。
- 理解电感电压方波与电流三角波的关系。
- 使用周期性稳态条件推导伏秒平衡和电荷平衡。

## 教学结构
1. 小纹波近似及其应用前提。
2. 开关状态与状态变量的统一定义。
3. Buck 两个子区间的电感电压、电流斜率。
4. 电感电流纹波与电感参数设计。
5. 启动暂态到周期性稳态。
6. 电感伏秒平衡与电容电荷平衡。

## 重点与易错点
- 不同子区间必须保持电压、电流参考方向一致。
- 周期性稳态要求周期起点和终点状态相等。
- 电感的激励是电压，电容的激励是电流。
""",
    "duration": 1662.92,
    "video_path": str(p08_video),
    "subtitle_path": str(PROJECT / "downloads" / f"{SERIES}-p08" / "电力电子.中文.srt"),
    "record_dir": str(p08_record),
    "article_path": str(p08_record / "article.md"),
    "generation": {"source": "existing_audited_materials"},
    "checkpoints": [
        checkpoint("small-ripple", 167, "小纹波近似", "老师讲完了直流分量、纹波分量以及忽略小纹波的条件。", [
            q("p08q01", "实际输出电压由哪两部分组成？", ["直流分量与纹波分量", "输入与负载", "电压与功率"], "直流分量与纹波分量", "vo(t)=Vo+vripple(t)"),
            q("p08q02", "纹波远小于直流分量时，可以近似认为 vo(t) 等于什么？", ["Vo", "0", "Vin"], "Vo"),
        ]),
        checkpoint("states", 348, "分区间分析与状态变量", "老师讲完了开关改变拓扑、各子区间分析和参考方向一致性。", [
            q("p08q03", "功率电子电路为什么需要分区间分析？", ["开关状态改变电路拓扑", "输出始终为零", "电感电流始终不变"], "开关状态改变电路拓扑"),
            q("p08q04", "下面哪一组都是课程定义的状态相关量？", ["iL、vL、iC、vo", "频率、功率因数", "温度、转速"], "iL、vL、iC、vo"),
        ]),
        checkpoint("interval-one", 515, "Buck 子区间 1", "老师完成了开关位置 1 时电感电压和电流正斜率推导。", [
            q("p08q05", "位置 1 时电感电压 vL 是什么？", ["Vin-vo(t)", "-vo(t)", "0"], "Vin-vo(t)"),
            q("p08q06", "小纹波近似后，diL/dt 是什么？", ["(Vin-Vo)/L", "-Vo/L", "0"], "(Vin-Vo)/L"),
            q("p08q07", "子区间 1 的电感电流如何变化？", ["线性上升", "线性下降", "保持不变"], "线性上升"),
        ]),
        checkpoint("interval-two", 693, "Buck 子区间 2", "老师完成了位置 2 的负电感电压、负斜率和完整周期波形。", [
            q("p08q08", "位置 2 时，小纹波近似下 vL 是什么？", ["-Vo", "Vin-Vo", "Vin"], "-Vo"),
            q("p08q09", "位置 2 时 diL/dt 是什么？", ["-Vo/L", "(Vin-Vo)/L", "0"], "-Vo/L"),
            q("p08q10", "一个周期内电感电压和电感电流分别是什么典型波形？", ["方波；三角波", "正弦波；方波", "三角波；直线"], "方波；三角波"),
        ]),
        checkpoint("inductor-ripple", 928, "电感电流纹波", "老师讲完了纹波定义、公式和电感值对纹波的影响。", [
            q("p08q11", "课程中的 ΔiL 是峰峰值的多少？", ["一半", "两倍", "四分之一"], "一半"),
            q("p08q12", "ΔiL=(Vin-Vo)/(2L) 还要乘以什么？", ["D·Ts", "D/Ts", "L·Ts"], "D·Ts"),
        ]),
        checkpoint("transient-steady", 1263, "启动暂态与周期性稳态", "老师讲完了开机初始状态、逐周期建立输出和稳态闭环条件。", [
            q("p08q13", "Buck 刚开机时，vo(0) 和 iL(0) 是多少？", ["0，0", "Vin，0", "0，Vin"], "0，0"),
            q("p08q14", "周期性稳态下，相邻周期同一时刻的 iL 满足什么？", ["iL((n+1)Ts)=iL(nTs)", "iL((n+1)Ts)=0", "iL((n+1)Ts)>iL(nTs)"], "iL((n+1)Ts)=iL(nTs)"),
        ]),
        checkpoint("volt-second", 1495, "电感伏秒平衡", "老师用电感电流周期变化为零推导了平均电感电压为零和 Buck 电压关系。", [
            q("p08q15", "周期性稳态下，一个周期的平均电感电压是多少？", ["0", "Vin", "Vo"], "0"),
            q("p08q16", "Buck 应用伏秒平衡得到的输出关系是什么？", ["Vo=D·Vin", "Vo=Vin/D", "Vo=(1-D)Vin"], "Vo=D·Vin"),
        ]),
        checkpoint("charge-balance", 1643, "电容电荷平衡", "老师讲完了稳态下电容平均电流为零的电荷平衡条件。", [
            q("p08q17", "周期性稳态下，一个周期的平均电容电流是多少？", ["0", "Vin/R", "Vo/R"], "0"),
        ]),
    ],
}

for lesson in (p07, p08):
    for key in ("video_path", "article_path"):
        if not Path(lesson[key]).is_file():
            raise FileNotFoundError(lesson[key])
    write_lesson(lesson)
