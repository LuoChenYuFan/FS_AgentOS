---
name: subtract
description: 把两个已经查出的数字相减。用户要比较费用和标准、询问超了多少时使用。禁止自己心算。
entrypoint: scripts/subtract.py
---

# 数字相减

## 何时使用

两个数字都已经由前面的技能查出，需要计算差值时使用本 Skill。酒店用均价，路线用费用，标准金额用从制度原文取出的数字。

## 如何调用

```text
run_skill(name="subtract", left="${{hotel.avg_price}}", right="${{cap.amount}}")
```

- `left`：被减数，例如酒店均价或路线费用。
- `right`：减数，例如差旅标准金额。

## 返回

- `value`：`left - right`。大于 0 表示超出，小于 0 表示低于标准。

## 约束

- 不要把还没查出的数字写死在参数里。
- 差额只能由本脚本计算，不要让模型直接报一个差值。
