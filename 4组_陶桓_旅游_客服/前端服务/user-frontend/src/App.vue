<script setup>
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { createRealtimeConnection, customerApi, listDemoUsers, login, newId, tourismApi } from './api'

marked.setOptions({ gfm: true, breaks: true })

const session = reactive({ token: localStorage.getItem('customer_token') || '', user: null })
const demoUsers = ref([])
const selectedUser = ref(localStorage.getItem('customer_id') || 'user_001')
const route = ref(`${location.pathname}${location.search}`)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const orders = ref([])
const products = ref([])
const order = ref(null)
const trip = ref(null)
const refunds = ref([])
const tickets = ref([])
const messages = ref([])
const historyMessages = ref([])
const expandedConversationIds = ref(new Set())
const conversationId = ref(null)
const conversationMode = ref('AI')
const currentTask = ref(null)
const draft = ref('')
const chatState = ref('closed')
const aiProcessingState = ref('idle')
const aiProcessingMessage = ref('')
const streaming = ref(null)
const choiceSending = ref(false)
const pendingConsult = ref(null)
const messageList = ref(null)
const refundPanel = ref(null)
const form = reactive({ refundKind: 'full', refundReason: '', travelerName: '', travelerPhone: '', ticketType: 'complaint', issueDescription: '' })
let realtime

const ORDER_TYPES = { hotel: '酒店', scenic: '景区门票', ticket: '票务', transfer: '接送服务' }
const REFUND_KIND = { full: '全额退款', partial: '部分退款', reschedule: '改期' }
const REFUND_STATUS = { pending: '待处理', approved: '已通过', processing: '处理中', refunded: '已退款', rejected: '未通过' }
const TICKET_STATUS = { open: '待处理', processing: '处理中', closed: '已关闭' }

const path = computed(() => route.value.split('?')[0])
const query = computed(() => new URLSearchParams(route.value.split('?')[1] || ''))
const orderMatch = computed(() => path.value.match(/^\/me\/orders\/([^/]+)(?:\/(cancel|travelers|refund|ticket))?$/))
const orderId = computed(() => orderMatch.value?.[1] || '')
const operation = computed(() => orderMatch.value?.[2] || '')
const selectedProfile = computed(() => demoUsers.value.find((user) => user.id === selectedUser.value))
const canCancel = computed(() => ['pending_payment', 'paid', 'upcoming'].includes(order.value?.status))
const canEditTraveler = computed(() => ['pending_payment', 'paid', 'upcoming'].includes(order.value?.status))
const canRefund = computed(() => !['cancelled', 'refunded'].includes(order.value?.status))
const serviceLabel = computed(() => conversationModeLabel(conversationMode.value))
const historyGroups = computed(() => {
  const groups = new Map()
  for (const message of historyMessages.value) {
    if (!groups.has(message.conversation_id)) {
      groups.set(message.conversation_id, {
        id: message.conversation_id,
        startedAt: message.created_at,
        messages: []
      })
    }
    groups.get(message.conversation_id).messages.push(message)
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      messages: group.messages.sort((left, right) => left.sequence - right.sequence),
      preview: messageText(group.messages[group.messages.length - 1])
    }))
    .sort((left, right) => new Date(right.startedAt) - new Date(left.startedAt))
})
const pageTitle = computed(() => {
  if (path.value === '/mall') return '产品'
  if (path.value === '/chat' || path.value === '/') return '智能客服'
  if (path.value === '/history') return '历史会话'
  if (path.value === '/me') return '我的'
  if (operation.value === 'cancel') return '取消预订'
  if (operation.value === 'travelers') return '修改出行人'
  if (operation.value === 'refund') return '申请退款'
  if (operation.value === 'ticket') return '提交工单'
  return '订单详情'
})

function navigate(to, { keepNotice = false } = {}) {
  if (!keepNotice) notice.value = ''
  history.pushState({}, '', to)
  route.value = `${location.pathname}${location.search}`
}

function conversationModeLabel(mode) {
  return { AI: 'AI 客服', BOT: 'AI 客服', QUEUED: '等待人工', HUMAN: '人工服务中', CLOSED: '已结束' }[mode] || mode
}

function unwrap(result) {
  if (!result.success) throw new Error(result.message || '请求失败')
  return result.data
}

