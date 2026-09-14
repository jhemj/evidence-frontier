"use strict";
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let current = null,
  snapshot = null,
  tabName = "investigate",
  token = "",
  busy = false,
  searchOffset = 0;
let refreshInFlight = false;
const evidenceTypes = {linux_detection:"침해 관련 단서",linux_environment:"분석 환경",linux_coverage:"수집 범위",linux_command:"명령 기록",linux_authentication:"인증 기록",linux_session:"세션·권한",linux_persistence:"자동 실행 설정",linux_cron_call:"예약 작업 호출",linux_binary:"실행파일 정적 정보",linux_account:"계정",linux_ssh_trust:"SSH 신뢰 설정",linux_inspection_result:"이전 점검 결과",linux_network:"통신 기록",linux_tool_result:"추가 검사 결과",linux_path_match:"파일 경로",linux_literal_match:"원문 검색 일치",linux_persistence_link:"설정·호출 대조",linux_audit_group:"동일 audit 사건",linux_audit:"audit 기록",linux_login_record:"로그인 기록",linux_configuration:"설정",linux_system_event:"시스템 기록"};
const labels = {
  ready: "준비됨",
  running: "조사 중",
  paused: "일시정지",
  pause_requested: "일시정지 · 진행 작업 마무리 중",
  complete: "설정 범위 완료",
  quiescent: "미확인 영역 있음",
  resource_limit: "실행 한도 도달",
  queued: "대기",
  covered: "확인됨",
  covered_zero: "지정 범위 내 기록 없음",
  unsupported: "미지원",
  blocked: "확인 필요",
  failed: "실패",
  partial: "일부 확인 · 미확인 범위 있음",
  candidate: "검토 후보",
  approved: "승인됨",
  rejected: "제외됨",
};
const size = (bytes) =>
  bytes > 1e9
    ? (bytes / 1e9).toFixed(1) + " GB"
    : bytes > 1e6
      ? (bytes / 1e6).toFixed(1) + " MB"
      : (bytes / 1000).toFixed(1) + " KB";
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").hidden = true), 7000);
}
async function api(path, method = "GET", body, raw = false) {
  const response = await fetch("/api" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "frontier",
      ...(token ? { "X-Workbench-Token": token } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (response.status === 401) {
    if (!$("auth-dialog").open) $("auth-dialog").showModal();
    throw Error("접속 암호를 입력하세요.");
  }
  if (!response.ok) {
    let error;
    try {
      error = await response.json();
    } catch {
      error = { detail: "서버 응답을 확인할 수 없습니다." };
    }
    throw Error(
      typeof error.detail === "string"
        ? error.detail
        : "입력 내용을 확인하세요.",
    );
  }
  return raw ? response : response.json();
}
async function perform(fn) {
  try {
    await fn();
  } catch (error) {
    toast(error.message);
  }
}
async function listCases() {
  const cases = await api("/cases");
  $("cases").innerHTML =
    cases
      .map(
        (c) =>
          `<button class="case-item ${c.id === current ? "active" : ""}" data-case="${esc(c.id)}">${esc(c.name)}<small>${esc(labels[c.status] || c.status)}</small></button>`,
      )
      .join("") || '<p class="small-hint">아직 시작한 조사가 없습니다.</p>';
  return cases;
}
async function selectCase(id) {
  current = id;
  searchOffset = 0;
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  await listCases();
  await refresh();
  await switchTab("investigate");
}
async function refresh() {
  if (!current || refreshInFlight) return;
  refreshInFlight = true;
  try {
  const previousCase = snapshot?.case;
  const requestedCase = current;
  const revision = snapshot?.case.id === requestedCase ? snapshot.view_revision : "";
  const nextSnapshot = await api("/cases/" + requestedCase + (revision ? "?since="+encodeURIComponent(revision) : ""));
  if (current !== requestedCase) return;
  snapshot = nextSnapshot.unchanged ? {...snapshot,case:nextSnapshot.case,task:nextSnapshot.task,evidence:nextSnapshot.evidence} : nextSnapshot;
  renderSnapshot();
  if (previousCase?.id === current && previousCase.status !== snapshot.case.status)
    await listCases();
  } finally { refreshInFlight = false; }
}
function renderSnapshot() {
  const s = snapshot,
    c = s.case;
  const timelineSources = new Set(s.evidence.filter(e=>e.connected!==false).map(e=>e.id));
  const timelineTasks = s.task.filter(t=>timelineSources.has(t.evidence_id));
  const timelineTask = timelineTasks.find(t => t.status === "running") || timelineTasks.find(t => t.status === "queued");
  const huntProgress = timelineTask?.progress;
  if (s.visual_timeline) s.visual_timeline.progress = {
    index: {linux_scan:1, linux_investigate:2, ai_judgment:3, investigation_report:4}[timelineTask?.action] ?? (timelineTask ? 0 : 4),
    text: huntProgress?.stage === "linux_hunt"
      ? `기본 점검 ${(huntProgress.files_done || 0).toLocaleString()} / ${(huntProgress.candidates || 0).toLocaleString()}개 파일 · 발견 단서 ${huntProgress.detections || 0}개 · 정황 검토 준비 중`
      : huntProgress?.stage === "linux_discovery" ? `조사 대상 확인 · ${(huntProgress.entries || 0).toLocaleString()}개 경로`
      : ["linux_investigate","ai_judgment"].includes(timelineTask?.action) ? c.investigation_stage
      : timelineTask?.label || c.investigation_result || "조사 결과를 기다리고 있습니다."
  };
  renderTimeline(s.visual_timeline, c);
  const connected = s.evidence.filter((e) => e.connected !== false);
  const activeIds = new Set(connected.map((e) => e.id));
  const coverage = s.coverage.filter((cell) => activeIds.has(cell.evidence_id));
  const disconnected = s.evidence.filter((e) => e.connected === false);
  const detachedOpen = $("evidence-files").querySelector("details")?.open;
  $("case-title").textContent = c.name;
  $("case-name").textContent = c.name;
  $("case-id").textContent = c.id;
  $("case-question").textContent =
    c.question || "증거를 연결하고 조사를 시작하세요.";
  $("case-status").textContent = labels[c.status] || c.status;
  $("case-status").className = "status " + c.status;
  $("run").innerHTML =
    ["running","pause_requested"].includes(c.status)
      ? "일시정지 <span>Ⅱ</span>"
      : c.status === "paused"
        ? "조사 계속 <span>▶</span>"
        : "침해조사 시작 <span>▶</span>";
  $("run").disabled = !connected.length;
  $("evidence-count").textContent = s.observation_count;
  $("coverage-label").textContent = `${s.summary.covered} / ${s.summary.total}`;
  $("coverage-progress").value = s.summary.covered;
  $("coverage-progress").max = s.summary.total || 1;
  const investigation = s.hypothesis.filter((h) => h.contract === "linux-v1" && activeIds.has(h.evidence_id));
  $("investigation-summary").hidden = !investigation.length;
  if (investigation.length) {
    const running = s.task.find((t) => activeIds.has(t.evidence_id) && t.status === "running");
    const stage = running?.action === "linux_investigate" ? c.investigation_stage : running?.label;
    $("investigation-summary").innerHTML = `<strong>${esc(stage || (c.status === "paused" ? "일시정지 · 저장한 단계에서 계속 가능" : c.investigation_result) || "침해조사 준비됨")}</strong><p>필수 조사 영역 ${investigation.length}개 · AI가 근거를 검토하고 필요한 도구를 실행합니다.</p>${s.report.length ? `<button class="secondary" data-download="${esc(s.report[s.report.length-1].id)}">최근 결과 패키지 내려받기 ↓</button>` : ""}`;
  }
  $("hypotheses").innerHTML = investigation.map((h) => `<article class="claim"><h3>${h.number}. ${esc(h.text)}</h3><span class="status">${esc(h.judgment)}${h.ai_candidate ? " · AI 검토 후보" : ""}</span><p>${esc(h.reasoning || "원문 조사와 검증을 기다리고 있습니다.")}</p><p>경쟁 설명: ${esc((h.competing_explanations || []).join(" / "))}</p><p>남은 확인: ${esc((h.remaining_checks || h.unavailable_materials || []).join(" / "))}</p><p>${(h.observation_ids || []).slice(0,6).map((id) => `<button data-ref="${esc(id)}">원문 근거 ${esc(id.slice(-6))}</button>`).join(" ")}</p></article>`).join("");
  const judgments = s.judgments || [];
  const findings = judgments.flatMap((j) => j.findings);
  $("judgment-results").hidden = !judgments.length;
  $("messages").hidden = !!judgments.length && !s.message.some((m) => !m.automatic && m.created_at > judgments.at(-1).created_at);
  $("suggestions").hidden = !!judgments.length;
  const judgmentSignature = current + judgments.map((j) => j.id).join(",");
  if (judgments.length && $("judgment-results").dataset.signature !== judgmentSignature) {
    const levels = ["확인", "유력", "미확인"];
    const badge = (level) => `<span class="judgment-badge level-${levels.indexOf(level)}">${esc(level)}</span>`;
    $("judgment-results").innerHTML = `<h2>AI 조사 결과</h2>${judgments.map((j) => `<p class="judgment-summary">${esc(j.summary)}</p>`).join("")}<div class="judgment-counts">${levels.map((level) => `<span>${badge(level)} <strong>${findings.filter((f) => f.judgment === level).length}</strong></span>`).join("")}</div><p class="small-hint">개별 주장에 대한 AI 평가 · 정상성 별도 미검증 · 침해 건수 아님. 확인: 원문이 직접 뒷받침 · 유력: 정황상 가장 타당 · 미확인: 자료 부족 또는 상충</p>${levels.map((level) => {
      const selected = findings.filter((f) => f.judgment === level);
      return selected.length ? `<section class="judgment-group" aria-label="${level} 판단">${selected.map((f) => `<article class="judgment-card">${badge(level)}<h3>${esc(f.title)}</h3><p>${esc(f.reason)}</p><details><summary>근거와 판단 이유 자세히</summary><p>${f.observation_ids.map((id) => `<button class="secondary" data-ref="${esc(id)}">원문 근거 ↗</button>`).join(" ") || "직접 연결할 근거 부족"}</p><p>다른 설명: ${esc(f.alternatives.join(" / ") || "별도 설명 없음")}</p><p>남은 확인: ${esc(f.remaining_checks.join(" / ") || "명시한 사실 범위에서 추가 검사 제안 없음")}</p></details></article>`).join("")}</section>` : "";
    }).join("")}`;
    $("judgment-results").dataset.signature = judgmentSignature;
  }
  const fileCard = (e) => {
    const detached = e.connected === false;
    const runningTask = s.task.find(
      (t) => t.evidence_id === e.id && t.status === "running",
    );
    const running = Boolean(runningTask);
    const progress = runningTask?.progress;
    const elapsed = runningTask?.started_at
      ? Math.max(
          0,
          Math.floor((Date.now() - new Date(runningTask.started_at)) / 60000),
        )
      : 0;
    const progressText =
      progress?.stage === "segment_hash"
        ? `세그먼트 해시 ${Math.floor((100 * progress.bytes_done) / (progress.total_bytes || 1))}% · ${progress.segment_index || 1}/${progress.segment_count}`
        : progress?.stage === "ewf_verify"
          ? `전체 디스크 무결성 검증 중 · ${elapsed}분 경과`
          : progress?.stage === "linux_discovery"
            ? `조사 경로 확인 · ${(progress.entries || 0).toLocaleString()}개 항목`
          : progress?.stage === "linux_contents"
            ? `원문 분석 · ${progress.files_done || 0}개 파일 · ${(progress.records || 0).toLocaleString()}개 행위 기록`
          : progress?.stage === "linux_hunt"
            ? `독립 기본점검 · ${progress.files_done || 0}/${progress.candidates || 0}개 파일 · ${progress.detections || 0}개 단서`
          : running
            ? `${runningTask.label} · ${elapsed}분 경과`
            : "";
    const locked = ["running","pause_requested"].includes(c.status) || running || busy;
    const reason = running
      ? "현재 읽기 작업이 끝난 뒤 해제할 수 있습니다."
      : ["running","pause_requested"].includes(c.status)
        ? "조사를 일시정지한 뒤 변경하세요."
        : "원본 파일과 조사 기록은 보존됩니다.";
    return `<div class="file-card"><span class="file-icon">${esc(e.name.split(".").pop().toUpperCase().slice(0, 4))}</span><div class="file-info"><strong>${esc(e.name)}</strong><small>${e.segment_count > 1 ? `${e.segment_count}개 세그먼트 · ` : ""}${size(e.total_size || e.size)} · ${detached ? "연결 해제됨" : e.sha256 ? "무결성 확인됨" : "검증 대기"}</small>${progressText ? `<small class="file-progress" role="status">${esc(progressText)}</small>` : ""}<button class="evidence-action" data-${detached ? "reconnect" : "disconnect"}="${esc(e.id)}" ${locked ? "disabled" : ""} title="${reason}" aria-label="${esc(e.name)} ${detached ? "다시 연결" : "연결 해제"}">${detached ? "다시 연결" : "연결 해제"}</button>${!detached && running ? "<small>검증 중에는 해제할 수 없습니다.</small>" : ""}</div></div>`;
  };
  $("evidence-files").innerHTML =
    (connected.map(fileCard).join("") ||
      '<p class="small-hint">＋ 버튼으로 첫 증거를 연결하세요.</p>') +
    (disconnected.length
      ? `<details class="disconnected-files" ${detachedOpen ? "open" : ""}><summary>연결 해제한 증거 ${disconnected.length}개</summary><p class="small-hint">원본과 기록은 보존됩니다. 새 분석과 보고서에서는 제외됩니다.</p>${disconnected.map(fileCard).join("")}</details>`
      : "");
  const phases = [...new Set(coverage.map((x) => x.phase))].sort((a,b) => a-b);
  $("steps").innerHTML =
    phases
      .map((phase) => {
        const cells = coverage.filter((x) => x.phase === phase),
          done = cells.every((x) =>
            ["covered", "covered_zero"].includes(x.status),
          ),
          running = cells.some((x) => x.status === "running"),
          gap = cells.some((x) =>
            ["failed", "blocked", "unsupported", "partial"].includes(x.status),
          );
        return `<div class="step ${done ? "done" : running ? "running" : gap ? "failed" : ""}"><span class="step-icon">${done ? "✓" : gap ? "!" : phase + 1}</span>${esc(cells[0].label)}</div>`;
      })
      .join("") ||
    '<p class="small-hint">증거에 맞춰 조사 범위를 준비합니다.</p>';
  $("tasks").innerHTML = s.task
    .filter((t) => activeIds.has(t.evidence_id))
    .map(
      (t) =>
        `<div class="task"><strong>${esc(t.label)}</strong> · ${esc(labels[t.status] || t.status)}<div class="meta">${esc(s.evidence.find((e) => e.id === t.evidence_id)?.name)}</div>${t.error ? `<p>${esc(t.error)}</p>` : ""}${["failed", "unsupported", "blocked"].includes(t.status) && t.attempts < 3 ? `<button data-retry="${esc(t.id)}">조건 해결 후 재시도</button>` : ""}</div>`,
    )
    .join("");
  const automaticMessages = s.message.filter((m) => m.automatic);
  const latestAutomatic = automaticMessages.at(-1);
  const latestIndex = latestAutomatic ? s.message.indexOf(latestAutomatic) : 0;
  const previousMessages = s.message.slice(0, latestIndex);
  const messageCard = (m) => {
    const text = esc(m.text).replace(/OBSERVATION-[a-f0-9]{12}/g,
      (id) => `<button class="inline-reference" data-ref="${id}" title="${id}">근거 ↗</button>`);
    return `<article class="message ${esc(m.role)} ${esc(m.mode || "")}"><div class="message-role">${m.role === "user" ? "나" : m.mode === "ai_candidate" ? "Frontier · AI 해석 후보" : "Frontier · 조사 안내"}</div>${text}${m.partial ? '<p class="small-hint">선택한 근거에 대한 AI 해석입니다. 미확인 범위는 조사 기록에 남습니다.</p>' : ""}</article>`;
  };
  const history = previousMessages.length
    ? `<details class="investigation-history"><summary>이전 대화와 조사 과정 ${previousMessages.length}개</summary>${previousMessages.map(messageCard).join("")}</details>` : "";
  const messages = history + s.message.slice(latestIndex).map(messageCard).join("");
  const messageSignature = current + connected.length + messages;
  if ($("messages").dataset.signature !== messageSignature) {
    $("messages").innerHTML =
      messages ||
      `<article class="message empty"><div class="message-role">Frontier</div>${connected.length ? "조사를 실행하면 증거의 무결성과 기본 기록부터 확인합니다. 확인할 내용을 질문으로 남겨주세요." : "증거를 연결하면 조사를 시작할 수 있습니다. 오른쪽 ＋ 버튼에서 파일을 선택하세요."}</article>`;
    $("messages").dataset.signature = messageSignature;
    $("messages").scrollTop = $("messages").scrollHeight;
  }
  $("claims").innerHTML = s.claim.length ? `<details class="investigation-history"><summary>이전 해석 기록 ${s.claim.length}개</summary>${s.claim.map((cl) => `<article class="claim"><h3>${esc(cl.text)}</h3><p>${esc(cl.uncertainty)}</p><p>대안: ${esc((cl.alternatives || []).join(" / "))}</p><p>${cl.observation_ids.map((id) => `<button data-ref="${esc(id)}">원문 근거 ↗</button>`).join(" ")}</p></article>`).join("")}</details>` : '<p class="small-hint">조사가 진행되면 근거를 바탕으로 AI가 자동 판단합니다.</p>';
  $("report-history").innerHTML = s.report
    .map(
      (r, i) =>
        `<button data-download="${esc(r.id)}">보고서 ${i + 1} · 미확인 범위 ${r.gap_count} ↓</button>`,
    )
    .join("");
}
async function switchTab(name) {
  tabName = name;
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
    b.setAttribute("aria-current", b.dataset.tab === name ? "page" : "false");
  });
  for (const n of ["investigate", "evidence", "report"])
    $("panel-" + n).hidden = n !== name;
  if (name === "evidence") await loadObservations();
  if (name === "report") await loadReport();
}
async function loadObservations() {
  const result = await api(
    `/cases/${current}/observations?q=${encodeURIComponent($("search").value)}&offset=${searchOffset}`,
  );
  $("observation-list").innerHTML =
    result.items
      .map(
        (o) =>
          `<details class="observation" id="${esc(o.id)}"><summary><time>${esc(o.timestamp ? new Date(o.timestamp).toLocaleString("ko-KR") : "시간 정보 없음")}</time><strong>${esc(evidenceTypes[o.type] || o.type)}</strong><span class="location">${esc(o.source_location)}</span></summary><pre>${esc(JSON.stringify(o.fields, null, 2))}</pre>${o.fields.artifact_path ? `<button class="secondary" data-source="${esc(o.id)}">해시 검증된 추출 원문 ↓</button>` : ""}<p class="meta">${esc(o.id)}<br>증거 ${esc(o.evidence_id)}<br>도구 기록 ${esc(o.receipt_id)}</p></details>`,
      )
      .join("") ||
    '<div class="empty-state">표시할 관측 기록이 없습니다. 조사를 실행하거나 검색어를 바꿔보세요.</div>';
  $("more-observations").hidden = searchOffset + 100 >= result.total;
}
async function loadReport() {
  const response = await api(
    `/cases/${current}/report-preview`,
    "GET",
    undefined,
    true,
  );
  $("report-preview").srcdoc = await response.text();
}
function openCaseDialog() {
  $("case-form").reset();
  $("case-dialog").showModal();
  $("name").focus();
}
$("new-case").onclick = openCaseDialog;
$("welcome-create").onclick = openCaseDialog;
$("case-form").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const c = await api("/cases", "POST", {
      name: $("name").value.trim(),
      question: $("question").value.trim(),
      profile: $("profile").value,
    });
    $("case-dialog").close();
    await selectCase(c.id);
  });
};
$("example-case").onclick = () =>
  perform(async () => {
    const files = await api("/evidence-files");
    const sample = files.find((f) => f.path === "activity.ndjson");
    if (!sample)
      throw Error(
        "예제는 examples 폴더를 증거 폴더로 연결할 때 사용할 수 있습니다.",
      );
    const c = await api("/cases", "POST", {
      name: "예제 · 실행 흔적 확인",
      question:
        "PowerShell 실행 흔적과 정상 관리 작업 가능성을 확인합니다. 합성 예제 자료입니다.",
      profile: "standard",
    });
    await api(`/cases/${c.id}/evidence`, "POST", { path: sample.path });
    await selectCase(c.id);
    await api(`/cases/${c.id}/start`, "POST");
    await refresh();
  });
