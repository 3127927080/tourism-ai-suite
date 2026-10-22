<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { adminApi, createRealtimeConnection, login } from './api'

const section = ref('handoffs')
const account = ref(localStorage.getItem('staff_id') || 'admin_001')
const session = reactive({ token: '', principal: null, name: '' })
const accepting = ref(false)
const uploading = ref(false)
const error = ref('')
const notice = ref('')
const handoffs = ref([])
const selectedHandoff = ref(null)
const conversation = ref(null)
const reply = ref('')
const documents = ref([])
const runs = ref([])
const selectedRun = ref(null)
const metrics = reactive({ service: {}, commerce: {}, ai: {} })
const upload = reactive({ file: null, title: '', category: 'policy' })
const evaluation = ref(null)
let realtime
let handoffRefreshTimer

const isAdmin = computed(() => session.principal?.role === 'admin')
const canManageSelectedHandoff = computed(() => (
  selectedHandoff.value?.status === 'active'
  && selectedHandoff.value.assigned_agent_id === session.principal?.user_id
))

function messageText(message) {
  const content = message.content || {}
  return content.text || content.blocks?.map((item) => item.content).join('\n\n') || '对象消息'
}

function scheduleHandoffRefresh() {
  window.clearTimeout(handoffRefreshTimer)
  handoffRefreshTimer = window.setTimeout(async () => {
    handoffRefreshTimer = null
    if (!session.token || section.value !== 'handoffs') return
    try {
      await loadHandoffs()
    } catch (cause) {
      error.value = cause.message
    }
  }, 150)
}

async function authenticate() {
  await execute(async () => {
    const result = await login(account.value)
    session.token = result.access_token
    session.principal = result.principal
    session.name = result.display_name
    section.value = isAdmin.value ? 'observability' : 'handoffs'
    localStorage.setItem('staff_id', account.value)
    window.clearTimeout(handoffRefreshTimer)
    realtime?.close()
    realtime = null
    if (!isAdmin.value) {
      let hasConnected = false
      realtime = createRealtimeConnection(session.token, {
        onState: (state) => {
          if (state !== 'connected') return
          if (hasConnected) scheduleHandoffRefresh()
          hasConnected = true
        },
        onEvent: (event) => {
          const handoffId = event.event_data?.handoff_id
          if (event.event_type === 'message_created' || event.event_type === 'handoff_changed') {
            scheduleHandoffRefresh()
          }
          if (event.event_type === 'handoff_changed' && event.event_data?.status === 'resolved') {
            handoffs.value = handoffs.value.filter((item) => item.id !== handoffId)
            if (selectedHandoff.value?.id === handoffId) {
              selectedHandoff.value = null
              conversation.value = null
            }
          }
        },
      })
    }
    await loadSection()
  })
}

async function loadSection() {
  selectedRun.value = null
  if (section.value === 'handoffs') await loadHandoffs()
  if (section.value === 'knowledge') documents.value = await adminApi.documents(session.token)
  if (section.value === 'observability') await loadObservability()
}

async function loadHandoffs() {
  handoffs.value = await adminApi.handoffs(session.token)
  if (selectedHandoff.value) {
    selectedHandoff.value = handoffs.value.find((item) => item.id === selectedHandoff.value.id) || null
    if (selectedHandoff.value) await openHandoff(selectedHandoff.value)
  }
}

async function openHandoff(item) {
  selectedHandoff.value = item
  conversation.value = await adminApi.conversation(session.token, item.conversation_id)
}

async function acceptHandoff() {
  accepting.value = true
  error.value = ''
  try {
    await adminApi.accept(session.token, selectedHandoff.value.id)
    notice.value = '会话已接入'
    await loadHandoffs()
  } catch (cause) {
    if (cause.status === 409) {
      const handoffId = selectedHandoff.value?.id
      await loadHandoffs()
      const handoff = handoffs.value.find((item) => item.id === handoffId)
      notice.value = handoff?.assigned_agent_id
        ? `该工单已被客服 ${handoff.assigned_agent_id} 接入`
        : '该工单已被其他客服接入'
    } else {
      error.value = cause.message
    }
  } finally {
    accepting.value = false
  }
}

async function sendReply() {
  const text = reply.value.trim()
  if (!text) return
  const messageId = `human_msg_${Date.now()}_${Math.random().toString(16).slice(2)}`
  await execute(async () => {
    await adminApi.reply(session.token, selectedHandoff.value.id, messageId, text)
    reply.value = ''
    conversation.value = await adminApi.conversation(session.token, selectedHandoff.value.conversation_id)
  })
}

async function resolveHandoff() {
  if (!confirm('确认结束人工服务并交还 AI 客服？')) return
  await execute(async () => {
    await adminApi.resolve(session.token, selectedHandoff.value.id)
    selectedHandoff.value = null
    conversation.value = null
    notice.value = '人工服务已结束'
    await loadHandoffs()
  })
}

