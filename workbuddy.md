# workbuddy.md · 运行环境说明

> 本文件说明「麦饭搭子」在 WorkBuddy 平台上的运行环境、安装启动方式与功能适配证明。
> 对应大赛提交要求中的「运行环境说明、启动方式、功能适配证明」。

---

## 一、运行环境

### 1.1 运行平台

| 项目 | 说明 |
| --- | --- |
| 运行平台 | **WorkBuddy**（在线运行） |
| 部署方式 | **零部署** —— 无需本地服务器、无需容器、无需域名 |
| 开发载体 | WorkBuddy 自定义 Skill + 提示词工程 + 结构化逻辑脚本 |
| 数据来源 | 麦当劳官方 MCP Server（实时接口） |
| 数据存储 | 轻量本地 JSON（`~/.maifan-dazi/profile.json`），**无数据库** |

### 1.2 语言与依赖

| 项目 | 要求 |
| --- | --- |
| Python | **3.8+**（已在 3.13.12 验证通过） |
| 第三方依赖 | **无**。全部脚本仅使用 Python 标准库 |
| 打包工具 | 无 |
| 服务端口 | 无 |

依赖清单（全部为标准库）：

```
argparse  json  itertools  math  os  re  random  shutil  struct  zlib
subprocess  sys  tempfile  time  pathlib  collections.Counter
```

> 其中 `zlib` / `struct` 被 `miniqr.py` 用来**自己拼 PNG**（手写 IHDR / IDAT / IEND 块），
> 从而做到「连二维码和图片输出都不需要第三方库」。

> 为什么坚持零依赖：赛事评审环境不可控，零依赖意味着**不会因为装不上包而跑不起来**，
> 也避免了第三方库版本冲突。

### 1.3 可选的外部工具

以下工具**仅在生成演示图片时需要**，不影响 Skill 的核心功能：

| 工具 | 用途 | 是否必需 |
| --- | --- | --- |
| Chromium 系浏览器（Edge / Chrome） | 把 `examples/*-card.html` 截图成 PNG | 否 |
| Pillow | 截掉截图留白 | 否 |
| Node.js + `jsQR` | **仅用于开发期验证**二维码能被第三方解码器扫出 | 否（产出与运行均不需要） |

> 注意：二维码的**生成**完全由 `scripts/miniqr.py` 用标准库完成；
> Node.js 与 jsQR 只出现在「我怎么证明它扫得出来」这一步，不影响任何运行环境。

---

## 二、目录结构

```
maifan-dazi/
├── SKILL.md                    # Skill 主文档，WorkBuddy 加载入口
├── README.md                   # 项目介绍
├── MCP_INTEGRATION.md          # MCP 接口对接说明（官方指定文件名）
├── CONTEST_DECLARATION.md      # 大赛官方原版声明（内容未改动）
├── mcp-config.example.json     # 脱敏 MCP 配置示例（仅环境变量占位符）
├── PROJECT_DECLARATION.md      # 项目自主声明（补充说明）
├── workbuddy.md                # 本文件
├── LICENSE                     # MIT 开源许可
├── scripts/                    # 逻辑脚本（全部零第三方依赖）
│   ├── planner.py              # 预算锁死搭配算法
│   ├── split.py                # 拼单 AA 分账引擎
│   ├── paylink.py              # 付款凭证生成器
│   ├── miniqr.py               # 零依赖二维码编码器
│   ├── parse_nutrition.py      # 营养接口原始文本解析
│   ├── fun.py                  # 三个趣味彩蛋
│   ├── profile.py              # 本地档案持久化
│   ├── pipeline.py             # 端到端编排
│   └── selftest.py             # 70 项边界用例自测
├── references/                 # 参考文档
├── connector/                  # 连接器配置示例
└── examples/                   # 演示与测试样例（含 4 张真实付款码）
```

---

## 三、安装与启动

### 3.1 方式一：作为 WorkBuddy Skill 安装（推荐）