function messageText(message) {
  const content = message.content || {}
  if (content.text) return content.text
  if (Array.isArray(content.blocks)) return content.blocks.map((block) => block.content).join('\n\n')
  if (content.type === 'order') return `已发送订单 ${content.id}`
  if (content.type === 'product') return `已发送产品 ${content.id}`
  return '暂不支持的消息'
}

function messageHtml(message) {
  return DOMPurify.sanitize(marked.parse(messageText(message)), { USE_PROFILES: { html: true } })
}

function formatSessionTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function formatMessageTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function toggleHistoryGroup(id) {
  const next = new Set(expandedConversationIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedConversationIds.value = next
}

function upsertMessage(collection, message) {
  const key = message.message_id || `${message.agent_run_id || ''}:${message.agent_outcome_seq || ''}:${message.sequence || ''}`
  const index = collection.findIndex((item) => {
    const itemKey = item.message_id || `${item.agent_run_id || ''}:${item.agent_outcome_seq || ''}:${item.sequence || ''}`
    return itemKey === key
  })
  if (index >= 0) collection[index] = message
  else collection.push(message)
}

function scrollToBottom() {
  nextTick(() => messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' }))
}

function appendMessage(message) {
  upsertMessage(messages.value, message)
  scrollToBottom()
}

function updateAiProcessing(state = 'idle', message = '') {
  aiProcessingState.value = state
  aiProcessingMessage.value = message
  scrollToBottom()
}

function handleRealtimeEvent(event) {
  const data = event.event_data || {}
  if (event.event_type === 'message_delta') {
    if (!streaming.value || streaming.value.message_id !== data.message_id) {
      streaming.value = { message_id: data.message_id, role: 'ai', text: '' }
    }
    streaming.value.text += data.delta || ''
    aiProcessingMessage.value = ''
    aiProcessingState.value = 'idle'
    scrollToBottom()
    return
  }
  if (event.event_type === 'message_created') {
    if (data.role === 'ai' && streaming.value && streaming.value.message_id === data.message_id) {
      streaming.value = null
    }
    appendMessage(data)
    if (path.value === '/history') {
      loadConversationHistory().catch((cause) => { error.value = `历史会话刷新失败：${cause.message}` })
    }
    if (data.role === 'ai') {
      if (data.content?.kind === 'error') updateAiProcessing('failed', messageText(data))
      else updateAiProcessing()
    }
    return
  }
  if (event.event_type === 'handoff_changed') {
    updateAiProcessing()
    if (data.conversation_mode) conversationMode.value = data.conversation_mode
    if (path.value === '/history') loadConversationHistory()
    else loadHistory()
  }
}

async function establishSession() {
  error.value = ''
  loading.value = true
  updateAiProcessing()
  try {
    const result = await login(selectedUser.value)
    session.token = result.access_token
    session.user = result
    localStorage.setItem('customer_token', session.token)
    localStorage.setItem('customer_id', selectedUser.value)
    realtime?.close()
    realtime = createRealtimeConnection(session.token, {
      onState: (state) => { chatState.value = state },
      onConnected: ({ reconnected }) => {
        if (reconnected) syncMissedMessages().catch((cause) => { error.value = `增量消息同步失败：${cause.message}` })
      },
      onEvent: handleRealtimeEvent,
    })
    await loadPage()
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  if (!session.token) return
  const history = await customerApi.history(session.token)
  messages.value = history.filter((message) => message.conversation_id === conversationId.value)
  await nextTick()
  messageList.value?.scrollTo({ top: messageList.value.scrollHeight })
}

async function loadConversationHistory() {
  const [current, history] = await Promise.all([
    customerApi.currentConversation(session.token),
    customerApi.history(session.token)
  ])
  conversationId.value = current.id
  conversationMode.value = current.mode
  currentTask.value = current.current_task || null
  historyMessages.value = history
  if (!expandedConversationIds.value.size && current.id) expandedConversationIds.value = new Set([current.id])
}

async function syncMissedMessages() {
  if (!session.token || !conversationId.value) return
  const lastSequence = messages.value.reduce((latest, message) => Math.max(latest, message.sequence || 0), 0)
  const history = await customerApi.history(session.token, lastSequence)
  history.filter((message) => message.conversation_id === conversationId.value).forEach((message) => upsertMessage(messages.value, message))
}

async function loadCurrentConversation() {
  if (!session.token) return
  const current = await customerApi.currentConversation(session.token)
  conversationId.value = current.id
  conversationMode.value = current.mode
  currentTask.value = current.current_task || null
  if (current.mode === 'AI' && current.is_processing) updateAiProcessing('active', 'AI 正在处理你的问题')
  else updateAiProcessing()
}

async function loadChat() {
  const [currentResult] = await Promise.allSettled([loadCurrentConversation()])
  const [historyResult, orderResult] = await Promise.allSettled([loadHistory(), tourismApi.orders(session.token)])
  const failures = []
  if (currentResult.status === 'rejected') failures.push(`当前会话加载失败：${currentResult.reason?.message || '未知错误'}`)
  if (historyResult.status === 'rejected') failures.push(`聊天记录加载失败：${historyResult.reason?.message || '未知错误'}`)
  if (orderResult.status === 'fulfilled') {
    try { orders.value = unwrap(orderResult.value) } catch (cause) { failures.push(`订单加载失败：${cause.message}`) }
  } else {
    failures.push(`订单加载失败：${orderResult.reason?.message || '未知错误'}`)
  }
  if (failures.length) error.value = failures.join('；')
}

async function loadOrder() {
  order.value = unwrap(await tourismApi.order(session.token, orderId.value))
  trip.value = null
  refunds.value = []
  tickets.value = []
  try { trip.value = unwrap(await tourismApi.trip(session.token, orderId.value)) } catch { /* 无出行信息 */ }
  try { refunds.value = unwrap(await tourismApi.refunds(session.token, orderId.value)) } catch { /* 无 */ }
  try { tickets.value = unwrap(await tourismApi.tickets(session.token, orderId.value)) } catch { /* 无 */ }
  form.travelerName = trip.value?.traveler_info?.[0]?.['姓名'] || ''
  form.travelerPhone = trip.value?.traveler_info?.[0]?.['手机'] || ''
  if (['refunds', 'tickets'].includes(query.value.get('section'))) {
    await nextTick()
    refundPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

async function loadPage() {
  if (!session.token) return
  error.value = ''
  loading.value = true
  try {
    if (path.value === '/mall') {
      products.value = unwrap(await tourismApi.products(session.token)).slice(0, 24)
    } else if (path.value === '/' || path.value === '/chat') {
      await loadChat()
      if (pendingConsult.value) {
        const { objectType, objectId, fields } = pendingConsult.value
        pendingConsult.value = null
        await sendPayload('object', { type: objectType, id: objectId, fields: fields || {} })
      }
    } else if (path.value === '/history') await loadConversationHistory()
    else if (path.value === '/me') orders.value = unwrap(await tourismApi.orders(session.token))
    else if (orderId.value) await loadOrder()
    else navigate('/mall')
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

async function sendPayload(type, content) {
  if (conversationMode.value === 'AI') updateAiProcessing('active', 'AI 正在处理你的问题')
  else updateAiProcessing()
  const optimistic = {
    message_id: newId('msg'),
    role: 'user',
    type,
    content,
    created_at: new Date().toISOString(),
    delivery_status: 'sending',
  }
  appendMessage(optimistic)
  try {
    const result = await customerApi.send(session.token, { message_id: optimistic.message_id, type, content })
    conversationId.value = result.conversation_id
    conversationMode.value = result.mode
    if (result.mode !== 'AI') updateAiProcessing()
    const current = messages.value.find((item) => item.message_id === optimistic.message_id)
    if (current?.delivery_status === 'sending') current.delivery_status = 'sent'
  } catch (cause) {
    updateAiProcessing()
    const current = messages.value.find((item) => item.message_id === optimistic.message_id)
    if (current?.delivery_status === 'sending') current.delivery_status = 'failed'
    error.value = cause.message
  }
}

async function sendMessage() {
  const text = draft.value.trim()
  if (!text || loading.value) return
  draft.value = ''
  await sendPayload('text', { text })
}

function consultObject(objectType, objectId, fields = {}) {
  pendingConsult.value = { objectType, objectId, fields }
  navigate('/chat')
}

async function submitCancel() {
  if (!confirm(`确认取消预订 ${orderId.value}？此操作会立即生效。`)) return
  await runWrite(async () => unwrap(await tourismApi.cancel(session.token, orderId.value)), '预订已取消', `/me/orders/${orderId.value}`)
}

async function submitTraveler() {
  const travelerInfo = [{ 姓名: form.travelerName, 手机: form.travelerPhone }]
  await runWrite(async () => unwrap(await tourismApi.updateTravelers(session.token, orderId.value, travelerInfo)), '出行人已更新', `/me/orders/${orderId.value}`)
}

async function submitRefund() {
  await runWrite(
    async () => unwrap(await tourismApi.createRefund(session.token, orderId.value, { kind: form.refundKind, reason: form.refundReason })),
    '退款申请已提交',
    `/me/orders/${orderId.value}?section=refunds`,
  )
}

async function submitTicket() {
  await runWrite(
    async () => unwrap(await tourismApi.createTicket(session.token, orderId.value, { ticket_type: form.ticketType, issue_description: form.issueDescription })),
    '工单已提交',
    `/me/orders/${orderId.value}?section=tickets`,
  )
}

async function runWrite(action, successMessage, target) {
  loading.value = true
  error.value = ''
  try {
    await action()
    notice.value = successMessage
    navigate(target, { keepNotice: true })
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

function actionHref(message) {
  return message.content?.action?.href
}

function messageChoices(message) {
  const choices = message.content?.choices
  if (!Array.isArray(choices)) return []
  return choices.filter((choice) => (['order', 'product'].includes(choice?.type) && typeof choice?.id === 'string' && typeof choice?.label === 'string'))
}

async function selectChoice(choice) {
  if (choiceSending.value) return
  choiceSending.value = true
  try {
    await sendPayload('object', { type: choice.type, id: choice.id, fields: {} })
  } finally {
    choiceSending.value = false
  }
}

onMounted(async () => {
  window.addEventListener('popstate', () => { route.value = `${location.pathname}${location.search}` })
  demoUsers.value = await listDemoUsers().catch(() => [])
  await establishSession()
})
onBeforeUnmount(() => realtime?.close())
watch(route, loadPage)
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <button class="brand" @click="navigate('/mall')"><span>旅小满</span> 旅游</button>
      <nav aria-label="主导航">
        <button :class="{ active: path === '/mall' }" @click="navigate('/mall')">产品</button>
        <button :class="{ active: path === '/chat' || path === '/' }" @click="navigate('/chat')">客服</button>
        <button :class="{ active: path === '/history' }" @click="navigate('/history')">历史</button>
        <button :class="{ active: path.startsWith('/me') }" @click="navigate('/me')">我的</button>
      </nav>
      <div class="account">
        <select v-model="selectedUser" aria-label="切换体验账号" @change="establishSession">
          <option v-for="user in demoUsers" :key="user.id" :value="user.id">{{ user.name }}</option>
        </select>
      </div>
    </header>

    <main :class="['page', { 'chat-page': path === '/chat' || path === '/' }]">
      <div v-if="error" class="alert error" role="alert">{{ error }} <button aria-label="关闭错误提示" @click="error = ''">×</button></div>
      <div v-if="notice" class="alert success" role="status">{{ notice }} <button aria-label="关闭成功提示" @click="notice = ''">×</button></div>

      <template v-if="path === '/mall'">
        <section class="mall-hero">
          <div>
            <p class="eyebrow">TRAVEL SELECT</p>
            <h1>挑一个，问清楚</h1>
            <p>这里是旅游产品清单。想了解详情、房态或余票，直接带着产品去问客服。</p>
          </div>
          <span>{{ products.length }} 个产品</span>
        </section>
        <section class="mall-grid" aria-label="旅游产品">
          <article v-for="item in products" :key="item.id" class="mall-card">
            <div class="mall-art"><span class="mall-art-badge">{{ ORDER_TYPES[item.type] || item.type }}</span></div>
            <div class="mall-card-body">
              <p>{{ item.city }} · {{ ORDER_TYPES[item.type] || item.type }}</p>
              <h2>{{ item.name }}</h2>
              <div class="mall-card-footer">
                <strong>¥{{ Number(item.price).toFixed(2) }}</strong>
                <button @click="consultObject('product', item.id)">咨询产品 <span>→</span></button>
              </div>
            </div>
          </article>
          <div v-if="!products.length" class="empty-state"><h2>暂无产品</h2><p>产品正在准备中。</p></div>
        </section>
      </template>

      <template v-else-if="path === '/chat' || path === '/'">
        <section class="chat-layout">
          <div class="chat-workspace">
            <section class="chat-intro">
              <div><p class="eyebrow">AI TRAVEL SERVICE</p><h1>有问题，直接问</h1><p>我会查询订单、出行信息和平台规则，并引导你到对应页面办理业务。</p></div>
              <div class="chat-status">
                <span :class="['service-mode', conversationMode.toLowerCase()]">{{ serviceLabel }}</span>
                <span :class="['connection', chatState]">{{ chatState === 'connected' ? '在线' : '连接中' }}</span>
              </div>
            </section>
            <div v-if="currentTask" class="task-hint">当前任务：{{ currentTask.task_type }}
              <span v-if="currentTask.missing_slots?.length">（待补充：{{ currentTask.missing_slots.join('、') }}）</span>
            </div>
            <section ref="messageList" class="messages" aria-label="对话消息" aria-live="polite">
              <div v-if="!messages.length" class="empty-chat">
                <strong>可以试试</strong>
                <button @click="draft = '帮我查订单 ORD_SANYA_001 的出行信息'; sendMessage()">帮我查订单出行信息</button>
                <button @click="draft = '三亚有哪些五星级酒店'; sendMessage()">三亚有哪些五星级酒店</button>
                <button @click="draft = '我要退款'; sendMessage()">我要退款</button>
              </div>
              <article v-for="message in messages" :key="message.message_id || message.sequence" :class="['message', message.role]">
                <span class="avatar">{{ message.role === 'user' ? '我' : message.role === 'human' ? '人' : 'AI' }}</span>
                <div class="bubble">
                  <div v-if="message.role === 'user'" class="message-text">{{ messageText(message) }}</div>
                  <div v-else class="message-text markdown-body" v-html="messageHtml(message)"></div>
                  <small v-if="message.delivery_status === 'sending'" class="delivery-state">发送中</small>
                  <small v-else-if="message.delivery_status === 'failed'" class="delivery-state failed">发送失败</small>
                  <div v-if="messageChoices(message).length" class="choice-list" aria-label="请选择咨询对象">
                    <button v-for="choice in messageChoices(message)" :key="`${choice.type}:${choice.id}`" :disabled="choiceSending" @click="selectChoice(choice)">
                      <span><strong>{{ choice.label }}</strong><small>{{ choice.description }}</small></span><b>→</b>
                    </button>
                  </div>
                  <button v-if="actionHref(message)" class="action-card" @click="navigate(actionHref(message))">
                    <span><strong>{{ message.content.action.label }}</strong><small>{{ message.content.action.description }}</small></span><b>→</b>
                  </button>
                </div>
              </article>
              <article v-if="streaming" class="message ai">
                <span class="avatar">AI</span>
                <div class="bubble"><div class="message-text markdown-body" v-html="DOMPurify.sanitize(marked.parse(streaming.text || '…'))"></div></div>
              </article>
              <article v-if="aiProcessingMessage" :class="['message', 'ai-processing', aiProcessingState]" role="status" aria-live="polite">
                <span class="avatar">AI</span>
                <div class="bubble processing-bubble">
                  <span v-if="aiProcessingState === 'active'" class="processing-dots" aria-hidden="true"><i></i><i></i><i></i></span>
                  <span v-else class="processing-failed-mark" aria-hidden="true">!</span>
                  <span>{{ aiProcessingMessage }}</span>
                </div>
              </article>
            </section>
            <form class="composer" @submit.prevent="sendMessage">
              <textarea v-model="draft" rows="2" aria-label="输入客服问题" placeholder="描述你的问题；业务操作会在对应页面确认" @keydown.enter.exact.prevent="sendMessage" />
              <button :disabled="!draft.trim()">发送</button>
            </form>
          </div>
        </section>
      </template>

      <template v-else-if="path === '/history'">
        <section class="history-hero">
          <div><p class="eyebrow">CONVERSATION ARCHIVE</p><h1>历史会话</h1><p>按会话整理，展开可回看完整咨询记录。</p></div>
          <span>{{ historyGroups.length }} 次会话</span>
        </section>
        <section class="history-list" aria-label="历史会话列表">
          <article v-for="(group, index) in historyGroups" :key="group.id" :class="['history-group', { expanded: expandedConversationIds.has(group.id) }]">
            <button class="history-group-head" :aria-expanded="expandedConversationIds.has(group.id)" @click="toggleHistoryGroup(group.id)">
              <span class="history-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <span class="history-summary">
                <span><strong>{{ formatSessionTime(group.startedAt) }}</strong><b v-if="group.id === conversationId">当前会话</b></span>
                <small>{{ group.preview }}</small>
              </span>
              <span class="history-count">{{ group.messages.length }} 条 <b>⌄</b></span>
            </button>
            <div v-if="expandedConversationIds.has(group.id)" class="history-transcript">
              <article v-for="message in group.messages" :key="message.message_id || message.sequence" :class="['history-message', message.role]">
                <span>{{ message.role === 'user' ? '我' : message.role === 'human' ? '人工客服' : 'AI 客服' }}</span>
                <div><p>{{ messageText(message) }}</p><time :datetime="message.created_at">{{ formatMessageTime(message.created_at) }}</time></div>
              </article>
            </div>
          </article>
          <div v-if="!historyGroups.length" class="empty-state"><h2>还没有历史会话</h2><p>完成第一次咨询后，会话记录会显示在这里。</p></div>
        </section>
      </template>

      <template v-else-if="path === '/me'">
        <section class="profile-card">
          <div class="profile-avatar">{{ session.user?.display_name?.slice(0, 1) || '我' }}</div>
          <div><p class="eyebrow">MEMBER CENTER</p><h1>{{ session.user?.display_name || '我的' }}</h1><p>{{ selectedProfile?.membership || '普通会员' }} · {{ selectedUser }}</p></div>
        </section>
        <header class="section-header"><div><h2>我的订单</h2><p>查看订单详情，并在订单内办理取消预订、修改出行人和退款。</p></div><span>{{ orders.length }} 笔</span></header>
        <section class="card-list">
          <article v-for="item in orders" :key="item.id" class="order-card">
            <div class="order-meta"><span>{{ item.id }}</span><b>{{ item.status_label }}</b></div>
            <h3>{{ item.items.map((entry) => entry.product_name).join('、') }}</h3>
            <div class="order-footer"><span>{{ item.travel_start_date || new Date(item.created_at).toLocaleDateString() }}</span><strong>¥{{ Number(item.total_amount).toFixed(2) }}</strong></div>
            <div class="order-actions">
              <button class="order-detail" @click="navigate(`/me/orders/${item.id}`)">订单详情</button>
              <button class="consult-order" @click="consultObject('order', item.id)">咨询订单 <span>→</span></button>
            </div>
          </article>
          <div v-if="!orders.length" class="empty-state"><h2>暂无订单</h2><p>当前账号还没有订单记录。</p></div>
        </section>
      </template>

      <template v-else-if="order">
        <header class="page-header">
          <div><button class="back" @click="navigate(operation ? `/me/orders/${order.id}` : '/me')">← 返回</button><p class="eyebrow">{{ order.id }}</p><h1>{{ pageTitle }}</h1></div>
          <span class="status-pill">{{ order.status_label }}</span>
        </header>

        <section v-if="!operation" class="detail-grid">
          <article class="panel wide">
            <h2>产品信息</h2>
            <div v-for="item in order.items" :key="item.product_id" class="line-item">
              <span><strong>{{ item.product_name }}</strong><small>{{ ORDER_TYPES[item.product_type] || item.product_type }} · ×{{ item.quantity }}</small></span>
              <b>¥{{ Number(item.unit_price).toFixed(2) }}</b>
            </div>
            <p v-if="order.travel_start_date" class="muted">出行日期：{{ order.travel_start_date }} 至 {{ order.travel_end_date }}</p>
            <div class="total"><span>实付款</span><strong>¥{{ Number(order.total_amount).toFixed(2) }}</strong></div>
          </article>

          <article class="panel">
            <h2>出行信息</h2>
            <template v-if="trip">
              <p v-if="trip.checkin_date">入住 {{ trip.checkin_date }} / 退房 {{ trip.checkout_date }}</p>
              <p v-if="trip.transport_no">{{ trip.transport_no }} · {{ trip.departure_time }} → {{ trip.arrival_time }}</p>
              <p v-if="trip.meeting_point">集合点：{{ trip.meeting_point }}</p>
              <small v-for="(ev, i) in trip.events || []" :key="i">{{ ev.time }} {{ ev.description }}</small>
            </template>
            <p v-else>暂时没有出行信息</p>
          </article>

          <article class="panel">
            <h2>出行人</h2>
            <template v-if="trip && trip.traveler_info && trip.traveler_info.length">
              <p v-for="(t, i) in trip.traveler_info" :key="i">{{ t['姓名'] || t.name || '出行人' }} {{ t['手机'] || t.phone || '' }}</p>
            </template>
            <p v-else>暂无出行人信息</p>
            <button v-if="canEditTraveler" class="text-button" @click="navigate(`/me/orders/${order.id}/travelers`)">修改出行人 →</button>
            <small v-else>当前订单状态不可修改</small>
          </article>

          <article ref="refundPanel" class="panel wide">
            <div class="panel-title"><h2>退款记录</h2><button v-if="canRefund" @click="navigate(`/me/orders/${order.id}/refund`)">申请退款</button></div>
            <div v-for="item in refunds" :key="item.id" class="after-sale-row">
              <span class="status-pill">{{ REFUND_STATUS[item.status] || item.status }}</span>
              <div><strong>{{ REFUND_KIND[item.kind] || item.kind }}</strong><p>{{ item.reason }}</p><small>{{ item.id }} · ¥{{ item.amount ?? '-' }}</small></div>
            </div>
            <p v-if="!refunds.length" class="muted">该订单暂无退款记录。</p>
            <div v-if="tickets.length" class="panel-title"><h3>工单</h3></div>
            <div v-for="item in tickets" :key="item.id" class="after-sale-row">
              <span class="status-pill">{{ TICKET_STATUS[item.status] || item.status }}</span>
              <div><strong>{{ item.ticket_type }}</strong><p>{{ item.issue_description }}</p><small>{{ item.id }}</small></div>
            </div>
          </article>

          <article class="panel actions wide">
            <h2>订单操作</h2>
            <button v-if="canCancel" class="danger-outline" @click="navigate(`/me/orders/${order.id}/cancel`)">取消预订</button>
            <button v-if="canRefund" class="text-button" @click="navigate(`/me/orders/${order.id}/refund`)">申请退款</button>
            <button class="text-button" @click="navigate(`/me/orders/${order.id}/ticket`)">提交工单</button>
          </article>
        </section>

        <section v-else class="operation-layout">
          <article class="panel order-summary"><span>{{ order.status_label }}</span><h2>{{ order.items.map((item) => item.product_name).join('、') }}</h2><p>订单 {{ order.id }} · ¥{{ Number(order.total_amount).toFixed(2) }}</p></article>
          <article v-if="operation === 'cancel'" class="panel operation-panel">
            <h2>确认取消</h2>
            <p>{{ canCancel ? '提交前已重新读取订单状态。取消成功后不可恢复。' : '订单当前状态不能直接取消。' }}</p>
            <button class="danger" :disabled="loading || !canCancel" @click="submitCancel">确认取消预订</button>
          </article>
          <form v-else-if="operation === 'travelers'" class="panel operation-panel" @submit.prevent="canEditTraveler && submitTraveler()">
            <h2>修改出行人</h2>
            <p v-if="!canEditTraveler">订单当前状态不能修改出行人。</p>
            <label>姓名<input v-model="form.travelerName" required :disabled="!canEditTraveler" /></label>
            <label>手机号<input v-model="form.travelerPhone" required :disabled="!canEditTraveler" /></label>
            <button :disabled="loading || !canEditTraveler">保存出行人</button>
          </form>
          <form v-else-if="operation === 'ticket'" class="panel operation-panel" @submit.prevent="submitTicket()">
            <h2>提交工单</h2>
            <label>工单类型<select v-model="form.ticketType"><option value="after_sale">售后</option><option value="complaint">投诉</option><option value="refund">退款</option></select></label>
            <label>问题描述<textarea v-model="form.issueDescription" rows="4" minlength="2" maxlength="1000" placeholder="请描述遇到的问题" required /></label>
            <button :disabled="loading || !form.issueDescription.trim()">提交工单</button>
          </form>
          <form v-else class="panel operation-panel" @submit.prevent="canRefund && submitRefund()">
            <h2>退款信息</h2>
            <p v-if="!canRefund">该订单当前不能发起退款。</p>
            <label>退款类型<select v-model="form.refundKind" :disabled="!canRefund"><option value="full">全额退款</option><option value="partial">部分退款</option><option value="reschedule">改期</option></select></label>
            <label>退款原因<textarea v-model="form.refundReason" rows="4" minlength="2" maxlength="300" placeholder="请说明退款原因" required :disabled="!canRefund" /></label>
            <button :disabled="loading || !canRefund">提交退款申请</button>
          </form>
        </section>
      </template>
    </main>
  </div>
</template>
