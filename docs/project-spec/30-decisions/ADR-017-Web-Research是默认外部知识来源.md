# ADR-017 Web Research 是默认外部知识来源

## 状态

ACCEPTED

## 决策

当缺失的是公开的项目方法论、行业知识、标准、当前技术信息或实现方案时：

```text
Web Research
```

是默认外部知识来源。

Project Runtime 不需要预置这些知识。

客户私有事实仍然走 User Clarification。

## 原因

外部公开知识动态变化且领域无限，无法通过核心程序或预装 Skill 完整覆盖。

## V1.9 修订

V1.9 修订：Web Research 仍是公开外部知识的默认来源，但不再是项目方法生成的默认第一步，仅在公开 Gap 触发。
