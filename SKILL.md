---
name: maifan-dazi
description: 麦饭搭子·趣味智能拼单助手。当用户要点麦当劳、拼单下单、多人点餐分配预算、AA 分账、算谁该付多少、要付款码/收款码/群收款、询问麦当劳菜单/优惠券/热量，或提到「麦饭搭子」「麦门」「宿舍拼单」「职场干饭」「下午茶拼单」「点外卖怎么最省」时使用本技能。通过麦当劳 MCP 实时拉取菜单与券，在给定预算内做零超支搭配、忌口过滤、热量测算，输出拼单 AA 分账与一人一码付款凭证，并产出麦门人格标签、幸运菜品抽签和省钱战绩海报。
---

# 麦饭搭子 · 趣味智能拼单助手

## 用途

在给定人数与预算的前提下，用麦当劳 MCP 的实时菜单数据，产出一份**零超支、忌口已过滤**的拼单方案，附带热量测算、一键复制下单清单、**拼单 AA 分账**、**一人一码付款凭证**，以及三个趣味彩蛋：麦门干饭人格、幸运菜品抽签、省钱战绩海报。

硬性承诺：
1. **最终方案实付金额永远 ≤ 用户预算**。宁可少点，不可超支。
2. **各人分账金额之和永远精确等于实付金额**，不多收一分，也不少收一分。

## 何时使用

- 用户要点麦当劳 / 拼单 / 多人点餐 / 分配预算
- 用户问「多少钱够吃」「怎么点最省」「有什么优惠券」「这个多少钱」
- 用户提到「麦饭搭子」「麦门」「干饭」「宿舍拼单」「职场干饭」「团餐」
- 用户要算麦当劳餐品热量、搭配减脂餐
- 用户要把 AA 结果收上来（「发个收款码」「怎么收钱」「谁还没转」）

## 前置依赖

必须连接**麦当劳 MCP 连接器**（工具前缀 `mcp__mcd-mcp__`）。若未连接，先引导用户在连接器管理里开通，**不要凭记忆编造菜单和价格**。

## 执行流程

### 第 1 步：采集需求（对话式，一次最多问一个回合）

不要一次性抛出所有问题。每轮只问一组，并给出可点击的快捷选项：

1. **用餐场景**：宿舍拼单 / 职场干饭 / 朋友聚餐 / 单人解馋 / 亲子用餐
2. **人数**：1-10
3. **总预算**：精确到元
4. **取餐方式**：到店自提 / 得来速 / 麦乐送外卖 / 企业团餐
5. **全员忌口**：不吃辣、不吃洋葱、不吃酸、减脂低卡、不吃油炸、儿童清淡、不吃生菜 + 自定义
6. **口味偏好**：偏爱炸鸡 / 汉堡 / 甜品 / 饮品 / 无偏好

先读本地档案，已经存过的偏好**不要重复问**：

```bash
python scripts/profile.py show
```

用户说得含糊时直接按最合理假设执行并在结果里标注，不要反复追问。

### 第 2 步：定位门店

按用户选的取餐方式走对应链路，参数严格照抄 `references/mcp-api.md` 的对照表：

- **到店自提**：`query-nearby-stores(beType=1, searchType=2, city, keyword)` → `storeCode`（**不要传 beCode**）
- **得来速**：`query-nearby-stores(beType=5, ...)` → `storeCode` + `beCode`
- **麦乐送**：`delivery-query-addresses()` → `addressId` → `delivery-query-stores(beType=2, addressId)` → `storeCode` + `beCode`
- **企业团餐**：`delivery-query-stores(beType=6, addressId)`，并额外调 `query-meal-assistance` 和 `query-promotions`

门店有歧义时把列表给用户选，不要自己替用户决定。

### 第 3 步：拉数据

同一个门店要一次拉齐三份数据：

1. `query-meals(storeCode, orderType, beType[, beCode])` → 菜单与价格
2. `query-store-coupons(storeCode, orderType, beType[, beCode])` → 当前门店可用券
3. `list-nutrition-foods()` → 热量/营养参数。**该接口返回的是纯文本表格，不是 JSON**，先把 `data` 字符串存成文件再用脚本转换：

   ```bash
   python scripts/parse_nutrition.py --input nutrition_raw.txt --out nutrition.json
   ```

   把得到的 `nutrition.json` 作为 request 的 `nutrition` 字段。跳过这步会导致所有餐品显示「缺少营养数据」。

券接口有三个（可领的、卡包里的、门店可用的），别调错：搭配算价必须用 `query-store-coupons`。

### 第 4 步：跑搭配算法

把上面的数据组装成 JSON，交给 `planner.py`。这一步**不要用自然语言在脑子里硬算金额**——必须走脚本，零超支才能保证：

