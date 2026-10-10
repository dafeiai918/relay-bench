# relay-bench · 中转站体检

一行命令，测出你的 OpenAI 兼容中转站**到底行不行**。

零依赖 · 单文件 · 跨平台 · 中文报告

---

## 测什么

| 项目 | 分值 | 说明 |
|---|---|---|
| 连通性与模型列表 | 20 | 接口可达性、响应耗时、可用模型数 |
| 流式输出 | 10 | 是否真流式、首 Token 延迟 |
| 并发能力 | 20 | N 并发成功率、平均延迟、P95 |
| 参数兼容性 | 25 | `max_tokens` / `temperature` / `json_mode` / `stop` 是否真生效 |
| 能力抽样 | 25 | 4 道陷阱题，识别"降智"信号 |

总分 100，自动分级 A-E。

---

## 用法

```bash
# 最简：直接跑
python relay_bench.py -u https://api.example.com/v1 -k sk-xxxx

# 指定模型、并发数
python relay_bench.py -u https://api.example.com/v1 -k sk-xxxx -m gpt-4o-mini -c 10

# 用环境变量（推荐，避免密钥进 shell 历史）
export RELAY_BASE_URL=https://api.example.com/v1
export RELAY_API_KEY=sk-xxxx
python relay_bench.py

# 导出报告
python relay_bench.py -u ... -k ... --json report.json --markdown report.md

# 跳过重项（快速跑）
python relay_bench.py -u ... -k ... --skip concurrency,params
```

Windows PowerShell：

```powershell
$env:RELAY_BASE_URL="https://api.example.com/v1"
$env:RELAY_API_KEY="sk-xxxx"
python relay_bench.py
```

---

## 报告长什么样

```
════════════════════════════════════════════════════════
  relay-bench · 中转站体检报告  v1.0.0
════════════════════════════════════════════════════════
  站点    https://api.example.com/v1
  密钥    sk-abc****f9e2
  模型    deepseek-chat
  耗时    18.4s

【1】连通性与模型列表                     ✅ 20/20
   ✓ 接口连通          0.42s
   ✓ 模型数量          87 个

【2】流式输出                             ✅ 10/10
   ✓ 首Token延迟       0.61s

【3】并发能力（5并发）                    ⚠️ 12/20
   ✓ 5并发成功率       80%
   ✓ 平均延迟          2.31s
   ✗ P95延迟           11.20s

【4】参数兼容性                           ✅ 25/25
   ✓ max_tokens       返回1240字
   ✓ temperature       47/47
   ✓ json_mode         合法JSON
   ✓ stop参数          stop生效

【5】能力抽样（启发式）                   ✅ 25/25
   ✓ 9.11 与 9.9 哪个… 9.9
   ✓ 单词 strawberr…   3
   ✓ 如果今天是星期…   星期六
   ✓ 篮子里有5个苹果…  6

════════════════════════════════════════════════════════
  总分    92/100   等级 A
════════════════════════════════════════════════════════
  推荐中转站  好同根  https://haotogen.com/
════════════════════════════════════════════════════════
```

---

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 总分 ≥ 60（可用） |
| 1 | 总分 < 60（不建议使用） |
| 2 | 无法完成体检（地址/密钥错误） |

方便写进 CI 或监控脚本：

```bash
python relay_bench.py -u "$URL" -k "$KEY" || echo "站点异常，告警！"
```

---

## 说明与免责

- **能力抽样不是模型真伪鉴定**，只是 4 道常见陷阱题的启发式信号，可能误判
- 所有测试均为标准 OpenAI 接口调用，不涉及任何破解或逆向
- 请只在你自己有权使用的站点上运行
- 测试会产生少量 Token 消耗

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [OneClickRelay](https://github.com/dafeiai918/oneclickrelay) | 一键把中转站配置进 60+ AI 客户端 |
| [relay-converter](https://github.com/dafeiai918/relay-converter) | 一份配置转成所有客户端格式 |
| [awesome-relay](https://github.com/dafeiai918/awesome-relay) | 中转站与客户端精选导航 |

---

## License

MIT