$("run").onclick = () =>
  perform(async () => {
    await api(
      `/cases/${current}/${["running","pause_requested"].includes(snapshot.case.status) ? "pause" : "start"}`,
      "POST",
    );
    await refresh();
    await listCases();
  });
$("add-evidence").onclick = () =>
  perform(async () => {
    const files = await api("/evidence-files");
    const registered = new Set(snapshot.evidence.map((e) => e.path));
    $("available-files").innerHTML =
      files
        .filter((f) => !registered.has(f.path))
        .map(
          (f) =>
            `<button data-add-file="${esc(f.path)}" ${f.error ? "disabled" : ""}><span>${esc(f.path)}${f.error ? `<small>${esc(f.error)}</small>` : ""}</span><span class="subtle">${f.segment_count > 1 ? `${f.segment_count}개 세그먼트 · ` : ""}${size(f.total_size || f.size)}</span></button>`,
        )
        .join("") ||
      '<div class="empty-state">추가할 파일이 없습니다. 설정한 증거 폴더에 자료를 넣어주세요.</div>';
    $("evidence-dialog").showModal();
  });
$("chat-form").onsubmit = (e) => {
  e.preventDefault();
  if (busy) return;
  perform(async () => {
    busy = true;
    $("ai-progress").hidden = false;
    $("send").disabled = true;
    const text = $("message").value;
    try {
      await api(`/cases/${current}/messages`, "POST", { message: text });
      $("message").value = "";
      await refresh();
    } finally {
      busy = false;
      $("ai-progress").hidden = true;
      $("send").disabled = false;
    }
  });
};
$("message").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
};
function providerForm() {
  return {
    protocol: $("protocol").value,
    base_url: $("base-url").value,
    model: $("model").value,
    falsifier_model: $("falsifier-model").value,
    trusted_lan: $("trusted-lan").checked,
  };
}
async function updateConnection() {
  const c = await api("/settings");
  $("model-status").textContent = c.model ? "모델 설정됨" : "설정 필요";
}
$("settings").onclick = () =>
  perform(async () => {
    const c = await api("/settings");
    $("protocol").value = c.protocol;
    $("base-url").value = c.base_url;
    $("model").value = c.model;
    $("falsifier-model").value = c.falsifier_model;
    $("trusted-lan").checked = c.trusted_lan;
    $("probe-result").textContent = "";
    $("settings-dialog").showModal();
  });