```bash
python scripts/planner.py --input request.json --out result.json
python scripts/planner.py --input request.json --md    # 只需面向用户的报告时，跳过落盘
```

request.json 的字段说明见脚本头部 docstring。**预算字段 `budget_fen`，单位是分**（¥100 = 10000）。
`menu` 里每项至少要有 `code`、`name`、`price_fen`；套餐额外给 `original_fen`，算法据此识别套餐省了多少并加权。

只有 `price`（元）没有 `price_fen` 时，脚本会自动换算单位；拿不准就显式传 `price_unit: "yuan"` 或 `"fen"`。

可能的结果：

- `feasible=true`：正常出方案
- `feasible=false` 且带 `error`：预算不够。**必须如实告知缺口金额**，并给出最低可行方案让用户决定是否加预算，不要偷偷超支
- `fallback_mode=true`：忌口太重，主食全被过滤掉了，已降级为拆单品组合，要向用户说明

### 第 5 步：算真实价格

拿 planner 输出的 items 调 MCP 复核：

```
calculate-price(storeCode, orderType, beType, items:[{productCode, quantity, couponId, couponCode}][, beCode])
```

返回金额**单位是分**，除以 100 再展示。若与 planner 估算有差异，以 `calculate-price` 为准并同步修正展示。

### 第 6 步：生成趣味内容

```bash
python scripts/fun.py all --result result.json --persons persons.json
```

`persons.json` 格式：`[{"name":"小明","prefer":["炸鸡"],"avoid":["辣"],"items":["麦辣鸡翅"]}]`
不传 `--persons` 时按餐位自动分配。

一轮出三块内容：麦门人格报告、幸运菜品抽签、省钱战绩海报。文案库在 `fun.py` 顶部的 `TAG_RULES` / `LUCKY_BLESSINGS` / `RANK_TIERS`，要加梗直接改常量。

### 第 7 步：拼单 AA 分账（多人拼单必做）

拼单真正会吵架的地方不是「点什么」，而是「谁该付多少」——有人拿了 ¥43 的套餐，有人只拿 ¥26，只报一个「人均」必然有人吃亏。所以多人场景必须跑分账：

```bash
python scripts/split.py --result result.json --persons persons.json
python scripts/split.py --result result.json --persons persons.json --mode even          # 人情局平摊
python scripts/split.py --result result.json --persons persons.json --mode treat --payer 老王  # 一人请客
python scripts/split.py --result result.json --persons persons.json --shared 大薯条 --shared 麦旋风 --round-yuan
```

要点：

- **默认 `fair` 按实吃分摊**：每人先按自己认领的餐品计价，再把订单优惠**按比例返还**（吃得多省得多），最后换算成实付金额。
- **公共餐品**用 `--shared` 声明（大薯条、麦乐鸡、甜品这类大家分的），所有公共餐品合成一个「AA 池」全员均分。**未被任何人认领的加购小食会自动进池**，符合真实拼单习惯。
- **零头无损**：整数除不尽的分用最大余额法分配，`各人应付之和 == 实付` 是脚本的硬保证。不要自己心算抹零。
- **`--round-yuan`** 额外给一版抹到整元的转账金额，方便真人发红包；差额由收钱方吸收，并在文案里写明。
- 输出含「和气指数 + 每人段位（散财童子 / AA 标兵 / 白嫖新秀…）」，以及**可直接复制到群里的收款文案**。

`persons.json` 也支持声明公共餐品：`{"shared": ["大薯条"], "persons": [...]}`。

### 第 8 步：组装最终输出

把 `result.json` 里的 `report_markdown`、趣味内容与分账内容拼成最终回复，顺序建议：

1. 预算与花费（含人均、结余）
2. 每人一个餐位表
3. 加购小食/饮品
4. 已自动过滤（忌口/避雷）
5. 可用优惠券 + 实付复核结果
6. 热量总览
7. 一键复制下单清单
8. 三个趣味彩蛋
9. **拼单 AA 分账 + 群收款文案**
10. **一人一码付款凭证**（多人拼单且需要收钱时）

### 第 9 步：生成付款凭证（多人拼单且要收钱时）

账算清楚了，最后一步是把钱收上来。多人拼单、用户提到「收款」「付款码」「怎么收钱」时执行：

```bash
# 已有分账结果（split.py --json 的输出）
python scripts/paylink.py gen --split split_out.json --order-id <订单号> --outdir pay --out pay_output.md

# 没有就让它内部跑一遍分账
python scripts/paylink.py gen --result result.json --persons persons.json --order-id <订单号> --outdir pay

# 收款方校验对方转来的备注
python scripts/paylink.py verify --token "MFD|MFD20261009|03|3212|BD"
```

