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
let progressReceivedAt = 0;
let reportReader = 'executive', selectedReport = null, reportCase = null;
const evidenceTypes = {linux_detection:"침해 관련 단서",linux_environment:"분석 환경",linux_coverage:"수집 범위",linux_command:"명령 기록",linux_authentication:"인증 기록",linux_session:"세션·권한",linux_persistence:"자동 실행 설정",linux_cron_call:"예약 작업 호출",linux_binary:"실행파일 정적 정보",linux_account:"계정",linux_ssh_trust:"SSH 신뢰 설정",linux_inspection_result:"이전 점검 결과",linux_network:"통신 기록",linux_tool_result:"추가 검사 결과",linux_path_match:"파일 경로",linux_literal_match:"원문 검색 일치",linux_persistence_link:"설정·호출 대조",linux_audit_group:"동일 audit 사건",linux_audit:"audit 기록",linux_login_record:"로그인 기록",linux_configuration:"설정",linux_system_event:"시스템 기록"};
Object.assign(evidenceTypes, {windows_environment:"Windows 수집 범위",windows_event:"Windows 이벤트",windows_process:"프로세스 시작",windows_powershell_start:"PowerShell 엔진 시작",windows_scriptblock:"스크립트 기록",windows_network:"네트워크 시도·결과",windows_task:"예약작업 설정",windows_registry:"레지스트리 설정",windows_srum_application:"SRUM 앱 사용",windows_srum_network:"SRUM 통신 계수",windows_file:"파일 정적 정보",windows_prior_interpretation:"기존 분석가 해석",windows_source_excerpt:"보존 자료 발췌",windows_counterevidence:"경로 혼동 반대 근거",windows_correlation:"Windows 자료 대조"});
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
  snapshot = nextSnapshot.unchanged ? {...snapshot,case:nextSnapshot.case,task:nextSnapshot.task,evidence:nextSnapshot.evidence,activity:nextSnapshot.activity,eta_estimate:nextSnapshot.eta_estimate} : nextSnapshot;
  progressReceivedAt = Date.now();
  renderSnapshot();
  if (previousCase?.id === current && previousCase.status !== snapshot.case.status)
    await listCases();
  } finally { refreshInFlight = false; }
}
function renderSnapshot() {
  const s = snapshot,
    c = s.case;
  renderInvestigationProgress(s, progressReceivedAt);
  if (typeof renderCockpit === 'function') renderCockpit(s);
  const timelineSources = new Set(s.evidence.filter(e=>e.connected!==false).map(e=>e.id));
  const timelineTasks = s.task.filter(t=>timelineSources.has(t.evidence_id));
  const timelineTask = timelineTasks.find(t => t.status === "running") || timelineTasks.find(t => t.status === "queued");
  const huntProgress = timelineTask?.progress;
  if (s.visual_timeline) s.visual_timeline.progress = {
    index: {linux_scan:1, windows_scan:1, linux_investigate:2, windows_investigate:2, ai_judgment:3, investigation_report:4}[timelineTask?.action] ?? (timelineTask ? 0 : 4),
    text: huntProgress?.stage === "linux_hunt"
      ? `기본 점검 ${(huntProgress.files_done || 0).toLocaleString()} / ${(huntProgress.candidates || 0).toLocaleString()}개 파일 · 발견 단서 ${huntProgress.detections || 0}개 · 정황 검토 준비 중`
      : huntProgress?.stage === "linux_discovery" ? `조사 대상 확인 · ${(huntProgress.entries || 0).toLocaleString()}개 경로`
      : ["linux_investigate","windows_investigate","ai_judgment"].includes(timelineTask?.action) ? c.investigation_stage
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
  $("case-id").textContent = `${c.id} · ${(c.target_os || 'linux') === 'windows' ? 'Windows' : 'Linux'}`;
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
  const investigation = s.hypothesis.filter((h) => ["linux-v1","windows-v1"].includes(h.contract) && activeIds.has(h.evidence_id));
  $("investigation-summary").hidden = true;
  $("hypotheses").innerHTML = investigation.map((h) => `<article class="claim"><h3>${h.number}. ${esc(h.text)}</h3><span class="status">${esc(h.judgment)}${h.ai_candidate ? " · AI 검토 후보" : ""}</span><p>${esc(h.reasoning || "원문 조사와 검증을 기다리고 있습니다.")}</p><p>경쟁 설명: ${esc((h.competing_explanations || []).join(" / "))}</p><p>남은 확인: ${esc((h.remaining_checks || h.unavailable_materials || []).join(" / "))}</p><p>${(h.observation_ids || []).slice(0,6).map((id) => `<button data-ref="${esc(id)}">원문 근거 ${esc(id.slice(-6))}</button>`).join(" ")}</p></article>`).join("");
  $("judgment-results").hidden = true;
  $("messages").hidden = false;
  $("suggestions").hidden = s.message.some(m => m.role === 'user');
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
    return `<div class="file-card"><span class="file-icon">${esc(e.name.split(".").pop().toUpperCase().slice(0, 4))}</span><div class="file-info"><strong>${esc(e.name)}</strong><small>${e.segment_count > 1 ? `${e.segment_count}개 세그먼트 · ` : ""}${size(e.total_size || e.size)} · ${detached ? "연결 해제됨" : e.sha256 ? "입력 해시 기록됨" : "검증 대기"}</small>${progressText ? `<small class="file-progress" role="status">${esc(progressText)}</small>` : ""}<button class="evidence-action" data-${detached ? "reconnect" : "disconnect"}="${esc(e.id)}" ${locked ? "disabled" : ""} title="${reason}" aria-label="${esc(e.name)} ${detached ? "다시 연결" : "연결 해제"}">${detached ? "다시 연결" : "연결 해제"}</button>${!detached && running ? "<small>검증 중에는 해제할 수 없습니다.</small>" : ""}</div></div>`;
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
  const conversationMessages = s.message.filter((m) => !m.automatic);
  const messageCard = (m) => {
    const text = esc(m.text).replace(/OBSERVATION-[a-f0-9]{12}/g,
      (id) => `<button class="inline-reference" data-ref="${id}" title="${id}">근거 ↗</button>`);
    return `<article class="message ${esc(m.role)} ${esc(m.mode || "")}"><div class="message-role">${m.role === "user" ? "나" : "Frontier"}${m.partial ? ' <span>부분 근거 답변</span>' : ''}</div>${text}</article>`;
  };
  const history = automaticMessages.length
    ? `<details class="investigation-history"><summary>에이전트 작업 기록 ${automaticMessages.length}</summary>${automaticMessages.map(messageCard).join("")}</details>` : "";
  const messages = conversationMessages.map(messageCard).join("") + history;
  const messageSignature = current + connected.length + messages;
  if ($("messages").dataset.signature !== messageSignature) {
    const nearBottom = $("messages").scrollHeight - $("messages").scrollTop - $("messages").clientHeight < 80;
    $("messages").innerHTML =
      messages ||
      `<article class="message empty"><div class="agent-orb">f</div><h3>함께 조사해요.</h3><p>${connected.length ? "궁금한 단서를 짚거나 다음 조사 방향을 알려주세요." : "상단 증거 관리에서 파일을 연결하세요."}</p></article>`;
    $("messages").dataset.signature = messageSignature;
    if (nearBottom) $("messages").scrollTop = $("messages").scrollHeight;
  }
  $("claims").innerHTML = s.claim.length ? `<details class="investigation-history"><summary>이전 해석 기록 ${s.claim.length}개</summary>${s.claim.map((cl) => `<article class="claim"><h3>${esc(cl.text)}</h3><p>${esc(cl.uncertainty)}</p><p>대안: ${esc((cl.alternatives || []).join(" / "))}</p><p>${cl.observation_ids.map((id) => `<button data-ref="${esc(id)}">원문 근거 ↗</button>`).join(" ")}</p></article>`).join("")}</details>` : '<p class="small-hint">조사가 진행되면 근거를 바탕으로 AI가 자동 판단합니다.</p>';
  $("report-history").innerHTML = s.report
    .map(
      (r, i) =>
        `<button data-report="${esc(r.id)}">스냅샷 ${i + 1} · 미확인 범위 ${r.gap_count}${r.reader_contract ? '' : ' · 이전 형식 원문 ZIP · 제한 배포'}</button>`,
    )
    .join("");
  const savedReport = s.report.find(r => r.id === selectedReport);
  if (savedReport && reportCase === current)
    $("report-snapshot-status").textContent = `저장된 동일 스냅샷 · ${savedReport.report_id}${savedReport.snapshot?.scope_revision !== s.report_scope_revision ? ' · 이후 조사 상태 또는 결과가 갱신됨' : ''}`;
}
async function switchTab(name) {
  tabName = name;
  if (name === "evidence") {
    if (!$("evidence-drawer").open) $("evidence-drawer").showModal();
    await loadObservations();
  } else if (name === "report") await switchCompanion('report');
}
async function loadObservations() {
  const result = await api(
    `/cases/${current}/observations?q=${encodeURIComponent($("search").value)}&offset=${searchOffset}`,
  );
  $("observation-list").innerHTML =
    result.items
      .map(
        (o) =>
          `<details class="observation" id="${esc(o.id)}"><summary><time>${esc(o.timestamp ? new Date(o.timestamp).toLocaleString("ko-KR") : "대표 시각 미기록 · 필드 확인")}</time><strong>${esc(evidenceTypes[o.type] || o.type)}</strong><span class="location">${esc(o.source_location)}</span></summary><pre>${esc(o.fields_display_json ?? JSON.stringify(o.fields, null, 2))}</pre>${o.fields.artifact_path ? `<button class="secondary" data-source="${esc(o.id)}">해시 검증된 추출 원문 ↓</button>` : ""}<p class="meta">${esc(o.id)}<br>증거 ${esc(o.evidence_id)}<br>도구 기록 ${esc(o.receipt_id)}</p></details>`,
      )
      .join("") ||
    '<div class="empty-state">표시할 관측 기록이 없습니다. 조사를 실행하거나 검색어를 바꿔보세요.</div>';
  $("more-observations").hidden = searchOffset + 100 >= result.total;
}
async function loadReport() {
  if (reportCase !== current) { reportCase = current; selectedReport = null; }
  const caseId = current, recordId = selectedReport, reader = reportReader;
  const response = await api(
    recordId ? `/reports/${recordId}/files/${reader}.html` : `/cases/${caseId}/report-preview?reader=${reader}`,
    "GET",
    undefined,
    true,
  );
  const html = await response.text();
  if (caseId !== current || recordId !== selectedReport || reader !== reportReader) return;
  $("report-preview").srcdoc = html;
  $("report-snapshot-status").textContent = recordId
    ? `저장본${response.headers.get('X-Report-Scope-Changed') === 'true' ? ' · 더 최신 조사 결과 있음' : ''}`
    : '실시간 초안 · 조사 진행에 따라 갱신';
  $("reader-executive").setAttribute('aria-pressed', reader === 'executive');
  $("reader-analyst").setAttribute('aria-pressed', reader === 'analyst');
}
$("reader-executive").onclick = () => perform(async () => { reportReader = 'executive'; await loadReport(); });
$("reader-analyst").onclick = () => perform(async () => { reportReader = 'analyst'; await loadReport(); });
$("download-word").onclick = () => perform(async () => {
  if (!selectedReport) { toast('먼저 새 스냅샷을 저장하세요.'); return; }
  await download(selectedReport, reportReader);
});
$("download-raw").onclick = () => perform(async () => {
  if (!selectedReport) { toast('먼저 새 스냅샷을 저장하세요.'); return; }
  await download(selectedReport);
});
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
      target_os: $("target-os").value,
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
    investigator: "native",
    investigation_strategy: $("investigation-strategy").value,
    think: $("think-mode").value,
    num_ctx: Number($("investigation-context").value),
    num_predict: Number($("investigation-output").value),
    assistant_num_ctx: Number($("assistant-context").value),
    assistant_num_predict: Number($("assistant-output").value),
    temperature: Number($("model-temperature").value),
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
    $("investigation-strategy").value = c.investigation_strategy ?? "guided";
    $("think-mode").value = c.think ?? "off";
    $("investigation-context").value = c.num_ctx ?? 32768;
    $("investigation-output").value = c.num_predict ?? 4000;
    $("assistant-context").value = c.assistant_num_ctx ?? 16384;
    $("assistant-output").value = c.assistant_num_predict ?? 2500;
    $("model-temperature").value = c.temperature ?? 0.1;
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
      selectedReport = r.id; reportCase = current;
      await refresh();
      await loadReport();
      await download(r.id, reportReader);
      toast("같은 스냅샷의 임원용·분석가용 HTML·Word를 저장했습니다.");
    } finally {
      $("save-report").disabled = false;
    }
  });
async function download(id, reader = null) {
  const route = reader ? `/reports/${encodeURIComponent(id)}/files/${reader}.docx` : `/reports/${encodeURIComponent(id)}/download`;
  const filename = reader ? `frontier-${reader}.docx` : 'frontier-restricted-evidence.zip';
  if (!token) {
    const link = document.createElement("a");
    link.href = '/api' + route;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    return;
  }
  const response = await api(route, "GET", undefined, true);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
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
    if (b.dataset.report) {
      const r = snapshot.report.find(r => r.id === b.dataset.report);
      if (!r.reader_contract) { await download(r.id); return; }
      selectedReport = r.id; reportCase = current; await loadReport();
    }
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
setInterval(() => { if (snapshot && !document.hidden) renderInvestigationProgress(snapshot, progressReceivedAt); }, 1000);
setInterval(() => {
  if (current && !document.hidden && !busy)
    perform(async () => {
      await refresh();
      if (["running","pause_requested"].includes(snapshot.case.status)) await listCases();
    });
}, 3500);