**步骤**

1. 启用麦当劳 MCP 连接器
   - 方式 A：WorkBuddy → 左下角「连接器」入口 → 搜索「麦当劳」→ 启用
   - 方式 B：手动配置，见 [`connector/README.md`](./connector/README.md)
2. 安装 Skill —— 任选其一：
   - 把 `maifan-dazi/` 整个目录复制到 `~/.workbuddy/skills/`
   - 或导入随包分发的 Skill 包 `maifan-dazi-skill-*.zip`
     （该文件为构建产物，未随仓库源码提交，见 README「赛事提交文件清单」一节说明）
3. 在对话中直接说出需求即可触发

**触发示例**

```
3 个人，预算 100，不吃辣，帮我拼个麦当劳
宿舍 4 人拼单，预算 80，有人减脂
我想点麦乐送，2 个人 60 块
把麦香鱼永久拉黑
```

**验证安装**

执行 `python scripts/planner.py --selftest --md`，能输出完整方案即安装成功。

### 3.2 方式二：命令行直接调用

适用于不想装 Skill、只想验证算法的场景。

```bash
# 1) 查看 / 初始化本地档案
python scripts/profile.py show

# 2) 组装请求骨架（自动带上避雷清单与历史偏好）
python scripts/profile.py collect --headcount 3 --budget 10000 > request.json

# 3) 把 MCP 拉取的 menu / nutrition / coupons 填入 request.json

# 4) 运行搭配算法
python scripts/planner.py --input request.json --out result.json
python scripts/planner.py --input request.json --md   # 只要报告

# 5) 生成趣味彩蛋
python scripts/fun.py all --result result.json --persons persons.json

# 6) 多人拼单：算 AA 分账
python scripts/split.py --result result.json --persons persons.json

# 7) 要把钱收上来：出一人一码的付款凭证
python scripts/paylink.py gen --split split_out.json --order-id MFD20261009 \
    --outdir pay --out pay_output.md

# 8) 收款方校验对方转来的备注（退出码 3 = 被篡改）
python scripts/paylink.py verify --token "MFD|MFD20261009|03|3212|BD"
```

### 3.3 方式三：端到端一键跑通

```bash
python scripts/pipeline.py \
    --request examples/demo_request.json \
    --nutrition-raw examples/nutrition_raw_2026-10-09.txt \
    --persons examples/demo_persons.json \
    --seed 20261009 \
    --out examples/final_output.md
```

一条命令完成：营养解析 → 搭配算法 → 趣味彩蛋 → AA 分账 → 最终 Markdown。

多人场景可加 `--shared <公共餐品>`（可重复）声明大家平分的餐品、`--round-yuan` 额外输出抹零金额、
`--mode even|treat --payer <姓名>` 切换平摊或请客模式。

---

## 四、功能适配证明

### 4.1 环境适配

| 验证项 | 结果 |
| --- | --- |
| Python 3.8+ 可运行 | ✅ 已在 3.13.12 验证 |
| 无需第三方包 | ✅ 仅标准库 |
| 无需本地服务器 | ✅ 纯脚本 |
| 无需数据库 | ✅ 本地 JSON 文件 |
| 跨平台 | ✅ 无平台相关 API（Windows / macOS / Linux 均可） |
| 中文编码 | ✅ 全部脚本显式处理 UTF-8 |

### 4.2 功能可用性证明

运行自测套件：

```bash
python scripts/selftest.py
```

**实测结果：70 项全部通过。**

覆盖范围：

