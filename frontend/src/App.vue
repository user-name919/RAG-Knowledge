<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";

// Key 仅保留在本页内存中，刷新或退出即清除，不写入 localStorage。
const key = ref(""),
  connected = ref(false),
  identity = ref({}),
  connecting = ref(false);
const bases = ref([]),
  selected = ref(""),
  docs = ref([]),
  results = ref([]);
const tab = ref("documents"),
  error = ref(""),
  notice = ref(""),
  busy = ref(false);
const searched = ref(false);
const query = ref(""),
  searching = ref(false),
  threshold = ref(0),
  topK = ref(8),
  sourceFilter = ref("");
const selectedFile = ref(null),
  uploadInput = ref(null),
  replaceInput = ref(null),
  replacing = ref(null);
const uploadTitle = ref(""),
  uploadTags = ref(""),
  dragging = ref(false);
const newBase = ref(""),
  showCreate = ref(false),
  deleting = ref(null);
let timer,
  loadGeneration = 0;
const currentBase = computed(() =>
  bases.value.find((b) => b.id === selected.value),
);
const files = computed(() => docs.value.filter((d) => d.file));
const ready = computed(
  () => files.value.filter((d) => d.file.index_status === "ready").length,
);
const active = computed(
  () =>
    files.value.filter((d) =>
      ["pending", "running"].includes(d.file.index_status),
    ).length,
);
const selectedBytes = computed(() => formatSize(selectedFile.value?.size || 0));
const states = {
  ready: "可检索",
  pending: "等待处理",
  running: "正在处理",
  failed: "处理失败",
  done: "已完成",
  superseded: "已过期",
};

// 统一请求入口：显式报告错误，表单请求交给浏览器生成 multipart boundary。
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "api-key": key.value, ...options.headers },
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {
      /* 保留 HTTP 提示 */
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
function jsonRequest(method, value) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}
function formatSize(bytes) {
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function formatTime(value) {
  return value
    ? new Date(value.endsWith("Z") ? value : value + "Z").toLocaleString(
        "zh-CN",
        { hour12: false },
      )
    : "—";
}
function stateLabel(status) {
  return states[status] || status;
}

// 分页拉取完整列表，避免只展示后端默认第一页而遗漏用户资料。
async function allPages(path) {
  const rows = [];
  for (let offset = 0; ; offset += 200) {
    const page = await api(`${path}?offset=${offset}&limit=200`);
    rows.push(...page.data);
    if (page.data.length < 200) return rows;
  }
}
async function connect() {
  connecting.value = true;
  error.value = "";
  try {
    identity.value = await api("/v1/me");
    bases.value = await allPages("/v1/knowledge_bases");
    connected.value = true;
    selected.value = bases.value[0]?.id || "";
    startPolling();
  } catch (e) {
    error.value = e.message;
  } finally {
    connecting.value = false;
  }
}
function disconnect() {
  clearTimeout(timer);
  loadGeneration++;
  key.value = "";
  connected.value = false;
  selected.value = "";
  docs.value = [];
  bases.value = [];
  results.value = [];
  error.value = "";
  notice.value = "";
}
async function createBase() {
  if (!newBase.value.trim()) return;
  busy.value = true;
  error.value = "";
  try {
    const created = await api(
      "/v1/knowledge_bases",
      jsonRequest("POST", { name: newBase.value.trim() }),
    );
    bases.value.push(created);
    selected.value = created.id;
    newBase.value = "";
    showCreate.value = false;
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function refreshDocuments() {
  if (!selected.value || !connected.value) return;
  const kb = selected.value,
    generation = ++loadGeneration;
  const rows = await allPages(`/v1/knowledge_bases/${kb}/documents`);
  // 用户切换库/退出或后来的刷新先返回时，不允许旧响应覆盖页面。
  if (generation === loadGeneration && selected.value === kb && connected.value)
    docs.value = rows;
}
function startPolling() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    if (!connected.value) return;
    try {
      if (active.value) await refreshDocuments();
    } catch (e) {
      error.value = e.message;
    }
    startPolling();
  }, 2500);
}
watch(selected, async () => {
  loadGeneration++;
  docs.value = [];
  results.value = [];
  searched.value = false;
  sourceFilter.value = "";
  error.value = "";
  notice.value = "";
  try {
    await refreshDocuments();
  } catch (e) {
    error.value = e.message;
  }
});
onBeforeUnmount(() => clearTimeout(timer));