$("probe").onclick = () =>
  perform(async () => {
    $("probe").disabled = true;
    try {
      const r = await api("/settings/probe", "POST", providerForm());
      $("model-list").innerHTML = r.models
        .map((m) => `<option value="${esc(m)}"></option>`)
        .join("");
      $("probe-result").textContent = r.models.length
        ? `${r.models.length}개 모델 확인 · ${r.models.join(", ")}`
        : "연결되었지만 설치된 모델이 없습니다.";
      if (!$("model").value && r.models.length) $("model").value = r.models[0];
    } finally {
      $("probe").disabled = false;
    }
  });
$("settings-form").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    await api("/settings", "PUT", providerForm());
    $("settings-dialog").close();
    await updateConnection();
    toast("모델 연결 설정을 저장했습니다.");
  });
};
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  searchOffset = 0;
  perform(loadObservations);
};
$("more-observations").onclick = () => {
  searchOffset += 100;
  perform(loadObservations);
};
$("save-report").onclick = () =>
  perform(async () => {
    $("save-report").disabled = true;
    try {
      const r = await api(`/cases/${current}/reports`, "POST");
      await refresh();
      await download(r.id);
      toast("보고서와 근거 기록, 체크섬을 저장했습니다.");
    } finally {
      $("save-report").disabled = false;
    }
  });
