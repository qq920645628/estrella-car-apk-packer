# Security Policy · 安全说明

## Token handling · 令牌处理

The published copy **does not contain a working token**. Every hardcoded token was replaced with the
placeholder `<YOUR_KB_TOKEN>` before publication.

公开副本**不含可用令牌**，所有内置令牌均已替换为占位符 `<YOUR_KB_TOKEN>`。

Inject your token via environment variables instead of editing source files:

```bash
export ESTRELLA_KB_TOKEN="your-token"     # Linux/macOS
set ESTRELLA_KB_TOKEN=your-token          # Windows CMD
$env:ESTRELLA_KB_TOKEN="your-token"       # PowerShell
```

If you accidentally commit a real token: rotate it immediately on the server
(`KB_TOKEN` in the server `.env`, restart the service). Knowledge-base writes are
token-gated, so a leaked token allows record injection — rotate, don't just delete.

若误提交真实令牌：请立即在服务端轮换（`server/.env` 的 `KB_TOKEN` 并重启）。知识库写入受令牌保护，
泄漏意味着他人可注入记录 —— 必须轮换而非仅删除。

## What never leaves your machine · 不会外泄的内容

- Your source tree and signing keys (`*.jks`, `keystore.properties`) — builds are local.
- Desensitisation (whitelist + value-level scrubbing of paths / secrets / key files) runs
  **before** any write, local or remote.

## Known distribution caveat · 已知分发提示

The cloud source snapshot distributed by the upstream server contains an **experimental community
MFi identity that is not Apple-certified**. Distribution is subject to Apple's MFi Program terms.
Host your own server if you need to control what is served.

云端源码快照含**社区实验性 MFi 身份（非 Apple 认证）**，分发受 Apple MFi 计划条款约束；
若需完全自控，请自建服务端。

## Reporting · 报告问题

Open a GitHub issue. Do **not** include tokens, keystores or personal paths in issue text.