function chooseFile(file) {
  error.value = "";
  notice.value = "";
  if (!file) return;
  if (!/\.(md|markdown|txt|pdf)$/i.test(file.name)) {
    error.value = "请选择 Markdown、TXT 或文本型 PDF。";
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    error.value = "单个文件最大 10 MB。";
    return;
  }
  if (!file.size) {
    error.value = "文件为空，请选择有内容的文件。";
    return;
  }
  selectedFile.value = file;
  uploadTitle.value = file.name.replace(/\.[^.]+$/, "");
}
function dropped(event) {
  dragging.value = false;
  chooseFile(event.dataTransfer.files[0]);
}
async function upload() {
  if (!selectedFile.value || !selected.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  const kb = selected.value;
  try {
    const form = new FormData();
    form.append("file", selectedFile.value);
    form.append("title", uploadTitle.value.trim() || selectedFile.value.name);
    form.append(
      "tags",
      JSON.stringify(
        uploadTags.value
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
      ),
    );
    await api(`/v1/knowledge_bases/${kb}/documents/upload`, {
      method: "POST",
      body: form,
    });
    selectedFile.value = null;
    uploadTitle.value = "";
    uploadTags.value = "";
    if (uploadInput.value) uploadInput.value.value = "";
    notice.value = "文件已接收，正在后台解析和建立索引。状态将自动更新。";
    await refreshDocuments();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
function chooseReplacement(doc) {
  replacing.value = doc;
  replaceInput.value.click();
}
async function replaceFile(event) {
  const file = event.target.files[0],
    doc = replacing.value;
  if (!file || !doc) return;
  busy.value = true;
  error.value = "";
  try {
    const form = new FormData();
    form.append("file", file);
    form.append("tags", JSON.stringify(doc.file.tags));
    await api(
      `/v1/knowledge_bases/${selected.value}/documents/${doc.id}/file`,
      { method: "PUT", body: form },
    );
    results.value = [];
    notice.value = "已提交替换，旧版本已停止参与检索。";
    await refreshDocuments();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
    event.target.value = "";
  }
}
async function retry(doc) {
  busy.value = true;
  error.value = "";
  try {
    await api(
      `/v1/knowledge_bases/${selected.value}/documents/${doc.id}/retry_failed`,
      { method: "POST" },
    );
    await refreshDocuments();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function remove() {
  busy.value = true;
  error.value = "";
  try {
    await api(
      `/v1/knowledge_bases/${selected.value}/documents/${deleting.value.id}`,
      { method: "DELETE" },
    );
    deleting.value = null;
    results.value = [];
    await refreshDocuments();
    notice.value = "文档已删除，不再参与检索。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function download(docId) {
  error.value = "";
  try {
    const response = await fetch(
      `/v1/knowledge_bases/${selected.value}/documents/${docId}/file`,
      { headers: { "api-key": key.value } },
    );
    if (!response.ok) throw new Error("原文件下载失败或已删除");
    const url = URL.createObjectURL(await response.blob()),
      link = document.createElement("a");
    link.href = url;
    link.download =
      docs.value.find((d) => d.id === docId)?.file?.filename || "document";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    error.value = e.message;
  }
}
async function search() {
  if (!query.value.trim()) return;
  searching.value = true;
  searched.value = true;
  error.value = "";
  results.value = [];
  const kb = selected.value;
  try {
    const result = await api(
      "/v1/knowledge_bases/recall",
      jsonRequest("POST", {
        knowledge_base_ids: [kb],
        document_ids: sourceFilter.value ? [sourceFilter.value] : [],
        query: query.value,
        retrieval_options: {
          top_k: Number(topK.value),
          score_threshold: Number(threshold.value),
        },
        weights: {
          vector_setting: { vector_weight: 0.8 },
          keyword_setting: { vector_weight: 0.2 },
        },
      }),
    );
    if (selected.value === kb) {
      results.value = result.data;
      notice.value = result.data.length
        ? `找到 ${result.data.length} 个相关片段`
        : "当前范围和阈值下没有结果。";
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    searching.value = false;
  }
}
</script>

<template>
  <div v-if="!connected" class="connect-page">
    <div class="login-brand">
      <span class="brand-mark">知</span> RAG KNOWLEDGE
    </div>
    <form class="connect-card" @submit.prevent="connect">
      <div class="eyebrow">团队知识库 / DEMO</div>
      <h1>让资料成为<br />可检索的知识。</h1>
      <p>
        上传产品文档、项目说明和运维手册，验证知识库如何理解并找到相关原文。
      </p>
      <label for="api-key">知识库 API Key</label>
      <input
        id="api-key"
        v-model="key"
        type="password"
        placeholder="输入本地 ADMIN_API_KEY 或已授权的 Key"
        required
        autocomplete="off"
      />
      <small
        >使用后端 .env 中的
        ADMIN_API_KEY，不是硅基流动密钥。仅在本页内存保存。</small
      >
      <p v-if="error" class="alert error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="connecting">
        {{ connecting ? "正在连接…" : "连接知识库 →" }}
      </button>
      <a href="/docs" target="_blank" rel="noreferrer">查看接口文档 ↗</a>
    </form>
  </div>
  <div v-else class="workspace">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">知</span>
        <div>团队知识库<small>RAG KNOWLEDGE</small></div>
      </div>
      <div class="side-label">
        知识空间
        <button
          v-if="identity.admin"
          class="icon-button"
          aria-label="新建知识库"
          @click="showCreate = !showCreate"
        >
          ＋
        </button>
      </div>
      <form v-if="showCreate" class="create-form" @submit.prevent="createBase">
        <input
          v-model="newBase"
          aria-label="知识库名称"
          placeholder="知识库名称"
          maxlength="200"
          required
        /><button :disabled="busy">创建</button>
      </form>
      <nav aria-label="知识库列表">
        <button
          v-for="base in bases"
          :key="base.id"
          class="base-button"
          :class="{ selected: base.id === selected }"
          :disabled="busy"
          @click="selected = base.id"
        >
          <span>▤</span>{{ base.name }}
        </button>
      </nav>
      <p v-if="!bases.length" class="side-empty">
        还没有知识库。{{
          identity.admin
            ? "点击上方 ＋ 创建。"
            : "请让管理员授予知识库访问权限。"
        }}
      </p>
      <div class="side-footer">
        <span class="online-dot"></span> 后端已连接
        <span class="role">{{ identity.can_write ? "可维护" : "只读" }}</span
        ><button @click="disconnect" :disabled="busy">退出连接</button>
      </div>
    </aside>
    <main>
      <header class="topbar">
        <span
          >工作空间 <span class="crumb">/</span>
          {{ currentBase?.name || "请选择知识库" }}</span
        ><a href="/docs" target="_blank" rel="noreferrer">API 文档 ↗</a>
      </header>
      <section class="content">
        <div class="page-heading">
          <div>
            <div class="eyebrow">KNOWLEDGE WORKSPACE</div>
            <h1>{{ currentBase?.name || "开始建立知识库" }}</h1>
            <p>管理资料、跟踪索引状态，用真实问法检查召回原文。</p>
          </div>
          <span class="demo-badge">DEMO · v0.2</span>
        </div>
        <div class="stats">
          <div>
            <span>资料总数</span><strong>{{ docs.length }}</strong>
          </div>
          <div>
            <span>已就绪文件</span><strong>{{ ready }}</strong>
          </div>
          <div>
            <span>处理中</span><strong>{{ active }}</strong>
          </div>
          <div>
            <span>检索方式</span><strong class="text-stat">语义 + 关键词</strong
            ><small>0.8 / 0.2</small>
          </div>
        </div>
        <div class="tabs" role="tablist">
          <button
            role="tab"
            :aria-selected="tab === 'documents'"
            :class="{ active: tab === 'documents' }"
            @click="tab = 'documents'"
          >
            文档资料</button
          ><button
            role="tab"
            :aria-selected="tab === 'search'"
            :class="{ active: tab === 'search' }"
            @click="tab = 'search'"
          >
            检索测试
          </button>
        </div>
        <p v-if="error" class="alert error" role="alert">{{ error }}</p>
        <p v-if="notice" class="alert success" role="status">{{ notice }}</p>
        <template v-if="selected && tab === 'documents'">
          <div v-if="identity.can_write" class="upload-card">
            <div class="section-heading">
              <div>
                <h2>上传文档</h2>
                <p>原文件保存后，后台自动解析、切分并建立索引。</p>
              </div>
              <span class="muted">单文件 ≤ 10 MB</span>
            </div>
            <label
              class="dropzone"
              :class="{ dragging }"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="dropped"
            >
              <input
                ref="uploadInput"
                type="file"
                accept=".md,.markdown,.txt,.pdf"
                aria-label="选择上传文档"
                :disabled="busy"
                @change="chooseFile($event.target.files[0])"
              />
              <span class="upload-icon">↑</span
              ><strong>{{
                selectedFile ? selectedFile.name : "点击选择，或拖入一份文档"
              }}</strong
              ><span>{{
                selectedFile ? selectedBytes : "Markdown · TXT · 文本型 PDF"
              }}</span>
            </label>
            <div v-if="selectedFile" class="upload-details">
              <label
                >文档标题<input v-model="uploadTitle" maxlength="200" /></label
              ><label
                >标签（逗号分隔）<input
                  v-model="uploadTags"
                  placeholder="如：老师，作业，产品A" /></label
              ><button class="primary" :disabled="busy" @click="upload">
                {{ busy ? "正在提交…" : "上传并建立索引" }}
              </button>
            </div>
            <p class="footnote">
              文本文件请使用 UTF-8 编码。扫描版 PDF 暂不支持
              OCR；部分页面无文字时会显示警告。
            </p>
          </div>
          <div class="document-panel">
            <div class="section-heading">
              <h2>
                文档列表 <span class="count">{{ docs.length }}</span>
              </h2>
              <button
                class="secondary"
                :disabled="busy"
                @click="refreshDocuments().catch((e) => (error = e.message))"
              >
                刷新状态
              </button>
            </div>
            <div v-if="!docs.length" class="empty">
              <span>▤</span>
              <h3>还没有资料</h3>
              <p>上传第一份文档，建立团队的知识来源。</p>
            </div>
            <div v-else class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>文档 / 来源</th>
                    <th>状态</th>
                    <th>片段</th>
                    <th>更新时间</th>
                    <th class="right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="doc in docs" :key="doc.id">
                    <td>
                      <div class="doc-title">
                        <span class="file-icon">{{
                          doc.file?.filename.split(".").pop().toUpperCase() ||
                          "QA"
                        }}</span>
                        <div>
                          <strong>{{ doc.title }}</strong
                          ><small
                            >{{
                              doc.file
                                ? `${formatSize(doc.file.size_bytes)} · v${doc.file.version}`
                                : "问答节点"
                            }}
                            <span
                              v-for="tag in doc.file?.tags"
                              :key="tag"
                              class="tag"
                              >{{ tag }}</span
                            ></small
                          >
                        </div>
                      </div>
                      <p v-if="doc.file?.last_error" class="row-error">
                        {{ doc.file.last_error }}
                      </p>
                      <p
                        v-for="warning in doc.file?.warnings"
                        :key="warning"
                        class="row-warning"
                      >
                        {{ warning }}
                      </p>
                    </td>
                    <td>
                      <span
                        class="status"
                        :class="doc.file?.index_status || 'qa'"
                        >{{
                          doc.file
                            ? stateLabel(doc.file.index_status)
                            : "QA 数据"
                        }}</span
                      >
                    </td>
                    <td>{{ doc.file?.chunk_count ?? "—" }}</td>
                    <td class="muted">
                      {{ formatTime(doc.file?.updated_at) }}
                    </td>
                    <td>
                      <div class="row-actions">
                        <button v-if="doc.file" @click="download(doc.id)">
                          下载</button
                        ><button
                          v-if="doc.file && identity.can_write"
                          :disabled="busy"
                          @click="chooseReplacement(doc)"
                        >
                          替换</button
                        ><button
                          v-if="
                            doc.file?.index_status === 'failed' &&
                            identity.can_write
                          "
                          :disabled="busy"
                          @click="retry(doc)"
                        >
                          重试</button
                        ><button
                          v-if="identity.can_write"
                          class="danger-text"
                          :disabled="busy"
                          @click="deleting = doc"
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <input
            ref="replaceInput"
            class="hidden"
            type="file"
            accept=".md,.markdown,.txt,.pdf"
            aria-label="选择替换文件"
            @change="replaceFile"
          />
        </template>
        <template v-if="selected && tab === 'search'">
          <form class="search-card" @submit.prevent="search">
            <div class="section-heading">
              <div>
                <h2>试着用自己的话提问</h2>
                <p>返回相关原文片段及来源，不生成聊天答案。</p>
              </div>
              <span class="pill">混合召回</span>
            </div>
            <textarea
              v-model="query"
              aria-label="检索问题"
              placeholder="例如：题布置下去了，学生那边为什么看不到？"
              required
              rows="3"
            ></textarea>
            <div class="search-options">
              <label
                >资料范围<select v-model="sourceFilter">
                  <option value="">当前知识库全部资料</option>
                  <option v-for="doc in docs" :key="doc.id" :value="doc.id">
                    {{ doc.title }}
                  </option>
                </select></label
              ><label
                >返回条数<input
                  v-model="topK"
                  type="number"
                  min="1"
                  max="50"
                  required /></label
              ><label
                >分数阈值<input
                  v-model="threshold"
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  required /></label
              ><button class="primary" :disabled="searching">
                {{ searching ? "正在检索…" : "开始检索 →" }}
              </button>
            </div>
            <p class="footnote">
              分数不是答案正确率。默认阈值为
              0，无关问题也可能有候选，请核对原文。
            </p>
          </form>
          <div class="result-heading">
            <h2>
              召回结果 <span class="count">{{ results.length }}</span>
            </h2>
            <span class="muted">按融合分数排序</span>
          </div>
          <article
            v-for="(row, index) in results"
            :key="row.id"
            class="result-card"
          >
            <header>
              <span class="rank">{{ String(index + 1).padStart(2, "0") }}</span>
              <div>
                <h3>{{ row.title }}</h3>
                <small
                  >{{ row.source_location }} · v{{ row.version }} ·
                  {{ row.type === "document" ? "文档" : "QA" }}</small
                >
              </div>
              <strong class="score">{{ row.score.toFixed(3) }}</strong>
            </header>
            <pre>{{ row.content }}</pre>
            <footer>
              <span
                >向量 {{ row.vector_score.toFixed(3) }} · 关键词
                {{ row.keyword_score.toFixed(3) }}</span
              ><button
                v-if="row.type === 'document'"
                @click="download(row.document_id)"
              >
                下载原文 ↗</button
              ><span v-else>QA 原文</span>
            </footer>
          </article>
          <div v-if="!results.length && !searching" class="empty">
            <span>⌕</span>
            <h3>{{ searched ? "没有匹配片段" : "在上方输入问题" }}</h3>
            <p>
              {{
                searched
                  ? "请检查索引状态、资料范围和分数阈值。"
                  : "检索结果会展示原文、来源位置和两路分数。"
              }}
            </p>
          </div>
        </template>
      </section>
    </main>
    <div v-if="deleting" class="modal-backdrop">
      <section
        class="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-title"
      >
        <h2 id="delete-title">删除这份资料？</h2>
        <p>“{{ deleting.title }}” 将停止参与检索，后台会清理原文件及索引。</p>
        <div class="modal-actions">
          <button class="secondary" :disabled="busy" @click="deleting = null">
            取消</button
          ><button class="danger" :disabled="busy" @click="remove">
            确认删除
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