async function download(id) {
  if (!token) {
    const link = document.createElement("a");
    link.href = `/api/reports/${encodeURIComponent(id)}/download`;
    link.download = "frontier-report.zip";
    document.body.append(link);
    link.click();
    link.remove();
    return;
  }
  const response = await api(`/reports/${id}/download`, "GET", undefined, true);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = "frontier-report.zip";
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("auth-form").onsubmit = (e) => {
  e.preventDefault();
  token = $("access-token").value;
  $("auth-dialog").close();
  perform(init);
};
document.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled) return;
  perform(async () => {
    if (b.dataset.close) $(b.dataset.close).close();
    if (b.dataset.case) await selectCase(b.dataset.case);
    if (b.dataset.tab) await switchTab(b.dataset.tab);
    if (b.dataset.question) {
      $("message").value = b.dataset.question;
      $("message").focus();
    }
    if (b.dataset.addFile) {
      b.disabled = true;
      try {
        await api(`/cases/${current}/evidence`, "POST", {
          path: b.dataset.addFile,
        });
        $("evidence-dialog").close();
        await refresh();
      } finally {
        b.disabled = false;
      }
    }
    if (b.dataset.retry) {
      await api(`/tasks/${b.dataset.retry}/retry`, "POST");
      await refresh();
      toast("작업을 대기열에 넣었습니다. 조사를 계속하세요.");
    }
    if (b.dataset.disconnect || b.dataset.reconnect) {
      const action = b.dataset.disconnect ? "disconnect" : "reconnect";
      b.disabled = true;
      try {
        await api(
          `/cases/${current}/evidence/${b.dataset[action]}/${action}`,
          "POST",
        );
        await refresh();
        await listCases();
        if (tabName === "report") await loadReport();
        toast(
          action === "disconnect"
            ? "연결을 해제했습니다. 원본과 조사 기록은 보존됩니다."
            : "증거를 다시 연결했습니다.",
        );
      } finally {
        b.disabled = false;
      }
    }
    if (b.dataset.download) await download(b.dataset.download);
    if (b.dataset.source) {
      if (!token) {
        const link = document.createElement("a");
        link.href = `/api/observations/${encodeURIComponent(b.dataset.source)}/source`;
        link.download = b.dataset.source + ".bin";
        document.body.append(link); link.click(); link.remove();
        return;
      }
      const response = await api(`/observations/${b.dataset.source}/source`, "GET", undefined, true);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = b.dataset.source + ".bin";
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    if (b.dataset.ref) {
      $("search").value = b.dataset.ref;
      searchOffset = 0;
      await loadObservations();
      await switchTab("evidence");
      $(b.dataset.ref)?.setAttribute("open", "");
      $(b.dataset.ref)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
});
async function init() {
  const cases = await listCases();
  await updateConnection();
  if (cases.length && !current) await selectCase(cases[0].id);
}
perform(init);
setInterval(() => {
  if (current && !document.hidden && !busy)
    perform(async () => {
      await refresh();
      if (["running","pause_requested"].includes(snapshot.case.status)) await listCases();
    });
}, 3500);