要点：

- **必须说清楚这不是支付指令**。微信、支付宝均未向第三方开放可签发的收款链接或群收款口令
  （支付宝收款码须由商户服务端 RSA2 签名签发，自行拼接一律无效），所以这个码**不会自动唤起支付**，
  扫码后仍需用户在 App 内完成转账。**不要暗示「扫码即付款」。**
- **订单号**自己生成一个可读的即可（如 `MFD20261009`），用途是对账，不是真实商户订单号。
- 备注格式为 `MFD|<订单号>|<序号>|<金额分>|<校验位>`，末两位是 CRC32 校验位；
  收款方用 `verify` 判定真伪，被篡改时**退出码 3**，脚本会直接指出期望值与实得值。
- `--outdir` 不传时只输出 UTF-8 字符画二维码，适合纯文本会话（如邮件、无图环境）。
- 默认纠错等级 M；要在 printed 材质上印或用脏污环境扫，传 `--level H`。

### 第 10 步：是否下单

**只有用户明确说「下单」「帮我买」才调 `create-order`**，且需要：

- orderType=1 时 `takeWayCode` 取自 `calculate-price` 返回的 `takeWayList[].code`
- 外送场景 `addressId` 必填
- 创建订单前必须向用户确认最终清单与金额

下单后引导：「可通过支付链接扫码或打开麦当劳 APP 完成支付，完成后回复『支付完成』」。

## 避雷清单管理

用户说「别再给我推荐 XX」「永久拉黑 XX」时：

```bash
python scripts/profile.py blacklist add 麦香鱼 --reason "上次不新鲜"
python scripts/profile.py taste add-avoid 香菜
```

之后 `python scripts/profile.py collect --headcount 3 --budget 10000` 会自动把避雷清单和常用口味带出来，直接生成 planner 入参骨架。

## 异常处理

| 情况 | 处理 |
| --- | --- |
| MCP 未连接 | 引导用户开通连接器，禁止编造菜单价格 |
| 接口超时/报错 | 提示「当前菜单加载中，请稍后重试」，同时给经典套餐备选（但**不写具体金额**，避免误导） |
| 预算不够 | 明示缺口，给最低可行方案让用户决策 |
| 忌口过滤后无主食 | 走降级拆单品，并向用户说明 |
| 门店查不到 | 让用户换个地标或城市重试 |
| 用户以为扫付款码会自动付钱 | 明确说明「这是信息凭证，不是支付指令」，扫码后仍需手动转账 |
| 备注校验不通过 | 如实告知期望值与实际值，让用户重新扫原始码，不要「大概对得上」就算过 |

## 资源索引

- `scripts/planner.py` — 预算锁死搭配算法（零超支保证、忌口过滤、营养合并、报告生成）
- `scripts/parse_nutrition.py` — 把 `list-nutrition-foods` 返回的原始文本转成 JSON
- `scripts/fun.py` — 麦门人格 / 幸运菜品 / 省钱战绩三个趣味模块
- `scripts/split.py` — 拼单 AA 分账（按实吃分摊、公共池均分、最大余额法零头无损、和气指数）
- `scripts/paylink.py` — 付款凭证生成器，把分账结果变成「一人一码 + 带 CRC32 校验的转账备注」
- `scripts/miniqr.py` — 零依赖二维码编码器（ISO/IEC 18004，版本 1~6，纠错 L/M/Q/H，PNG/SVG/字符画）
- `scripts/profile.py` — 本地避雷清单、口味档案、历史战绩（JSON 落盘，无数据库）
- `scripts/pipeline.py` — 端到端编排，一条命令跑完「营养解析 → 搭配 → 彩蛋 → AA 分账 → 最终 Markdown」
- `scripts/selftest.py` — 70 项边界用例自测，改动算法后**务必先跑一遍**
- `references/mcp-api.md` — MCP 接口参数对照、调用链路、踩坑清单
- `references/dietary-rules.md` — 忌口与偏好的关键词词表

每个脚本都支持 `--help`，不确定参数时先看 `--help`，不要凭猜测拼参数。

## 不可违反的边界

1. **不许伪造支付能力**。微信/支付宝均未开放第三方可签发的收款链接或群收款口令，
   任何「扫码自动付款」「一键代收」的说法都是假的。本项目只产出**信息凭证**。
2. **不许编造菜单、价格、优惠**。接口返回空就如实写「券省 ¥0」。
3. **金额一律用「分」做整数运算**，最后一步才分配尾数；展示时才 ÷100。
4. `planner` 的 `extra_items` 是**逐单位重复**的列表，消费时每个元素算一个单位，
   **绝不能再乘它的 `qty`**，否则重复计价。
