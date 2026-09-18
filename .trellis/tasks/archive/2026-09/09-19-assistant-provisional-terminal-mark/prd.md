# G2：取消/失败后临时正文的终态标记

## 状态与目标

planning；父任务 `09-17-dual-agent-latency-result-integrity`，主会话内联。F 的真实浏览器取消验收（UI2-6）发现：run 已终态为 `cancelled`、无错误横幅、无无限等待，但**临时气泡仍显示「生成中…」**。目标：终态到达时把已显示的临时正文标为终态，不再声称仍在生成。

## 背景与证据

UI2-6（`09-17-dual-agent-controlled-acceptance`，`main@563e670`）实测：

```
event_types = [message.user_added, run.started, turn.started, assistant.text_delta, run.cancelled]
provisional_before_cancel = 1
provisional_after          = 1   ← 取消后仍在
assistant_count            = 2
pending_indicator          = False
error_notice               = null
```

即：`run.cancelled` 已到，`finishRun` 把 `partition.error` 置 null（正确），但**没有任何路径清理或重标 `provisional: true` 的气泡**，而 `MessageList.vue` 对 `item.provisional === true` 渲染 `data-test="provisional-mark"` 的「生成中…」。

这与 09-18 增量显示设计的表格直接冲突：

> | failed / cancelled | 保留已显示的安全部分并标终态 | 不当作完成答案或历史 |

现状是「保留」了，但**没有标终态**——用户在取消后看到一句永远「生成中…」的半截正文，无法判断它已经结束。

## 需求

| ID | 要求 |
| --- | --- |
| G2-R1 | `run.cancelled` / `run.failed` / `run.expired` 到达时，临时投影必须进入终态展示（不再标「生成中…」）。 |
| G2-R2 | 保留已显示的安全正文（设计明确要求「保留已显示的安全部分」），不得改成清空；也不得把它当完成答案（不得进历史/引用绑定）。 |
| G2-R3 | 与 `assistant.text_reset` 的语义区分保持：reset 是「丢弃」（隐藏），终态是「保留但结束」。两者不可合并。 |
| G2-R4 | 切换会话/切换空间仍丢弃临时投影（既有行为不回退）。 |

## 验收

| ID | 可观察结果 |
| --- | --- |
| G2-AC1 | F 的 UI2-6 通过：`run.cancelled` 后 `provisional_after == 0`（或该气泡不再带「生成中…」标记），且已显示正文仍在。 |
| G2-AC2 | store 回归：`text_delta → run.cancelled` 后气泡仍在、文本不变、但不再是 provisional；`run.failed` 同；`text_reset` 仍是隐藏。 |
| G2-AC3 | 既有 `assistant.text_reset`/权威替换/切换会话丢弃的回归不退化。 |
| G2-AC4 | frontend 门禁全绿（lint/type-check/test/build）。 |

## 依赖与非目标

依赖 F 的 UI2-6 探针（已存在）与 09-18 已归档的增量显示实现。与 D2（run 级压缩）不共享文件。

不实施：不改 sidecar/后端事件协议、不改取消的服务端收敛（D-F1 已修）、不新增历史写入、不给临时正文加引用或持久 id、不改变「生成中…」在**真正进行中**时的展示。

## 开放问题（实现前需定）

设计表格说「标终态」，但**没有定义终态标签长什么样**。可选：移除 `provisional-mark` 而不加新标签（最小、只消除误导），或加一个「已取消/已中断」标记。后者是新的用户可见文案，属产品决定，需在 design.md 里明确后再实现；默认按最小改动（移除「生成中…」，保留正文）处理。