async function submitDocument() {
  if (!upload.file) return
  uploading.value = true
  error.value = ''
  try {
    await adminApi.upload(session.token, upload.file, upload.title || upload.file.name, upload.category)
    upload.file = null
    upload.title = ''
    notice.value = '文档已进入索引队列'
    documents.value = await adminApi.documents(session.token)
  } catch (cause) {
    error.value = cause.message
  } finally {
    uploading.value = false
  }
}

async function loadObservability() {
  const requests = [
    adminApi.runs(session.token)
      .then((value) => { runs.value = value })
      .catch(() => { runs.value = [] }),
  ]
  if (isAdmin.value) {
    requests.push(
      adminApi.serviceMetrics(session.token)
        .then((value) => { metrics.service = value })
        .catch(() => { metrics.service = {} }),
      adminApi.commerceSummary(session.token)
        .then((value) => { metrics.commerce = value })
        .catch(() => { metrics.commerce = {} }),
      adminApi.aiMetrics(session.token)
        .then((value) => { metrics.ai = value })
        .catch(() => { metrics.ai = {} }),
    )
  }
  await Promise.allSettled(requests)
}

async function inspectRun(run) {
  await execute(async () => { selectedRun.value = await adminApi.run(session.token, run.id) })
}

async function runEvaluation() {
  await execute(async () => {
    evaluation.value = await adminApi.evaluate(session.token)
    notice.value = '评测已完成'
  })
}

async function execute(action) {
  error.value = ''
  try { await action() } catch (cause) { error.value = cause.message }
}

onMounted(authenticate)
onBeforeUnmount(() => {
  window.clearTimeout(handoffRefreshTimer)
  realtime?.close()
})
</script>

