---
name: cs-tourism
description: 旅游智能客服系统（本目录）的开发与改造 Skill：tourism-service fork、ai-service 工具旅游化、TaskState 多轮槽位、token 级流式、评测与验收。
---

# 旅游智能客服系统开发 Skill

本目录是旅游智能客服的**工作副本**，所有改造在此进行；源仓库只读。

## 服务与端口

| 服务 | 端口 | 说明 |
|---|---|---|
| Customer Service | 8000 | 会话/消息/Turn/工单/Outbox/WS；含 `Conversation.task_state` |
| Tourism Service | 8001 | 只读业务数据 + 受控写接口（fork 自课程 ecommerce-service） |
| AI Service | 8002 | Agent：工具/Skill/校验/TaskState/NDJSON 流式/评测 |
| 知识库 Knowledge | 8005 | 旅游知识检索（只返回片段） |

## 已完成改造（按阶段）

1. **tourism-service fork**：包名 `tourism_service`；旅游实体（TravelProduct/TripInfo/Refund/Ticket）；6 城种子；只读 + 受控写接口（退款/工单/取消/改出行人）。
2. **ai-service 工具旅游化**：`TourismDataClient`；7 个只读工具重命名；输出模型、Skill、Prompt、平台规则、Fact/Action、P0/P1 收口。
3. **TaskState 多轮槽位**：CS `Conversation.task_state` JSONB + 透传/写回；AI `tasks/{schema,state,reducer}` + `AgentOutput.task_update` + context 注入。
4. **token 级流式**：AI `/runs` 返回 NDJSON（`run_delta` + 最终事件）；CS 网关流式消费、Outbox `message_delta`。
5. **评测旅游化**：`eval/datasets` 换旅游集；`run_eval` 动作映射/规则旅游化。

## 待办

- 前端（`前端服务/user-frontend`、`admin-frontend`）旅游化，见 `前端服务/前端改造待办.md`。
- 环境联调：8005 知识库就绪后打通知识链路；`confirm_run` 前重查（P0-3）等 Harness 增强。

## 工作方式

- 源仓库只读，只复制不改：
  - `E:\LLM_student_project\customer-service`
  - `E:\LLM_student_project\ai-service`
  - 课程 ecommerce-service（tourism-service 的 fork 基础）
- 设计文档：仓库根目录 `客服项目改造设计.md`、`任务状态与多轮槽位设计.md`、`总开工包.md`。