| 模块 | 用例数 | 覆盖内容 |
| --- | --- | --- |
| `planner.py` | 19 | 空菜单、缺字段、预算 0/负数、人数 0、全忌口剔除、预算缺口、多档预算零超支、1-10 人喂饱与不过度供给、元/分单位一致性、券门槛、避雷清单、营养缺失、报告完整性、单人低预算 |
| `fun.py` | 7 | 人数 0、空人员、缺字段、单餐品、种子可复现、段位边界（0% 与 30%） |
| `parse_nutrition.py` | 6 | 空输入、仅计数行、重复去重、字段映射、通俗类比、非法数值 |
| `profile.py` | 8 | 首次运行、嵌套目录自动创建、重复添加、移除不存在项、collect 带出偏好 |
| `split.py` | 13 | 最大余额法 300 组随机分配闭合性、除不尽、权重全 0、总额 0、优惠按比例返还、平摊模式、请客模式 payer 不存在、公共池除不尽、`extra_items` 不按 qty 重复计价、认领不存在的餐品、CLI `sum_check` |
| `paylink.py` + `miniqr.py` | 17 | QR 版本/纠错结构表自洽、容量单调递增、最小版本选择、超长数据报错、矩阵尺寸符合 `4v+17`、编码可复现、三个定位图形位置、深色模块占比、备注往返一致、备注长度受控、篡改被检出、多人备注不重复、PNG 文件头合法、SVG 合法、字符画行数、付款条目金额与合计一致、每条备注可自解析 |

### 4.2.1 二维码的跨语言验证（开发期完成，非运行依赖）

二维码「画得对不对」不能靠肉眼判断。因此除了内置用例，还做了一轮**跨语言端到端验证**：
Python 编码 → PNG 落盘 → Node 的第三方解码器 `jsQR` 扫描。

| 验证批次 | 结果 |
| --- | --- |
| 参考矩阵喂给 jsQR（先证明工具链本身可靠） | ✅ 正确解码 |
| 自产矩阵 30 例（L/M/Q/H × 版本 1~6 × 中文 / URL / 满容量） | ✅ **30/30 通过** |
| 真实落盘的 4 张付款码 PNG（264×264 → 放大至 1272×1272 模拟摄像头） | ✅ **4/4 通过** |

> Node + jsQR **只在开发期验证环节出现**，项目的产出与运行**完全不需要**它们。
> 详见 [README.md](./README.md)「付款码是怎么保证『扫得出来』的」一节。

### 4.3 硬性承诺的验证方式

本项目有三条核心承诺，都有可复现的验证路径。

**承诺一：实付 ≤ 预算。**

```bash
python scripts/selftest.py --only planner
```

其中「多档预算都不超支」用例会对 7 档预算逐一校验 `实付 ≤ 预算`；
「预算不足」用例校验此时会**明确报出缺口**而非偷偷超支。

算法层面，以下三条为硬约束（违反即评分返回 `-∞`，不可能被输出）：

1. 总价 > 预算
2. 餐位份额总和 < 人数
3. 总件数 > `max(人数+1, 1.8×人数)`

**承诺二：各人分账之和 == 实付。**

```bash
python scripts/selftest.py --only split
```

「最大余额法：300 组随机分配总和精确且非负」用例以随机权重与随机总额批量验证分配闭合性；
「公共池除不尽：3 人分 10 分不出错」「平摊模式除不尽：总和仍等于实付」等用例覆盖尾数边界。
CLI 输出的 `sum_check` 字段即该承诺的运行时自校验：`sum(各人应付) == 实付`。

**承诺三：付款码一定扫得出来，备注一定可机检。**

```bash
python scripts/selftest.py --only paylink
```

内置用例覆盖矩阵尺寸 `4v+17`、三个定位图形位置、深色模块占比、PNG/SVG 文件头合法性等结构性质；
在此基础上再用第三方解码器 `jsQR` 做跨语言验证（见 4.2.1）。

备注侧则由 `paylink.py verify` 提供运行时判定：

```bash
python scripts/paylink.py verify --token "MFD|MFD20261009|03|3212|BD"   # ✅ 通过，exit 0
python scripts/paylink.py verify --token "MFD|MFD20261009|03|0900|BD"   # ❌ 被篡改，exit 3
```