<template>
  <div class="admin-shell">
    <header class="topbar">
      <div class="logo"><span>小满</span><div><strong>客服后台</strong><small>CONTROL CENTER</small></div></div>
      <nav aria-label="后台主导航">
        <button v-if="!isAdmin" :class="{ active: section === 'handoffs' }" :aria-current="section === 'handoffs' ? 'page' : undefined" @click="section = 'handoffs'; loadSection()">人工工作台</button>
        <button v-if="isAdmin" :class="{ active: section === 'observability' }" :aria-current="section === 'observability' ? 'page' : undefined" @click="section = 'observability'; loadSection()">监控指标</button>
      </nav>
      <div class="account">
        <select v-model="account" aria-label="切换后台账号" @change="authenticate"><option value="admin_001">系统管理员</option><option value="agent_001">客服小周</option></select>
        <small>{{ session.principal?.role || 'loading' }}</small>
      </div>
    </header>

    <main class="admin-main">
      <div v-if="error" class="alert error" role="alert">{{ error }} <button aria-label="关闭错误提示" @click="error = ''">×</button></div>
      <div v-if="notice" class="alert success" role="status">{{ notice }} <button aria-label="关闭成功提示" @click="notice = ''">×</button></div>

      <section v-if="section === 'handoffs'" class="workspace">
        <div class="queue panel">
          <div class="panel-title"><h2>待处理会话</h2><span>{{ handoffs.length }}</span></div>
          <div class="queue-scroll">
            <button v-for="item in handoffs" :key="item.id" :class="['queue-item', { active: selectedHandoff?.id === item.id }]" @click="openHandoff(item)">
              <span class="customer-avatar">{{ item.user_id.slice(-2) }}</span><span><strong>{{ item.user_id }}</strong><small>{{ item.summary }}</small></span><b>{{ item.status === 'active' ? `处理中 · ${item.assigned_agent_id}` : '等待' }}</b>
            </button>
            <div v-if="!handoffs.length" class="empty"><strong>队列已清空</strong><span>当前没有需要人工介入的会话</span></div>
          </div>
        </div>
        <div class="conversation panel">
          <template v-if="selectedHandoff && conversation">
            <div class="conversation-head">
              <div><strong>{{ selectedHandoff.user_id }}</strong><small>{{ selectedHandoff.id }}</small></div>
              <div>
                <button v-if="selectedHandoff.status === 'waiting'" :disabled="accepting" @click="acceptHandoff">{{ accepting ? '接入中…' : '接入会话' }}</button>
                <button v-else-if="canManageSelectedHandoff" class="ghost" @click="resolveHandoff">结束服务</button>
                <template v-else>
                  <small>由 {{ selectedHandoff.assigned_agent_id }} 处理中</small>
                </template>
              </div>
            </div>
            <div class="transcript" aria-label="会话记录" aria-live="polite"><article v-for="message in conversation.messages" :key="message.id || message.message_id" :class="message.role"><small>{{ message.role }}</small><p>{{ messageText(message) }}</p></article></div>
            <form class="reply-box" @submit.prevent="sendReply"><textarea v-model="reply" rows="2" aria-label="回复用户" :disabled="!canManageSelectedHandoff" :placeholder="canManageSelectedHandoff ? '回复用户' : selectedHandoff.status === 'waiting' ? '接入后回复用户' : `由 ${selectedHandoff.assigned_agent_id} 处理中`" /><button :disabled="!canManageSelectedHandoff || !reply.trim()">发送</button></form>
          </template>
          <div v-else class="empty large"><strong>选择一条会话</strong><span>查看上下文并接入人工服务</span></div>
        </div>
      </section>

      <section v-else-if="section === 'knowledge'" class="knowledge-layout">
        <form class="panel upload" @submit.prevent="submitDocument"><p>ADD SOURCE</p><h2>上传知识文档</h2><label>文件<input type="file" accept=".txt,.md,.markdown,.pdf" required @change="upload.file = $event.target.files[0]" /></label><label>标题<input v-model="upload.title" placeholder="默认使用文件名" /></label><label>分类<select v-model="upload.category"><option value="policy">规则政策</option><option value="product">商品知识</option><option value="service">服务话术</option><option value="general">通用</option></select></label><button v-if="isAdmin" :disabled="uploading || !upload.file">{{ uploading ? '上传中…' : '上传并索引' }}</button><small v-else>仅管理员可以上传知识</small></form>
        <div class="panel documents"><div class="panel-title"><h2>知识文档</h2><span>{{ documents.length }}</span></div><div class="document-scroll"><table><thead><tr><th>标题</th><th>分类</th><th>状态</th><th>版本</th></tr></thead><tbody><tr v-for="item in documents" :key="item.id"><td><strong>{{ item.title }}</strong><small>{{ item.source_name }}</small></td><td>{{ item.category }}</td><td><span :class="['state', item.status]">{{ item.status }}</span></td><td>v{{ item.version }}</td></tr></tbody></table><div v-if="!documents.length" class="empty"><strong>暂无知识文档</strong></div></div></div>
      </section>

      <section v-else class="observability-layout">
        <div v-if="isAdmin" class="metrics"><article><span>AI Runs</span><strong>{{ metrics.ai.agent_runs || 0 }}</strong><small>平均 {{ metrics.ai.average_latency_ms || 0 }} ms</small></article><article><span>Token 用量</span><strong>{{ (metrics.ai.input_tokens || 0) + (metrics.ai.output_tokens || 0) }}</strong><small>输入 {{ metrics.ai.input_tokens || 0 }} · 输出 {{ metrics.ai.output_tokens || 0 }}</small></article><article><span>会话</span><strong>{{ metrics.service.conversations || 0 }}</strong><small>{{ metrics.service.queued || 0 }} 个排队</small></article><article><span>人工介入</span><strong>{{ metrics.service.handoffs || 0 }}</strong><small>{{ metrics.service.human || 0 }} 个进行中</small></article><article><span>售后单</span><strong>{{ metrics.commerce.after_sales || 0 }}</strong><small>{{ metrics.commerce.orders || 0 }} 笔订单</small></article></div>
        <div class="audit-grid">
          <div class="panel runs"><div class="panel-title"><h2>最近运行</h2><button v-if="isAdmin" class="ghost" @click="runEvaluation">运行评测</button></div><button v-for="run in runs" :key="run.id" class="run-row" @click="inspectRun(run)"><span><strong>{{ run.id }}</strong><small>{{ run.conversation_id }} · {{ run.model_name }}</small></span><span :class="['state', run.state.toLowerCase()]">{{ run.state }}</span><b>{{ run.latency_ms || 0 }} ms</b></button></div>
          <div class="panel inspection"><template v-if="selectedRun"><div class="panel-title"><h2>Run 详情</h2><span>{{ selectedRun.run.state }}</span></div><dl><dt>Prompt</dt><dd>{{ selectedRun.run.prompt_version }}</dd><dt>Tokens</dt><dd>输入 {{ selectedRun.run.input_tokens || 0 }} · 输出 {{ selectedRun.run.output_tokens || 0 }}</dd><dt>Outcome</dt><dd><pre>{{ JSON.stringify(selectedRun.run.outcome, null, 2) }}</pre></dd></dl><h3>工具调用</h3><article v-for="call in selectedRun.tool_calls" :key="call.tool_call_id" class="tool-call"><strong>{{ call.tool_name }}</strong><small>{{ call.latency_ms }} ms</small><pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre></article></template><div v-else class="empty large"><strong>选择一次 Run</strong><span>查看最终结果与只读工具调用审计</span></div></div>
        </div>
        <pre v-if="evaluation" class="panel evaluation">{{ JSON.stringify(evaluation, null, 2) }}</pre>
      </section>
    </main>
  </div>
</template>
