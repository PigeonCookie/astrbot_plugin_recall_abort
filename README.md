# astrbot_plugin_recall_abort

**撤回即作废** —— 用户在 QQ 里撤回消息后，正在生成的回复在发送前被丢弃，模型立刻停止分析，一个字都不发出去。

## 为什么需要

AstrBot 的默认行为是：消息发出后就开始生成回复，用户这时撤回，回复还会照常发出去。
本插件在撤回的瞬间把这条消息标记为作废，并在三个位置各自拦一道：

1. `event_message_type` 钩子抓 `group_recall` / `friend_recall` 通知，记下被撤回的 `message_id`；
2. `on_llm_request` 进模型前先查一次，已经撤回就不浪费 token；
3. `on_decorating_result` 预发送前最后一道闸，命中就 `clear_result()` + `stop_event()`。

## 安装

在 AstrBot 的插件目录下克隆：

```bash
cd data/plugins
git clone https://github.com/PigeonCookie/astrbot_plugin_recall_abort.git
```

然后重启 AstrBot，或在 WebUI 的插件页里重载。

## 配置

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enable` | bool | `true` | 总开关，关闭后恢复 AstrBot 默认行为 |
| `window` | int | `120` | 撤回记录的保留时长（秒），超过清掉 |
| `notify` | bool | `true` | 在日志里记录每次撤回拦截 |

## 已知限制

- 撤回通知比回复**慢**的时候拦不住 —— 这是平台时序决定的，不是插件的问题。
- 依赖 `aiocqhttp` 适配器上报的撤回 notice 事件。

## 许可

MIT