配合 4.2.1 的 4/4 实盘扫描，形成「**编码 → 落盘 → 扫码 → 校验**」的完整闭环。

### 4.4 实时数据适配证明

项目不含任何硬编码菜单。证明方式：

- `examples/demo_3p_100yuan.json` 中的价格均取自 2026-10-09 的 `query-meals` 实际返回
- `examples/nutrition_raw_2026-10-09.txt` 为 `list-nutrition-foods` 的原始返回文本
- 门店 `storeCode=1420762`（麦当劳深圳同方信息港餐厅）为 `query-nearby-stores` 实际返回

### 4.5 接口异常时的降级行为

| 场景 | 实测行为 |
| --- | --- |
| `query-store-coupons` 返回空数组 | 券省显示 ¥0，**不编造优惠** |
| `query-nearby-stores` 无收藏（code=600050） | 回退为按城市 + 关键词搜索 |
| 预算不足 | 明确报缺口金额，不产出超支方案 |
| 忌口过重导致无主食 | `fallback_mode=true`，降级拆单品并提示用户 |
| 营养数据缺失 | 热量显示「—」并提示缺少数据，不崩溃 |

---

## 五、Skill 触发词

在 WorkBuddy 中，以下表述会触发本 Skill：

**点餐类**：点麦当劳、拼单、多人点餐、分配预算、怎么点最省、多少钱够吃
**查询类**：有什么优惠券、这个多少钱、麦当劳热量、减脂怎么点
**梗文化**：麦饭搭子、麦门、干饭、宿舍拼单、职场干饭、下午茶拼单、团餐

---

## 六、常见问题

**Q：没有连接麦当劳 MCP 能用吗？**
A：不能产出方案。项目**不会**编造菜单与价格，会先引导你完成连接器接入。

**Q：为什么金额计算不直接交给大模型？**
A：零超支必须是可验证的硬约束。交给确定性脚本，同一份输入必得同一份输出，
既不会「算错放过」，也便于评审复现。详见 [MCP_INTEGRATION.md](./MCP_INTEGRATION.md) 第十一节。

**Q：付款码扫一下就自动付款了吗？**
A：**不会，也不应该。** 微信支付与支付宝均未向第三方开放可签发的收款链接或群收款口令
（支付宝收款码须由商户服务端经 RSA2 签名签发，官方明确说明自行拼接一律无效）。
本项目的码是**付款信息凭证**：扫出来是一条带 CRC32 校验位的转账备注，
你仍需在自己 App 内完成转账。项目不伪造这类链接，也不声称能代收款。

**Q：二维码是调第三方库生成的吗？**
A：不是。`scripts/miniqr.py` 用纯标准库实现了 ISO/IEC 18004 编码
（GF(256) 里德-所罗门纠错、8 种掩码惩罚评分、版本 1~6、纠错等级 L/M/Q/H），
连 PNG 都是手写 `zlib` + `struct` 拼出来的。这样在评审环境里不会因为装不上包而跑不起来。
正确性由第三方解码器 `jsQR` 跨语言验证（30/30 + 真实 PNG 4/4）。

**Q：避雷清单存在哪？会不会上传？**
A：存在本机 `~/.maifan-dazi/profile.json`，**不上传任何服务器**。
可用环境变量 `MAIFAN_PROFILE` 改路径。

**Q：支持哪些取餐方式？**
A：到店自提（beType=1）、得来速（beType=5）、麦乐送（beType=2）、企业团餐（beType=6）。

**Q：可以改玩梗风格吗？**
A：可以。文案库在 `scripts/fun.py` 顶部的 `TAG_RULES` / `LUCKY_BLESSINGS` / `RANK_TIERS`，
直接改常量即可，无需动逻辑。

---

**项目**：麦饭搭子 · 趣味智能拼单助手
**平台**：WorkBuddy + 麦当劳官方 MCP
**环境要求**：Python 3.8+，零第三方依赖，零部署
