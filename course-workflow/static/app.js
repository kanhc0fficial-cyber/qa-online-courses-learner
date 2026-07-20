const app = document.querySelector("#app");
const VOLUME_BOOST_KEY = "course-player-volume-boost";
const VOLUME_BOOST_LEVELS = [1, 1.5, 2];

const escapeHtml = (value = "") => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[ch]));
const formatTime = seconds => `${String(Math.floor(seconds / 60)).padStart(2,"0")}:${String(Math.floor(seconds % 60)).padStart(2,"0")}`;
const normalize = value => String(value || "").toLowerCase().replace(/[\s（）()·。；;，,′'"“”]/g, "").replace(/ₛ/g,"s");
let layoutEditing = false;
let homeLayout = {source_order:[], lesson_order:{}, titles:{}};

function header(extra = "") {
  return `<header class="site-header">
    <a class="logo" href="/" aria-label="返回课程工坊首页"><img src="/static/course-flow.svg" alt=""></a>
    <div><p class="eyebrow">POWER ELECTRONICS · LESSON FORGE</p><h1>互动课程工坊</h1></div>
    ${extra || `<div class="series-lock"><i class="lock-dot"></i><span>任意 B 站视频</span></div>`}
  </header>`;
}

function renderMarkdown(source) {
  const images = [];
  const prepared = String(source || "")
    .replace(/<!--[^]*?-->/g, "")
    .replace(/<span[^>]*>([^]*?)<\/span>/gi, "$1")
    .replace(/<strong>([^]*?)<\/strong>/gi, "**$1**")
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, url) => {
      const safeUrl = String(url).trim();
      const allowed = safeUrl.startsWith("/api/lessons/") || safeUrl === "missing-image";
      const token = `@@ARTICLE_IMAGE_${images.length}@@`;
      images.push({alt: String(alt || "课程图片"), url: allowed ? safeUrl : "missing-image"});
      return token;
    });
  const lines = escapeHtml(prepared).split(/\r?\n/);
  const output = [];
  let list = false;
  for (const raw of lines) {
    let line = raw.replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>");
    const imageMatch = line.trim().match(/^@@ARTICLE_IMAGE_(\d+)@@$/);
    if (imageMatch) { if(list){output.push("</ul>");list=false} const image=images[Number(imageMatch[1])]; output.push(image.url === "missing-image" ? `<div class="missing-image">图片文件不存在：${escapeHtml(image.alt)}</div>` : `<figure class="article-image"><img src="${escapeHtml(image.url)}" alt="${escapeHtml(image.alt)}" loading="lazy"><figcaption>${escapeHtml(image.alt)}</figcaption></figure>`); }
    else if (/^### /.test(line)) { if(list){output.push("</ul>");list=false} output.push(`<h3>${line.slice(4)}</h3>`); }
    else if (/^## /.test(line)) { if(list){output.push("</ul>");list=false} output.push(`<h2>${line.slice(3)}</h2>`); }
    else if (/^# /.test(line)) { if(list){output.push("</ul>");list=false} output.push(`<h1>${line.slice(2)}</h1>`); }
    else if (/^- /.test(line)) { if(!list){output.push("<ul>");list=true} output.push(`<li>${line.slice(2)}</li>`); }
    else if (/^&gt; /.test(line)) { if(list){output.push("</ul>");list=false} output.push(`<blockquote>${line.slice(5)}</blockquote>`); }
    else if (!line.trim()) { if(list){output.push("</ul>");list=false} }
    else { if(list){output.push("</ul>");list=false} output.push(`<p>${line}</p>`); }
  }
  if (list) output.push("</ul>");
  return output.join("");
}

function setupVolumeBoost(video) {
  const savedBoost = Number(localStorage.getItem(VOLUME_BOOST_KEY));
  let boost = VOLUME_BOOST_LEVELS.includes(savedBoost) ? savedBoost : 1.5;
  let audioContext = null, sourceNode = null, gainNode = null, limiterNode = null;
  const buttons = [...document.querySelectorAll("[data-volume-boost]")];
  const status = document.querySelector("#volume-boost-status");

  function renderSelection() {
    buttons.forEach(button => {
      const selected = Number(button.dataset.volumeBoost) === boost;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    status.textContent = boost === 1 ? "原始音量" : `日常增强 ${Math.round(boost * 100)}%`;
  }

  async function ensureAudioGraph() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      status.textContent = "当前浏览器不支持音量增强";
      return;
    }
    if (!audioContext) {
      audioContext = new AudioContextClass();
      sourceNode = audioContext.createMediaElementSource(video);
      gainNode = audioContext.createGain();
      limiterNode = audioContext.createDynamicsCompressor();
      limiterNode.threshold.value = -3;
      limiterNode.knee.value = 0;
      limiterNode.ratio.value = 12;
      limiterNode.attack.value = 0.003;
      limiterNode.release.value = 0.15;
      sourceNode.connect(gainNode).connect(limiterNode).connect(audioContext.destination);
    }
    gainNode.gain.value = boost;
    if (audioContext.state === "suspended") await audioContext.resume();
  }

  buttons.forEach(button => button.addEventListener("click", async () => {
    boost = Number(button.dataset.volumeBoost);
    localStorage.setItem(VOLUME_BOOST_KEY, String(boost));
    renderSelection();
    try { await ensureAudioGraph(); }
    catch (error) { status.textContent = "音量增强启用失败，请重试"; }
  }));
  video.addEventListener("play", () => ensureAudioGraph().catch(() => {
    status.textContent = "音量增强启用失败，请重试";
  }));
  renderSelection();
}

async function jsonFetch(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败：${response.status}`);
  return data;
}

async function dashboard() {
  const [data, layout] = await Promise.all([
    jsonFetch("/api/series"),
    jsonFetch("/api/home-layout"),
  ]);
  homeLayout = layout;
  const shelves = buildCourseShelves(data.lessons, homeLayout);
  app.innerHTML = `<main class="shell">
    ${header()}
    <section class="hero">
      <div class="hero-copy">
        <img class="hero-illustration" src="/static/course-flow.svg" alt="课程知识路径图" loading="eager">
        <div><p class="eyebrow">ONE VIDEO · ONE WORKFLOW · ONE URL</p><h2>从视频链接到<br><em>可互动课程</em></h2><p>支持任意 B 站 BV 视频。音标课程使用画面 OCR 作为唯一事实源，不读取中英混杂字幕。</p></div>
        <div class="flow-strip">
          ${["校验系列","下载字幕","理解 PPT","教案与题目","网址播放"].map((x,i)=>`<div class="flow-step"><span>0${i+1}</span><strong>${x}</strong></div>`).join("")}
        </div>
      </div>
      <div class="task-cards">
      <form class="submit-card" id="job-form">
        <p class="eyebrow">NEW VIDEO</p><h3>制作一节互动课</h3><p>粘贴任意 B 站视频链接或 BV 号；单集视频无需填写分集。</p>
        <div class="field"><label for="source">B站视频链接或 BV 号</label><input id="source" name="source" required placeholder="https://www.bilibili.com/video/BV... ?p=1"></div>
        <div class="field-row">
          <div class="field"><label for="part">分集 P</label><input id="part" name="part" type="number" min="1" placeholder="9"></div>
          <label class="check-field"><input id="reuse" type="checkbox" checked> 优先复用已下载视频</label>
        </div>
        <div class="field"><label for="validation-profile">课程链路</label><select id="validation-profile" name="validationProfile"><option value="strict_course">严格课程（PPT + 字幕）</option><option value="phonetics_course">音标课程（OCR，不用字幕）</option><option value="general_video">通用视频</option></select></div>
        <label class="check-field"><input id="ppt-complete" type="checkbox" checked> 完整 PPT 记录（音标课程会自动关闭）</label>
        <button class="submit-button" type="submit">开始自动制作 →</button><p class="form-message" id="form-message"></p>
      </form>
      <form class="submit-card batch-card" id="batch-form">
        <p class="eyebrow">MULTI-P WORKFLOW</p><h3>处理多个分集</h3><p>指定起止 P，可仅下载资源，也可继续并行或串行制作课程。</p>
        <div class="field"><label for="batch-source">B站视频链接或 BV 号</label><input id="batch-source" name="source" required placeholder="BV... 或视频链接"></div>
        <div class="range-fields">
          <div class="field"><label for="start-part">从 P</label><input id="start-part" name="startPart" type="number" min="1" max="999" required value="1"></div>
          <div class="field"><label for="end-part">到 P</label><input id="end-part" name="endPart" type="number" min="1" max="999" required value="1"></div>
        </div>
        <div class="field"><label for="batch-action">任务模式</label><select id="batch-action" name="batchAction"><option value="download">仅下载资源</option><option value="course">下载并制作课程</option></select></div>
        <div class="field"><label for="execution-mode">执行方式</label><select id="execution-mode" name="executionMode"><option value="serial">串行（逐个执行）</option><option value="parallel">并发（最多同时 3 个）</option></select></div>
        <div id="batch-course-options" hidden>
          <div class="field"><label for="batch-validation-profile">课程链路</label><select id="batch-validation-profile"><option value="strict_course">严格课程（PPT + 字幕）</option><option value="phonetics_course">音标课程（OCR，不用字幕）</option><option value="general_video">通用视频</option></select></div>
          <label class="check-field"><input id="batch-ppt-complete" type="checkbox" checked> 完整 PPT 记录（音标课程会自动关闭）</label>
        </div>
        <div class="download-policy"><span>新下载清晰度</span><strong>720p 固定</strong></div>
        <label class="check-field"><input id="batch-reuse" type="checkbox" checked> 已下载的分集直接复用</label>
        <button class="submit-button" type="submit">开始批量下载 →</button><p class="form-message" id="batch-message"></p>
      </form>
      </div>
    </section>
    <div class="section-head task-status-head"><div><p class="eyebrow">PIPELINE STATUS</p><h2>当前与最近任务</h2></div><span>失败现场会保留；可恢复的音标 OCR 从已完成段继续</span></div>
    <section class="jobs-panel prominent-jobs"><div class="job-list" id="job-list">${renderJobs(data.jobs)}</div></section>
    <div class="section-head course-display-head"><div><p class="eyebrow">READY TO LEARN</p><h2>课程陈列</h2></div><div class="display-actions"><span>${shelves.length} 个陈列 · ${data.lessons.length} 节可播放</span><button class="layout-button" id="layout-toggle">${layoutEditing ? "完成调整" : "调整陈列"}</button>${layoutEditing ? `<button class="layout-button subtle" id="layout-reset">恢复默认排序</button>` : ""}</div></div>
    <section class="course-shelves">${shelves.length ? shelves.map((shelf,index)=>courseShelf(shelf,index,shelves.length)).join("") : `<div class="empty">尚无完成课程</div>`}</section>
  </main>`;
  bindJobForm();
  bindBatchForm();
  bindLayoutControls(shelves);
  if (data.jobs.some(job => ["queued","running"].includes(job.status))) pollDashboard();
}

function buildCourseShelves(lessons, layout) {
  const groups = new Map();
  lessons.forEach(lesson => {
    const sourceId = lesson.series_id || String(lesson.source_url || "").split("?")[0] || "未分类";
    if (!groups.has(sourceId)) groups.set(sourceId, []);
    groups.get(sourceId).push(lesson);
  });
  const defaultSources = [...groups.keys()];
  const sourceOrder = [
    ...(layout.source_order || []).filter(sourceId => groups.has(sourceId)),
    ...defaultSources.filter(sourceId => !(layout.source_order || []).includes(sourceId)),
  ];
  return sourceOrder.map(sourceId => {
    const defaultLessons = groups.get(sourceId).slice().sort((a,b) =>
      Number(a.part || 0) - Number(b.part || 0) || String(a.id).localeCompare(String(b.id))
    );
    const customOrder = layout.lesson_order?.[sourceId] || [];
    const byId = new Map(defaultLessons.map(lesson => [lesson.id, lesson]));
    const ordered = [
      ...customOrder.map(id => byId.get(id)).filter(Boolean),
      ...defaultLessons.filter(lesson => !customOrder.includes(lesson.id)),
    ];
    const sourceUrl = String(ordered[0]?.source_url || "").split("?")[0];
    return {
      sourceId,
      sourceUrl,
      title: layout.titles?.[sourceId] || `课程系列 · ${sourceId}`,
      lessons: ordered,
    };
  });
}

function courseShelf(shelf, index, total) {
  const controls = layoutEditing ? `<div class="shelf-controls"><button data-shelf-move="-1" data-source="${escapeHtml(shelf.sourceId)}" ${index===0?"disabled":""}>↑ 前移</button><button data-shelf-move="1" data-source="${escapeHtml(shelf.sourceId)}" ${index===total-1?"disabled":""}>↓ 后移</button></div>` : "";
  const title = layoutEditing
    ? `<input class="shelf-title-input" data-shelf-title="${escapeHtml(shelf.sourceId)}" value="${escapeHtml(shelf.title)}" maxlength="80" aria-label="${escapeHtml(shelf.sourceId)} 陈列名称">`
    : `<h3>${escapeHtml(shelf.title)}</h3>`;
  return `<section class="course-shelf" data-source="${escapeHtml(shelf.sourceId)}"><header class="shelf-header"><div><p class="eyebrow">COURSE COLLECTION</p>${title}<p>${escapeHtml(shelf.sourceId)} · ${shelf.lessons.length} 节${shelf.sourceUrl ? ` · <a href="${escapeHtml(shelf.sourceUrl)}" target="_blank" rel="noreferrer">原视频 ↗</a>` : ""}</p></div>${controls}</header><div class="lesson-grid">${shelf.lessons.map((lesson,lessonIndex)=>adjustableLessonCard(lesson,shelf,lessonIndex)).join("")}</div></section>`;
}

function adjustableLessonCard(lesson, shelf, index) {
  if (!layoutEditing) return lessonCard(lesson);
  return `<div class="lesson-adjust-item">${lessonCard(lesson)}<div class="lesson-order-controls"><button data-lesson-move="-1" data-source="${escapeHtml(shelf.sourceId)}" data-lesson="${escapeHtml(lesson.id)}" ${index===0?"disabled":""}>← 前移</button><button data-lesson-move="1" data-source="${escapeHtml(shelf.sourceId)}" data-lesson="${escapeHtml(lesson.id)}" ${index===shelf.lessons.length-1?"disabled":""}>后移 →</button></div></div>`;
}

async function saveHomeLayout() {
  homeLayout = await jsonFetch("/api/home-layout", {
    method:"PUT",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(homeLayout),
  });
  await dashboard();
}

function bindLayoutControls(shelves) {
  document.querySelector("#layout-toggle").onclick = async () => {
    layoutEditing = !layoutEditing;
    await dashboard();
    document.querySelector(".course-display-head")?.scrollIntoView({behavior:"smooth",block:"start"});
  };
  document.querySelector("#layout-reset")?.addEventListener("click", async () => {
    homeLayout = {source_order:[], lesson_order:{}, titles:{}};
    await saveHomeLayout();
  });
  document.querySelectorAll("[data-shelf-move]").forEach(button => button.onclick = async () => {
    const order = shelves.map(shelf => shelf.sourceId);
    const index = order.indexOf(button.dataset.source);
    const target = index + Number(button.dataset.shelfMove);
    [order[index], order[target]] = [order[target], order[index]];
    homeLayout.source_order = order;
    await saveHomeLayout();
  });
  document.querySelectorAll("[data-lesson-move]").forEach(button => button.onclick = async () => {
    const shelf = shelves.find(item => item.sourceId === button.dataset.source);
    const order = shelf.lessons.map(lesson => lesson.id);
    const index = order.indexOf(button.dataset.lesson);
    const target = index + Number(button.dataset.lessonMove);
    [order[index], order[target]] = [order[target], order[index]];
    homeLayout.lesson_order[shelf.sourceId] = order;
    await saveHomeLayout();
  });
  document.querySelectorAll("[data-shelf-title]").forEach(input => input.addEventListener("change", async () => {
    const title = input.value.trim();
    if (title) homeLayout.titles[input.dataset.shelfTitle] = title;
    else delete homeLayout.titles[input.dataset.shelfTitle];
    await saveHomeLayout();
  }));
}

function lessonCard(lesson) {
  const questions = lesson.checkpoints.reduce((sum,item)=>sum+(item.questions?.length||0),0);
  return `<a class="lesson-card" href="${lesson.url}"><div class="lesson-top"><span class="part-badge">P${String(lesson.part).padStart(2,"0")}</span><span class="ready-badge">● 已就绪</span></div><h3>${escapeHtml(lesson.title)}</h3><p>${escapeHtml(lesson.overview || "教案、题目和时间戳已经生成。")}</p><div class="lesson-meta"><span>${formatTime(lesson.duration || 0)}</span><span>${lesson.checkpoints.length} 检查点</span><span>${questions} 题</span><b class="lesson-arrow">→</b></div></a>`;
}

function renderJobs(jobs) {
  if (!jobs.length) return `<div class="empty">提交分集后，进度会显示在这里。</div>`;
  return jobs.map(job => {
    const completeStatus = job.kind === "download" ? "已下载" : `<a href="${job.lesson_url}">打开课程 →</a>`;
    const status = job.status === "complete" ? completeStatus : escapeHtml(job.status.toUpperCase());
    const kind = job.kind === "download" ? `<span class="job-kind">下载 · 720p</span>` : "";
    const profile = job.validation_profile === "phonetics_course" ? "音标课程 · OCR" : (job.validation_profile === "strict_course" ? "严格课程" : "通用视频");
    const source = job.source_id ? `${profile} · ${job.source_id}` : profile;
    return `<div class="job ${job.status}"><strong>P${String(job.part).padStart(2,"0")}${kind}</strong><div class="job-copy"><b class="job-source">${escapeHtml(source)}</b><span>${escapeHtml(job.stage_label || job.stage)}</span><div class="job-progress"><i style="width:${job.progress || 0}%"></i></div>${job.error ? `<span>${escapeHtml(job.error)}</span>`:""}</div><div class="job-status">${status}</div></div>`;
  }).join("");
}

function bindJobForm() {
  const form = document.querySelector("#job-form");
  const profile = document.querySelector("#validation-profile");
  const pptComplete = document.querySelector("#ppt-complete");
  const syncProfile = () => {
    const phonetics = profile.value === "phonetics_course";
    pptComplete.disabled = phonetics;
    if (phonetics) pptComplete.checked = false;
  };
  profile.addEventListener("change", syncProfile);
  syncProfile();
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    const message = document.querySelector("#form-message");
    button.disabled = true; message.textContent = "正在校验并创建任务…";
    try {
      const payload={source:form.source.value,part:form.part.value ? Number(form.part.value) : null,reuse_download:document.querySelector("#reuse").checked,ppt_complete:pptComplete.checked,validation_profile:profile.value};
      const result = await jsonFetch("/api/jobs", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      if (result.confirmation_required) {
        message.textContent = `P${result.part} 已有完成课程，等待确认。`;
        showRebuildConfirmation(result,payload,message);
      } else {
        message.textContent = "任务已经开始，可在下方查看进度。";
        setTimeout(refreshDashboardToJobs,600);
      }
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

function bindBatchForm() {
  const form = document.querySelector("#batch-form");
  const action = document.querySelector("#batch-action");
  const courseOptions = document.querySelector("#batch-course-options");
  const profile = document.querySelector("#batch-validation-profile");
  const pptComplete = document.querySelector("#batch-ppt-complete");
  const button = form.querySelector("button");
  const updateMode = () => {
    const courseMode = action.value === "course";
    courseOptions.hidden = !courseMode;
    button.textContent = courseMode ? "开始多 P 课程制作 →" : "开始批量下载 →";
  };
  action.addEventListener("change", updateMode);
  profile.addEventListener("change", () => {
    const phonetics = profile.value === "phonetics_course";
    pptComplete.disabled = phonetics;
    if (phonetics) pptComplete.checked = false;
  });
  updateMode();
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const message = document.querySelector("#batch-message");
    const courseMode = action.value === "course";
    button.disabled = true;
    message.textContent = courseMode ? "正在创建多 P 课程任务…" : "正在创建批量下载任务…";
    try {
      const startPart = Number(form.startPart.value);
      const endPart = Number(form.endPart.value);
      const payload = {
        source: form.source.value,
        start_part: startPart,
        end_part: endPart,
        execution_mode: form.executionMode.value,
        reuse_download: document.querySelector("#batch-reuse").checked,
      };
      if (courseMode) {
        payload.ppt_complete = pptComplete.checked;
        payload.validation_profile = profile.value;
      }
      const result = await jsonFetch(courseMode ? "/api/course-batches" : "/api/download-batches", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(payload),
      });
      const mode = result.execution_mode === "parallel" ? "并发" : "串行";
      const skipped = result.skipped_parts?.length ? `；已跳过完成课程：${result.skipped_parts.map(part=>`P${part}`).join("、")}` : "";
      message.textContent = courseMode
        ? `P${startPart}–P${endPart} 已按${mode}模式开始制作${skipped}。`
        : `P${startPart}–P${endPart} 已按${mode}模式开始下载，固定 720p。`;
      setTimeout(refreshDashboardToJobs,600);
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

async function refreshDashboardToJobs() {
  await dashboard();
  document.querySelector(".jobs-panel")?.scrollIntoView({behavior:"smooth",block:"start"});
}

function showRebuildConfirmation(result,payload,message) {
  document.querySelector("#rebuild-confirmation")?.remove();
  const root=document.createElement("div");
  root.id="rebuild-confirmation";
  root.innerHTML=`<div class="modal-backdrop"><section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="rebuild-title"><p class="eyebrow">EXISTING LESSON</p><h2 id="rebuild-title">P${escapeHtml(result.part)} 已有完成课程</h2><p><strong>${escapeHtml(result.existing_lesson_title)}</strong></p><p>重新制作会创建一个新的时间戳版本，并重新执行理解、教案和出题流程。只有确认后才会开始。</p><div class="confirm-actions"><button class="back-link" id="rebuild-cancel">取消</button><a class="secondary-button" href="${escapeHtml(result.existing_lesson_url)}">打开已有课程</a><button class="primary-button" id="rebuild-confirm">确认重新制作</button></div></section></div>`;
  document.body.appendChild(root);
  root.querySelector("#rebuild-cancel").onclick=()=>{root.remove();message.textContent="已取消重新制作。"};
  root.querySelector("#rebuild-confirm").onclick=async()=>{
    const confirmButton=root.querySelector("#rebuild-confirm");
    confirmButton.disabled=true;confirmButton.textContent="正在创建任务…";
    try {
      const created=await jsonFetch("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...payload,force_rebuild:true})});
      root.remove();message.textContent=`P${created.part} 重新制作任务已开始。`;
      setTimeout(refreshDashboardToJobs,600);
    } catch(error) {
      message.textContent=error.message;confirmButton.disabled=false;confirmButton.textContent="确认重新制作";
    }
  };
}

let pollTimer;
function pollDashboard() { clearTimeout(pollTimer); pollTimer = setTimeout(async()=>{ if(location.pathname!=="/") return; await dashboard(); },3000); }

async function lessonPage(id) {
  const lesson = await jsonFetch(`/api/lessons/${encodeURIComponent(id)}`);
  const storageKey = `lesson-progress:${id}`;
  let completed = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]"));
  let active = null, checkedQuestions = new Set(), answers = {}, modalTab = "quiz", manualOpen = false;
  let checkpointFullscreenTarget = null;
  app.innerHTML = `<main class="shell">
    ${header(`<div class="lesson-header-actions"><a class="back-link" href="/">← 返回工坊</a><div class="series-lock"><i class="lock-dot"></i><span>P${String(lesson.part).padStart(2,"0")}</span></div></div>`)}
    <section class="lesson-layout">
      <div class="player-card"><div class="video-wrap"><video id="video" controls preload="metadata" src="${lesson.video_url}">${lesson.subtitle_url ? `<track kind="subtitles" src="${lesson.subtitle_url}" srclang="zh-CN" label="中文字幕" default>` : ""}</video><span class="mode-chip">知识点自动暂停</span>${lesson.subtitle_url ? `<span class="subtitle-chip">CC 中文字幕</span>` : ""}</div><div class="volume-boost"><div><p class="eyebrow">VOLUME BOOST</p><strong id="volume-boost-status" aria-live="polite">日常增强 150%</strong></div><div class="volume-boost-options" role="group" aria-label="音量增强"><button type="button" data-volume-boost="1" aria-pressed="false">100%</button><button type="button" data-volume-boost="1.5" aria-pressed="true">150%</button><button type="button" data-volume-boost="2" aria-pressed="false">200%</button></div></div><div class="player-info"><div><p class="eyebrow">CURRENT LESSON</p><h2>${escapeHtml(lesson.title)}</h2></div><div class="playhead" id="playhead">00:00</div></div></div>
      <aside class="lesson-sidebar"><p class="eyebrow">LEARNING ROUTE</p><h2>${lesson.checkpoints.length} 个检查点</h2><div class="progress-summary"><span id="done-count">${completed.size}/${lesson.checkpoints.length}</span><div class="progress-line"><i id="progress-line" style="width:${completed.size/lesson.checkpoints.length*100}%"></i></div><button class="back-link" id="reset">重置</button></div><ol class="checkpoint-list" id="checkpoints"></ol></aside>
    </section>
    <section class="content-panel"><div class="tabs"><button class="tab active" data-tab="plan">教案</button><button class="tab" data-tab="articles">Article 笔记 <span>${lesson.article_count || 0}</span></button><button class="tab" data-tab="overview">课程概览</button></div><div class="markdown" id="content">${renderMarkdown(lesson.teaching_plan)}</div></section>
    <div id="modal-root"></div>
  </main>`;
  const video = document.querySelector("#video"), modalRoot = document.querySelector("#modal-root");
  setupVolumeBoost(video);
  let articleManifest = null, articleData = null;
  video.addEventListener("loadedmetadata", () => {
    if (video.textTracks && video.textTracks.length) video.textTracks[0].mode = "showing";
  });

  function renderCheckpoints() {
    document.querySelector("#checkpoints").innerHTML = lesson.checkpoints.map((cp,i)=>`<li class="${completed.has(cp.id)?"done":""}"><button data-index="${i}"><span class="checkpoint-index">${completed.has(cp.id)?"✓":String(i+1).padStart(2,"0")}</span><span class="checkpoint-copy"><strong>${escapeHtml(cp.title)}</strong><small>${formatTime(cp.time)} · ${cp.questions.length} 题</small></span><span class="jump-arrow">↗</span></button></li>`).join("");
    document.querySelectorAll("#checkpoints button").forEach(button=>button.onclick=()=>openQuiz(lesson.checkpoints[Number(button.dataset.index)], true));
    document.querySelector("#done-count").textContent=`${completed.size}/${lesson.checkpoints.length}`;
    document.querySelector("#progress-line").style.width=`${completed.size/lesson.checkpoints.length*100}%`;
  }
  renderCheckpoints();
  document.querySelector("#reset").textContent="全部重置";
  document.querySelector("#reset").onclick=()=>{ completed=new Set();localStorage.removeItem(storageKey);renderCheckpoints();video.currentTime=0; };
  video.addEventListener("timeupdate",()=>{ document.querySelector("#playhead").textContent=formatTime(video.currentTime); syncArticleBlocks(); if(active)return; const due=lesson.checkpoints.find(cp=>cp.time<=video.currentTime&&!completed.has(cp.id)); if(due)openQuiz(due, false); });

  document.querySelectorAll(".tab").forEach(tab=>tab.onclick=async()=>{
    document.querySelectorAll(".tab").forEach(item=>item.classList.toggle("active",item===tab));
    const content=document.querySelector("#content");
    if(tab.dataset.tab==="plan") content.innerHTML=renderMarkdown(lesson.teaching_plan);
    if(tab.dataset.tab==="overview") content.innerHTML=`<h2>课程概览</h2><p class="overview-copy">${escapeHtml(lesson.overview)}</p>`;
    if(tab.dataset.tab==="articles") await openArticles(content);
  });

  async function openArticles(content) {
    content.innerHTML="<p>正在读取 Article 笔记及时间轴…</p>";
    await ensureArticles();
    if (!articleData) { content.innerHTML="<div class=\"empty\">这节课没有 article Markdown。</div>"; return; }
    renderArticleArchive(content);
  }

  function renderArticleArchive(content) {
    const kindLabels={final:"最终笔记",source:"源笔记",version:"历史版本",draft:"生成草稿",rejected:"被拒版本"};
    content.innerHTML=`<div class="article-toolbar"><div><p class="eyebrow">MARKDOWN ARCHIVE</p><h2>按 PPT 时间戳分块</h2></div><label>笔记文件<select id="article-select">${articleManifest.files.map(file=>`<option value="${escapeHtml(file.name)}" ${file.name===articleData.filename?"selected":""}>${escapeHtml(file.name)} · ${kindLabels[file.kind]||file.kind}</option>`).join("")}</select></label></div><div class="article-file-note"><span class="article-kind ${escapeHtml(articleData.kind)}">${kindLabels[articleData.kind]||articleData.kind}</span><strong>${escapeHtml(articleData.filename)}</strong><span>${articleData.blocks.length} 个时间块；点击时间可跳转视频</span></div><div class="article-timeline">${articleData.blocks.map((block,index)=>`<article class="article-time-block" data-time="${block.time}" id="${escapeHtml(block.id)}"><div class="article-time-rail"><button data-jump="${block.time}">${formatTime(block.time)}</button><span>PPT ${String(block.frame_number || index+1).padStart(2,"0")}</span></div><div class="article-block-body"><p class="block-title">${escapeHtml(block.title)}</p>${renderMarkdown(block.markdown)}</div></article>`).join("")}</div>`;
    content.querySelector("#article-select").onchange=async event=>{await loadArticleFile(event.target.value);renderArticleArchive(content)};
    content.querySelectorAll("[data-jump]").forEach(button=>button.onclick=()=>{video.currentTime=Number(button.dataset.jump);video.play();window.scrollTo({top:0,behavior:"smooth"})});
    syncArticleBlocks();
  }

  async function loadArticleFile(filename) {
    articleData=await jsonFetch(`/api/lessons/${encodeURIComponent(id)}/articles/${encodeURIComponent(filename)}`);
    return articleData;
  }

  async function ensureArticles() {
    if (!articleManifest) articleManifest=await jsonFetch(`/api/lessons/${encodeURIComponent(id)}/articles`);
    if (!articleData && articleManifest.files.length) await loadArticleFile(articleManifest.default || articleManifest.files[0].name);
  }

  function syncArticleBlocks() {
    if (!articleData) return;
    const blocks=[...document.querySelectorAll(".article-time-block")];
    let current=null;
    for (const block of blocks) if (Number(block.dataset.time)<=video.currentTime+0.25) current=block;
    blocks.forEach(block=>block.classList.toggle("current",block===current));
  }

  function notesForCheckpoint(cp) {
    if (!articleData) return [];
    if (Array.isArray(cp.note_frame_numbers)) {
      const frameNumbers=new Set(cp.note_frame_numbers.map(Number));
      return articleData.blocks.filter(block=>frameNumbers.has(Number(block.frame_number)));
    }
    // Compatibility fallback for lessons generated before explicit note mapping.
    const index=lesson.checkpoints.indexOf(cp);
    const previous=index > 0 ? Number(lesson.checkpoints[index-1].time) : -1;
    const current=Number(cp.time)+0.5;
    const aligned=articleData.blocks.filter(block=>Number(block.time)>previous&&Number(block.time)<=current);
    if (aligned.length) return aligned;
    const preceding=articleData.blocks.filter(block=>Number(block.time)<=current);
    return preceding.length ? [preceding[preceding.length-1]] : [];
  }

  function fullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  async function exitFullscreenForCheckpoint(openedManually) {
    checkpointFullscreenTarget = null;
    if (openedManually) return;
    const target = fullscreenElement();
    if (!target) return;
    const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
    if (!exitFullscreen) return;
    try {
      checkpointFullscreenTarget = target;
      await exitFullscreen.call(document);
    } catch (error) {
      checkpointFullscreenTarget = null;
      console.warn("无法在检查点自动退出全屏。", error);
    }
  }

  function restoreFullscreenAfterCheckpoint() {
    const target = checkpointFullscreenTarget;
    checkpointFullscreenTarget = null;
    if (!target || fullscreenElement()) return;
    const connectedTarget = target.isConnected ? target : video;
    const requestFullscreen = connectedTarget.requestFullscreen || connectedTarget.webkitRequestFullscreen;
    if (!requestFullscreen) return;
    try {
      const result = requestFullscreen.call(connectedTarget);
      if (result && typeof result.catch === "function") {
        result.catch(error => console.warn("无法在继续播放后恢复全屏。", error));
      }
    } catch (error) {
      console.warn("无法在继续播放后恢复全屏。", error);
    }
  }

  async function openQuiz(cp, openedManually=false) {
    video.pause();active=cp;manualOpen=openedManually;checkedQuestions=new Set();answers={};modalTab="quiz";
    modalRoot.innerHTML=`<div class="modal-backdrop"><section class="quiz-modal"><p>正在对齐题目与时间点笔记…</p></section></div>`;
    await exitFullscreenForCheckpoint(openedManually);
    try { await ensureArticles(); } catch (error) { articleData=null; }
    if (active===cp) renderQuiz();
  }
  function renderQuiz() {
    const results=active.questions.map(q=>q.answers.some(answer=>normalize(answer)===normalize(answers[q.id])));
    const allAnswered=active.questions.every(q=>String(answers[q.id]||"").trim());
    const allChecked=active.questions.every(q=>checkedQuestions.has(q.id));
    const notes=notesForCheckpoint(active);
    const kindLabels={final:"最终笔记",source:"源笔记",version:"历史版本",draft:"生成草稿",rejected:"被拒版本"};
    const notesHtml=!articleData?`<div class="empty">当前课程没有可读取的 Article 笔记。</div>`:`<div class="modal-note-toolbar"><span>${previousCheckpointLabel(active)} → ${formatTime(active.time)}，共 ${notes.length} 个笔记块</span><label>笔记版本<select id="modal-article-select">${articleManifest.files.map(file=>`<option value="${escapeHtml(file.name)}" ${file.name===articleData.filename?"selected":""}>${escapeHtml(file.name)} · ${kindLabels[file.kind]||file.kind}</option>`).join("")}</select></label></div><div class="modal-note-list">${notes.map((block,index)=>`<article class="modal-note-block"><div class="modal-note-meta"><button data-note-jump="${block.time}">${formatTime(block.time)}</button><span>PPT ${String(block.frame_number||index+1).padStart(2,"0")}</span><strong>${escapeHtml(block.title)}</strong></div><div class="markdown">${renderMarkdown(block.markdown)}</div></article>`).join("")||`<div class="empty">这个检查点前没有新的笔记块。</div>`}</div>`;
    modalRoot.innerHTML=`<div class="modal-backdrop"><section class="quiz-modal" role="dialog" aria-modal="true"><header class="quiz-header"><div><p class="eyebrow">${formatTime(active.time)} · KNOWLEDGE CHECK</p><h2>${escapeHtml(active.title)}</h2><p>${escapeHtml(active.summary)}</p></div><div class="quiz-header-actions"><span class="part-badge">${active.questions.length} 题</span>${manualOpen?`<button class="modal-close" id="modal-close" aria-label="关闭">×</button>`:""}</div></header><div class="modal-view-tabs"><button class="${modalTab==="quiz"?"active":""}" data-modal-tab="quiz">题目</button><button class="${modalTab==="notes"?"active":""}" data-modal-tab="notes">笔记 <span>${notes.length}</span></button></div>${modalTab==="quiz"?`<div class="question-list">${active.questions.map((q,i)=>questionHtml(q,i,results[i])).join("")}</div><footer class="quiz-footer"><div class="score">${allChecked?`<strong>${results.filter(Boolean).length}/${results.length}</strong>查看解释后继续播放。`:"完成全部题目后检查答案。"}</div><button class="primary-button" id="quiz-action" ${!allChecked&&!allAnswered?"disabled":""}>${allChecked?"继续播放 →":"检查答案"}</button></footer>`:notesHtml}</section></div>`;
    modalRoot.querySelectorAll("[data-modal-tab]").forEach(button=>button.onclick=()=>{modalTab=button.dataset.modalTab;renderQuiz()});
    if (manualOpen) modalRoot.querySelector("#modal-close").onclick=()=>{checkpointFullscreenTarget=null;active=null;modalRoot.innerHTML=""};
    const articleSelect=modalRoot.querySelector("#modal-article-select");
    if(articleSelect) articleSelect.onchange=async event=>{await loadArticleFile(event.target.value);renderQuiz()};
    modalRoot.querySelectorAll("[data-note-jump]").forEach(button=>button.onclick=()=>{checkpointFullscreenTarget=null;video.currentTime=Number(button.dataset.noteJump);active=null;modalRoot.innerHTML="";video.play()});
    modalRoot.querySelectorAll("input").forEach(input=>input.onchange=()=>{answers[input.dataset.qid]=input.value;renderQuiz()});
    modalRoot.querySelectorAll("[data-reset-q]").forEach(button=>button.onclick=()=>{delete answers[button.dataset.resetQ];checkedQuestions.delete(button.dataset.resetQ);renderQuiz()});
    const action=modalRoot.querySelector("#quiz-action");
    if(action) action.onclick=()=>{ if(!allChecked){checkedQuestions=new Set(active.questions.map(q=>q.id));renderQuiz();return;} restoreFullscreenAfterCheckpoint();completed.add(active.id);localStorage.setItem(storageKey,JSON.stringify([...completed]));active=null;modalRoot.innerHTML="";renderCheckpoints();setTimeout(()=>video.play(),80); };
  }
  function previousCheckpointLabel(cp) { const index=lesson.checkpoints.indexOf(cp); return index>0?formatTime(lesson.checkpoints[index-1].time):"课程开始"; }
  function questionHtml(q,index,isCorrect) {
    const isChecked=checkedQuestions.has(q.id);
    const klass=isChecked?(isCorrect?"correct":"wrong"):"";
    const controls=q.type==="text"?`<input class="text-answer" data-qid="${escapeHtml(q.id)}" value="${escapeHtml(answers[q.id]||"")}" placeholder="输入简短答案" ${isChecked?"disabled":""}>`:`<div class="options">${q.options.map(option=>`<label class="option ${answers[q.id]===option?"selected":""}"><input type="radio" data-qid="${escapeHtml(q.id)}" name="${escapeHtml(q.id)}" value="${escapeHtml(option)}" ${answers[q.id]===option?"checked":""} ${isChecked?"disabled":""}><span>${escapeHtml(option)}</span></label>`).join("")}</div>`;
    return `<article class="question ${klass}"><div class="question-heading"><h3><span>${String(index+1).padStart(2,"0")}</span>${escapeHtml(q.prompt)}</h3><button class="question-reset" data-reset-q="${escapeHtml(q.id)}" ${!answers[q.id]&&!isChecked?"disabled":""}>重置本题</button></div>${controls}${isChecked?`<p class="feedback"><strong>${isCorrect?"回答正确":"参考答案"}</strong><span>${escapeHtml(q.explanation || q.answers[0])}</span></p>`:""}</article>`;
  }
}

async function route() {
  try {
    const match=location.pathname.match(/^\/lessons\/([^/]+)\/?$/);
    if(match) await lessonPage(decodeURIComponent(match[1])); else await dashboard();
  } catch(error) { app.innerHTML=`<div class="loading-screen"><h2>页面没有准备好</h2><p>${escapeHtml(error.message)}</p><a class="back-link" href="/">返回首页</a></div>`; }
}
route();
