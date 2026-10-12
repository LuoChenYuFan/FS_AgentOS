---
name: amount_from_text
description: 从制度或知识库原文里取出金额。用户要把费用和差旅标准、报销上限比较时，先用本 Skill 取出标准金额。
entrypoint: scripts/amount_from_text.py
---

# 从原文取金额

## 何时使用

前面已经检索到制度、报销标准或知识库原文，需要从这段文字里取出一个金额时使用本 Skill。

## 如何调用

```text
run_skill(name="amount_from_text", text="${{knowledge.chunks}}")
```

`text` 放检索到的原文，不要改写，也不要自己先算出数字再传进来。

## 返回

- `amount`：取出的金额。优先取「标准」「报销」「上限」「不超过」「低于」附近的数字；没有这些词时取文中第一个数字；一个数字都没有则为 0。

## 约束

- 只取原文里写明的数字，不要估算，不要四舍五入成另一个标准。
